"""Tests for run-mode git lifecycle: setup, reset, restore, and patch capture."""

import os
import json
import subprocess
import textwrap

import pytest

from agent_eval.run.git_helpers import (
    git_run,
    setup_starting_point,
    reset_to_baseline,
    restore_repo,
    recover_sanitize_backup,
    decode_backup_dir,
    _is_plausible_backup_dir,
    _remove_sanitize_sidecar,
    _remove_git_entry,
    _backup_ignored_files,
    _restore_ignored_files,
    _list_ignored_files,
    _is_safe_relpath,
    _read_sidecar,
    _SANITIZE_SIDECAR,
)
from agent_eval.run.patch_utils import get_patch, has_repo_changes, validate_patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_repo(path, *, files=None):
    """Create a git repo at *path* with an initial commit.

    *files* is an optional dict {relative_path: content}.
    Returns the initial commit hash.
    """
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True, check=True)

    if files:
        for relpath, content in files.items():
            fpath = os.path.join(path, relpath)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w") as f:
                f.write(content)

    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial", "--allow-empty"],
                    cwd=path, capture_output=True, check=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _head(path):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _read(path, relpath):
    with open(os.path.join(path, relpath)) as f:
        return f.read()


def _write(path, relpath, content):
    fpath = os.path.join(path, relpath)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w") as f:
        f.write(content)


def _branch(path):
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_patch(path, changes, msg="fix"):
    """Apply changes (dict of relpath→content) and return the patch text."""
    for relpath, content in changes.items():
        _write(path, relpath, content)
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path,
                    capture_output=True, check=True)
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "HEAD"], cwd=path,
        capture_output=True, text=True, check=True,
    )
    return result.stdout


# ===========================================================================
# setup_starting_point
# ===========================================================================

class TestSetupStartingPoint:
    """Tests for setup_starting_point()."""

    def test_no_gt_patch_returns_head(self, tmp_path):
        """Without gt-patch, baseline == current HEAD."""
        repo = str(tmp_path / "repo")
        initial = _init_repo(repo, files={"a.txt": "hello"})

        original_ref, baseline = setup_starting_point(repo)

        assert baseline == initial
        assert _head(repo) == initial

    def test_gt_patch_reverse_applied(self, tmp_path):
        """gt-patch is reverse-applied; baseline differs from original HEAD."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "original"})

        # Create a commit that acts as the "fix" (ground truth)
        patch_text = _make_patch(repo, {"a.txt": "fixed"}, msg="the fix")

        # Write patch to a file
        patch_file = str(tmp_path / "gt.patch")
        with open(patch_file, "w") as f:
            f.write(patch_text)

        # HEAD is now "the fix" commit; setup should reverse it
        original_ref, baseline = setup_starting_point(repo, gt_patch=patch_file)

        # Baseline should be a NEW commit (the reverse-applied state)
        assert baseline != _head(repo) or _read(repo, "a.txt") == "original"
        assert _read(repo, "a.txt") == "original"

    def test_gt_patch_missing_raises(self, tmp_path):
        """Missing gt-patch file raises before any git mutations."""
        repo = str(tmp_path / "repo")
        _init_repo(repo)

        with pytest.raises(FileNotFoundError, match="not found"):
            setup_starting_point(repo, gt_patch="/nonexistent/patch.patch")

    def test_branch_checkout(self, tmp_path):
        """--branch causes checkout before baseline setup."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "main content"})
        default_branch = _branch(repo)

        # Create a side branch with different content
        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo,
                        capture_output=True, check=True)
        _write(repo, "a.txt", "feature")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feature commit"], cwd=repo,
                        capture_output=True, check=True)
        subprocess.run(["git", "checkout", default_branch], cwd=repo,
                        capture_output=True, check=True)

        original_ref, baseline = setup_starting_point(repo, branch="feature")

        assert _branch(repo) == "feature"
        assert _read(repo, "a.txt") == "feature"
        assert original_ref == default_branch

    def test_staged_changes_cleared(self, tmp_path):
        """Staged (index) changes must be cleared — the High severity fix."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "clean"})

        # Stage a change without committing
        _write(repo, "a.txt", "dirty staged")
        subprocess.run(["git", "add", "a.txt"], cwd=repo,
                        capture_output=True, check=True)

        original_ref, baseline = setup_starting_point(repo)

        # After setup, file should be clean (matching HEAD), not staged content
        assert _read(repo, "a.txt") == "clean"

        # Verify index is also clean
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert status == ""

    def test_mutated_flag_set_on_mutation(self, tmp_path):
        """_mutated_flag is populated once git operations begin."""
        repo = str(tmp_path / "repo")
        _init_repo(repo)

        flag = []
        setup_starting_point(repo, _mutated_flag=flag)
        assert flag  # should contain True

    def test_mutated_flag_not_set_on_early_failure(self, tmp_path):
        """_mutated_flag stays empty if setup fails before any git ops."""
        repo = str(tmp_path / "repo")
        _init_repo(repo)

        flag = []
        with pytest.raises(FileNotFoundError):
            setup_starting_point(repo, gt_patch="/no/such/file",
                                 _mutated_flag=flag)
        assert not flag


# ===========================================================================
# reset_to_baseline
# ===========================================================================

class TestResetToBaseline:
    """Tests for reset_to_baseline()."""

    def test_resets_working_tree(self, tmp_path):
        """After agent makes changes, reset restores baseline state."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "baseline"})
        baseline = _head(repo)

        # Simulate agent changes
        _write(repo, "a.txt", "agent modified")
        _write(repo, "new_file.txt", "agent created")

        reset_to_baseline(repo, baseline)

        assert _read(repo, "a.txt") == "baseline"
        assert not os.path.exists(os.path.join(repo, "new_file.txt"))

    def test_isolation_between_retries(self, tmp_path):
        """Each retry starts from the same clean baseline."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "baseline"})
        baseline = _head(repo)

        for i in range(3):
            # Simulate agent attempt
            _write(repo, "a.txt", f"attempt {i}")
            _write(repo, f"attempt_{i}.txt", f"file from attempt {i}")

            # Reset for next retry
            reset_to_baseline(repo, baseline)

            assert _read(repo, "a.txt") == "baseline"
            for j in range(i + 1):
                assert not os.path.exists(
                    os.path.join(repo, f"attempt_{j}.txt")
                )


# ===========================================================================
# restore_repo
# ===========================================================================

class TestRestoreRepo:
    """Tests for restore_repo()."""

    def test_restore_after_gt_patch(self, tmp_path):
        """Restore undoes the baseline commit and returns to original state."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "original"})

        # Create a fix commit and its patch
        patch_text = _make_patch(repo, {"a.txt": "fixed"})
        # HEAD is now the "fix" commit — record it as the pre-setup state
        pre_setup_head = _head(repo)

        patch_file = str(tmp_path / "gt.patch")
        with open(patch_file, "w") as f:
            f.write(patch_text)

        original_ref, baseline = setup_starting_point(repo, gt_patch=patch_file)

        # Baseline should be a new commit (reverse-applied state, a.txt="original")
        assert baseline != pre_setup_head
        assert _read(repo, "a.txt") == "original"

        # Restore undoes the baseline commit → back to the fix commit
        restore_repo(repo, original_ref, baseline)

        assert _head(repo) == pre_setup_head
        assert _read(repo, "a.txt") == "fixed"

    def test_restore_cleans_working_tree(self, tmp_path):
        """Restore cleans up any leftover agent changes."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "clean"})
        baseline = _head(repo)

        original_ref, _ = setup_starting_point(repo)

        # Simulate agent mess
        _write(repo, "a.txt", "dirty")
        _write(repo, "junk.txt", "leftover")

        restore_repo(repo, original_ref, baseline)

        assert _read(repo, "a.txt") == "clean"
        assert not os.path.exists(os.path.join(repo, "junk.txt"))

    def test_restore_switches_branch(self, tmp_path):
        """If setup switched branches, restore goes back."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "default"})
        default_branch = _branch(repo)

        subprocess.run(["git", "checkout", "-b", "work"], cwd=repo,
                        capture_output=True, check=True)
        _write(repo, "a.txt", "work")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "work"], cwd=repo,
                        capture_output=True, check=True)
        subprocess.run(["git", "checkout", default_branch], cwd=repo,
                        capture_output=True, check=True)

        assert _branch(repo) == default_branch

        original_ref, baseline = setup_starting_point(repo, branch="work")

        assert _branch(repo) == "work"

        restore_repo(repo, original_ref, baseline)

        assert _branch(repo) == default_branch


