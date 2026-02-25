"""LLM-based problem statement rewriting and simplification."""

import os
from pathlib import Path

from dotenv import load_dotenv

_dotenv_loaded = False

# Maximum number of characters from the patch to include in the LLM prompt.
# Large diffs can exceed model context windows; we keep only the head which
# is usually enough for the LLM to understand scope and intent.
MAX_PATCH_CHARS = 32_000

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

REWRITE_SYSTEM_PROMPT = """\
You are helping create a clear problem statement for a coding benchmark.

You will receive:
1. An ORIGINAL DESCRIPTION from a pull request (may be vague, incomplete, or empty).
2. A GROUND TRUTH PATCH showing the actual code changes that were made.

Your job is to write a problem statement that explains the ISSUE — the bug, missing feature, \
design flaw, or user-facing problem — NOT a description of what the patch does. \
The patch is provided only so you can understand the context and scope of the issue. \
Think of it as writing the bug report or feature request that would have led to this patch.

Guidelines:
- Describe the problem, gap, or need from the user's or developer's perspective.
- Explain why the current state is wrong or insufficient (e.g., "X does not support Y", \
"calling Z raises an error when…", "there is no way to…").
- You may mention relevant class names, function names, or modules to scope the issue, \
but do NOT describe the specific changes, implementation steps, or code structure from the patch.
- Do NOT include code snippets, diffs, file paths, or line-level details.
- Do NOT phrase it as instructions ("Add …", "Implement …", "Refactor …"). \
Instead describe the issue ("X is missing", "Y fails when…", "Z duplicates logic from…").
- If the patch includes tests, you may note what behavior should be verified, \
but do NOT describe the test code itself.
- Keep the statement concise but thorough — typically 3-8 sentences."""

SIMPLIFY_SYSTEM_PROMPT = """\
You are helping create a simplified, vaguer version of a coding task description for a benchmark.

Given the following detailed problem statement from a pull request, generate a SHORT (1-2 sentences) simplified version that:
1. Always starts with "This issue" (e.g., "This issue requests...", "This issue asks for...", "This issue reports...")
2. Captures the core intent but removes specific implementation details
3. Omits specific class names, function names, or technical specifics where possible (keep only the most essential ones)
4. Uses simpler, more generic language
5. Does NOT include any file paths or code examples
6. Maintains enough information that a skilled developer could still understand the general direction"""

# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------

REWRITE_EXAMPLES = [
    {
        "original": "Add UUID v7 support",
        "patch_summary": (
            "Adds a UUID v7 generator method to IdUtil, implements the v7 algorithm "
            "in the UUID class with timestamp-based ordering, and adds unit tests for "
            "version/variant validation, uniqueness, and monotonicity."
        ),
        "rewritten": (
            "The project currently supports UUID v1 and v4 generation through the IdUtil "
            "utility class, but lacks support for UUID v7. UUID v7 is a newer standard "
            "that produces time-ordered unique identifiers, which is useful for database "
            "keys and distributed systems. There is currently no way to generate UUID v7 "
            "values using the existing ID utilities. The UUID v7 implementation should "
            "follow the same patterns as the existing UUID versions and be accessible "
            "through IdUtil. Test coverage for UUID v7 properties such as version/variant "
            "correctness, uniqueness, and monotonic ordering is also needed."
        ),
    },
    {
        "original": "",
        "patch_summary": (
            "Refactors PredicateUtil to delegate to StreamUtil instead of duplicating "
            "stream logic, adds @SuppressWarnings for generics, and expands unit tests."
        ),
        "rewritten": (
            "PredicateUtil currently contains stream-handling logic that duplicates "
            "functionality already available in StreamUtil. This duplication makes the "
            "codebase harder to maintain and increases the risk of inconsistencies. "
            "Additionally, there are unchecked generic-parameter warnings that should "
            "be addressed, and the existing unit test coverage for the affected "
            "functionality is incomplete."
        ),
    },
]

SIMPLIFY_EXAMPLES = [
    {
        "original": (
            "Design and implement a UUID v7 generator, referencing the style/approach "
            "used by existing versions (e.g., v1, v4). Integrate the UUID v7 generator "
            "into the IdUtil utility class so it can be used similarly to other ID/UUID "
            "helpers. Additionally, extend the unit tests in IdUtilTest to cover UUID v7, "
            "including: Validating UUID v7 properties (e.g., correct version and variant). "
            "Testing UUID v7 uniqueness, monotonicity (ordering behavior), and performance."
        ),
        "simplified": (
            "This issue requests adding support for UUID v7 generation to the project "
            "and including some basic tests to verify it works correctly."
        ),
    },
    {
        "original": (
            "Update the codebase so that PredicateUtil is implemented using StreamUtil "
            "(i.e., refactor/rewire logic to reuse Stream utilities rather than "
            "duplicating stream-handling behavior). Additionally: Complete/expand unit "
            "tests for the affected functionality. Suppress generic-parameter warnings "
            "where appropriate."
        ),
        "simplified": (
            "This issue asks for refactoring `PredicateUtil` to reuse `StreamUtil`, "
            "adding missing tests, and cleaning up generics warnings."
        ),
    },
    {
        "original": (
            "When compiling a function with torch.compile(..., dynamic=True), the "
            "compiled function recompiles every time for inputs of different sequence "
            "lengths, even though dynamic shapes are expected to avoid recompilation. "
            "Fix the underlying cause in the compilation/guarding pathway so that this "
            "dynamic-shape pattern does not recompile unnecessarily."
        ),
        "simplified": (
            "This issue reports a bug where `torch.compile(dynamic=True)` recompiles "
            "too often when input sizes change dynamically. It requests a fix and a "
            "regression test."
        ),
    },
]

