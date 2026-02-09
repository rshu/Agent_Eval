"""HTTP fetching utilities for PR descriptions and patch files."""

import os
import re

import requests


def is_url(value: str) -> bool:
    """Check if a value is a URL."""
    return value.startswith("http://") or value.startswith("https://")


def detect_platform(url: str) -> str:
    """Detect whether a URL points to GitHub or Gitee."""
    if "github.com" in url:
        return "github"
    elif "gitee.com" in url:
        return "gitee"
    else:
        raise ValueError(f"Unsupported platform for URL: {url}")


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
    """
    gh_match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url
    )
    if gh_match:
        owner = _validate_segment("owner", gh_match.group(1))
        repo = _validate_segment("repo", gh_match.group(2))
        return ("github", owner, repo, gh_match.group(3))

    gitee_match = re.match(
        r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", pr_url
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


def parse_repo_url(repo_url: str) -> str:
    """Extract the repository name from a repo URL."""
    # Strip trailing slash and .git suffix
    url = repo_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Last path segment is the repo name
    return url.split("/")[-1]


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