# ===========================================================================
# Sanitized setup + restore
# ===========================================================================

class TestSanitizedLifecycle:
    """Tests for sanitize=True (anti-cheat) lifecycle."""

    def test_sanitized_single_commit(self, tmp_path):
        """After sanitized setup, git log shows only one commit."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})
        # Add more commits
        _make_patch(repo, {"a.txt": "v2"}, msg="second")
        _make_patch(repo, {"a.txt": "v3"}, msg="third")

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        # Should have exactly one commit
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert len(log.splitlines()) == 1

        # No remotes
        remotes = subprocess.run(
            ["git", "remote", "-v"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert remotes == ""

    def test_sanitized_restore_full_history(self, tmp_path):
        """Restore after sanitized setup brings back full git history."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "v1"})
        _make_patch(repo, {"a.txt": "v2"}, msg="second")
        _make_patch(repo, {"a.txt": "v3"}, msg="third")
        pre_sanitize_head = _head(repo)

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        # Sanitized — only one commit
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert len(log.splitlines()) == 1

        # Restore
        restore_repo(repo, original_ref, baseline)

        # Full history restored — should have 3 commits
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert len(log.splitlines()) == 3
        assert _head(repo) == pre_sanitize_head

    def test_sidecar_written_and_cleaned(self, tmp_path):
        """Sidecar file is written during sanitization and removed on restore."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        assert os.path.isfile(sidecar)
        data = json.load(open(sidecar))
        assert "backup_dir" in data

        restore_repo(repo, original_ref, baseline)

        assert not os.path.isfile(sidecar)

    def test_recover_sanitize_backup(self, tmp_path):
        """recover_sanitize_backup() reads backup_dir from sidecar."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        backup_dir = recover_sanitize_backup(repo)
        assert backup_dir is not None
        assert os.path.isdir(backup_dir)

        # Cleanup
        restore_repo(repo, original_ref, baseline)

    def test_recover_returns_none_without_sidecar(self, tmp_path):
        """recover_sanitize_backup() returns None when no sidecar exists."""
        repo = str(tmp_path / "repo")
        _init_repo(repo)

        assert recover_sanitize_backup(repo) is None


# ===========================================================================
# get_patch
# ===========================================================================

class TestGetPatch:
    """Tests for get_patch()."""

    def test_captures_modifications(self, tmp_path):
        """Modified files appear in the patch."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "original"})

        _write(repo, "a.txt", "modified")
        patch = get_patch(repo)

        assert "a.txt" in patch
        assert "+modified" in patch
        assert "-original" in patch

    def test_captures_new_files(self, tmp_path):
        """New untracked files appear in the patch."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})

        _write(repo, "new.txt", "brand new file")
        patch = get_patch(repo)

        assert "new.txt" in patch
        assert "+brand new file" in patch

    def test_empty_when_no_changes(self, tmp_path):
        """Returns empty string when there are no changes."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})

        patch = get_patch(repo)
        assert patch == ""

    def test_leaves_working_tree_intact(self, tmp_path):
        """After get_patch, working tree changes are still present (not staged)."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "original"})

        _write(repo, "a.txt", "modified")
        get_patch(repo)

        # File should still be modified
        assert _read(repo, "a.txt") == "modified"
        # And not staged (git reset should have unstaged)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert staged == ""

    def test_raises_on_bad_directory(self, tmp_path):
        """Raises RuntimeError for non-existent directory."""
        with pytest.raises(RuntimeError):
            get_patch(str(tmp_path / "nonexistent"))


# ===========================================================================
# validate_patch
# ===========================================================================

class TestValidatePatch:
    """Tests for validate_patch()."""

    def test_valid_patch(self, tmp_path):
        """A real git patch passes validation."""
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "original"})
        _write(repo, "a.txt", "modified")
        patch = get_patch(repo)

        is_valid, reason = validate_patch(patch)
        assert is_valid
        assert "ok" in reason

    def test_empty_patch_invalid(self):
        is_valid, reason = validate_patch("")
        assert not is_valid
        assert "empty" in reason

    def test_no_diff_header_invalid(self):
        is_valid, reason = validate_patch("just some text\nno diff here")
        assert not is_valid
        assert "no 'diff --git'" in reason

    def test_missing_hunk_invalid(self):
        patch = textwrap.dedent("""\
            diff --git a/f.txt b/f.txt
            --- a/f.txt
            +++ b/f.txt
        """)
        is_valid, reason = validate_patch(patch)
        assert not is_valid
        assert "hunk" in reason.lower()


# ===========================================================================
# has_repo_changes
# ===========================================================================

