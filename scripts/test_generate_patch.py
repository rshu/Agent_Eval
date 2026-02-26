"""
Run an opencode agent on a target repo to generate a code patch for a PR issue.

Workflow:
  1. Checkout the PR branch in the target repo (--branch)
  2. Reverse-apply the ground truth patch to get the pre-fix starting point (--gt-patch)
  3. Commit that state as a baseline so HEAD = starting point
  4. Sanitize git history (destroy + re-init) and strip repo URLs from prompt
  5. Send the issue description to the agent
  6. Capture the agent's changes as a git-style patch (git diff against baseline HEAD)
  7. For each retry, hard-reset to the baseline commit (with -fdx to remove agent artifacts)
  8. Restore the repo to its original state on exit (with -fd to preserve pre-existing ignored files)

Note on file preservation:
  - Setup runs ``git clean -fd`` to ensure a clean working tree before baseline
    creation.  This DELETES pre-existing untracked files (files not tracked by git
    and not gitignored).  These files cannot be restored.  Ensure the target repo
    has no important untracked files before running.
  - Between retries, ``git clean -fdx`` removes ALL untracked AND ignored files
    for full isolation — agent-created build caches, .pyc files, etc. are wiped.
  - During restore, ``git clean -fd`` is used, which preserves gitignored files.
    This means pre-existing ignored files survive, but any NEW ignored files
    created by the agent during the final attempt also survive restore.
    Strict original-state restoration is not guaranteed for untracked or
    gitignored artifacts.

Usage:
    python test_generate_patch.py \\
        -d /path/to/hutool \\
        --branch pr_1263 \\
        --gt-patch patches/1263.patch \\
        -f prompts/1263.md \\
        -o output/1263.patch \\
        -t trajectory/1263.json
"""

import os
import re
import sys
import json
import stat
import time
import shutil
import tempfile
import argparse
import platform
import subprocess
import requests
from datetime import datetime, timezone
from requests.auth import HTTPBasicAuth
from typing import Any, Optional

# Import model resolution and message handling from companion script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from check_opencode_server import (
    resolve_model,
    choose_server_model,
    is_assistant_message,
    normalize_message,
    assistant_error_message,
    wait_for_assistant_message,
)

MAX_RETRIES = 3
_SANITIZED_PREFIX = "__sanitized__:"

BASE_URL = os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/")
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD")


class AgentDidNotRunError(RuntimeError):
    """Raised when a request is accepted but no assistant reply is produced."""


# ── HTTP helper ─────────────────────────────────────────────────────────

def opencode_request(method: str, path: str, json_body: Any = None,
                     params: Optional[dict] = None, timeout: int = 300) -> Any:
    url = f"{BASE_URL}{path}"
    auth = HTTPBasicAuth(USERNAME, PASSWORD) if PASSWORD else None
    r = requests.request(method, url, json=json_body, params=params,
                         auth=auth, timeout=timeout)
    r.raise_for_status()
    if not r.content:
        return None
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        return r.text


# ── Git helpers ──────────────────────────────────────────────────────────

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


def setup_starting_point(directory: str, branch: Optional[str] = None,
                         gt_patch: Optional[str] = None,
                         sanitize: bool = False,
                         _mutated_flag: Optional[list] = None) -> tuple[str, str]:
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
            git_run(["checkout", branch], directory)
            _mark_mutated()
            print(f"[ok] Checked out branch: {branch}")

    # Record the HEAD of the branch BEFORE our baseline commit (for cleanup)
    branch_head = git_run(["rev-parse", "HEAD"], directory).stdout.strip()

    # 4) Ensure clean working tree (preserves pre-existing ignored files).
    #    These are lifecycle-critical: baseline prep must start from a clean tree.
    _mark_mutated()
    git_run(["checkout", "."], directory)
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
    result = git_run(["apply", "--reverse", gt_patch_abs], directory, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to reverse-apply ground truth patch: {gt_patch}\n"
            f"{result.stderr.strip()}"
        )
    print(f"[ok] Reverse-applied ground truth patch: {os.path.basename(gt_patch)}")

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


