"""Git lifecycle management for agent evaluation."""

import math
import os
import json
import re
import stat
import shutil
import tempfile
import subprocess


_SANITIZED_PREFIX = "__sanitized__:"

# Sidecar file written inside the workspace after sanitization so that the
# partial-setup fallback in command.py can locate the .git backup even when
# setup_starting_point() never returns.
_SANITIZE_SIDECAR = ".agent_eval_sanitize_meta.json"


def git_run(args: list[str], directory: str, timeout: int = 60,
            check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command, optionally raising on failure."""
    result = subprocess.run(
        ["git"] + args,
        cwd=directory, capture_output=True, text=True, timeout=timeout,
    )
    if check and result.returncode != 0:
        cmd_str = " ".join(["git"] + args)
        raise RuntimeError(f"git command failed: {cmd_str}\n{result.stderr.strip()}")
    return result


def _make_sanitized_ref(saved_ref: str, backup_dir: str,
                        branch_head: str) -> str:
    """Encode sanitization metadata into original_ref for restore_repo()."""
    return _SANITIZED_PREFIX + json.dumps({
        "saved_ref": saved_ref,
        "backup_dir": backup_dir,
        "branch_head": branch_head,
    })


def _remove_git_entry(path: str) -> None:
    """Remove a .git entry (directory, file, or symlink).

    Symlinks are removed directly via ``os.remove()`` — *not* followed —
    so a symlink-to-directory is properly unlinked rather than having its
    target tree deleted.

    Directories are made fully writable before removal so that
    ``_lock_backup_dir``-protected trees can be cleaned up.
    """
    if os.path.islink(path):
        os.remove(path)
    elif os.path.isdir(path):
        # Ensure every node is writable before rmtree — handles locked
        # backup directories from _lock_backup_dir.  topdown=True so we
        # chmod each directory before descending into it.
        for root, dirs, files in os.walk(path, topdown=True):
            try:
                os.chmod(root, stat.S_IRWXU)
            except OSError:
                pass
            for name in files:
                try:
                    os.chmod(os.path.join(root, name), stat.S_IRWXU)
                except OSError:
                    pass
        shutil.rmtree(path)
    elif os.path.isfile(path):
        os.remove(path)


def _list_ignored_files(directory: str) -> list[str]:
    """Return paths of ignored files relative to the repo root."""
    result = git_run(
        ["ls-files", "--others", "--ignored", "--exclude-standard"],
        directory, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _read_sidecar(directory: str, backup_dir: str | None = None) -> dict | None:
    """Read sidecar data, preferring the most-trusted source available.

    The in-repo sidecar can be modified or deleted by the agent, so
    out-of-repo copies are preferred.  Lookup order (most → least trusted):

    1. Explicit ``backup_dir`` parameter → ``<backup_dir>/sidecar.json``
       (most trusted — comes from the encoded ``original_ref`` which the
       agent cannot tamper because it is held in Python memory)
    2. ``.git/info/sidecar_backup`` hint → durable copy path
       (second-most trusted — agent *can* retarget the hint file)
    3. In-repo sidecar (least trusted — agent can freely tamper)

    The optional *backup_dir* parameter lets callers that already know
    the backup location (e.g. ``restore_repo`` and ``reset_to_baseline``
    via the encoded original_ref) bypass the hint file entirely.

    The returned dict is sanitized: ``pre_agent_ignored`` is guaranteed to
    be a list of strings (non-string values are silently dropped).
    """
    def _try_load(path: str) -> dict | None:
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            # Guard against malformed JSON that isn't a dict (e.g. a list
            # or scalar) — _sanitize() assumes dict and would crash.
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _sanitize(data: dict) -> dict:
        """Ensure sidecar fields have expected types."""
        # Validate backup_dir — must be a string or None
        bd = data.get("backup_dir")
        if bd is not None and not isinstance(bd, str):
            data["backup_dir"] = None

        # Validate pre_agent_ignored — must be a list of strings
        pai = data.get("pre_agent_ignored")
        if isinstance(pai, list):
            data["pre_agent_ignored"] = [x for x in pai if isinstance(x, str)]
        elif pai is not None:
            # Unexpected type — drop it entirely
            data["pre_agent_ignored"] = []

        # Validate pre_agent_modes — must be a dict of {str: int}
        # Guard against NaN/inf floats (e.g. from JSON 1e999) that would
        # make int() raise ValueError/OverflowError.
        pam = data.get("pre_agent_modes")
        if isinstance(pam, dict):
            data["pre_agent_modes"] = {
                k: int(v) for k, v in pam.items()
                if isinstance(k, str) and isinstance(v, (int, float))
                and (isinstance(v, int) or math.isfinite(v))
            }
        elif pam is not None:
            data["pre_agent_modes"] = {}

        return data

    # 1. Durable copy via explicit backup_dir (most trusted — comes from
    #    the encoded original_ref held in Python memory; agent cannot
    #    tamper it even with full filesystem access)
    if backup_dir:
        data = _try_load(os.path.join(backup_dir, "sidecar.json"))
        if data is not None:
            return _sanitize(data)

    # 2. Durable copy via .git/info hint (agent can retarget this file,
    #    so it is less trusted than the explicit backup_dir above)
    durable_hint = os.path.join(directory, ".git", "info", "sidecar_backup")
    if os.path.isfile(durable_hint):
        try:
            with open(durable_hint) as f:
                durable_path = f.read().strip()
        except Exception:
            durable_path = ""
        data = _try_load(durable_path)
        if data is not None:
            return _sanitize(data)

    # 3. In-repo sidecar (least trusted — only used when durable copies
    #    are unavailable, e.g. non-sanitized runs or partial setups)
    data = _try_load(os.path.join(directory, _SANITIZE_SIDECAR))
    if data is not None:
        return _sanitize(data)

    return None


def _backup_ignored_files(directory: str, backup_dir: str) -> tuple[list[str], dict[str, int]]:
    """Copy ignored files to ``backup_dir/ignored/`` and return their paths and modes.

    Backs up *contents* (not just paths) so that files the agent edits or
    deletes can be fully restored later.  Also records each file's original
    ``st_mode`` so permissions can be faithfully restored.

    Returns ``(ignored_paths, modes_dict)``.
    """
    ignored = _list_ignored_files(directory)
    modes: dict[str, int] = {}
    if not ignored or not backup_dir:
        return ignored, modes
    ignored_root = os.path.join(backup_dir, "ignored")
    for relpath in ignored:
        src = os.path.join(directory, relpath)
        if os.path.isfile(src):
            modes[relpath] = os.stat(src).st_mode
            dst = os.path.join(ignored_root, relpath)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    return ignored, modes


def _is_safe_relpath(relpath) -> bool:
    """Return True only if *relpath* is a safe relative path for restore.

    This validates the path *string* without touching the filesystem —
    the destination may not exist yet or may be a malicious symlink.
    The actual filesystem boundary check (parent dir realpath) is done
    separately after ``makedirs`` in the caller.

    Rejects:
    - Non-string / empty inputs (defence against malformed sidecar).
    - Absolute paths.
    - ``..``-escaped paths (traversal out of repo).
    - Paths inside ``.git/`` (overwriting git internals — config,
      hooks, etc. — could execute arbitrary code on the next git op).
    """
    if not isinstance(relpath, str) or not relpath:
        return False
    if os.path.isabs(relpath):
        return False
    normed = os.path.normpath(relpath)
    # After normpath, leading ".." means the path escapes upward
    if normed.startswith(".."):
        return False
    # Block writes into .git/ — these are not working-tree files and
    # overwriting them (e.g. config, hooks/*) is a code-execution vector.
    if normed == ".git" or normed.startswith(".git" + os.sep):
        return False
    return True


def _is_git_tracked(directory: str, relpath: str) -> bool:
    """Return True if *relpath* is tracked by git (i.e. in the index)."""
    result = git_run(
        ["ls-files", "--error-unmatch", relpath],
        directory, check=False,
    )
    return result.returncode == 0


def _restore_ignored_files(directory: str, backup_dir: str,
                           pre_agent_ignored: set[str],
                           pre_agent_modes: dict[str, int] | None = None) -> None:
    """Restore pre-existing ignored files and remove agent-created ones.

    1. Restores every pre-existing ignored file from ``backup_dir/ignored/``
       — handles edits (overwritten), deletes (recreated), and type changes
       (directory/symlink replaced with original regular file).
    2. Removes any currently-ignored file that was NOT in the pre-agent set
       — handles agent-created temp/artifact files.

    Security:
    - Each relpath from ``pre_agent_ignored`` is validated (must be relative,
      must resolve inside the repo) **before** any filesystem mutation.
      This prevents the agent from injecting absolute or ``..``-escaped
      paths into the in-repo sidecar to trigger deletes outside the repo.
    - Each relpath is verified to NOT be a tracked file.  A tampered sidecar
      could inject tracked paths to overwrite repo content with payload files
      placed in the backup directory.
    - The agent's entity at the destination is removed and the resolved
      parent directory is re-verified after ``makedirs`` to guard against
      symlinked parent directories.
    """
    real_repo = os.path.realpath(directory)

    # 1. Restore pre-existing files from the content backup
    ignored_root = os.path.join(backup_dir, "ignored")
    if os.path.isdir(ignored_root):
        for relpath in pre_agent_ignored:
            # Gate: reject absolute paths and traversals that escape the repo.
            if not _is_safe_relpath(relpath):
                continue

            # Gate: reject tracked files — a tampered sidecar could inject
            # tracked paths to overwrite repo content from the backup.
            if _is_git_tracked(directory, relpath):
                continue

            src = os.path.join(ignored_root, relpath)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(directory, relpath)

            # Ensure parent directories exist.
            dst_dir = os.path.dirname(dst)
            os.makedirs(dst_dir, exist_ok=True)

            # Re-verify the resolved parent is inside the repo AFTER
            # makedirs — a symlinked parent placed by the agent would
            # resolve elsewhere.
            real_dst_dir = os.path.realpath(dst_dir)
            if not (real_dst_dir == real_repo
                    or real_dst_dir.startswith(real_repo + os.sep)):
                continue

            # NOW safe to remove whatever the agent left at dst —
            # symlink, directory, or modified file — without following
            # symlinks.
            if os.path.lexists(dst):
                if os.path.isdir(dst) and not os.path.islink(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)  # removes files and symlinks alike

            shutil.copy2(src, dst)
            # Restore original file permissions.  The backup may be
            # read-only (from _lock_backup_dir), so shutil.copy2 copies
            # locked permissions.  Use the recorded original mode when
            # available, masking off setuid/setgid/sticky and ensuring
            # at minimum user-write.
            if pre_agent_modes and relpath in pre_agent_modes:
                safe_mode = (pre_agent_modes[relpath] & 0o0777) | stat.S_IWUSR
                os.chmod(dst, safe_mode)
            else:
                os.chmod(dst, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    # 2. Remove ignored files the agent created
    current = set(_list_ignored_files(directory))
    # Use backup contents as an authoritative complement to pre_agent_ignored:
    # even if the sidecar was tampered (e.g., pre_agent_ignored set to []),
    # files that physically exist in the backup were pre-existing and must
    # not be deleted.
    backed_up = set()
    if os.path.isdir(ignored_root):
        for root, _dirs, files in os.walk(ignored_root):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), ignored_root)
                backed_up.add(rel)
    known_pre_existing = pre_agent_ignored | backed_up
    new_files = current - known_pre_existing - {_SANITIZE_SIDECAR}
    for relpath in new_files:
        if not _is_safe_relpath(relpath):
            continue
        full = os.path.join(directory, relpath)
        try:
            if os.path.islink(full) or os.path.isfile(full):
                os.remove(full)
            elif os.path.isdir(full):
                shutil.rmtree(full)
        except OSError:
            pass


def _lock_backup_dir(backup_dir: str) -> None:
    """Make the sidecar and ignored-file backup read-only.

    Only locks ``sidecar.json`` and ``ignored/`` — the attack surface.
    The ``.git`` backup is left writable so ``restore_repo`` can copy it
    back without permission errors.

    The agent runs as the same OS user and could ``chmod`` it back, but
    this blocks naive or accidental writes and raises the bar for attacks.
    """
    # Lock sidecar.json
    sidecar = os.path.join(backup_dir, "sidecar.json")
    if os.path.isfile(sidecar):
        os.chmod(sidecar, stat.S_IRUSR | stat.S_IRGRP)

    # Lock ignored/ tree
    ignored_root = os.path.join(backup_dir, "ignored")
    if os.path.isdir(ignored_root):
        for root, dirs, files in os.walk(ignored_root, topdown=False):
            for name in files:
                os.chmod(os.path.join(root, name), stat.S_IRUSR | stat.S_IRGRP)
            for name in dirs:
                os.chmod(os.path.join(root, name),
                         stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
        os.chmod(ignored_root,
                 stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)


def _sanitize_git_history(directory: str) -> tuple[str, str]:
    """Back up .git, then re-init with a single 'base' commit.

    Returns (new_head_hash, backup_dir) where backup_dir holds the
    original .git so restore_repo() can bring the repo back.

    Handles both .git directories (normal repos) and .git files
    (worktrees / submodules).

    If re-init fails after .git has been deleted, the original .git is
    restored from the backup before re-raising, so git history is never
    irreversibly lost.
    """
    git_dir = os.path.join(directory, ".git")
    backup_dir = ""
    if os.path.isdir(git_dir) or os.path.isfile(git_dir):
        backup_dir = tempfile.mkdtemp(prefix="agent_eval_git_bak_")
        backup_git = os.path.join(backup_dir, ".git")
        if os.path.isdir(git_dir):
            shutil.copytree(git_dir, backup_git, symlinks=True)
        else:
            shutil.copy2(git_dir, backup_git)
        _remove_git_entry(git_dir)

    try:
        git_run(["init"], directory)
        git_run(["config", "user.name", "agent-eval"], directory)
        git_run(["config", "user.email", "agent-eval@noreply"], directory)
        git_run(["add", "-A"], directory)
        git_run(["commit", "-m", "base", "--allow-empty"], directory)
    except Exception:
        # Re-init failed after .git was deleted — restore from backup
        # so the original history is not permanently lost.
        if backup_dir:
            backup_git = os.path.join(backup_dir, ".git")
            _remove_git_entry(git_dir)  # clean up partial init
            if os.path.isdir(backup_git):
                shutil.copytree(backup_git, git_dir, symlinks=True)
            elif os.path.isfile(backup_git):
                shutil.copy2(backup_git, git_dir)
            _remove_git_entry(backup_dir)
            print("[ok] Sanitization failed; original .git restored from backup")
        raise

    new_head = git_run(["rev-parse", "HEAD"], directory).stdout.strip()
    print(f"[ok] Sanitized git history; single commit: {new_head[:10]}")
    if backup_dir:
        print(f"[ok] Original .git backed up to: {backup_dir}")
        # Hide the sidecar from all git operations so it never leaks
        # into agent-generated patches.  Set up the exclude BEFORE
        # capturing the ignored-files snapshot so the sidecar itself
        # (once written) is properly excluded from git ls-files.
        exclude_file = os.path.join(directory, ".git", "info", "exclude")
        os.makedirs(os.path.dirname(exclude_file), exist_ok=True)
        with open(exclude_file, "a") as f:
            f.write(f"\n{_SANITIZE_SIDECAR}\n")
        # Back up ignored files (contents + paths) BEFORE the agent runs.
        # During retry resets and final restore we restore pre-existing
        # files from this backup and remove agent-created ones.
        pre_agent_ignored, pre_agent_modes = _backup_ignored_files(directory, backup_dir)
        # Write the sidecar with backup location AND ignored snapshot.
        sidecar_data = {
            "backup_dir": backup_dir,
            "pre_agent_ignored": pre_agent_ignored,
            "pre_agent_modes": pre_agent_modes,
        }
        sidecar = os.path.join(directory, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump(sidecar_data, f)
        # Write a durable copy inside the backup directory (outside the
        # repo) so _read_sidecar can recover even if the agent deletes
        # the in-repo sidecar.
        durable_path = os.path.join(backup_dir, "sidecar.json")
        with open(durable_path, "w") as f:
            json.dump(sidecar_data, f)
        # Store the durable path inside .git/info/ — the agent cannot
        # delete files there without breaking git itself.
        durable_hint = os.path.join(directory, ".git", "info", "sidecar_backup")
        with open(durable_hint, "w") as f:
            f.write(durable_path)
        # Lock backup dir to read-only — blocks naive tampering even
        # though the agent (same user) could chmod it back.
        _lock_backup_dir(backup_dir)
    return new_head, backup_dir


def setup_starting_point(directory: str, branch: str | None = None,
                         gt_patch: str | None = None,
                         repo_url: str | None = None,
                         sanitize: bool = False,
                         _mutated_flag: list | None = None) -> tuple[str, str]:
    """
    Prepare the repo at the starting point for agent evaluation.

    Steps:
      1. Validate gt_patch exists (before any git operations)
      2. Record the current ref (branch name or commit) for later restoration
      3. Checkout the target branch if specified
      4. Hard-reset to ensure a clean working tree
      5. Reverse-apply the ground truth patch to undo the fix
      6. Stage + commit the result as the "baseline" commit

    After this, HEAD = baseline = pre-fix starting point.
    `git diff HEAD` will capture only the agent's changes.
    `git reset --hard HEAD` will restore the starting point for retries.

    Returns (original_ref, baseline_commit).

    If ``_mutated_flag`` is a list, ``True`` is appended once the first
    destructive git operation is about to run.  This lets callers know
    whether cleanup is needed if the function raises partway through.
    """
    def _mark_mutated():
        if _mutated_flag is not None and not _mutated_flag:
            _mutated_flag.append(True)

    # 1) Validate gt_patch BEFORE any git modifications
    gt_patch_abs = None
    if gt_patch:
        gt_patch_abs = os.path.abspath(gt_patch)
        if not os.path.isfile(gt_patch_abs):
            raise FileNotFoundError(f"Ground truth patch not found: {gt_patch_abs}")

    # 2) Record where we are so we can restore later
    ref_result = git_run(["rev-parse", "--abbrev-ref", "HEAD"], directory, check=False)
    original_ref = ref_result.stdout.strip()
    if original_ref == "HEAD":
        # Detached HEAD — save the commit hash instead
        original_ref = git_run(["rev-parse", "HEAD"], directory).stdout.strip()

    # 3) Checkout the target branch if specified
    if branch:
        current_branch = ref_result.stdout.strip()
        if current_branch != branch:
            result = git_run(["checkout", branch], directory, check=False)
            if result.returncode != 0:
                # Branch not found locally — try fetching from remote
                # Resolve remote URL: prefer origin, fall back to repo_url param
                resolved_url = git_run(
                    ["remote", "get-url", "origin"],
                    directory, check=False,
                ).stdout.strip() or repo_url

                fetched = False
                if resolved_url:
                    print(f"[..] Branch '{branch}' not found locally, "
                          f"fetching from {resolved_url}...")
                    fetch_result = git_run(
                        ["fetch", resolved_url, f"{branch}:{branch}"],
                        directory, check=False,
                    )
                    fetched = fetch_result.returncode == 0

                if not fetched and resolved_url:
                    # Try fetching as a PR ref (e.g. pr_692 → pull/692/head)
                    pr_match = re.match(r"pr[_-]?(\d+)$", branch, re.IGNORECASE)
                    if pr_match:
                        pr_number = pr_match.group(1)
                        print(f"[..] Trying PR ref: pull/{pr_number}/head...")
                        fetch_result = git_run(
                            ["fetch", resolved_url,
                             f"pull/{pr_number}/head:{branch}"],
                            directory, check=False,
                        )
                        fetched = fetch_result.returncode == 0

                if not fetched:
                    raise RuntimeError(
                        f"Branch '{branch}' not found locally or on remote."
                    )
                git_run(["checkout", branch], directory)
            _mark_mutated()
            print(f"[ok] Checked out branch: {branch}")

    # Record the HEAD of the branch BEFORE our baseline commit (for cleanup)
    branch_head = git_run(["rev-parse", "HEAD"], directory).stdout.strip()

    # If the gt_patch lives inside the repo, copy it out before reset/clean
    # destroys it.
    _gt_patch_tmp = None
    if gt_patch_abs:
        abs_dir = os.path.abspath(directory) + os.sep
        if gt_patch_abs.startswith(abs_dir):
            _fd, _gt_patch_tmp = tempfile.mkstemp(suffix=".patch", prefix="gt_safe_")
            os.close(_fd)
            shutil.copy2(gt_patch_abs, _gt_patch_tmp)
            gt_patch_abs = _gt_patch_tmp

    # 4) Hard-reset to ensure a clean working tree AND index.
    #    `git reset --hard HEAD` clears both staged and unstaged changes;
    #    `git checkout .` would only restore working tree from the index,
    #    leaving any pre-staged changes intact.
    _mark_mutated()
    git_run(["reset", "--hard", "HEAD"], directory)
    git_run(["clean", "-fd"], directory)

    if not gt_patch_abs:
        # No ground truth patch — current HEAD is the starting point
        print(f"[ok] Starting point: HEAD ({branch_head[:10]})")
        if sanitize:
            pre_sanitize_head = branch_head
            branch_head, backup_dir = _sanitize_git_history(directory)
            original_ref = _make_sanitized_ref(original_ref, backup_dir,
                                               pre_sanitize_head)
        return original_ref, branch_head

    # 5) Reverse-apply the ground truth patch
    try:
        result = git_run(["apply", "--reverse", gt_patch_abs], directory, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to reverse-apply ground truth patch: {gt_patch}\n"
                f"{result.stderr.strip()}"
            )
        print(f"[ok] Reverse-applied ground truth patch: {os.path.basename(gt_patch)}")
    finally:
        # Clean up the safety copy regardless of success/failure
        if _gt_patch_tmp and os.path.isfile(_gt_patch_tmp):
            os.remove(_gt_patch_tmp)

    # Check if there are actual changes to commit
    status = git_run(["status", "--porcelain"], directory).stdout.strip()
    if not status:
        print("[warn] Ground truth patch reverse-apply produced no changes.")
        if sanitize:
            pre_sanitize_head = branch_head
            branch_head, backup_dir = _sanitize_git_history(directory)
            original_ref = _make_sanitized_ref(original_ref, backup_dir,
                                               pre_sanitize_head)
        return original_ref, branch_head

    # 6) Commit the baseline state
    git_run(["add", "-A"], directory)
    git_run(
        ["-c", "user.name=Agent Eval", "-c", "user.email=agent-eval@noreply",
         "commit", "-m", "baseline: pre-patch starting point (auto-generated)"],
        directory,
    )
    baseline = git_run(["rev-parse", "HEAD"], directory).stdout.strip()
    print(f"[ok] Baseline committed: {baseline[:10]}")

    if sanitize:
        baseline, backup_dir = _sanitize_git_history(directory)
        original_ref = _make_sanitized_ref(original_ref, backup_dir,
                                           branch_head)

    return original_ref, baseline


def decode_backup_dir(original_ref: str) -> str | None:
    """Extract the trusted backup_dir from an encoded original_ref.

    Returns the backup directory path, or ``None`` if ``original_ref``
    is not a sanitized ref or does not contain a valid backup_dir.

    Callers (e.g. ``command.py``) use this to pass the trusted
    ``backup_dir`` to ``reset_to_baseline()`` so that the sidecar
    lookup cannot be redirected by agent-tampered hint files.
    """
    if not original_ref or not original_ref.startswith(_SANITIZED_PREFIX):
        return None
    try:
        meta = json.loads(original_ref[len(_SANITIZED_PREFIX):])
        bd = meta.get("backup_dir", "")
        return bd if isinstance(bd, str) and bd else None
    except Exception:
        return None


def reset_to_baseline(directory: str, baseline_commit: str,
                      backup_dir: str | None = None) -> None:
    """Reset the repo to the baseline commit (starting point for each attempt).

    After the standard ``git clean -fd`` (which skips ignored files),
    pre-existing ignored files are restored from the content backup and
    agent-created ignored files are removed.  This ensures ``.env`` survives
    even if the agent edited or deleted it.

    *backup_dir* is the trusted backup directory from the encoded
    ``original_ref``.  When provided, it is passed to ``_read_sidecar()``
    so the sidecar lookup prefers the durable copy and cannot be
    redirected by agent-tampered hint files or in-repo sidecars.

    Raises RuntimeError if either git command fails, so callers know
    the repo may not be in a clean state.
    """
    git_run(["reset", "--hard", baseline_commit], directory)
    git_run(["clean", "-fd"], directory)
    sidecar = _read_sidecar(directory, backup_dir=backup_dir)
    if (sidecar
            and isinstance(sidecar.get("backup_dir"), str)
            and sidecar["backup_dir"]
            and sidecar.get("pre_agent_ignored") is not None):
        _restore_ignored_files(
            directory, sidecar["backup_dir"],
            set(sidecar["pre_agent_ignored"]),
            pre_agent_modes=sidecar.get("pre_agent_modes"),
        )


def _is_plausible_backup_dir(path: str) -> bool:
    """Return True only if *path* looks like a backup dir we created.

    ``_sanitize_git_history`` creates backup dirs via
    ``tempfile.mkdtemp(prefix="agent_eval_git_bak_")``.  This check
    ensures we don't blindly trust a sidecar-reported ``backup_dir``
    that could point to an unrelated directory.

    Checks:
    - Must be a string and exist as a directory
    - Basename starts with our known prefix
    - Parent directory resolves to the system temp directory
    - Contains a ``.git`` entry (the backed-up git history)
    """
    if not isinstance(path, str) or not path:
        return False
    if not os.path.isdir(path):
        return False
    if not os.path.basename(path).startswith("agent_eval_git_bak_"):
        return False
    try:
        real_parent = os.path.realpath(os.path.dirname(path))
        real_tmp = os.path.realpath(tempfile.gettempdir())
        if not (real_parent == real_tmp
                or real_parent.startswith(real_tmp + os.sep)):
            return False
    except Exception:
        return False
    # Must contain the .git backup that _sanitize_git_history creates
    backup_git = os.path.join(path, ".git")
    if not (os.path.isdir(backup_git) or os.path.isfile(backup_git)):
        return False
    return True


def recover_sanitize_backup(directory: str) -> str | None:
    """Read the sidecar file and return the backup_dir path, or None.

    Called by the partial-setup fallback in command.py when
    ``setup_starting_point`` crashed after sanitization but before returning.

    The returned path is validated to look like a real agent_eval backup
    (correct prefix, lives in the temp directory, contains a ``.git``
    entry).  If the sidecar reports a path that fails validation, ``None``
    is returned — this prevents a tampered sidecar from directing the
    caller to delete an arbitrary external directory.
    """
    data = _read_sidecar(directory)
    if data is None:
        return None
    bd = data.get("backup_dir") or None
    if bd and not _is_plausible_backup_dir(bd):
        print(f"[warn] Sidecar backup_dir failed validation, ignoring: {bd}")
        return None
    return bd


def _remove_sanitize_sidecar(directory: str) -> None:
    """Remove the sidecar file if it exists."""
    sidecar = os.path.join(directory, _SANITIZE_SIDECAR)
    if os.path.isfile(sidecar):
        os.remove(sidecar)


def restore_repo(directory: str, original_ref: str, baseline_commit: str) -> None:
    """
    Restore the repo to its original state before setup.

    If we created a baseline commit (gt-patch workflow), remove it by resetting
    the branch back. Then switch back to the original branch if we changed it.

    Raises RuntimeError if a critical restore step fails (e.g. git reset,
    git checkout of a branch). Read-only queries and best-effort cleanup
    (checkout ., clean) use check=False and do not raise.
    """
    if original_ref.startswith(_SANITIZED_PREFIX):
        meta = json.loads(original_ref[len(_SANITIZED_PREFIX):])
        saved_ref = meta.get("saved_ref", "")
        backup_dir = meta.get("backup_dir", "")
        branch_head = meta.get("branch_head", "")

        # Read the sidecar BEFORE any cleanup — restoring the original .git
        # and git clean will remove it.  We need both pre_agent_ignored paths
        # and backup_dir (which holds the ignored-files content backup).
        # Pass backup_dir so _read_sidecar can find the durable copy even
        # when both the in-repo sidecar and .git/info hint are gone.
        sidecar_data = _read_sidecar(directory, backup_dir=backup_dir)
        pre_ignored = set(sidecar_data["pre_agent_ignored"]) if (
            sidecar_data and sidecar_data.get("pre_agent_ignored") is not None
        ) else None

        # Restore the original .git from backup (handles both dir and file)
        git_dir = os.path.join(directory, ".git")
        backup_git = os.path.join(backup_dir, ".git") if backup_dir else ""
        if not (backup_git and (os.path.isdir(backup_git) or os.path.isfile(backup_git))):
            # Backup missing — history cannot be restored; this is a hard failure
            git_run(["reset", "--hard", "HEAD"], directory, check=False)
            git_run(["clean", "-fd"], directory, check=False)
            raise RuntimeError(
                "Cannot restore repo: sanitized .git backup not found "
                f"(expected at {backup_git or '<empty>'}). "
                "Working tree cleaned but original history is lost."
            )

        _remove_git_entry(git_dir)
        if os.path.isdir(backup_git):
            shutil.copytree(backup_git, git_dir, symlinks=True)
        else:
            shutil.copy2(backup_git, git_dir)
        # Do NOT delete backup_dir yet — _restore_ignored_files needs the
        # content backup in backup_dir/ignored/.
        print("[ok] Original .git restored from backup")

        # Undo the baseline commit (if any) by resetting to the original
        # branch HEAD before setup_starting_point added it.
        if branch_head:
            git_run(["reset", "--hard", branch_head], directory)  # critical
            print(f"[ok] Branch reset to original tip: {branch_head[:10]}")
        else:
            git_run(["reset", "--hard", "HEAD"], directory, check=False)
        git_run(["clean", "-fd"], directory, check=False)

        # Restore pre-existing ignored files from content backup and
        # remove agent-created ignored files.
        if pre_ignored is not None:
            pre_modes = sidecar_data.get("pre_agent_modes") if sidecar_data else None
            _restore_ignored_files(directory, backup_dir, pre_ignored,
                                   pre_agent_modes=pre_modes)

        # Now safe to delete the backup directory.
        _remove_git_entry(backup_dir)

        # Switch back to the original branch/ref
        if saved_ref:
            current_ref = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                                  directory, check=False).stdout.strip()
            if current_ref == "HEAD":
                current_ref = git_run(["rev-parse", "HEAD"],
                                      directory, check=False).stdout.strip()
            if saved_ref != current_ref:
                git_run(["checkout", saved_ref], directory)  # critical
                print(f"[ok] Switched back to: {saved_ref}")
        _remove_sanitize_sidecar(directory)
        print("[ok] Repo fully restored to original state")
        return

    # ── Non-sanitized restore path ──

    current_head = git_run(["rev-parse", "HEAD"], directory, check=False).stdout.strip()

    # If the working tree is dirty (mid-attempt), get back to baseline first
    if baseline_commit != current_head:
        git_run(["reset", "--hard", baseline_commit], directory)  # critical
        git_run(["clean", "-fd"], directory, check=False)

    # Only undo the baseline commit if we actually created one.
    # Check via commit message to avoid incorrectly rewinding a pre-existing commit.
    commit_msg = git_run(["log", "-1", "--format=%s", baseline_commit],
                         directory, check=False).stdout.strip()
    if commit_msg == "baseline: pre-patch starting point (auto-generated)":
        parent = git_run(["rev-parse", "--verify", f"{baseline_commit}^"],
                         directory, check=False).stdout.strip()
        if parent and parent != baseline_commit:
            git_run(["reset", "--hard", parent], directory)  # critical
            print(f"[ok] Removed baseline commit; branch restored to {parent[:10]}")
        else:
            git_run(["reset", "--hard", "HEAD"], directory, check=False)
    else:
        git_run(["reset", "--hard", "HEAD"], directory, check=False)
    git_run(["clean", "-fd"], directory, check=False)

    # Switch back to original branch/ref if we changed it
    current_ref = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                          directory, check=False).stdout.strip()
    if current_ref == "HEAD":
        current_ref = git_run(["rev-parse", "HEAD"], directory, check=False).stdout.strip()
    if original_ref and original_ref != current_ref:
        git_run(["checkout", original_ref], directory)  # critical
        print(f"[ok] Switched back to: {original_ref}")

    _remove_sanitize_sidecar(directory)