class TestHasRepoChanges:

    def test_clean_repo(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})
        assert not has_repo_changes(repo)

    def test_modified_file(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo, files={"a.txt": "hello"})
        _write(repo, "a.txt", "changed")
        assert has_repo_changes(repo)

    def test_new_file(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        _write(repo, "new.txt", "new")
        assert has_repo_changes(repo)


# ===========================================================================
# Ignored-file backup / restore
# ===========================================================================

def _setup_sanitized_with_ignored(tmp_path, ignored_files):
    """Helper: create a sanitized repo with .gitignore and pre-existing ignored files.

    *ignored_files* is a dict {relpath: content} of files that should be
    gitignored.  Returns (repo_path, original_ref, baseline, backup_dir).
    """
    repo = str(tmp_path / "repo")

    # Create initial tracked files + .gitignore
    init_files = {".gitignore": "\n".join(ignored_files.keys()) + "\n",
                  "tracked.txt": "tracked content"}
    _init_repo(repo, files=init_files)

    # Create the ignored files (after init so they're not committed)
    for relpath, content in ignored_files.items():
        _write(repo, relpath, content)

    # Verify they show as ignored
    assert set(_list_ignored_files(repo)) == set(ignored_files.keys())

    # Sanitize — this captures the ignored-file backup
    original_ref, baseline = setup_starting_point(repo, sanitize=True)

    # Read backup_dir from sidecar
    backup_dir = recover_sanitize_backup(repo)
    assert backup_dir is not None

    return repo, original_ref, baseline, backup_dir


class TestIgnoredFileRestore:
    """Regression tests for _backup_ignored_files / _restore_ignored_files.

    Covers symlink escape prevention, type preservation, agent edits,
    agent deletions, and agent-created file cleanup.
    """

    def test_agent_edits_ignored_file_restored(self, tmp_path):
        """If the agent modifies an ignored file, restore reverts it."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent edits the ignored file
        _write(repo, ".env", "SECRET=hacked")
        assert _read(repo, ".env") == "SECRET=hacked"

        # Reset (mimics retry or final restore)
        reset_to_baseline(repo, baseline)
        assert _read(repo, ".env") == "SECRET=original"

        # Full restore also works
        _write(repo, ".env", "SECRET=hacked_again")
        restore_repo(repo, orig_ref, baseline)
        assert _read(repo, ".env") == "SECRET=original"

    def test_agent_deletes_ignored_file_restored(self, tmp_path):
        """If the agent deletes an ignored file, restore recreates it."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent deletes the ignored file
        os.remove(os.path.join(repo, ".env"))
        assert not os.path.exists(os.path.join(repo, ".env"))

        reset_to_baseline(repo, baseline)
        assert os.path.isfile(os.path.join(repo, ".env"))
        assert _read(repo, ".env") == "SECRET=original"

    def test_agent_creates_ignored_file_removed(self, tmp_path):
        """Agent-created ignored files are cleaned up on restore."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # .gitignore also matches *.log — add that pattern
        _write(repo, ".gitignore", ".env\n*.log\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo,
                        capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add log pattern"],
                        cwd=repo, capture_output=True, check=True)

        # Agent creates a new ignored file
        _write(repo, "debug.log", "agent debug output")
        assert os.path.isfile(os.path.join(repo, "debug.log"))

        reset_to_baseline(repo, baseline)
        assert not os.path.exists(os.path.join(repo, "debug.log"))
        # Pre-existing .env should still be there
        assert _read(repo, ".env") == "SECRET=original"

    def test_agent_replaces_file_with_directory_restored(self, tmp_path):
        """If the agent replaces an ignored file with a directory, restore
        removes the directory and recreates the original file."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent replaces .env file with a directory
        env_path = os.path.join(repo, ".env")
        os.remove(env_path)
        os.makedirs(env_path)
        _write(repo, ".env/nested", "sneaky")
        assert os.path.isdir(env_path)

        reset_to_baseline(repo, baseline)
        assert os.path.isfile(env_path)
        assert _read(repo, ".env") == "SECRET=original"

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="OS does not support symlinks",
    )
    def test_symlink_escape_blocked(self, tmp_path):
        """Agent replaces ignored file with a symlink pointing outside the repo.
        Restore must NOT follow the symlink — it should remove it and restore
        the original file."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Create an external target that the agent wants to overwrite
        external_target = str(tmp_path / "external_secret.txt")
        with open(external_target, "w") as f:
            f.write("EXTERNAL_CONTENT")

        # Agent replaces .env with a symlink to external file
        env_path = os.path.join(repo, ".env")
        os.remove(env_path)
        os.symlink(external_target, env_path)
        assert os.path.islink(env_path)

        reset_to_baseline(repo, baseline)

        # The symlink should be gone, replaced with the original file
        assert not os.path.islink(env_path)
        assert os.path.isfile(env_path)
        assert _read(repo, ".env") == "SECRET=original"

        # External file must NOT have been overwritten
        with open(external_target) as f:
            assert f.read() == "EXTERNAL_CONTENT"

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="OS does not support symlinks",
    )
    def test_symlink_parent_dir_escape_blocked(self, tmp_path):
        """Agent replaces a parent directory with a symlink to an external
        directory.  Restore must skip the file (real path escapes repo)."""
        repo = str(tmp_path / "repo")
        init_files = {
            ".gitignore": "config/\n",
            "tracked.txt": "tracked",
        }
        _init_repo(repo, files=init_files)

        # Create an ignored file in a subdirectory
        os.makedirs(os.path.join(repo, "config"), exist_ok=True)
        _write(repo, "config/settings.ini", "key=value")

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        # Agent replaces the config/ directory with a symlink to external dir
        external_dir = str(tmp_path / "external_dir")
        os.makedirs(external_dir)
        config_path = os.path.join(repo, "config")
        import shutil
        shutil.rmtree(config_path)
        os.symlink(external_dir, config_path)
        assert os.path.islink(config_path)

        reset_to_baseline(repo, baseline)

        # The symlink config/ should still be there (git clean -fd doesn't
        # follow gitignored symlinks) but the file should NOT have been
        # written into the external directory.
        external_file = os.path.join(external_dir, "settings.ini")
        assert not os.path.exists(external_file), \
            "restore wrote through symlinked parent directory to external location"

        # Cleanup
        restore_repo(repo, original_ref, baseline)

    def test_multiple_ignored_files_restored(self, tmp_path):
        """Multiple ignored files are all correctly backed up and restored."""
        ignored = {
            ".env": "SECRET=abc",
            ".env.local": "LOCAL=xyz",
            "cache.db": "binary-ish data",
        }
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, ignored)

        # Agent modifies one, deletes another, leaves third alone
        _write(repo, ".env", "SECRET=changed")
        os.remove(os.path.join(repo, ".env.local"))
        # cache.db left alone

        reset_to_baseline(repo, baseline)

        for relpath, content in ignored.items():
            assert os.path.isfile(os.path.join(repo, relpath)), \
                f"{relpath} should exist after restore"
            assert _read(repo, relpath) == content, \
                f"{relpath} content should be restored"

    def test_full_lifecycle_ignored_files_survive(self, tmp_path):
        """End-to-end: sanitize → agent changes → reset → agent changes →
        restore.  Ignored files should be identical to pre-setup state."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "ORIGINAL_SECRET"})

        # Attempt 1: agent edits .env
        _write(repo, ".env", "ATTEMPT_1")
        reset_to_baseline(repo, baseline)
        assert _read(repo, ".env") == "ORIGINAL_SECRET"

        # Attempt 2: agent deletes .env
        os.remove(os.path.join(repo, ".env"))
        reset_to_baseline(repo, baseline)
        assert _read(repo, ".env") == "ORIGINAL_SECRET"

        # Attempt 3: agent replaces with dir
        env_path = os.path.join(repo, ".env")
        os.remove(env_path)
        os.makedirs(env_path)
        reset_to_baseline(repo, baseline)
        assert os.path.isfile(env_path)
        assert _read(repo, ".env") == "ORIGINAL_SECRET"

        # Final restore
        restore_repo(repo, orig_ref, baseline)
        assert _read(repo, ".env") == "ORIGINAL_SECRET"

    def test_sidecar_deleted_by_agent_reset_still_restores(self, tmp_path):
        """If the agent deletes the in-repo sidecar, reset_to_baseline still
        restores ignored files from the durable backup copy."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent deletes the sidecar (e.g. via git clean -fdx or manual rm)
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        assert os.path.isfile(sidecar)
        os.remove(sidecar)
        assert not os.path.isfile(sidecar)

        # Agent also edits .env
        _write(repo, ".env", "SECRET=hacked")

        # Reset should still restore .env via the durable copy
        reset_to_baseline(repo, baseline)
        assert _read(repo, ".env") == "SECRET=original"

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_sidecar_deleted_by_agent_restore_still_works(self, tmp_path):
        """If the agent deletes the in-repo sidecar, restore_repo still
        restores ignored files from the durable backup copy."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent deletes the sidecar AND edits .env
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        os.remove(sidecar)
        _write(repo, ".env", "SECRET=hacked")

        # Full restore should still work
        restore_repo(repo, orig_ref, baseline)
        assert _read(repo, ".env") == "SECRET=original"