def reset_to_baseline(directory: str, baseline_commit: str) -> None:
    """Reset the repo to the baseline commit (starting point for each attempt).

    Raises RuntimeError if either git command fails, so callers know
    the repo may not be in a clean state.
    """
    git_run(["reset", "--hard", baseline_commit], directory)
    git_run(["clean", "-fdx"], directory)


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

        # Restore the original .git from backup (handles both dir and file)
        git_dir = os.path.join(directory, ".git")
        backup_git = os.path.join(backup_dir, ".git") if backup_dir else ""
        if not (backup_git and (os.path.isdir(backup_git) or os.path.isfile(backup_git))):
            # Backup missing — history cannot be restored; this is a hard failure
            git_run(["checkout", "."], directory, check=False)
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
        _remove_git_entry(backup_dir)
        print("[ok] Original .git restored from backup")

        # Undo the baseline commit (if any) by resetting to the original
        # branch HEAD before setup_starting_point added it.
        if branch_head:
            git_run(["reset", "--hard", branch_head], directory)  # critical
            print(f"[ok] Branch reset to original tip: {branch_head[:10]}")
        else:
            git_run(["checkout", "."], directory, check=False)
        git_run(["clean", "-fd"], directory, check=False)

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
            git_run(["checkout", "."], directory, check=False)
    else:
        git_run(["checkout", "."], directory, check=False)
    git_run(["clean", "-fd"], directory, check=False)

    # Switch back to original branch/ref if we changed it
    current_ref = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                          directory, check=False).stdout.strip()
    if current_ref == "HEAD":
        current_ref = git_run(["rev-parse", "HEAD"], directory, check=False).stdout.strip()
    if original_ref and original_ref != current_ref:
        git_run(["checkout", original_ref], directory)  # critical
        print(f"[ok] Switched back to: {original_ref}")


def get_patch(directory: str) -> str:
    """
    Get a standard git-style unified diff of all changes against HEAD (baseline).

    Temporarily stages everything (including new untracked files) so the diff
    captures tracked modifications, deletions, AND new files in one clean patch.
    """
    try:
        # Stage everything so new untracked files appear in the diff
        subprocess.run(["git", "add", "-A"], cwd=directory,
                       capture_output=True, timeout=30)
        # Diff all staged changes against HEAD
        result = subprocess.run(
            ["git", "diff", "--cached", "HEAD"],
            cwd=directory, capture_output=True, text=True, timeout=60,
        )
        # Unstage everything (leave working tree intact)
        subprocess.run(["git", "reset", "HEAD", "--quiet"], cwd=directory,
                       capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Best-effort unstage if we got interrupted after staging
        try:
            subprocess.run(["git", "reset", "HEAD", "--quiet"], cwd=directory,
                           capture_output=True, timeout=10)
        except Exception:
            pass
    return ""


def has_repo_changes(directory: str) -> bool:
    """Check if the repo has any uncommitted changes or untracked files."""
    result = git_run(["status", "--porcelain"], directory, check=False)
    return bool(result.stdout.strip())


def _remove_git_entry(path: str) -> None:
    """Remove a .git entry (directory or file) with force-writable fallback."""
    def _force_writable(func, fpath, _exc_info):
        os.chmod(fpath, stat.S_IWRITE)
        func(fpath)
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=_force_writable)
    elif os.path.isfile(path):
        os.remove(path)


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
    return new_head, backup_dir


