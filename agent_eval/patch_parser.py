"""Parse unified diff (patch) files to extract changed file paths."""

import re


def _parse_diff_git_line(line: str) -> str | None:
    """Extract the b-side file path from a 'diff --git a/... b/...' line.

    Handles filenames with spaces by finding the midpoint split of the
    two paths (which must be equal length for renamed files, or we take
    the portion after ' b/').
    """
    prefix = "diff --git "
    rest = line[len(prefix):]

    # Try splitting on ' b/' — take the last occurrence to handle spaces
    idx = rest.rfind(" b/")
    if idx == -1:
        return None
    return rest[idx + 3:]


def extract_files_from_patch(patch_text: str) -> list[str]:
    """Extract unique file paths from a unified diff.

    Primary: parse 'diff --git a/... b/...' lines (uses b-side path).
    Fallback: parse '+++ b/...' lines.
    Filters /dev/null and deduplicates while preserving order.
    """
    files = []
    seen: set[str] = set()

    # Primary: diff --git lines
    diff_git_re = re.compile(r"^diff --git ", re.MULTILINE)
    matches: list[str] = []
    for m in diff_git_re.finditer(patch_text):
        line_end = patch_text.find("\n", m.start())
        full_line = patch_text[m.start():line_end if line_end != -1 else len(patch_text)]
        path = _parse_diff_git_line(full_line)
        if path:
            matches.append(path)

    # Fallback to +++ b/ lines if no diff --git lines found
    if not matches:
        plus_pattern = re.compile(r"^\+\+\+ b/(.+?)$", re.MULTILINE)
        matches = plus_pattern.findall(patch_text)

    for path in matches:
        path = path.strip()
        if path and path != "/dev/null" and path not in seen:
            seen.add(path)
            files.append(path)

    return files