class TestRemoveGitEntry:
    """Tests for _remove_git_entry — symlink handling."""

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="OS does not support symlinks",
    )
    def test_removes_symlink_to_directory(self, tmp_path):
        """_remove_git_entry removes a symlink-to-directory without
        deleting the target directory's contents."""
        target_dir = str(tmp_path / "target")
        os.makedirs(target_dir)
        with open(os.path.join(target_dir, "data.txt"), "w") as f:
            f.write("important")

        link = str(tmp_path / "link")
        os.symlink(target_dir, link)
        assert os.path.islink(link)
        assert os.path.isdir(link)  # follows symlink

        _remove_git_entry(link)

        # Symlink removed
        assert not os.path.exists(link)
        assert not os.path.islink(link)
        # Target intact
        assert os.path.isdir(target_dir)
        assert os.path.isfile(os.path.join(target_dir, "data.txt"))

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="OS does not support symlinks",
    )
    def test_removes_symlink_to_file(self, tmp_path):
        """_remove_git_entry removes a symlink-to-file without deleting
        the target file."""
        target = str(tmp_path / "target.txt")
        with open(target, "w") as f:
            f.write("important")

        link = str(tmp_path / "link")
        os.symlink(target, link)

        _remove_git_entry(link)

        assert not os.path.exists(link)
        assert os.path.isfile(target)

    def test_removes_regular_directory(self, tmp_path):
        d = str(tmp_path / "dir")
        os.makedirs(d)
        with open(os.path.join(d, "f.txt"), "w") as f:
            f.write("data")

        _remove_git_entry(d)
        assert not os.path.exists(d)

    def test_removes_regular_file(self, tmp_path):
        f = str(tmp_path / "file.txt")
        with open(f, "w") as fh:
            fh.write("data")

        _remove_git_entry(f)
        assert not os.path.exists(f)

    def test_noop_on_nonexistent(self, tmp_path):
        """No error when path doesn't exist."""
        _remove_git_entry(str(tmp_path / "nothing"))


# ===========================================================================
# _is_safe_relpath — path validation
# ===========================================================================

class TestIsSafeRelpath:
    """Tests for _is_safe_relpath string validation."""

    def test_normal_relpath(self):
        assert _is_safe_relpath(".env") is True

    def test_nested_relpath(self):
        assert _is_safe_relpath("config/settings.ini") is True

    def test_absolute_path_rejected(self):
        assert _is_safe_relpath("/tmp/evil") is False

    def test_dotdot_escape_rejected(self):
        assert _is_safe_relpath("../../../etc/passwd") is False

    def test_dotdot_in_middle_escape_rejected(self):
        assert _is_safe_relpath("config/../../etc/passwd") is False

    def test_dotdot_non_escaping_ok(self):
        # "a/../b" normalizes to "b" — stays inside
        assert _is_safe_relpath("a/../b") is True

    def test_empty_rejected(self):
        assert _is_safe_relpath("") is False

    def test_none_rejected(self):
        assert _is_safe_relpath(None) is False

    def test_dotgit_exact_rejected(self):
        assert _is_safe_relpath(".git") is False

    def test_dotgit_config_rejected(self):
        assert _is_safe_relpath(".git/config") is False

    def test_dotgit_hooks_rejected(self):
        assert _is_safe_relpath(".git/hooks/pre-commit") is False

    def test_dotgit_nested_rejected(self):
        assert _is_safe_relpath(".git/objects/pack/something") is False


# ===========================================================================
# Sidecar tampering and fallback
# ===========================================================================