def sanitize_prompt(prompt: str) -> str:
    """Strip repo URLs from the prompt to prevent agents from looking up the PR online."""
    # Stage 1: remove the **Repo Link:** block
    sanitized = re.sub(
        r'\n*\*\*Repo Link:\*\*\s*\n\[.*?\]\(.*?\)\s*\n*',
        '\n\n', prompt,
    )
    # Stage 2: catch any remaining git-hosting URLs
    sanitized = re.sub(
        r'https?://(?:github\.com|gitee\.com|gitlab\.com)/\S+',
        '[REDACTED]', sanitized,
    )
    # Collapse excessive blank lines
    sanitized = re.sub(r'\n{3,}', '\n\n', sanitized)
    return sanitized.strip()


# ── Core workflow steps ─────────────────────────────────────────────────

def check_health() -> dict:
    health = opencode_request("GET", "/global/health")
    print(f"[ok] Server up — version: {health.get('version', '?')}")
    return health


def create_session(directory: str) -> str:
    session = opencode_request("POST", "/session",
                               json_body={"title": "patch-gen"},
                               params={"directory": directory})
    sid = session["id"]
    print(f"[ok] Session: {sid}")
    return sid


def send_task(session_id: str, prompt: str, directory: str,
              agent: str = "build", model: Optional[dict] = None) -> Any:
    body: dict[str, Any] = {
        "agent": agent,
        "parts": [{"type": "text", "text": prompt}],
    }
    if model:
        body["model"] = model
    model_desc = f"{model['providerID']}:{model['modelID']}" if model else "server default"
    print(f"[..] Sending task to '{agent}' (model: {model_desc}) — waiting for response...")
    msg = opencode_request("POST", f"/session/{session_id}/message",
                           json_body=body,
                           params={"directory": directory},
                           timeout=600)

    # If POST returned no body, poll for the assistant reply
    if msg is None or (isinstance(msg, str) and not msg.strip()):
        print("[..] Message POST returned no body; polling for assistant reply...")
        try:
            result = wait_for_assistant_message(session_id, directory=directory)
            print("[ok] Agent finished.")
            return result
        except TimeoutError:
            raise AgentDidNotRunError(
                "Agent did NOT run — no assistant messages received after polling.\n"
                "       Check that --model matches a valid providerID:modelID."
            )

    # If POST returned a list, find the last assistant message
    if isinstance(msg, list):
        assistant_msgs = [m for m in msg if is_assistant_message(m)]
        if assistant_msgs:
            print("[ok] Agent finished.")
            return normalize_message(assistant_msgs[-1])
        try:
            result = wait_for_assistant_message(session_id, directory=directory)
            print("[ok] Agent finished.")
            return result
        except TimeoutError:
            raise AgentDidNotRunError(
                "Agent did NOT run — no assistant messages in session.\n"
                "       Check that --model matches a valid providerID:modelID."
            )

    # Single dict message
    if isinstance(msg, dict):
        if is_assistant_message(msg):
            print("[ok] Agent finished.")
            return normalize_message(msg)

    # Unexpected or non-assistant response — try polling
    print("[..] Unexpected response shape; polling for assistant reply...")
    try:
        result = wait_for_assistant_message(session_id, directory=directory)
        print("[ok] Agent finished.")
        return result
    except TimeoutError:
        raise AgentDidNotRunError(
            "Agent did NOT run — no assistant reply received.\n"
            "       Check that --model matches a valid providerID:modelID."
        )


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def validate_patch(patch: str) -> tuple[bool, str]:
    """
    Validate that a patch is a well-formed git-style unified diff.
    Returns (is_valid, reason).

    Validates each 'diff --git' block independently — every block must have:
      - '---' and '+++' file headers
      - At least one '@@ ... @@' hunk header
      - At least one content line (starting with ' ', '+', '-', or '\\')
    """
    if not patch or not patch.strip():
        return False, "empty patch"

    lines = patch.strip().splitlines()

    # Split into per-file blocks at each 'diff --git' boundary
    blocks: list[list[str]] = []
    for line in lines:
        if line.startswith("diff --git "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)

    if not blocks:
        return False, "no 'diff --git' header found"

    # Validate each block independently
    for i, block in enumerate(blocks, 1):
        header = block[0]
        has_minus = any(l.startswith("--- ") for l in block)
        has_plus = any(l.startswith("+++ ") for l in block)
        if not has_minus or not has_plus:
            return False, f"block {i} ({header}): missing '---' or '+++' file headers"

        hunks = [l for l in block if _HUNK_RE.match(l)]
        if not hunks:
            return False, f"block {i} ({header}): no '@@ ... @@' hunk headers"

        # Count content lines within hunks
        in_hunk = False
        content_lines = 0
        for line in block:
            if _HUNK_RE.match(line):
                in_hunk = True
                continue
            if in_hunk:
                if line.startswith(("diff --git ", "--- ", "+++ ",
                                    "index ", "new file", "deleted file")):
                    in_hunk = False
                    continue
                if line and line[0] in (" ", "+", "-", "\\"):
                    content_lines += 1

        if content_lines == 0:
            return False, f"block {i} ({header}): hunk headers present but no diff content"

    return True, f"ok ({len(blocks)} file(s))"


