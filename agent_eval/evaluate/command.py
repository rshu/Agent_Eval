"""Evaluate mode: compare agent patches against ground truth using an LLM judge."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent_eval.generate.fetcher import is_url, fetch_patch_from_url
from .evaluator import PatchEvaluator


def _read_file(path: str) -> str:
    """Read a local file or fetch content from a URL."""
    if is_url(path):
        print(f"[..] Downloading {path}...")
        try:
            content = fetch_patch_from_url(path)
        except Exception as e:
            print(f"[error] Failed to download: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[ok] Downloaded ({len(content)} bytes)")
        return content
    p = Path(path)
    if not p.is_file():
        print(f"[error] File not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"[error] File is not valid UTF-8: {path}", file=sys.stderr)
        sys.exit(1)


def _resolve_text_or_file(value: str) -> str:
    """If *value* looks like a path to a .md/.txt file, read it; else return as-is.

    Heuristic: a value that ends with ``.md``/``.txt`` is treated as a file
    path when it **exists**, or when it *looks* path-like (contains a path
    separator or has no whitespace — e.g. ``issue.md``, ``dir/issue.md``).
    Multi-word text with spaces (e.g. ``"Need to update foo.md"``) is
    returned as literal issue text.
    """
    p = Path(value)
    if p.suffix.lower() in (".md", ".txt"):
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                print(f"[error] File is not valid UTF-8: {value}", file=sys.stderr)
                sys.exit(1)
        # Looks like a file path (separator present, or single token with no
        # whitespace) — error on missing.  Text with spaces is treated as
        # literal issue text even if it happens to end with .md/.txt.
        _has_sep = os.sep in value or "/" in value
        _has_whitespace = any(c.isspace() for c in value)
        if _has_sep or not _has_whitespace:
            print(f"[error] Issue file not found: {value}", file=sys.stderr)
            sys.exit(1)
        # Warn when treating a .md/.txt-suffixed value with spaces as
        # literal text — could be a typo in a spaced filename.
        print(
            f"[warn] Treating --issue-statement as literal text "
            f"(ends with {p.suffix}, but contains spaces and no path "
            f"separator): {value!r}",
            file=sys.stderr,
        )
    return value


def handler(args):
    # -- validate required args --
    missing = []
    if not getattr(args, "agent_patch", None):
        missing.append("--agent-patch")
    if not getattr(args, "gt_patch", None):
        missing.append("--gt-patch")
    if not getattr(args, "issue_statement", None):
        missing.append("--issue-statement")
    if missing:
        print(
            f"[error] Evaluate mode requires: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # -- read inputs --
    agent_patch = _read_file(args.agent_patch)
    gt_patch = _read_file(args.gt_patch)
    issue_statement = _resolve_text_or_file(args.issue_statement)

    # -- env / credentials --
    load_dotenv()
    api_key = os.environ.get("EVAL_API_KEY", "")
    base_url = os.environ.get("EVAL_BASE_URL") or None
    provider = os.environ.get("EVAL_PROVIDER") or None
    model = (
        getattr(args, "eval_model", None)
        or os.environ.get("EVAL_MODEL")
        or "gpt-5.2"
    )
    import math
    try:
        temperature = float(os.environ.get("EVAL_TEMPERATURE", "0.3"))
    except (ValueError, TypeError):
        print("[error] EVAL_TEMPERATURE must be a valid number", file=sys.stderr)
        sys.exit(1)
    if not math.isfinite(temperature) or temperature < 0:
        print("[error] EVAL_TEMPERATURE must be a finite number >= 0", file=sys.stderr)
        sys.exit(1)
    try:
        max_tokens = int(os.environ.get("EVAL_MAX_TOKENS", "20480"))
    except (ValueError, TypeError):
        print("[error] EVAL_MAX_TOKENS must be a valid integer", file=sys.stderr)
        sys.exit(1)
    if max_tokens < 1:
        print("[error] EVAL_MAX_TOKENS must be a positive integer", file=sys.stderr)
        sys.exit(1)

    if not api_key:
        print("[error] EVAL_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    # -- evaluate --
    evaluator = PatchEvaluator()
    result_json, error = evaluator.evaluate(
        api_key=api_key,
        issue_statement=issue_statement,
        model_name=model,
        base_url=base_url,
        provider=provider,
        agent_patch=agent_patch,
        gt_patch=gt_patch,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    if error:
        print(f"[error] Evaluation failed: {error}", file=sys.stderr)
        sys.exit(1)

    # -- print summary --
    try:
        parsed = PatchEvaluator._strict_loads(result_json)
        if isinstance(parsed, dict) and PatchEvaluator._is_evaluation_result(parsed):
            score = parsed.get("overall_score", "?")
            verdict = parsed.get("verdict", "?")
            print(f"[ok] Verdict: {verdict} | Overall score: {score}")
        else:
            print(
                "[warn] LLM response is not a valid evaluation result",
                file=sys.stderr,
            )
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # -- output --
    output_path = getattr(args, "eval_output", None)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(result_json, encoding="utf-8")
        print(f"[ok] Evaluation result written to {output_path}")
    else:
        print(result_json)
