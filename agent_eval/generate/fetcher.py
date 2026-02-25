"""HTTP fetching utilities for PR descriptions and patch files."""

import os
import re

import requests


def is_url(value: str) -> bool:
    """Check if a value is a URL (scheme check is case-insensitive)."""
    lower = value[:8].lower()
    return lower.startswith("http://") or lower.startswith("https://")


_SAFE_SEGMENT = re.compile(r"^[\w.\-]+$")


def _validate_segment(label: str, value: str) -> str:
    """Validate that a URL path segment contains only safe characters."""
    if not _SAFE_SEGMENT.match(value):
        raise ValueError(f"Invalid {label} in PR URL: {value!r}")
    return value


def parse_pr_url(pr_url: str) -> tuple[str, str, str, str]:
    """Parse a PR URL into (platform, owner, repo, pr_number).

    Supports:
      - GitHub: https://github.com/owner/repo/pull/123
      - Gitee:  https://gitee.com/owner/repo/pulls/123

    Host matching is case-insensitive (e.g. ``GitHub.com`` is accepted).
    Trailing slashes, query strings, and fragments are stripped.
    """
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(pr_url)
    # Normalize: lowercase host (strip default port), strip trailing slash /
    # query / fragment so that browser-copied URLs like ".../pull/42/" or
    # ".../pull/42?tab=files" work.
    clean_path = parsed.path.rstrip("/")
    normalized = parsed._replace(
        netloc=_normalize_host(parsed),
        path=clean_path,
        query="",
        fragment="",
    )
    clean_url = urlunparse(normalized)

    gh_match = re.fullmatch(
        r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", clean_url
    )
    if gh_match:
        owner = _validate_segment("owner", gh_match.group(1))
        repo = _validate_segment("repo", gh_match.group(2))
        return ("github", owner, repo, gh_match.group(3))

    gitee_match = re.fullmatch(
        r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", clean_url
    )
    if gitee_match:
        owner = _validate_segment("owner", gitee_match.group(1))
        repo = _validate_segment("repo", gitee_match.group(2))
        return ("gitee", owner, repo, gitee_match.group(3))

    raise ValueError(
        f"Cannot parse PR URL: {pr_url}\n"
        "Expected format:\n"
        "  GitHub: https://github.com/owner/repo/pull/123\n"
        "  Gitee:  https://gitee.com/owner/repo/pulls/123"
    )


_SUPPORTED_HOSTS = {"github.com", "gitee.com"}

_HOST_TO_PLATFORM = {"github.com": "github", "gitee.com": "gitee"}

# Default ports that can be stripped without changing semantics.
_DEFAULT_PORTS = {"https": ":443", "http": ":80"}


def _normalize_host(parsed) -> str:
    """Return the lowercase hostname, stripping any default port.

    E.g. ``GitHub.com:443`` with scheme ``https`` → ``github.com``.
    """
    host = parsed.netloc.lower()
    default_port = _DEFAULT_PORTS.get(parsed.scheme.lower(), "")
    if default_port and host.endswith(default_port):
        host = host[: -len(default_port)]
    return host


def _parse_repo_url_full(repo_url: str) -> tuple[str, str, str]:
    """Parse a repo URL into (platform, owner, repo).

    Expects exactly ``https://<host>/<owner>/<repo>[.git]`` — extra path
    segments (e.g. ``/tree/main``, ``/pull/1``) are rejected.

    Enforces that the URL points to GitHub or Gitee (case-insensitive host).
    Strips trailing slashes, ``.git`` suffix, query strings, and fragments.

    Raises:
        ValueError: If the URL is malformed, not a supported platform,
            missing owner/repo, or has extra path segments.
    """
    from urllib.parse import urlparse

    parsed = urlparse(repo_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"--repo-url is not a valid URL: {repo_url}")
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"--repo-url must use http or https scheme (got {parsed.scheme!r})"
        )
    host = _normalize_host(parsed)
    if host not in _SUPPORTED_HOSTS:
        raise ValueError(
            f"--repo-url must be a GitHub or Gitee URL (got host {parsed.netloc!r})"
        )
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        raise ValueError(
            f"--repo-url must contain owner/repo path (got {repo_url!r})"
        )
    # Strip .git suffix from the repo segment (not the whole path, so that
    # e.g. repo.git/info is not silently accepted as "repo").
    repo = segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        raise ValueError(
            f"--repo-url has an empty repo name (got {repo_url!r})"
        )
    if len(segments) != 2:
        raise ValueError(
            f"--repo-url has unexpected extra path segments — expected "
            f"https://<host>/owner/repo (got {repo_url!r})"
        )
    owner = _validate_segment("owner", segments[0])
    _validate_segment("repo", repo)
    return (_HOST_TO_PLATFORM[host], owner, repo)


def parse_repo_url(repo_url: str) -> str:
    """Extract the repository name from a repo URL.

    Expects a URL of the form ``https://<host>/<owner>/<repo>[.git]`` where
    ``<host>`` is ``github.com`` or ``gitee.com`` (case-insensitive).
    Strips trailing slashes, ``.git`` suffix, and any query string or
    fragment so that e.g. ``https://github.com/org/repo?tab=readme``
    correctly returns ``repo``.

    Raises:
        ValueError: If the URL is malformed, not a supported platform, or
            does not contain an ``owner/repo`` path.
    """
    _, _, repo = _parse_repo_url_full(repo_url)
    return repo


def validate_repo_pr_match(repo_url: str, pr_url: str) -> None:
    """Verify that ``--repo-url`` and ``--pr-url`` refer to the same repository.

    Both URLs are parsed and their platform, owner, and repo name are compared
    (case-insensitive for owner/repo, since GitHub and Gitee treat them that way).

    Raises:
        ValueError: If the URLs point to different platforms or repositories.
    """
    repo_platform, repo_owner, repo_name = _parse_repo_url_full(repo_url)
    pr_platform, pr_owner, pr_repo, _ = parse_pr_url(pr_url)

    if repo_platform != pr_platform:
        raise ValueError(
            f"--repo-url ({repo_platform}) and --pr-url ({pr_platform}) "
            f"are on different platforms"
        )
    if (repo_owner.lower() != pr_owner.lower()
            or repo_name.lower() != pr_repo.lower()):
        raise ValueError(
            f"--repo-url and --pr-url refer to different repositories: "
            f"{repo_owner}/{repo_name} vs {pr_owner}/{pr_repo}"
        )


def fetch_pr_description(pr_url: str) -> str:
    """Fetch the PR body/description text via the platform's REST API."""
    platform, owner, repo, pr_number = parse_pr_url(pr_url)

    if platform == "github":
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    else:
        api_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = {}
        token = os.environ.get("GITEE_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

    resp = requests.get(api_url, headers=headers, timeout=30)

    if resp.status_code == 403 and platform == "github":
        raise RuntimeError(
            "GitHub API rate limit exceeded. Set the GITHUB_TOKEN environment "
            "variable to increase your rate limit."
        )
    if resp.status_code == 404:
        raise RuntimeError(f"PR not found or not accessible: {pr_url}")

    resp.raise_for_status()
    data = resp.json()
    body = data.get("body") or ""
    if not body.strip():
        title = data.get("title", "")
        if title:
            return title
        raise RuntimeError(f"PR has no description or title: {pr_url}")
    return body


def fetch_patch_from_url(url: str) -> str:
    """Download patch content from a URL."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text