def print_response(msg: Any) -> None:
    if msg is None:
        print("[warn] No response from agent.")
        return
    parts = msg.get("parts", [])
    for p in parts:
        if p.get("type") == "text":
            print(f"  {p['text']}")
    tool_parts = [p for p in parts if p.get("type") == "tool"]
    if tool_parts:
        print(f"[info] {len(tool_parts)} tool call(s) made")


def cleanup_session(session_id: str, directory: str) -> None:
    try:
        opencode_request("DELETE", f"/session/{session_id}",
                         params={"directory": directory})
        print("[ok] Session cleaned up.")
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
        pass


# ── Trajectory collection ───────────────────────────────────────────────

def _parse_part(part: dict) -> dict:
    """Normalize a single message part into a detailed structured record."""
    ptype = part.get("type", "unknown")

    if ptype == "text":
        return {
            "type": "text",
            "text": part.get("text", ""),
        }

    if ptype == "tool":
        return {
            "type": "tool_call",
            "tool_name": part.get("name", part.get("toolName", "?")),
            "tool_id": part.get("id", part.get("toolCallId", "")),
            "state": part.get("state", "?"),            # pending/running/completed/error
            "input": part.get("input", part.get("args", {})),
            "output": part.get("output", part.get("result", "")),
            "error": part.get("error", None),
            # Timing if the server provides it
            "started_at": part.get("startedAt", None),
            "finished_at": part.get("finishedAt", None),
        }

    if ptype == "reasoning":
        return {
            "type": "reasoning",
            "text": part.get("text", part.get("reasoning", "")),
        }

    if ptype == "step-start":
        return {
            "type": "step_start",
            "name": part.get("name", ""),
        }

    if ptype == "step-finish":
        return {
            "type": "step_finish",
            "name": part.get("name", ""),
        }

    if ptype == "snapshot":
        return {
            "type": "snapshot",
            "data": part.get("data", part.get("snapshot", {})),
        }

    # Catch-all: preserve the raw part for anything unknown
    return {"type": ptype, "raw": part}


def _parse_message(msg: dict) -> dict:
    """Parse a single message into a structured trajectory entry."""
    return {
        "message_id": msg.get("id", ""),
        "role": msg.get("role", "?"),
        "created_at": msg.get("createdAt", msg.get("created_at", None)),
        "model": msg.get("model", None),
        "info": msg.get("info", {}),      # token usage, cost, etc.
        "metadata": msg.get("metadata", {}),
        "parts": [_parse_part(p) for p in msg.get("parts", [])],
    }


