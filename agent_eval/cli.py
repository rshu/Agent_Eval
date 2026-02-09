"""Command-line interface for agent_eval prompt generator."""

import argparse
import sys

from .renderer import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_eval",
        description="Generate v1/v2/v3 prompt files for Agent_Eval benchmarks.",
    )
    parser.add_argument(
        "--repo-url",
        required=True,
        help="Repository URL (GitHub or Gitee)",
    )
    parser.add_argument(
        "--pr-url",
        required=True,
        help="Pull request URL",
    )
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to a local .patch file or a URL to download one",
    )
    parser.add_argument(
        "--problem-statement",
        default=None,
        help="Problem statement text or path to a text file. If omitted, fetched from PR.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: Prompts/<ProjectName>/)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        out = run(
            repo_url=args.repo_url,
            pr_url=args.pr_url,
            patch=args.patch,
            problem_statement=args.problem_statement,
            output_dir=args.output_dir,
        )
        print(f"\nDone. Output written to: {out}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