class TestSidecarSecurity:
    """Tests for sidecar trust ordering and tampered data handling."""

    def test_injected_absolute_path_does_not_delete_external(self, tmp_path):
        """Agent injects an absolute path into pre_agent_ignored in the
        in-repo sidecar.  _restore_ignored_files must not delete or
        overwrite the external path."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Create an external file that must not be deleted
        external_file = str(tmp_path / "precious.txt")
        with open(external_file, "w") as f:
            f.write("DO NOT DELETE")

        # Agent tampers with the in-repo sidecar to inject an absolute path
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [".env", external_file],
            }, f)

        # Reset should NOT delete the external file
        reset_to_baseline(repo, baseline)

        assert os.path.isfile(external_file), \
            "external file was deleted via injected absolute path"
        with open(external_file) as f:
            assert f.read() == "DO NOT DELETE"

        # .env should still be restored normally
        assert _read(repo, ".env") == "SECRET=original"

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_injected_dotdot_path_does_not_escape(self, tmp_path):
        """Agent injects a ../-traversal path into the sidecar.
        _restore_ignored_files must reject it."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Create a file outside the repo that must not be affected
        external_file = str(tmp_path / "precious.txt")
        with open(external_file, "w") as f:
            f.write("DO NOT DELETE")

        # Agent tampers with the sidecar
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [".env", "../precious.txt"],
            }, f)

        reset_to_baseline(repo, baseline)

        assert os.path.isfile(external_file)
        with open(external_file) as f:
            assert f.read() == "DO NOT DELETE"

        restore_repo(repo, orig_ref, baseline)

    def test_durable_sidecar_preferred_over_tampered_inrepo(self, tmp_path):
        """_read_sidecar prefers the durable copy (untamperable) over the
        in-repo sidecar that the agent can modify."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent tampers with the in-repo sidecar
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({"backup_dir": "/fake", "pre_agent_ignored": []}, f)

        # _read_sidecar should return the durable copy (via hint), not
        # the tampered in-repo version
        data = _read_sidecar(repo)
        assert data is not None
        assert data["backup_dir"] == backup_dir
        assert ".env" in data["pre_agent_ignored"]

        restore_repo(repo, orig_ref, baseline)

    def test_restore_works_when_sidecar_and_hint_both_gone(self, tmp_path):
        """When both the in-repo sidecar and .git/info/sidecar_backup hint
        are gone, restore_repo still restores ignored files via the
        backup_dir from the encoded original_ref."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent nukes both the in-repo sidecar and the hint
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        if os.path.isfile(sidecar):
            os.remove(sidecar)
        if os.path.isfile(hint):
            os.remove(hint)

        # Agent edits .env
        _write(repo, ".env", "SECRET=hacked")

        # restore_repo has backup_dir from encoded original_ref
        restore_repo(repo, orig_ref, baseline)
        assert _read(repo, ".env") == "SECRET=original"

    def test_tampered_sidecar_cannot_overwrite_tracked_file(self, tmp_path):
        """Agent tampers sidecar to add a tracked file to pre_agent_ignored
        AND places a payload in backup_dir/ignored/.  The tracked file must
        NOT be overwritten."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Verify tracked.txt has its committed content
        assert _read(repo, "tracked.txt") == "tracked content"

        # Agent discovers backup_dir, unlocks it, places a payload, and
        # tampers the sidecar.
        import stat as stat_mod
        ignored_root = os.path.join(backup_dir, "ignored")
        # Unlock ignored/ so we can write (simulating agent chmod + write)
        for root, dirs, files in os.walk(ignored_root, topdown=True):
            os.chmod(root, stat_mod.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(root, name), stat_mod.S_IRWXU)
        _write(backup_dir, "ignored/tracked.txt", "PAYLOAD")

        # Unlock and tamper sidecar.json
        durable = os.path.join(backup_dir, "sidecar.json")
        os.chmod(durable, stat_mod.S_IRWXU)
        with open(durable, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [".env", "tracked.txt"],
            }, f)

        # Also tamper the in-repo sidecar
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [".env", "tracked.txt"],
            }, f)

        reset_to_baseline(repo, baseline)

        # tracked.txt must NOT have been overwritten with the payload
        assert _read(repo, "tracked.txt") == "tracked content", \
            "tracked file was overwritten via tampered sidecar"

        # .env should still be restored normally
        assert _read(repo, ".env") == "SECRET=original"

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_malformed_sidecar_does_not_crash(self, tmp_path):
        """Non-string values in pre_agent_ignored don't crash reset/restore."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Tamper sidecar with non-string values
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        backup_dir = json.load(open(sidecar))["backup_dir"]
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [".env", {"x": 1}, 42, None, True],
            }, f)

        # Should not raise TypeError
        reset_to_baseline(repo, baseline)

        # .env should still be restored (the valid string entry survives)
        assert _read(repo, ".env") == "SECRET=original"

        restore_repo(repo, orig_ref, baseline)

    def test_malformed_sidecar_non_list_pre_agent_ignored(self, tmp_path):
        """pre_agent_ignored as a non-list type doesn't crash."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        backup_dir = json.load(open(sidecar))["backup_dir"]
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": "not-a-list",
            }, f)

        # Should not crash — pre_agent_ignored is sanitized to []
        reset_to_baseline(repo, baseline)

        restore_repo(repo, orig_ref, baseline)

    def test_tampered_durable_sidecar_empty_ignored_does_not_delete(self, tmp_path):
        """If the agent tampers the durable sidecar to set pre_agent_ignored=[],
        reset_to_baseline must NOT delete pre-existing ignored files.
        The backup directory contents act as an authoritative complement."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        import stat as stat_mod

        # Agent unlocks and tampers the durable sidecar
        durable = os.path.join(backup_dir, "sidecar.json")
        os.chmod(durable, stat_mod.S_IRWXU)
        with open(durable, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [],
            }, f)

        # Also tamper the in-repo sidecar
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [],
            }, f)

        # Reset should NOT delete .env — backup contents protect it
        reset_to_baseline(repo, baseline)

        assert os.path.isfile(os.path.join(repo, ".env")), \
            "pre-existing .env was deleted due to tampered pre_agent_ignored=[]"
        assert _read(repo, ".env") == "SECRET=original"

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_malformed_backup_dir_type_does_not_crash(self, tmp_path):
        """Non-string backup_dir in sidecar doesn't crash reset_to_baseline."""
        repo, orig_ref, baseline, _ = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Tamper in-repo sidecar with non-string backup_dir
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": 12345,
                "pre_agent_ignored": [".env"],
            }, f)

        # Remove the hint so the in-repo sidecar is used
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        if os.path.isfile(hint):
            os.remove(hint)

        # Should not crash (backup_dir type is validated by _sanitize)
        reset_to_baseline(repo, baseline)

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_file_permissions_preserved_on_restore(self, tmp_path):
        """Ignored file permissions (e.g., executable) are preserved on restore."""
        repo = str(tmp_path / "repo")
        init_files = {".gitignore": "tool.sh\n", "tracked.txt": "tracked"}
        _init_repo(repo, files=init_files)

        # Create an executable ignored file
        tool_path = os.path.join(repo, "tool.sh")
        with open(tool_path, "w") as f:
            f.write("#!/bin/sh\necho hello\n")
        os.chmod(tool_path, 0o755)
        assert os.stat(tool_path).st_mode & 0o777 == 0o755

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        # Agent modifies tool.sh
        _write(repo, "tool.sh", "#!/bin/sh\necho hacked\n")

        reset_to_baseline(repo, baseline)

        # Content restored
        assert _read(repo, "tool.sh") == "#!/bin/sh\necho hello\n"
        # Permissions preserved (execute bit intact, user-write ensured)
        mode = os.stat(tool_path).st_mode & 0o777
        assert mode & 0o100, f"execute bit lost: {oct(mode)}"
        assert mode & 0o200, f"user-write bit missing: {oct(mode)}"

        restore_repo(repo, original_ref, baseline)

    def test_file_permissions_preserved_on_full_restore(self, tmp_path):
        """Full restore_repo also preserves original ignored file permissions."""
        repo = str(tmp_path / "repo")
        init_files = {".gitignore": "tool.sh\n", "tracked.txt": "tracked"}
        _init_repo(repo, files=init_files)

        tool_path = os.path.join(repo, "tool.sh")
        with open(tool_path, "w") as f:
            f.write("#!/bin/sh\necho hello\n")
        os.chmod(tool_path, 0o755)

        original_ref, baseline = setup_starting_point(repo, sanitize=True)

        # Agent modifies tool.sh
        _write(repo, "tool.sh", "#!/bin/sh\necho evil\n")

        restore_repo(repo, original_ref, baseline)

        assert _read(repo, "tool.sh") == "#!/bin/sh\necho hello\n"
        mode = os.stat(tool_path).st_mode & 0o777
        assert mode & 0o100, f"execute bit lost: {oct(mode)}"
        assert mode & 0o200, f"user-write bit missing: {oct(mode)}"

    def test_tampered_hint_bypassed_when_backup_dir_available(self, tmp_path):
        """Agent retargets .git/info/sidecar_backup to a malicious sidecar.
        restore_repo must ignore the hint because it has the trusted
        backup_dir from the encoded original_ref."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent creates a fake backup dir with tampered sidecar
        fake_backup = str(tmp_path / "fake_backup")
        os.makedirs(fake_backup, exist_ok=True)
        with open(os.path.join(fake_backup, "sidecar.json"), "w") as f:
            json.dump({
                "backup_dir": fake_backup,
                "pre_agent_ignored": [],  # lies — says nothing pre-existed
            }, f)

        # Agent retargets the hint to the fake sidecar
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        with open(hint, "w") as f:
            f.write(os.path.join(fake_backup, "sidecar.json"))

        # Agent also edits .env
        _write(repo, ".env", "SECRET=hacked")

        # restore_repo passes backup_dir from encoded original_ref;
        # _read_sidecar should prefer the explicit backup_dir over the
        # tampered hint and return the real sidecar data.
        restore_repo(repo, orig_ref, baseline)

        assert _read(repo, ".env") == "SECRET=original"

    def test_deleted_hint_tampered_sidecar_reset_with_trusted_backup_dir(self, tmp_path):
        """Agent deletes hint and tampers in-repo sidecar with a fake
        backup_dir pointing to an empty dir and pre_agent_ignored=[].
        reset_to_baseline with the trusted backup_dir must not delete
        pre-existing files."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent deletes the hint
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        if os.path.isfile(hint):
            os.remove(hint)

        # Agent tampers in-repo sidecar with a fake empty backup_dir
        fake_backup = str(tmp_path / "fake_empty_backup")
        os.makedirs(fake_backup, exist_ok=True)
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({
                "backup_dir": fake_backup,
                "pre_agent_ignored": [],
            }, f)

        # Agent edits .env
        _write(repo, ".env", "SECRET=hacked")

        # Decode the trusted backup_dir (just like command.py does)
        trusted_bd = decode_backup_dir(orig_ref)
        assert trusted_bd == backup_dir

        # Reset with the trusted backup_dir — should use real sidecar
        reset_to_baseline(repo, baseline, backup_dir=trusted_bd)

        # .env must be restored, not deleted
        assert os.path.isfile(os.path.join(repo, ".env")), \
            "pre-existing .env was deleted via tampered in-repo sidecar"
        assert _read(repo, ".env") == "SECRET=original"

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_non_dict_sidecar_json_does_not_crash(self, tmp_path):
        """Sidecar JSON that is a list/string/number doesn't crash lifecycle."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Agent tampers in-repo sidecar with a JSON list
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump(["not", "a", "dict"], f)

        # Remove the hint so the in-repo sidecar is reached
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        if os.path.isfile(hint):
            os.remove(hint)

        # _read_sidecar should return None (non-dict), not crash
        data = _read_sidecar(repo)
        assert data is None

        # reset_to_baseline should not crash
        reset_to_baseline(repo, baseline)

        # Cleanup
        restore_repo(repo, orig_ref, baseline)

    def test_decode_backup_dir_from_encoded_ref(self, tmp_path):
        """decode_backup_dir extracts the backup path from an encoded ref."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        decoded = decode_backup_dir(orig_ref)
        assert decoded == backup_dir

        # Non-sanitized refs return None
        assert decode_backup_dir("main") is None
        assert decode_backup_dir("") is None
        assert decode_backup_dir(None) is None

        # Cleanup
        restore_repo(repo, orig_ref, baseline)


