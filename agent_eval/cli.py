"""Command-line interface for agent-eval: generate, run, and evaluate modes."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-eval",
        description="Generate, run, and evaluate coding agent benchmarks.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["generate", "run", "evaluate"],
        help="Operation mode",
    )

    # ── Generate mode arguments ──
    gen = parser.add_argument_group("generate mode")
    gen.add_argument("--repo-url", help="Repository URL (GitHub or Gitee)")
    gen.add_argument("--pr-url", help="Pull request URL")
    gen.add_argument("--patch", help="Path to a local .patch file or a URL to download one")
    gen.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: prompt_variants/<ProjectName>/)",
    )

    # ── Run mode arguments ──
    run = parser.add_argument_group("run mode")
    run.add_argument("-d", "--directory", help="Target project directory")
    run.add_argument("-f", "--prompt-file", help="Read the prompt from a .md file")
    run.add_argument(
        "--branch",
        help="Git branch to checkout before starting "
             "(e.g. pr_1263 after fetching the PR)",
    )

    # ── Shared: run + evaluate ──
    shared = parser.add_argument_group("run / evaluate shared")
    shared.add_argument(
        "--gt-patch",
        help="Ground truth patch — local file path or URL "
             "(run: reverse-applied for starting point; "
             "evaluate: compared against agent patch)",
    )

    # ── Evaluate mode arguments ──
    eva = parser.add_argument_group("evaluate mode")
    eva.add_argument("--agent-patch", help="Path to agent-generated patch file")
    eva.add_argument(
        "--issue-statement",
        help="Issue text or path to a .md/.txt file (evaluate mode)",
    )
    eva.add_argument(
        "--eval-model",
        default=None,
        help="LLM judge model name (default: EVAL_MODEL env or gpt-5.2)",
    )
    eva.add_argument("--eval-output", help="Path to write evaluation results")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mode == "generate":
        from .generate.command import handler
        handler(args)
    elif args.mode == "run":
        from .run.command import handler
        handler(args)
    elif args.mode == "evaluate":
        from .evaluate.command import handler
        handler(args)
