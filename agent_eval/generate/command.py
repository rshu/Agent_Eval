"""Generate mode: produce v1/v2/v3 prompt files from a PR."""

import sys

from .renderer import run


def handler(args):
    """Entry point for generate mode."""
    for name in ("repo_url", "pr_url", "patch"):
        if not getattr(args, name, None):
            sys.exit(f"Error: --{name.replace('_', '-')} is required for generate mode")

    try:
        out = run(
            repo_url=args.repo_url,
            pr_url=args.pr_url,
            patch=args.patch,
            output_dir=args.output_dir,
        )
        print(f"\nDone. Output written to: {out}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
