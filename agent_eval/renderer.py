"""Orchestrator: ties all modules together to generate prompt files."""

import os
from pathlib import Path

from .fetcher import (
    fetch_patch_from_url,
    fetch_pr_description,
    is_url,
    parse_pr_url,
    parse_repo_url,
)
from .patch_parser import extract_files_from_patch
from .simplifier import rewrite_problem_statement, simplify_problem_statement
from .templates import render_v1, render_v2, render_v3


def resolve_output_dir(repo_url: str, output_dir: str | None) -> Path:
    """Determine the output directory for generated files."""
    if output_dir:
        return Path(output_dir)
    repo_name = parse_repo_url(repo_url)
    # Capitalize each segment (e.g., "triton-ascend" -> "Triton-Ascend")
    project_name = "-".join(seg.capitalize() for seg in repo_name.split("-"))
    return Path("Prompts") / project_name


def resolve_pr_number(pr_url: str) -> str:
    """Extract the PR number from a PR URL."""
    _, _, _, pr_number = parse_pr_url(pr_url)
    return pr_number


def load_problem_statement(problem_statement_arg: str | None, pr_url: str) -> str:
    """Get the problem statement from argument (text or file) or fetch from PR."""
    if problem_statement_arg:
        if os.path.isfile(problem_statement_arg):
            with open(problem_statement_arg, "r", encoding="utf-8") as f:
                return f.read().strip()
        return problem_statement_arg
    print("No --problem-statement provided, fetching from PR...")
    return fetch_pr_description(pr_url)


def load_patch(patch_arg: str) -> str:
    """Load patch content from a file path or URL."""
    if is_url(patch_arg):
        print(f"Downloading patch from {patch_arg}...")
        return fetch_patch_from_url(patch_arg)
    if not os.path.isfile(patch_arg):
        raise FileNotFoundError(f"Patch file not found: {patch_arg}")
    with open(patch_arg, "r", encoding="utf-8") as f:
        return f.read()


def run(
    repo_url: str,
    pr_url: str,
    patch: str,
    problem_statement: str | None = None,
    output_dir: str | None = None,
) -> Path:
    """Main pipeline: generate v1, v2, v3 prompt files.

    Returns the output directory path.
    """
    # 1. Get original problem statement
    original_ps = load_problem_statement(problem_statement, pr_url)
    print(f"Original problem statement loaded ({len(original_ps)} chars).")

    # 2. Load and parse patch
    patch_text = load_patch(patch)
    files = extract_files_from_patch(patch_text)
    print(f"Extracted {len(files)} file(s) from patch.")
    if not files:
        print("Warning: no files extracted from patch. v3 will have an empty file list.")

    # 3. Rewrite problem statement via LLM (original + patch -> detailed v1)
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    print(f"Rewriting problem statement via {provider} API...")
    rewritten = rewrite_problem_statement(original_ps, patch_text)
    print(f"Rewritten problem statement generated ({len(rewritten)} chars).")

    # 4. Generate simplified statement via LLM (rewritten -> vague v2)
    print(f"Generating simplified problem statement (v2) via {provider} API...")
    simplified = simplify_problem_statement(rewritten)
    print("Simplified statement generated.")

    # 5. Render templates
    v1 = render_v1(repo_url, rewritten)
    v2 = render_v2(repo_url, simplified)
    v3 = render_v3(repo_url, rewritten, files)

    # 6. Write output files
    out = resolve_output_dir(repo_url, output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pr_num = resolve_pr_number(pr_url)

    for suffix, content in [("v1", v1), ("v2", v2), ("v3", v3)]:
        path = out / f"pr_{pr_num}_{suffix}.md"
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path}")

    return out