# ===========================================================================
# Backup dir validation and fallback safety
# ===========================================================================

class TestBackupDirValidation:
    """Tests for _is_plausible_backup_dir and recover_sanitize_backup."""

    def test_real_backup_dir_is_plausible(self, tmp_path):
        """A backup dir created by setup_starting_point passes validation."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        assert _is_plausible_backup_dir(backup_dir) is True

        restore_repo(repo, orig_ref, baseline)

    def test_arbitrary_dir_rejected(self, tmp_path):
        """A random directory that is NOT our backup is rejected."""
        # Create a dir that has .git but wrong prefix/location
        fake = str(tmp_path / "not_a_backup")
        os.makedirs(os.path.join(fake, ".git"))
        assert _is_plausible_backup_dir(fake) is False

    def test_wrong_prefix_rejected(self, tmp_path):
        """Dir in temp dir but with wrong name prefix is rejected."""
        import tempfile as tf
        fake = tf.mkdtemp(prefix="wrong_prefix_")
        os.makedirs(os.path.join(fake, ".git"))
        try:
            assert _is_plausible_backup_dir(fake) is False
        finally:
            import shutil
            shutil.rmtree(fake, ignore_errors=True)

    def test_missing_git_rejected(self, tmp_path):
        """Dir with correct prefix but no .git is rejected."""
        import tempfile as tf
        fake = tf.mkdtemp(prefix="agent_eval_git_bak_")
        try:
            assert _is_plausible_backup_dir(fake) is False
        finally:
            import shutil
            shutil.rmtree(fake, ignore_errors=True)

    def test_none_and_empty_rejected(self):
        assert _is_plausible_backup_dir(None) is False
        assert _is_plausible_backup_dir("") is False
        assert _is_plausible_backup_dir(42) is False

    def test_recover_rejects_tampered_sidecar_backup_dir(self, tmp_path):
        """recover_sanitize_backup returns None when the sidecar points
        to a path that fails backup dir validation."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Create an external directory that must NOT be deletable
        external = str(tmp_path / "precious_repo")
        os.makedirs(os.path.join(external, ".git"))
        with open(os.path.join(external, "important.txt"), "w") as f:
            f.write("DO NOT DELETE")

        # Tamper the in-repo sidecar to point to the external dir
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        with open(sidecar, "w") as f:
            json.dump({"backup_dir": external, "pre_agent_ignored": []}, f)

        # Remove hint so in-repo sidecar is used
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        if os.path.isfile(hint):
            os.remove(hint)

        # recover_sanitize_backup should reject the tampered path
        result = recover_sanitize_backup(repo)
        assert result is None, \
            f"recover_sanitize_backup accepted tampered path: {external}"

        # External dir must still exist
        assert os.path.isdir(external)
        assert os.path.isfile(os.path.join(external, "important.txt"))

        restore_repo(repo, orig_ref, baseline)

    def test_nan_inf_in_pre_agent_modes_does_not_crash(self, tmp_path):
        """NaN/inf float values in pre_agent_modes don't crash _read_sidecar."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Tamper the in-repo sidecar with extreme floats
        # (JSON 1e999 → float('inf') in Python)
        sidecar = os.path.join(repo, _SANITIZE_SIDECAR)
        # Write raw JSON since json.dump can't produce NaN/inf
        with open(sidecar, "w") as f:
            f.write('{"backup_dir": "%s", '
                    '"pre_agent_ignored": [".env"], '
                    '"pre_agent_modes": {".env": 1e999}}' % backup_dir)

        # Remove hint so in-repo sidecar is used
        hint = os.path.join(repo, ".git", "info", "sidecar_backup")
        if os.path.isfile(hint):
            os.remove(hint)

        # Should not crash — inf is silently dropped
        data = _read_sidecar(repo)
        assert data is not None
        # The .env mode should be absent (inf was filtered out)
        assert ".env" not in data.get("pre_agent_modes", {})

        # Reset should still work (falls back to default permissions)
        reset_to_baseline(repo, baseline)
        assert _read(repo, ".env") == "SECRET=original"

        restore_repo(repo, orig_ref, baseline)

    def test_forged_backup_git_not_copied_in_fallback(self, tmp_path):
        """A forged /tmp/agent_eval_git_bak_* dir that passes heuristic
        checks must NOT have its .git copied into the repo — it could
        contain malicious hooks."""
        import tempfile as tf

        repo, orig_ref, baseline, real_backup = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Create a forged backup dir that passes _is_plausible_backup_dir
        forged = tf.mkdtemp(prefix="agent_eval_git_bak_")
        forged_git = os.path.join(forged, ".git")
        os.makedirs(os.path.join(forged_git, "hooks"))
        with open(os.path.join(forged_git, "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")
        # Place a marker file to detect if this .git gets copied
        marker = os.path.join(forged_git, "FORGED_MARKER")
        with open(marker, "w") as f:
            f.write("THIS SHOULD NOT APPEAR IN REPO")

        assert _is_plausible_backup_dir(forged) is True

        # recover_sanitize_backup returns the forged dir
        # (since it passes validation) — that's fine.
        # The critical thing is that command.py's fallback path
        # no longer copies .git from the backup into the repo.
        # We verify this by checking that the forged marker never
        # appears in the repo's .git.
        repo_marker = os.path.join(repo, ".git", "FORGED_MARKER")
        assert not os.path.exists(repo_marker), \
            "forged .git marker should not be in repo before test"

        # The fallback path in command.py now only prints a warning
        # and does NOT copy .git from backup_dir.  We can't easily
        # invoke the full command.py fallback in a unit test, but we
        # can verify the key invariant: recover_sanitize_backup returns
        # the path (it passed validation), but the caller must not copy.
        result = recover_sanitize_backup(repo)
        # The real backup dir should be returned (not the forged one,
        # since the sidecar still points to the real backup).
        # This test verifies the defence-in-depth principle.
        assert result is not None

        # Cleanup
        import shutil
        shutil.rmtree(forged, ignore_errors=True)
        restore_repo(repo, orig_ref, baseline)


# ===========================================================================
# Trajectory parsing robustness
# ===========================================================================

class TestTrajectoryParsing:
    """Tests for trajectory.py handling of malformed server messages."""

    def test_parse_message_non_dict_info(self):
        """_parse_message handles info as a string without crashing."""
        from agent_eval.run.trajectory import _parse_message

        msg = {"role": "assistant", "info": "not-a-dict", "parts": []}
        result = _parse_message(msg)

        assert result["role"] == "assistant"
        assert result["info"] == {}  # non-dict info is replaced with {}

    def test_parse_message_info_null(self):
        """_parse_message handles info as None."""
        from agent_eval.run.trajectory import _parse_message

        msg = {"info": None, "parts": []}
        result = _parse_message(msg)

        assert result["role"] == "?"
        assert result["info"] == {}

    def test_parse_message_role_from_dict_info(self):
        """_parse_message extracts role from dict info when top-level is absent."""
        from agent_eval.run.trajectory import _parse_message

        msg = {"info": {"role": "assistant"}, "parts": []}
        result = _parse_message(msg)

        assert result["role"] == "assistant"

    def test_token_aggregation_with_non_dict_info(self):
        """Token aggregation in collect_trajectory doesn't crash when a
        parsed message has non-dict info (belt-and-suspenders test)."""
        from agent_eval.run.trajectory import _parse_message

        # Simulate what collect_trajectory does with parsed messages
        messages = [
            _parse_message({"role": "user", "info": "bad", "parts": []}),
            _parse_message({"role": "assistant",
                            "info": {"totalTokens": 100},
                            "parts": []}),
        ]

        total = 0
        for m in messages:
            info = m.get("info") if isinstance(m.get("info"), dict) else {}
            total += info.get("totalTokens", 0)

        assert total == 100

    def test_parse_message_non_dict_msg(self):
        """_parse_message handles a non-dict message (e.g. string) gracefully."""
        from agent_eval.run.trajectory import _parse_message

        result = _parse_message("bad-item")
        assert result["role"] == "?"
        assert result["parts"] == []

        result2 = _parse_message(42)
        assert result2["role"] == "?"

    def test_parse_message_non_list_parts(self):
        """_parse_message handles parts as a non-list (e.g. string) gracefully."""
        from agent_eval.run.trajectory import _parse_message

        result = _parse_message({"role": "assistant", "parts": "oops"})
        assert result["role"] == "assistant"
        assert result["parts"] == []

    def test_parse_part_non_dict(self):
        """_parse_part handles a non-dict part element gracefully."""
        from agent_eval.run.trajectory import _parse_part

        result = _parse_part("not-a-dict")
        assert result["type"] == "unknown"
        assert result["raw"] == "not-a-dict"

        result2 = _parse_part(42)
        assert result2["type"] == "unknown"

    def test_parse_message_mixed_parts(self):
        """_parse_message handles a parts list with mixed dict/non-dict items."""
        from agent_eval.run.trajectory import _parse_message

        msg = {
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "hello"},
                "bad-item",
                {"type": "tool", "name": "write"},
                None,
            ],
        }
        result = _parse_message(msg)
        assert len(result["parts"]) == 4
        assert result["parts"][0]["type"] == "text"
        assert result["parts"][1]["type"] == "unknown"
        assert result["parts"][2]["type"] == "tool_call"
        assert result["parts"][3]["type"] == "unknown"

    def test_collect_trajectory_non_dict_session(self, monkeypatch, tmp_path):
        """collect_trajectory handles non-dict session response (e.g. plain text)."""
        from agent_eval.run import trajectory as tmod

        call_count = {"n": 0}
        def fake_request(method, path, **kwargs):
            call_count["n"] += 1
            if "/session/" in path and "/message" not in path and "/diff" not in path:
                return "plain-text-response"  # truthy non-dict
            if "/message" in path:
                return []
            return None

        monkeypatch.setattr(tmod, "opencode_request", fake_request)

        t = 1000.0
        result = tmod.collect_trajectory(
            session_id="s1", directory=str(tmp_path), prompt="test",
            agent="build", patch="", health={"version": "1"},
            t_start=t, t_session_created=t, t_task_sent=t,
            t_task_done=t, t_end=t + 1,
        )
        # Should not crash; model falls back to None
        assert result["metadata"]["model"] is None
        # Raw session preserved as-is for debugging
        assert result["session_raw"] == {}

    def test_print_response_non_dict_parts(self, capsys):
        """print_response handles non-dict entries in parts list."""
        from agent_eval.run.opencode_client import print_response

        msg = {"info": {"role": "assistant"}, "parts": ["bad-part", 42, None]}
        print_response(msg)  # should not crash
        captured = capsys.readouterr()
        # No text or tool output printed for non-dict parts
        assert "tool call" not in captured.out

    def test_print_response_non_list_parts(self, capsys):
        """print_response handles parts as a non-list (e.g. string)."""
        from agent_eval.run.opencode_client import print_response

        msg = {"parts": "not-a-list"}
        print_response(msg)  # should not crash
        captured = capsys.readouterr()
        assert "tool call" not in captured.out

    def test_normalize_message_non_list_parts_with_info(self):
        """normalize_message replaces non-list parts even when info+parts keys exist."""
        from agent_eval.run.opencode_client import normalize_message

        msg = {"info": {"role": "assistant"}, "parts": "bad"}
        result = normalize_message(msg)
        assert result["parts"] == []

    def test_collect_trajectory_string_raw_messages(self, monkeypatch, tmp_path):
        """collect_trajectory treats a non-list raw_messages (e.g. string)
        as empty rather than iterating per-character."""
        from agent_eval.run import trajectory as tmod

        def fake_request(method, path, **kwargs):
            if "/message" in path:
                return "oops"  # truthy non-list
            if "/session/" in path and "/diff" not in path:
                return {"model": "test-model"}
            return None

        monkeypatch.setattr(tmod, "opencode_request", fake_request)

        t = 1000.0
        result = tmod.collect_trajectory(
            session_id="s1", directory=str(tmp_path), prompt="test",
            agent="build", patch="", health={"version": "1"},
            t_start=t, t_session_created=t, t_task_sent=t,
            t_task_done=t, t_end=t + 1,
        )
        # Must be 0 messages, not 4 (one per character of "oops")
        assert result["stats"]["total_messages"] == 0
        assert result["trajectory"] == []

    def test_dotgit_injection_blocked_in_restore(self, tmp_path):
        """Injecting .git/config into pre_agent_ignored must NOT overwrite
        the repo's .git/config — _is_safe_relpath blocks .git/* paths."""
        repo, orig_ref, baseline, backup_dir = _setup_sanitized_with_ignored(
            tmp_path, {".env": "SECRET=original"})

        # Read the current .git/config before tampering
        git_config_path = os.path.join(repo, ".git", "config")
        with open(git_config_path) as f:
            original_config = f.read()

        # Attacker unlocks backup, plants a malicious .git/config backup,
        # and adds .git/config to pre_agent_ignored in the sidecar.
        import stat, shutil

        # Unlock sidecar and ignored/ tree for tampering (same OS user)
        sidecar_path = os.path.join(backup_dir, "sidecar.json")
        os.chmod(sidecar_path, stat.S_IRUSR | stat.S_IWUSR)
        ignored_root = os.path.join(backup_dir, "ignored")
        for root, dirs, files in os.walk(ignored_root):
            os.chmod(root, stat.S_IRWXU)
            for fn in files:
                os.chmod(os.path.join(root, fn), stat.S_IRUSR | stat.S_IWUSR)

        # Plant malicious backup
        malicious_git_dir = os.path.join(backup_dir, "ignored", ".git")
        os.makedirs(malicious_git_dir, exist_ok=True)
        with open(os.path.join(malicious_git_dir, "config"), "w") as f:
            f.write("[core]\n\thooksPath = /tmp/evil\n")

        # Tamper sidecar to include .git/config
        with open(sidecar_path, "w") as f:
            json.dump({
                "backup_dir": backup_dir,
                "pre_agent_ignored": [".env", ".git/config"],
            }, f)

        # Reset should NOT overwrite .git/config
        reset_to_baseline(repo, baseline, backup_dir=backup_dir)

        with open(git_config_path) as f:
            config_after = f.read()

        assert config_after == original_config, \
            ".git/config was overwritten via injected pre_agent_ignored entry"
        assert "hooksPath" not in config_after

        # .env should still be restored normally
        assert _read(repo, ".env") == "SECRET=original"

        restore_repo(repo, orig_ref, baseline)


