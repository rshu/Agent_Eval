"""
Batch LLM patch generator.

Reads .md prompt files from an input directory, sends each to an
OpenAI-compatible chat completions API, extracts the git-style patch
from the response, and saves it as a .patch file.

LLM configuration is read from .env (RAW_GEN_BASE_URL, RAW_GEN_API_KEY, RAW_GEN_MODEL,
RAW_GEN_TEMPERATURE, RAW_GEN_MAX_TOKENS).  CLI flags override .env values.

Usage:
    # With .env configured (RAW_GEN_BASE_URL, RAW_GEN_API_KEY, RAW_GEN_MODEL, ...):
    python toolkits/llm_patch_generator.py --input-dir prompt_variants/Hutool

    # Custom output directory:
    python toolkits/llm_patch_generator.py \
        --input-dir prompt_variants/Hutool \
        --output-dir generated_patches/raw_glm5

    # Override .env with CLI flags:
    python toolkits/llm_patch_generator.py \
        --input-dir prompt_variants/Hutool \
        --base-url https://api.example.com \
        --api-key sk-xxx \
        --model glm-5 \
        --temperature 0.3 \
        --max-tokens 8192 \
        --max-retries 3
"""

import argparse
import json
import os
import re
import sys
from typing import Any

import requests

# ── .env loader ─────────────────────────────────────────────────────────

def _clean_env_scalar(raw: str) -> str:
    """Normalize scalar env values.

    - Trims whitespace
    - Removes trailing inline comments like `value  # comment`
    - Unwraps single/double quotes
    """
    value = raw.strip()
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from *path* into os.environ (no overwrite)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = _clean_env_scalar(value)
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _clean_env_scalar(raw)
    if value == "":
        return default
    try:
        return float(value)
    except ValueError:
        print(
            f"[warn] Invalid {name}={raw!r}; using default {default}",
            file=sys.stderr,
        )
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _clean_env_scalar(raw)
    if value == "":
        return default
    try:
        return int(value)
    except ValueError:
        print(
            f"[warn] Invalid {name}={raw!r}; using default {default}",
            file=sys.stderr,
        )
        return default


# ── Patch extraction ────────────────────────────────────────────────────

def extract_patch(text: str) -> str | None:
    """Extract a unified diff / git patch from LLM response text.

    Tries (in order):
      1. Fenced code blocks tagged ``diff`` or ``patch``
      2. Bare fenced code blocks whose content looks like a diff
      3. Raw unified diff markers in the text
    Returns the patch string, or None if nothing found.
    """
    # 1. Fenced blocks with diff/patch language tag
    fenced = re.findall(
        r"```(?:diff|patch)\s*\n(.*?)```", text, re.DOTALL,
    )
    if fenced:
        return "\n".join(fenced).strip()

    # 2. Bare fenced blocks containing diff markers
    bare = re.findall(r"```\s*\n(.*?)```", text, re.DOTALL)
    for block in bare:
        if _looks_like_diff(block):
            return block.strip()

    # 3. Raw diff markers outside code fences
    lines = text.splitlines(keepends=True)
    diff_lines: list[str] = []
    in_diff = False
    for line in lines:
        if line.startswith("diff --git ") or (
            line.startswith("--- ") and not in_diff
        ):
            in_diff = True
        if in_diff:
            diff_lines.append(line)

    if diff_lines and _looks_like_diff("".join(diff_lines)):
        return "".join(diff_lines).strip()

    return None


def _looks_like_diff(text: str) -> bool:
    """Heuristic: does *text* contain unified diff markers?"""
    return bool(
        re.search(r"^diff --git ", text, re.MULTILINE)
        or (
            re.search(r"^--- ", text, re.MULTILINE)
            and re.search(r"^\+\+\+ ", text, re.MULTILINE)
        )
    )