# ---------------------------------------------------------------------------
# User message builders
# ---------------------------------------------------------------------------


def _truncate_patch(patch_text: str, limit: int = MAX_PATCH_CHARS) -> str:
    """Return *patch_text* truncated to *limit* characters with a notice."""
    if len(patch_text) <= limit:
        return patch_text
    return patch_text[:limit] + "\n\n[… patch truncated for length …]\n"


def _build_rewrite_message(original: str, patch_text: str) -> str:
    """Build the few-shot user message for rewriting."""
    examples = ""
    for i, ex in enumerate(REWRITE_EXAMPLES, 1):
        examples += (
            f"Example {i}:\n"
            f"ORIGINAL DESCRIPTION: {ex['original'] or '(empty)'}\n"
            f"PATCH SUMMARY: {ex['patch_summary']}\n"
            f"REWRITTEN: {ex['rewritten']}\n\n"
        )

    original_section = original.strip() if original.strip() else "(empty)"
    safe_patch = _truncate_patch(patch_text)

    return (
        f"{examples}"
        f"Now rewrite the following:\n"
        f"---\n"
        f"ORIGINAL DESCRIPTION:\n{original_section}\n\n"
        f"GROUND TRUTH PATCH:\n{safe_patch}\n"
        f"---\n\n"
        f"Output ONLY the rewritten problem statement, nothing else."
    )


def _build_simplify_message(problem_statement: str) -> str:
    """Build the few-shot user message for simplification."""
    examples = ""
    for i, ex in enumerate(SIMPLIFY_EXAMPLES, 1):
        examples += (
            f"Example {i}:\n"
            f"ORIGINAL: {ex['original']}\n"
            f"SIMPLIFIED: {ex['simplified']}\n\n"
        )

    return (
        f"{examples}"
        f"Now simplify the following problem statement:\n"
        f"---\n"
        f"{problem_statement}\n"
        f"---\n\n"
        f"Output ONLY the simplified 1-2 sentence version, nothing else."
    )


# ---------------------------------------------------------------------------
# LLM client helpers
# ---------------------------------------------------------------------------


def _ensure_dotenv() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
        _dotenv_loaded = True


def _get_llm_config() -> dict:
    _ensure_dotenv()
    return {
        "provider": os.getenv("GEN_PROVIDER", "openai").lower(),
        "model": os.getenv("GEN_MODEL", "gpt-5.2"),
        "base_url": os.getenv("GEN_BASE_URL") or None,
        "api_key": os.getenv("GEN_API_KEY") or None,
        "temperature": float(os.getenv("GEN_TEMPERATURE", "0.3")),
        "max_tokens": int(os.getenv("GEN_MAX_TOKENS", "4096")),
    }


def _call_llm(system_prompt: str, user_message: str) -> str:
    """Call the configured LLM provider and return the response text."""
    cfg = _get_llm_config()

    provider = cfg["provider"]
    if provider not in ("openai", "anthropic"):
        raise ValueError(
            f"Invalid GEN_PROVIDER={provider!r}. Must be 'openai' or 'anthropic'."
        )

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(
            **({"base_url": cfg["base_url"]} if cfg["base_url"] else {}),
            **({"api_key": cfg["api_key"]} if cfg["api_key"] else {}),
            timeout=60.0,
        )
        response = client.chat.completions.create(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("LLM returned an empty response (OpenAI provider)")
        return response.choices[0].message.content.strip()
    else:
        import anthropic

        client = anthropic.Anthropic(
            **({"base_url": cfg["base_url"]} if cfg["base_url"] else {}),
            **({"api_key": cfg["api_key"]} if cfg["api_key"] else {}),
            timeout=60.0,
        )
        message = client.messages.create(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        if not message.content or not hasattr(message.content[0], "text"):
            raise RuntimeError("LLM returned an empty response (Anthropic provider)")
        return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rewrite_problem_statement(original: str, patch_text: str) -> str:
    """Rewrite a problem statement using the original description and ground truth patch."""
    user_message = _build_rewrite_message(original, patch_text)
    return _call_llm(REWRITE_SYSTEM_PROMPT, user_message)


def simplify_problem_statement(problem_statement: str) -> str:
    """Generate a simplified (v2) version of a problem statement."""
    user_message = _build_simplify_message(problem_statement)
    return _call_llm(SIMPLIFY_SYSTEM_PROMPT, user_message)