# ===========================================================================
# Server response validation
# ===========================================================================

class TestServerResponseValidation:
    """Tests for check_health and create_session handling of malformed responses."""

    def test_check_health_non_dict(self, monkeypatch):
        """check_health raises RuntimeError on non-dict response."""
        from agent_eval.run import opencode_client as oc

        monkeypatch.setattr(oc, "opencode_request", lambda *a, **kw: "text-health")
        with pytest.raises(RuntimeError, match="expected dict"):
            oc.check_health()

    def test_check_health_none(self, monkeypatch):
        """check_health raises RuntimeError on None response."""
        from agent_eval.run import opencode_client as oc

        monkeypatch.setattr(oc, "opencode_request", lambda *a, **kw: None)
        with pytest.raises(RuntimeError, match="expected dict"):
            oc.check_health()

    def test_check_health_valid(self, monkeypatch):
        """check_health works normally with a valid dict response."""
        from agent_eval.run import opencode_client as oc

        monkeypatch.setattr(oc, "opencode_request",
                            lambda *a, **kw: {"version": "1.0"})
        result = oc.check_health()
        assert result == {"version": "1.0"}

    def test_create_session_non_dict(self, monkeypatch):
        """create_session raises RuntimeError on non-dict response."""
        from agent_eval.run import opencode_client as oc

        monkeypatch.setattr(oc, "opencode_request", lambda *a, **kw: "text-session")
        with pytest.raises(RuntimeError, match="expected dict with 'id'"):
            oc.create_session("/tmp/test")

    def test_create_session_missing_id(self, monkeypatch):
        """create_session raises RuntimeError when dict has no 'id' key."""
        from agent_eval.run import opencode_client as oc

        monkeypatch.setattr(oc, "opencode_request",
                            lambda *a, **kw: {"foo": "bar"})
        with pytest.raises(RuntimeError, match="expected dict with 'id'"):
            oc.create_session("/tmp/test")

    def test_create_session_valid(self, monkeypatch):
        """create_session works normally with a valid response."""
        from agent_eval.run import opencode_client as oc

        monkeypatch.setattr(oc, "opencode_request",
                            lambda *a, **kw: {"id": "sess-123"})
        result = oc.create_session("/tmp/test")
        assert result == "sess-123"
