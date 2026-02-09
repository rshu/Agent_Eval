"""Markdown template rendering for v1, v2, and v3 prompt files."""

TASK_LINE_V1 = "Fix/implement the requested change in the repository based on the PR issue description."
TASK_LINE_V2 = "Implement the requested change described in the issue."

DELIVERABLE = (
    "Generate a standard **git-style patch file** (unified diff, i.e., .patch file) "
    "that implements the feature and adds/updates the necessary tests."
)


def format_file_list(files: list[str]) -> str:
    """Format a list of file paths as markdown bullet items."""
    return "\n".join(f"* `{f}`" for f in files)


def render_v1(repo_url: str, problem_statement: str) -> str:
    """Render the v1 (detailed) prompt markdown."""
    return (
        f"**Task:**\n"
        f"You are an automated coding agent. {TASK_LINE_V1}\n"
        f"\n"
        f"**Repo Link:**\n"
        f"[{repo_url}]({repo_url})\n"
        f"\n"
        f"**Problem Statement:**\n"
        f"{problem_statement}\n"
        f"\n"
        f"**Deliverable:**\n"
        f"{DELIVERABLE}\n"
    )


def render_v2(repo_url: str, simplified_statement: str) -> str:
    """Render the v2 (vague) prompt markdown."""
    return (
        f"**Task:**\n"
        f"You are an automated coding agent. {TASK_LINE_V2}\n"
        f"\n"
        f"**Repo Link:**\n"
        f"[{repo_url}]({repo_url})\n"
        f"\n"
        f"**Problem Statement:**\n"
        f"{simplified_statement}\n"
        f"\n"
        f"**Deliverable:**\n"
        f"{DELIVERABLE}\n"
    )


def render_v3(repo_url: str, problem_statement: str, files: list[str]) -> str:
    """Render the v3 (detailed + file list) prompt markdown.

    Identical to v1, with an additional relevant-files section before the deliverable.
    """
    file_list = format_file_list(files)
    return (
        f"**Task:**\n"
        f"You are an automated coding agent. {TASK_LINE_V1}\n"
        f"\n"
        f"**Repo Link:**\n"
        f"[{repo_url}]({repo_url})\n"
        f"\n"
        f"**Problem Statement:**\n"
        f"{problem_statement}\n"
        f"\n"
        f"**Relevant files to update (non-exhaustive but recommended focus):**\n"
        f"\n"
        f"{file_list}\n"
        f"\n"
        f"**Deliverable:**\n"
        f"{DELIVERABLE}\n"
    )