def collect_trajectory(session_id: str, directory: str, prompt: str,
                       agent: str, patch: str, health: dict,
                       t_start: float, t_session_created: float,
                       t_task_sent: float, t_task_done: float,
                       t_end: float, error: Optional[str] = None,
                       gt_patch_path: Optional[str] = None,
                       branch: Optional[str] = None,
                       baseline_commit: Optional[str] = None) -> dict:
    """
    Build a comprehensive trajectory record with full metadata.
    Fetches session info, all messages, file status, and diff data.
    """
    # Fetch session details
    session = opencode_request("GET", f"/session/{session_id}",
                               params={"directory": directory})

    # Fetch every message in the conversation
    raw_messages = opencode_request("GET", f"/session/{session_id}/message",
                                    params={"directory": directory}) or []

    # Fetch file-level change status
    try:
        file_status = opencode_request("GET", "/file/status",
                                       params={"directory": directory})
    except requests.HTTPError:
        file_status = None

    # Fetch raw diff data (structured, before we flatten to string)
    try:
        raw_diff = opencode_request("GET", f"/session/{session_id}/diff",
                                    params={"directory": directory})
    except requests.HTTPError:
        raw_diff = None

    # Parse messages into structured trajectory steps
    messages = [_parse_message(m) for m in raw_messages]

    # Compute stats from messages
    tool_calls = []
    reasoning_steps = []
    for m in messages:
        for p in m["parts"]:
            if p["type"] == "tool_call":
                tool_calls.append(p)
            elif p["type"] == "reasoning":
                reasoning_steps.append(p)

    tool_summary = {}
    for tc in tool_calls:
        name = tc["tool_name"]
        tool_summary[name] = tool_summary.get(name, 0) + 1

    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    for m in messages:
        info = m.get("info") or {}
        total_tokens += info.get("totalTokens", info.get("total_tokens", 0))
        prompt_tokens += info.get("promptTokens", info.get("prompt_tokens", 0))
        completion_tokens += info.get("completionTokens", info.get("completion_tokens", 0))

    return {
        # ── Session metadata ──
        "metadata": {
            "session_id": session_id,
            "directory": directory,
            "directory_name": os.path.basename(directory),
            "agent": agent,
            "server_url": BASE_URL,
            "server_version": health.get("version", "?"),
            "model": (session or {}).get("model", None),
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "branch": branch,
            "ground_truth_patch": gt_patch_path,
            "baseline_commit": baseline_commit,
        },

        # ── Input ──
        "input": {
            "prompt": prompt,
            "prompt_length": len(prompt),
        },

        # ── Output ──
        "output": {
            "patch": patch,
            "patch_length": len(patch),
            "patch_lines": len(patch.splitlines()) if patch else 0,
            "has_patch": bool(patch),
            "error": error,
        },

        # ── Timing (seconds) ──
        "timing": {
            "total_duration": round(t_end - t_start, 3),
            "session_creation": round(t_session_created - t_start, 3),
            "task_execution": round(t_task_done - t_task_sent, 3),
            "diff_retrieval": round(t_end - t_task_done, 3),
            "started_at": datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat(),
            "finished_at": datetime.fromtimestamp(t_end, tz=timezone.utc).isoformat(),
        },

        # ── Token usage (aggregated across all messages) ──
        "token_usage": {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },

        # ── Agent behavior stats ──
        "stats": {
            "total_messages": len(messages),
            "user_messages": sum(1 for m in messages if m["role"] == "user"),
            "assistant_messages": sum(1 for m in messages if m["role"] == "assistant"),
            "total_tool_calls": len(tool_calls),
            "tool_call_breakdown": tool_summary,
            "failed_tool_calls": sum(1 for tc in tool_calls if tc["state"] == "error"),
            "reasoning_steps": len(reasoning_steps),
        },

        # ── Full conversation trajectory ──
        "trajectory": messages,

        # ── Raw session & file data from server ──
        "session_raw": session,
        "file_status": file_status,
        "diff_raw": raw_diff,
    }


