"""Trajectory collection and recording."""

import os
import json
import platform
from datetime import datetime, timezone
from typing import Optional

import requests

from .opencode_client import opencode_request, BASE_URL


def _parse_part(part) -> dict:
    """Normalize a single message part into a detailed structured record."""
    if not isinstance(part, dict):
        return {"type": "unknown", "raw": part}
    ptype = part.get("type", "unknown")

    if ptype == "text":
        return {
            "type": "text",
            "text": part.get("text", ""),
        }

    if ptype == "tool":
        return {
            "type": "tool_call",
            "tool_name": part.get("name", part.get("toolName", "?")),
            "tool_id": part.get("id", part.get("toolCallId", "")),
            "state": part.get("state", "?"),            # pending/running/completed/error
            "input": part.get("input", part.get("args", {})),
            "output": part.get("output", part.get("result", "")),
            "error": part.get("error", None),
            # Timing if the server provides it
            "started_at": part.get("startedAt", None),
            "finished_at": part.get("finishedAt", None),
        }

    if ptype == "reasoning":
        return {
            "type": "reasoning",
            "text": part.get("text", part.get("reasoning", "")),
        }

    if ptype == "step-start":
        return {
            "type": "step_start",
            "name": part.get("name", ""),
        }

    if ptype == "step-finish":
        return {
            "type": "step_finish",
            "name": part.get("name", ""),
        }

    if ptype == "snapshot":
        return {
            "type": "snapshot",
            "data": part.get("data", part.get("snapshot", {})),
        }

    # Catch-all: preserve the raw part for anything unknown
    return {"type": ptype, "raw": part}


def _parse_message(msg) -> dict:
    """Parse a single message into a structured trajectory entry."""
    if not isinstance(msg, dict):
        return {
            "message_id": "",
            "role": "?",
            "created_at": None,
            "model": None,
            "info": {},
            "metadata": {},
            "parts": [],
        }
    info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
    role = msg.get("role") or info.get("role") or "?"
    parts = msg.get("parts")
    if not isinstance(parts, list):
        parts = []
    return {
        "message_id": msg.get("id", ""),
        "role": role,
        "created_at": msg.get("createdAt", msg.get("created_at", None)),
        "model": msg.get("model", None),
        "info": info,                      # token usage, cost, etc.
        "metadata": msg.get("metadata", {}),
        "parts": [_parse_part(p) for p in parts],
    }


def collect_trajectory(session_id: str, directory: str, prompt: str,
                       agent: str, patch: str, health: dict,
                       t_start: float, t_session_created: float,
                       t_task_sent: float, t_task_done: float,
                       t_end: float, error: Optional[str] = None,
                       gt_patch_path: Optional[str] = None,
                       branch: Optional[str] = None,
                       baseline_commit: Optional[str] = None) -> dict:
    """
    Build a comprehensive trajectory record with full metadata.
    Fetches session info, all messages, file status, and diff data.
    """
    # Fetch session details
    session = opencode_request("GET", f"/session/{session_id}",
                               params={"directory": directory})
    if not isinstance(session, dict):
        session = {}

    # Fetch every message in the conversation
    raw_messages = opencode_request("GET", f"/session/{session_id}/message",
                                    params={"directory": directory})
    if isinstance(raw_messages, dict):
        raw_messages = [raw_messages]
    if not isinstance(raw_messages, list):
        raw_messages = []

    # Fetch file-level change status
    try:
        file_status = opencode_request("GET", "/file/status",
                                       params={"directory": directory})
    except requests.HTTPError:
        file_status = None

    # Fetch raw diff data (structured, before we flatten to string)
    try:
        raw_diff = opencode_request("GET", f"/session/{session_id}/diff",
                                    params={"directory": directory})
    except requests.HTTPError:
        raw_diff = None

    # Parse messages into structured trajectory steps
    messages = [_parse_message(m) for m in raw_messages]

    # Compute stats from messages
    tool_calls = []
    reasoning_steps = []
    for m in messages:
        for p in m["parts"]:
            if p["type"] == "tool_call":
                tool_calls.append(p)
            elif p["type"] == "reasoning":
                reasoning_steps.append(p)

    tool_summary = {}
    for tc in tool_calls:
        name = tc["tool_name"]
        tool_summary[name] = tool_summary.get(name, 0) + 1

    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    for m in messages:
        info = m.get("info") if isinstance(m.get("info"), dict) else {}
        total_tokens += info.get("totalTokens", info.get("total_tokens", 0))
        prompt_tokens += info.get("promptTokens", info.get("prompt_tokens", 0))
        completion_tokens += info.get("completionTokens", info.get("completion_tokens", 0))

    return {
        # ── Session metadata ──
        "metadata": {
            "session_id": session_id,
            "directory": directory,
            "directory_name": os.path.basename(directory),
            "agent": agent,
            "server_url": BASE_URL,
            "server_version": health.get("version", "?"),
            "model": session.get("model", None),
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "branch": branch,
            "ground_truth_patch": gt_patch_path,
            "baseline_commit": baseline_commit,
        },

        # ── Input ──
        "input": {
            "prompt": prompt,
            "prompt_length": len(prompt),
        },

        # ── Output ──
        "output": {
            "patch": patch,
            "patch_length": len(patch),
            "patch_lines": len(patch.splitlines()) if patch else 0,
            "has_patch": bool(patch),
            "error": error,
        },

        # ── Timing (seconds) ──
        "timing": {
            "total_duration": round(t_end - t_start, 3),
            "session_creation": round(t_session_created - t_start, 3),
            "task_execution": round(t_task_done - t_task_sent, 3),
            "diff_retrieval": round(t_end - t_task_done, 3),
            "started_at": datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat(),
            "finished_at": datetime.fromtimestamp(t_end, tz=timezone.utc).isoformat(),
        },

        # ── Token usage (aggregated across all messages) ──
        "token_usage": {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },

        # ── Agent behavior stats ──
        "stats": {
            "total_messages": len(messages),
            "user_messages": sum(1 for m in messages if m["role"] == "user"),
            "assistant_messages": sum(1 for m in messages if m["role"] == "assistant"),
            "total_tool_calls": len(tool_calls),
            "tool_call_breakdown": tool_summary,
            "failed_tool_calls": sum(1 for tc in tool_calls if tc["state"] == "error"),
            "reasoning_steps": len(reasoning_steps),
        },

        # ── Full conversation trajectory ──
        "trajectory": messages,

        # ── Raw session & file data from server ──
        "session_raw": session,
        "file_status": file_status,
        "diff_raw": raw_diff,
    }


def save_trajectory(trajectory: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(trajectory, f, ensure_ascii=False, indent=2, default=str)
    size_kb = os.path.getsize(out_path) / 1024
    n_msgs = trajectory.get("stats", {}).get("total_messages", "?")
    n_tools = trajectory.get("stats", {}).get("total_tool_calls", "?")
    print(f"[ok] Trajectory saved to {out_path} "
          f"({size_kb:.1f} KB, {n_msgs} messages, {n_tools} tool calls)")