def _normalize_message_content(content: Any) -> str:
    """Normalize chat-completions message content into plain text.

    Supports:
      - str
      - list[dict] text blocks (e.g. [{"type": "text", "text": "..."}])
      - list[str]
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue

            if not isinstance(item, dict):
                continue

            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue

            nested = item.get("content")
            if isinstance(nested, str):
                parts.append(nested)

        if parts:
            return "".join(parts)

    raise ValueError(
        "Unsupported choices[0].message.content format "
        f"({type(content).__name__}); expected text string or list of text blocks",
    )


# ── LLM caller ──────────────────────────────────────────────────────────

def call_llm(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    provider: str = "openai",
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int = 300,
) -> str:
    """Send *prompt* to an LLM API and return the assistant text.

    Supports two providers:
      - ``openai``    — POST ``{base_url}/chat/completions``
      - ``anthropic`` — POST ``{base_url}/messages``

    Raises on HTTP or response-format errors.
    """
    if provider == "anthropic":
        return _call_anthropic(base_url, api_key, model, prompt,
                               temperature, max_tokens, timeout)
    return _call_openai(base_url, api_key, model, prompt,
                        temperature, max_tokens, timeout)


def _call_openai(
    base_url: str, api_key: str, model: str, prompt: str,
    temperature: float | None, max_tokens: int | None, timeout: int,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    resp = requests.post(url, json=body, headers=headers, timeout=timeout,
                         stream=True)
    resp.raise_for_status()

    # Collect streamed SSE chunks
    collected: list[str] = []
    finish_reason = None
    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data: "):
            continue
        payload = raw_line[len("data: "):]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        text = delta.get("content")
        if text:
            collected.append(text)
        fr = chunk.get("choices", [{}])[0].get("finish_reason")
        if fr:
            finish_reason = fr

    if finish_reason and finish_reason != "stop":
        print(f"\n[warn] finish_reason={finish_reason} (response may be truncated)")
    else:
        print(f"[finish_reason={finish_reason}]", end=" ")

    content = "".join(collected)
    if not content:
        raise ValueError("Empty response from streaming chat/completions API")
    return content


def _call_anthropic(
    base_url: str, api_key: str, model: str, prompt: str,
    temperature: float | None, max_tokens: int | None, timeout: int,
) -> str:
    url = f"{base_url.rstrip('/')}/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens or 8192,
    }
    if temperature is not None:
        body["temperature"] = temperature

    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()

    data = resp.json()
    try:
        blocks = data["content"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "Invalid response shape from Anthropic messages API "
            "(missing content)",
        ) from exc
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    if not texts:
        raise ValueError("Anthropic response contained no text blocks")
    return "\n".join(texts)


# ── Prompt rewriting ─────────────────────────────────────────────────────

_AGENT_PHRASE = "You are an automated coding agent."
_PATCH_PHRASE = (
    "You are a code generation assistant. "
    "Output the complete git-style unified diff patch directly in a "
    "single fenced ```diff code block. "
    "Do not attempt to run commands, explore files, or ask questions."
)


def _rewrite_prompt(prompt: str) -> str:
    """Replace the agentic role instruction with a direct patch-generation instruction."""
    if _AGENT_PHRASE in prompt:
        return prompt.replace(_AGENT_PHRASE, _PATCH_PHRASE, 1)
    return prompt


# ── CLI & main loop ─────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch-generate patches by sending .md prompts to an LLM API.",
    )
    p.add_argument(
        "--input-dir", required=True,
        help="Directory containing .md prompt files",
    )
    p.add_argument(
        "--output-dir", default="generated_patches/raw_glm5",
        help="Directory to save .patch files (default: generated_patches/raw_glm5)",
    )
    p.add_argument(
        "--provider", default=os.getenv("RAW_GEN_PROVIDER", "openai"),
        choices=["openai", "anthropic"],
        help="API provider format (default: RAW_GEN_PROVIDER from .env, or openai)",
    )
    p.add_argument(
        "--base-url", default=os.getenv("RAW_GEN_BASE_URL", ""),
        help="LLM API base URL (default: RAW_GEN_BASE_URL from .env)",
    )
    p.add_argument(
        "--api-key", default=os.getenv("RAW_GEN_API_KEY", ""),
        help="API key (default: RAW_GEN_API_KEY from .env)",
    )
    p.add_argument(
        "--model", default=os.getenv("RAW_GEN_MODEL", ""),
        help="Model name (default: RAW_GEN_MODEL from .env)",
    )
    p.add_argument(
        "--temperature", type=float,
        default=_env_float("RAW_GEN_TEMPERATURE", 0.3),
        help="Sampling temperature (default: RAW_GEN_TEMPERATURE from .env, or 0.3)",
    )
    p.add_argument(
        "--max-tokens", type=int,
        default=_env_int("RAW_GEN_MAX_TOKENS", 8192),
        help="Max output tokens (default: RAW_GEN_MAX_TOKENS from .env, or 8192)",
    )
    p.add_argument(
        "--max-retries", type=int, default=3,
        help="Max retries per prompt on extraction failure (default: 3)",
    )
    p.add_argument(
        "--debug", action="store_true",
        help="Print verbose details: request params, full LLM response, extraction result",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # ── Validate input dir ──────────────────────────────────────────
    if not os.path.isdir(args.input_dir):
        print(f"[error] Input directory not found: {args.input_dir}")
        sys.exit(1)

    md_files = sorted(
        f for f in os.listdir(args.input_dir) if f.endswith(".md")
    )
    if not md_files:
        print(f"[warn] No .md files found in {args.input_dir}")
        sys.exit(0)

    for name, attr in [("base-url", "base_url"), ("api-key", "api_key"), ("model", "model")]:
        if not getattr(args, attr):
            print(f"[error] --{name} is required (or set RAW_GEN_{attr.upper()} in .env)")
            sys.exit(1)

    debug = args.debug

    # ── Ensure output dir exists ────────────────────────────────────
    os.makedirs(args.output_dir, exist_ok=True)

    if debug:
        print(f"[debug] provider:    {args.provider}")
        print(f"[debug] base_url:    {args.base_url}")
        print(f"[debug] model:       {args.model}")
        print(f"[debug] temperature: {args.temperature}")
        print(f"[debug] max_tokens:  {args.max_tokens}")
        print(f"[debug] max_retries: {args.max_retries}")
        print(f"[debug] input_dir:   {args.input_dir}")
        print(f"[debug] output_dir:  {args.output_dir}")
        print(f"[debug] files:       {md_files}")
        print()

    # ── Process each prompt ─────────────────────────────────────────
    total = len(md_files)
    succeeded = 0
    skipped = 0
    failed = 0

    for idx, fname in enumerate(md_files, 1):
        stem = os.path.splitext(fname)[0]
        src = os.path.join(args.input_dir, fname)
        dst = os.path.join(args.output_dir, f"{stem}.patch")

        # Resume: skip if .patch already exists and is non-empty
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            print(f"[{idx}/{total}] {fname} — skipped (patch exists)")
            skipped += 1
            succeeded += 1
            continue

        with open(src, encoding="utf-8") as pf:
            prompt = pf.read()

        prompt = _rewrite_prompt(prompt)

        if debug:
            print(f"[debug] prompt ({len(prompt)} chars):\n{prompt}")
            print()

        patch = None
        attempts = 1 + args.max_retries  # initial + retries

        for attempt in range(1, attempts + 1):
            label = f"[{idx}/{total}] {fname}"
            if attempt > 1:
                label += f" (retry {attempt - 1}/{args.max_retries})"
            print(f"{label} — calling LLM ...", end=" ", flush=True)

            try:
                content = call_llm(
                    args.base_url, args.api_key, args.model, prompt,
                    provider=args.provider,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                )
            except (requests.RequestException, ValueError) as exc:
                print(f"API error: {exc}")
                if debug:
                    import traceback
                    traceback.print_exc()
                continue

            if debug:
                print()
                print(f"[debug] response ({len(content)} chars):\n{content}")
                print()

            patch = extract_patch(content)
            if patch:
                print("ok")
                if debug:
                    print(f"[debug] extracted patch: {len(patch)} chars, {patch.count(chr(10))+1} lines")
                break
            print("no patch found in response")
            if debug:
                # Save the raw response for inspection
                raw_path = os.path.join(
                    args.output_dir,
                    f"{stem}_attempt{attempt}_raw.txt",
                )
                with open(raw_path, "w", encoding="utf-8") as rf:
                    rf.write(content)
                print(f"[debug] raw response saved to {raw_path}")

        if patch:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(patch + "\n")
            print(f"  -> saved {dst}")
            succeeded += 1
        else:
            print(f"  !! FAILED after {attempts} attempts")
            failed += 1

    # ── Summary ─────────────────────────────────────────────────────
    summary = f"\nDone: {succeeded}/{total} succeeded, {failed}/{total} failed"
    if skipped:
        summary += f" ({skipped} skipped/resumed)"
    print(summary)


if __name__ == "__main__":
    main()