def save_trajectory(trajectory: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(trajectory, f, ensure_ascii=False, indent=2, default=str)
    size_kb = os.path.getsize(out_path) / 1024
    n_msgs = trajectory.get("stats", {}).get("total_messages", "?")
    n_tools = trajectory.get("stats", {}).get("total_tool_calls", "?")
    print(f"[ok] Trajectory saved to {out_path} "
          f"({size_kb:.1f} KB, {n_msgs} messages, {n_tools} tool calls)")


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run an opencode agent to generate a code patch for a PR issue")
    parser.add_argument("-d", "--directory", required=True,
                        help="Target project directory")
    parser.add_argument("-f", "--prompt-file", required=True,
                        help="Read the prompt from a .md file")
    parser.add_argument("-o", "--output",
                        help="Write patch to file (default: ./output.patch)")
    parser.add_argument("-t", "--trajectory",
                        help="Save agent trajectory to this JSON file")
    parser.add_argument("--branch",
                        help="Git branch to checkout before starting "
                             "(e.g. pr_1263 after fetching the PR)")
    parser.add_argument("--gt-patch",
                        help="Ground truth patch file to reverse-apply, establishing "
                             "the pre-fix starting point for the agent")
    args = parser.parse_args()

    agent = "build"
    max_retries = MAX_RETRIES

    # ── Validate inputs (before any git mutations) ──

    with open(args.prompt_file) as f:
        prompt = f.read().strip()

    prompt = sanitize_prompt(prompt)
    print("[ok] Prompt sanitized (repo URLs removed)")

    directory = os.path.abspath(args.directory)

    if args.gt_patch:
        gt_patch_abs = os.path.abspath(args.gt_patch)
        if not os.path.isfile(gt_patch_abs):
            print(f"[error] Ground truth patch not found: {gt_patch_abs}")
            sys.exit(1)

    # ── 1) Health check ──

    t_start = time.time()
    health = check_health()

    # ── 2) Resolve model from config ──

    configured_model, configured_name = resolve_model(agent=agent)

    selected_model, provider_label, model_display, model_warning = choose_server_model(
        configured_model,
        preferred_name=configured_name,
        directory=directory,
    )
    if model_warning:
        print(f"[warn] {model_warning}")
    if selected_model:
        print(f"[ok] Model: {provider_label or selected_model['providerID']}:"
              f"{model_display or selected_model['modelID']}")
    elif provider_label and model_display:
        print(f"[ok] Model: {provider_label}:{model_display} (server default)")
    else:
        print("[ok] Model: server default")

    # ── 3) Setup starting point (always sanitized) ──
    # Record pre-setup ref so the finally block can do basic cleanup even if
    # setup_starting_point() fails partway through (e.g. after checkout but
    # before sanitization).
    pre_setup_ref = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                            directory, check=False).stdout.strip()
    if pre_setup_ref == "HEAD":
        pre_setup_ref = git_run(["rev-parse", "HEAD"],
                                directory, check=False).stdout.strip()

    original_ref = None       # set by setup_starting_point on success
    baseline_commit = None    # set by setup_starting_point on success
    mutated_flag = []         # populated by setup_starting_point on first mutation

    # ── 4) Main retry loop ──

    attempts = []
    final_patch = ""
    final_error = None
    final_session_id = None
    t_session_created = t_start
    t_task_sent = t_start
    t_task_done = t_start

    try:
        original_ref, baseline_commit = setup_starting_point(
            directory,
            branch=args.branch,
            gt_patch=args.gt_patch,
            sanitize=True,
            _mutated_flag=mutated_flag,
        )

        for attempt in range(1, max_retries + 1):
            print(f"\n{'='*40}")
            print(f"[attempt {attempt}/{max_retries}]")
            print(f"{'='*40}")

            patch = ""
            error = None
            abort_retries = False
            t_session_created = time.time()
            t_task_sent = t_session_created
            t_task_done = t_session_created
            session_id = None

            try:
                # Reset to the baseline (starting point) before each retry
                if attempt > 1:
                    print("[..] Resetting repo to baseline...")
                    reset_to_baseline(directory, baseline_commit)
                    print(f"[ok] Repo reset to baseline ({baseline_commit[:10]}).")

                # Create session
                session_id = create_session(directory)
                t_session_created = time.time()
                final_session_id = session_id

                # Send the coding task
                t_task_sent = time.time()
                msg = send_task(session_id, prompt, directory,
                                agent=agent, model=selected_model)
                t_task_done = time.time()

                print_response(msg)

                # Check that the agent actually produced changes
                if not has_repo_changes(directory):
                    print("[warn] Agent responded but made no changes to the repo.")
                    patch = ""
                else:
                    patch = get_patch(directory)

            except AgentDidNotRunError as e:
                error = str(e)
                t_task_done = time.time()
                abort_retries = True
                print(f"[error] {e}")

            except Exception as e:
                error = str(e)
                t_task_done = time.time()
                print(f"[error] {e}")

            # Validate the patch
            if abort_retries:
                is_valid, reason = False, "agent did not run"
            elif patch:
                is_valid, reason = validate_patch(patch)
            else:
                is_valid, reason = False, "empty patch"

            t_attempt_end = time.time()

            # Record this attempt
            attempt_record = {
                "attempt": attempt,
                "session_id": session_id,
                "patch_valid": is_valid,
                "patch_validation_reason": reason,
                "patch_length": len(patch),
                "error": error,
                "duration": round(t_attempt_end - (t_session_created if session_id else t_task_sent), 3),
            }
            attempts.append(attempt_record)

            # Collect trajectory for this attempt before any cleanup
            if args.trajectory and session_id:
                try:
                    attempt_trajectory = collect_trajectory(
                        session_id=session_id,
                        directory=directory,
                        prompt=prompt,
                        agent=agent,
                        patch=patch,
                        health=health,
                        t_start=t_start,
                        t_session_created=t_session_created,
                        t_task_sent=t_task_sent,
                        t_task_done=t_task_done,
                        t_end=t_attempt_end,
                        error=error,
                        gt_patch_path=args.gt_patch,
                        branch=args.branch,
                        baseline_commit=baseline_commit,
                    )
                    attempt_record["trajectory"] = attempt_trajectory
                except (requests.HTTPError, requests.ConnectionError) as te:
                    print(f"[warn] Could not collect trajectory for attempt {attempt}: {te}")

            if is_valid:
                print(f"[ok] Patch is valid ({reason}).")
                final_patch = patch
                final_error = None
                break
            else:
                print(f"[warn] Patch invalid: {reason}.")
                final_error = error if error else f"attempt {attempt}: patch invalid — {reason}"
                # Clean up this failed session before retrying
                if session_id:
                    cleanup_session(session_id, directory)
                if abort_retries:
                    print("[error] Non-retryable failure detected; aborting further attempts.")
                    break
                if attempt < max_retries:
                    print(f"[..] Retrying ({attempt}/{max_retries})...")
        else:
            # All retries exhausted
            print(f"\n[error] All {max_retries} attempts failed to produce a valid patch.")

        t_end = time.time()

        # ── 5) Write the patch file ──

        if final_patch:
            output_path = args.output or os.path.join(os.getcwd(), "output.patch")
            output_path = os.path.abspath(output_path)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                f.write(final_patch)
            print(f"[ok] Patch written to {output_path}")

        # ── 6) Save trajectory ──

        if args.trajectory:
            # Use the trajectory from the successful attempt, or the last attempt
            final_trajectory = None
            for a in reversed(attempts):
                if "trajectory" in a:
                    final_trajectory = a["trajectory"]
                    break

            if final_trajectory is None:
                # Fallback: minimal record if no trajectory could be collected
                final_trajectory = {
                    "metadata": {
                        "session_id": final_session_id,
                        "directory": directory,
                        "directory_name": os.path.basename(directory),
                        "agent": agent,
                        "server_url": BASE_URL,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "branch": args.branch,
                        "ground_truth_patch": args.gt_patch,
                        "baseline_commit": baseline_commit,
                        "sanitized": True,
                    },
                    "input": {"prompt": prompt, "prompt_length": len(prompt)},
                    "output": {"patch": final_patch, "has_patch": bool(final_patch), "error": final_error},
                }

            # Inject sanitization flag into metadata
            if "metadata" in final_trajectory:
                final_trajectory["metadata"]["sanitized"] = True

            # Override output with the final result
            final_trajectory["output"] = {
                "patch": final_patch,
                "patch_length": len(final_patch),
                "patch_lines": len(final_patch.splitlines()) if final_patch else 0,
                "has_patch": bool(final_patch),
                "error": final_error,
            }
            final_trajectory["timing"] = {
                "total_duration": round(t_end - t_start, 3),
                "started_at": datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat(),
                "finished_at": datetime.fromtimestamp(t_end, tz=timezone.utc).isoformat(),
            }
            # Strip per-attempt trajectory data to avoid duplication in the attempts list
            clean_attempts = [{k: v for k, v in a.items() if k != "trajectory"}
                              for a in attempts]
            final_trajectory["retry"] = {
                "max_retries": max_retries,
                "total_attempts": len(attempts),
                "attempts": clean_attempts,
            }
            save_trajectory(final_trajectory, args.trajectory)

    finally:
        # ── 7) Restore repo to original state (guaranteed) ──

        restore_failed = False
        if original_ref is not None and baseline_commit is not None:
            # Setup completed — use full restore_repo logic
            try:
                restore_repo(directory, original_ref, baseline_commit)
            except Exception as e:
                restore_failed = True
                print(f"[error] Failed to restore repo: {e}")
        elif not mutated_flag:
            # Setup failed before any git mutations (e.g. bad gt_patch path,
            # bad branch name on first checkout).  Nothing to undo.
            print("[ok] Setup failed before mutating repo; no cleanup needed")
        else:
            # Setup started mutating but did not complete — best-effort
            # cleanup using pre-setup ref.  If setup failed after committing
            # the baseline (or after _sanitize_git_history destroyed .git),
            # we need to undo that too.
            print("[warn] Setup did not complete; attempting basic cleanup...")
            try:
                git_run(["checkout", "."], directory, check=False)
                git_run(["clean", "-fd"], directory, check=False)

                # Check if setup left a baseline commit on the current branch.
                head_msg = git_run(["log", "-1", "--format=%s"],
                                   directory, check=False).stdout.strip()
                if head_msg == "baseline: pre-patch starting point (auto-generated)":
                    parent = git_run(["rev-parse", "--verify", "HEAD^"],
                                     directory, check=False).stdout.strip()
                    if parent:
                        git_run(["reset", "--hard", parent], directory)  # critical
                        print(f"[ok] Removed leftover baseline commit; "
                              f"reset to {parent[:10]}")

                current = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                                  directory, check=False).stdout.strip()
                if current == "HEAD":
                    current = git_run(["rev-parse", "HEAD"],
                                      directory, check=False).stdout.strip()
                if pre_setup_ref and pre_setup_ref != current:
                    git_run(["checkout", pre_setup_ref], directory)  # critical
                    print(f"[ok] Switched back to pre-setup ref: {pre_setup_ref}")
                print("[ok] Basic cleanup done")
            except Exception as e:
                restore_failed = True
                print(f"[error] Basic cleanup failed: {e}")

        # ── 8) Cleanup final session ──

        if final_session_id and final_patch:
            cleanup_session(final_session_id, directory)

    # ── 9) Exit with appropriate code ──

    if restore_failed:
        print("[error] Exiting with error: repo restore failed")
        sys.exit(2)
    if not final_patch:
        sys.exit(1)


if __name__ == "__main__":
    main()
