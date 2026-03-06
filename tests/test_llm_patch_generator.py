"""Tests for toolkits/llm_patch_generator.py."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "toolkits" / "llm_patch_generator.py"
SPEC = importlib.util.spec_from_file_location("llm_patch_generator", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _mock_stream_response(chunks: list[dict]):
    """Build a mock requests.Response that streams SSE lines."""
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
    lines.append("data: [DONE]")

    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.iter_lines.return_value = iter(lines)
    return resp


def _make_chunks(text: str, finish_reason: str = "stop") -> list[dict]:
    """Create SSE chunks that reconstruct *text* as a streamed response."""
    chunks = [
        {"choices": [{"delta": {"content": text}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": finish_reason}]},
    ]
    return chunks


def test_parse_args_uses_defaults_for_invalid_numeric_env(monkeypatch):
    monkeypatch.setenv("RAW_GEN_TEMPERATURE", "bad-value")
    monkeypatch.setenv("RAW_GEN_MAX_TOKENS", "")

    args = MODULE.parse_args(["--input-dir", "dummy"])

    assert args.temperature == 0.3
    assert args.max_tokens == 8192


def test_parse_args_supports_inline_comments_in_numeric_env(monkeypatch):
    monkeypatch.setenv("RAW_GEN_TEMPERATURE", "0.7   # tuned")
    monkeypatch.setenv("RAW_GEN_MAX_TOKENS", "4096  # token cap")

    args = MODULE.parse_args(["--input-dir", "dummy"])

    assert args.temperature == 0.7
    assert args.max_tokens == 4096


def test_call_llm_openai_streaming():
    content = "```diff\n--- a/a\n+++ b/a\n```"
    chunks = _make_chunks(content)
    mock_resp = _mock_stream_response(chunks)

    with patch.object(MODULE.requests, "post", return_value=mock_resp) as post:
        text = MODULE.call_llm(
            base_url="https://example.com/v1",
            api_key="k",
            model="m",
            prompt="p",
        )

    assert "```diff" in text
    called_url = post.call_args.args[0]
    assert called_url == "https://example.com/v1/chat/completions"
    # Verify streaming was requested
    called_body = post.call_args.kwargs.get("json", {})
    assert called_body.get("stream") is True


def test_call_llm_openai_empty_stream_raises():
    """An empty stream (no content chunks) should raise ValueError."""
    mock_resp = _mock_stream_response([])

    with patch.object(MODULE.requests, "post", return_value=mock_resp):
        with pytest.raises(ValueError, match="Empty response"):
            MODULE.call_llm(
                base_url="https://example.com",
                api_key="k",
                model="m",
                prompt="p",
            )


def test_call_llm_anthropic():
    payload = {
        "content": [
            {"type": "text", "text": "patch content"},
        ],
    }
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload

    with patch.object(MODULE.requests, "post", return_value=resp) as post:
        text = MODULE.call_llm(
            base_url="https://api.anthropic.com",
            api_key="k",
            model="m",
            prompt="p",
            provider="anthropic",
        )

    assert text == "patch content"
    called_url = post.call_args.args[0]
    assert called_url == "https://api.anthropic.com/messages"


def test_extract_patch_fenced_diff():
    text = "Here is the patch:\n```diff\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n```\n"
    patch = MODULE.extract_patch(text)
    assert patch is not None
    assert "--- a/f.py" in patch


def test_extract_patch_raw_diff():
    text = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n"
    patch = MODULE.extract_patch(text)
    assert patch is not None
    assert "diff --git" in patch


def test_extract_patch_none_on_no_diff():
    assert MODULE.extract_patch("No diff content here.") is None


def test_rewrite_prompt_replaces_agent_phrase():
    prompt = "**Task:**\nYou are an automated coding agent. Do something."
    result = MODULE._rewrite_prompt(prompt)
    assert "automated coding agent" not in result
    assert "code generation assistant" in result
    assert "Do something." in result


def test_rewrite_prompt_preserves_non_matching():
    prompt = "Generate a patch for this issue."
    assert MODULE._rewrite_prompt(prompt) == prompt
