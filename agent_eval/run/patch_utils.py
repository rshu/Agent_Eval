"""Stateless patch and prompt utilities."""

import re
import subprocess

from .git_helpers import git_run, _SANITIZE_SIDECAR

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")

# Internal files that must never appear in agent-generated patches.
_INTERNAL_FILES = {_SANITIZE_SIDECAR}


def _is_internal_diff(header: str) -> bool:
    """Check if a diff --git header touches an internal file (exact path match).

    Matches ``a/<name> b/`` (a-side) and line ending with ``b/<name>`` (b-side)
    to avoid substring collisions (e.g. ``.file.json-notes`` != ``.file.json``).
    """
    for name in _INTERNAL_FILES:
        if f"a/{name} b/" in header or header.rstrip().endswith(f"b/{name}"):
            return True
    return False


def _strip_internal_files(patch: str) -> str:
    """Remove diff blocks that touch internal agent_eval files."""
    if not patch:
        return patch
    lines = patch.splitlines(keepends=True)
    result: list[str] = []
    skip = False
    for line in lines:
        if line.startswith("diff --git "):
            skip = _is_internal_diff(line)
        if not skip:
            result.append(line)
    out = "".join(result)
    return out if out.strip() else ""


def get_patch(directory: str) -> str:
    """
    Get a standard git-style unified diff of all changes against HEAD (baseline).

    Temporarily stages everything (including new untracked files) so the diff
    captures tracked modifications, deletions, AND new files in one clean patch.

    Raises:
        RuntimeError: If ``git add`` or ``git diff`` fails.
    """
    def _unstage() -> None:
        """Best-effort unstage so the working tree is left intact."""
        try:
            subprocess.run(["git", "reset", "HEAD", "--quiet"], cwd=directory,
                           capture_output=True, timeout=10)
        except Exception:
            pass

    try:
        # Stage everything so new untracked files appear in the diff
        add_result = subprocess.run(
            ["git", "add", "-A"], cwd=directory,
            capture_output=True, text=True, timeout=30,
        )
        if add_result.returncode != 0:
            _unstage()
            raise RuntimeError(
                f"git add -A failed (exit {add_result.returncode}): "
                f"{add_result.stderr.strip()}"
            )

        # Diff all staged changes against HEAD
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "HEAD"],
            cwd=directory, capture_output=True, text=True, timeout=60,
        )
        # Always unstage before checking the result
        _unstage()

        if diff_result.returncode != 0:
            raise RuntimeError(
                f"git diff --cached failed (exit {diff_result.returncode}): "
                f"{diff_result.stderr.strip()}"
            )

        raw = diff_result.stdout if diff_result.stdout.strip() else ""
        return _strip_internal_files(raw)

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _unstage()
        raise RuntimeError(f"git command failed: {e}") from e


def has_repo_changes(directory: str) -> bool:
    """Check if the repo has any uncommitted changes or untracked files."""
    result = git_run(["status", "--porcelain"], directory, check=False)
    return bool(result.stdout.strip())


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

        # Rename-only, mode-change-only, or binary-only blocks have no hunks
        # and that is valid.
        is_rename = (any(l.startswith("rename from ") for l in block)
                     and any(l.startswith("rename to ") for l in block))
        is_mode_change = (any(l.startswith("old mode ") for l in block)
                          and any(l.startswith("new mode ") for l in block))
        is_binary = any(l.startswith("Binary files ") for l in block)

        has_minus = any(l.startswith("--- ") for l in block)
        has_plus = any(l.startswith("+++ ") for l in block)
        hunks = [l for l in block if _HUNK_RE.match(l)]

        # Metadata-only blocks (pure rename / mode change / binary) are valid as-is
        if not hunks and (is_rename or is_mode_change or is_binary):
            continue

        if not has_minus or not has_plus:
            return False, f"block {i} ({header}): missing '---' or '+++' file headers"

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


def sanitize_prompt(prompt: str) -> str:
    """Strip repo URLs from the prompt to prevent agents from looking up the PR online."""
    # Stage 1: remove the **Repo Link:** block
    sanitized = re.sub(
        r'\n*\*\*Repo Link:\*\*\s*\n\[.*?\]\(.*?\)\s*\n*',
        '\n\n', prompt,
    )
    # Stage 2: catch any remaining git-hosting URLs (case-insensitive host)
    sanitized = re.sub(
        r'https?://(?:github\.com|gitee\.com|gitlab\.com)/\S+',
        '[REDACTED]', sanitized, flags=re.IGNORECASE,
    )
    # Collapse excessive blank lines
    sanitized = re.sub(r'\n{3,}', '\n\n', sanitized)
    return sanitized.strip()
