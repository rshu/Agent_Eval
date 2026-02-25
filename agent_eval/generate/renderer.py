"""Orchestrator: ties all modules together to generate prompt files."""

from pathlib import Path

from .fetcher import (
    fetch_patch_from_url,
    fetch_pr_description,
    is_url,
    parse_pr_url,
    parse_repo_url,
    validate_repo_pr_match,
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
    return Path("prompt_variants") / project_name


def resolve_pr_number(pr_url: str) -> str:
    """Extract the PR number from a PR URL."""
    _, _, _, pr_number = parse_pr_url(pr_url)
    return pr_number


def load_patch(patch_arg: str) -> str:
    """Load patch content from a file path or URL."""
    if is_url(patch_arg):
        print(f"Downloading patch from {patch_arg}...")
        return fetch_patch_from_url(patch_arg)
    p = Path(patch_arg)
    if not p.is_file():
        raise FileNotFoundError(f"Patch file not found: {patch_arg}")
    return p.read_text(encoding="utf-8")


def run(
    repo_url: str,
    pr_url: str,
    patch: str,
    output_dir: str | None = None,
) -> Path:
    """Main pipeline: generate v1, v2, v3 prompt files.

    Returns the output directory path.
    """
    # 0. Validate & cross-check URLs, then load patch — fail fast before LLM calls
    validate_repo_pr_match(repo_url, pr_url)
    patch_text = load_patch(patch)

    # 1. Fetch problem statement from the PR
    print("Fetching problem statement from PR...")
    original_ps = fetch_pr_description(pr_url)
    print(f"Original problem statement loaded ({len(original_ps)} chars).")

    # 2. Parse patch for file list
    files = extract_files_from_patch(patch_text)
    print(f"Extracted {len(files)} file(s) from patch.")
    if not files:
        print("Warning: no files extracted from patch. v3 will have an empty file list.")

    # 3. Rewrite problem statement via LLM (original + patch -> detailed v1)
    print("Rewriting problem statement via LLM...")
    rewritten = rewrite_problem_statement(original_ps, patch_text)
    print(f"Rewritten problem statement generated ({len(rewritten)} chars).")

    # 4. Generate simplified statement via LLM (rewritten -> vague v2)
    print("Generating simplified problem statement (v2) via LLM...")
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
