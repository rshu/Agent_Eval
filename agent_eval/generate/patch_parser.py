"""Parse unified diff (patch) files to extract changed file paths."""

import re


def _unquote_path(raw: str) -> str:
    r"""Remove surrounding double-quotes and unescape a git-quoted path.

    Git quotes paths containing special characters (spaces, non-ASCII, etc.)
    as C-style strings: ``"path/with spaces/file.txt"``.  Non-ASCII bytes are
    encoded as octal sequences like ``\303\251`` (UTF-8 for ``é``).
    """
    if not (raw.startswith('"') and raw.endswith('"')):
        return raw

    raw = raw[1:-1]

    # Decode escape sequences (including octal) into raw bytes, then decode
    # as UTF-8.  We process byte-by-byte to handle mixed ASCII + octal.
    out: list[int] = []
    i = 0
    while i < len(raw):
        if raw[i] == '\\' and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == '\\':
                out.append(ord('\\'))
                i += 2
            elif nxt == '"':
                out.append(ord('"'))
                i += 2
            elif nxt == 'n':
                out.append(ord('\n'))
                i += 2
            elif nxt == 't':
                out.append(ord('\t'))
                i += 2
            elif nxt in '01234567':
                # Octal: up to 3 digits
                end = i + 2
                while end < len(raw) and end < i + 4 and raw[end] in '01234567':
                    end += 1
                out.append(int(raw[i + 1:end], 8))
                i = end
            else:
                out.append(ord(raw[i]))
                i += 1
        else:
            out.append(ord(raw[i]))
            i += 1

    return bytes(out).decode("utf-8", errors="replace")


def _parse_quoted_pair(rest: str) -> str | None:
    """Parse b-path from a diff --git line where paths are quoted."""
    # Expect: "a/..." "b/..."
    if not rest.startswith('"'):
        return None
    # Find end of first quoted string
    i = 1
    while i < len(rest):
        if rest[i] == '\\':
            i += 2
            continue
        if rest[i] == '"':
            break
        i += 1
    else:
        return None
    # rest[i] == closing quote of a-side
    remaining = rest[i + 1:]  # should be ' "b/..."'
    if not remaining.startswith(' "'):
        return None
    b_quoted = remaining[1:]  # '"b/..."'
    path = _unquote_path(b_quoted)
    if path.startswith("b/"):
        path = path[2:]
    return path


def _parse_diff_git_line(line: str) -> str | None:
    """Extract the b-side file path from a ``diff --git a/... b/...`` line.

    Handles three cases:
    1. Quoted paths: ``diff --git "a/..." "b/..."``
    2. Unquoted non-rename: uses symmetry (a-path sans ``a/`` == b-path sans
       ``b/``) to find the correct split when filenames contain `` b/``.
    3. Unquoted rename: tries every `` b/`` split; the caller can cross-check
       against ``+++ b/`` lines via :func:`extract_files_from_patch`.
    """
    prefix = "diff --git "
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix):]

    # Case 1: quoted paths
    if rest.startswith('"'):
        return _parse_quoted_pair(rest)

    # Case 2+3: unquoted — try each ' b/' as the split point.
    # Collect all candidates; prefer the symmetric (non-rename) match.
    candidates: list[str] = []
    idx = 0
    while True:
        idx = rest.find(" b/", idx)
        if idx == -1:
            break
        a_part = rest[:idx]       # e.g. "a/src/a b/c.txt"
        b_part = rest[idx + 1:]   # e.g. "b/src/a b/c.txt"
        if a_part.startswith("a/") and b_part.startswith("b/"):
            if a_part[2:] == b_part[2:]:
                return b_part[2:]  # symmetric match — unambiguous
            candidates.append(b_part[2:])
        idx += 1

    # No symmetric match found; return the last candidate (best guess for
    # renames), or None.  The caller can cross-check with +++ lines.
    return candidates[-1] if candidates else None


# Matches a `+++ …` line with either quoted or unquoted path.
_PLUS_RE = re.compile(
    r'^\+\+\+ (?:"((?:[^"\\]|\\.)+)"|b/(.+))$',
    re.MULTILINE,
)

# Binary section markers emitted by git for non-text diffs.
_BINARY_RE = re.compile(r"^(?:Binary files .* differ|GIT binary patch)$", re.MULTILINE)


def _extract_plus_path(m: re.Match) -> str | None:
    """Return the path from a ``+++ b/…`` or ``+++ "b/…"`` match."""
    quoted = m.group(1)
    if quoted is not None:
        path = _unquote_path('"' + quoted + '"')
        if path.startswith("b/"):
            path = path[2:]
        return path
    return m.group(2)


def extract_files_from_patch(patch_text: str) -> list[str]:
    """Extract unique file paths from a unified diff.

    Primary: parse ``diff --git a/… b/…`` lines (uses b-side path).
    Cross-check: the ``+++ b/…`` line *within the same section* is used to
    resolve ambiguous renames.  Binary sections (no ``+++`` line) are handled
    correctly for parsing, but are excluded from output because v3 should only
    suggest text-edit candidate files.
    Fallback: if no ``diff --git`` lines are found, use ``+++`` paths only.
    Filters ``/dev/null`` and deduplicates while preserving order.
    """
    files: list[str] = []
    seen: set[str] = set()

    # Locate all diff --git line positions so we can determine section
    # boundaries (each section runs from one diff --git to the next).
    diff_git_re = re.compile(r"^diff --git ", re.MULTILINE)
    section_starts = [m.start() for m in diff_git_re.finditer(patch_text)]

    matches: list[str] = []
    for i, start in enumerate(section_starts):
        # Section spans from this diff --git to the next (or end of text).
        section_end = section_starts[i + 1] if i + 1 < len(section_starts) else len(patch_text)
        section = patch_text[start:section_end]

        # Exclude binary-changed files from v3 "Relevant files".
        if _BINARY_RE.search(section):
            continue

        line_end = section.find("\n")
        full_line = section[:line_end if line_end != -1 else len(section)]
        path = _parse_diff_git_line(full_line)
        if not path:
            continue

        # Cross-check against the +++ line *within this section only*.
        plus_match = _PLUS_RE.search(section)
        if plus_match:
            alt = _extract_plus_path(plus_match)
            if alt and alt.strip() != "/dev/null" and alt.strip() != path:
                # The +++ line is unambiguous — prefer it for renames with
                # tricky filenames (e.g. containing " b/").
                path = alt.strip()

        matches.append(path)

    # Fallback to +++ b/ lines if no diff --git lines found
    if not matches:
        plus_paths = [_extract_plus_path(m) for m in _PLUS_RE.finditer(patch_text)]
        matches = [p for p in plus_paths if p]

    for path in matches:
        path = path.strip()
        if path and path != "/dev/null" and path not in seen:
            seen.add(path)
            files.append(path)

    return files
