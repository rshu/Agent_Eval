"""Data loading, parsing, and aggregate metrics."""

import glob
import json
import os
import statistics
from pathlib import Path
from typing import Any


def safe_get(d: Any, *keys, default=None):
    """Safe nested dict access."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return d


def load_trajectory(file_path: str) -> dict:
    """Load trajectory JSON with error handling."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return {"_error": str(exc)}


def _percentile(values: list[float], q: float) -> float:
    """Compute percentile using nearest-rank (q in [0, 1])."""
    if not values:
        return 0.0
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)
    vals = sorted(values)
    idx = max(0, min(len(vals) - 1, int((len(vals) - 1) * q)))
    return vals[idx]


def parse_steps(raw: dict) -> list[dict]:
    """Normalize each message in trajectory[] into a step dict."""
    trajectory = raw.get("trajectory", [])
    if not isinstance(trajectory, list):
        return []

    steps = []
    for idx, msg in enumerate(trajectory):
        if not isinstance(msg, dict):
            continue

        info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
        role = msg.get("role") or safe_get(info, "role", default="?")
        message_id = msg.get("message_id", "")

        # Per-step tokens
        tokens_info = safe_get(info, "tokens", default={})
        if not isinstance(tokens_info, dict):
            tokens_info = {}
        tokens = {
            "total": tokens_info.get("total", 0) or 0,
            "input": tokens_info.get("input", 0) or 0,
            "output": tokens_info.get("output", 0) or 0,
            "reasoning": tokens_info.get("reasoning", 0) or 0,
            "cache_read": safe_get(tokens_info, "cache", "read", default=0) or 0,
            "cache_write": safe_get(tokens_info, "cache", "write", default=0) or 0,
        }

        # Per-step duration
        t_created = safe_get(info, "time", "created", default=None)
        t_completed = safe_get(info, "time", "completed", default=None)
        duration = None
        if isinstance(t_created, (int, float)) and isinstance(t_completed, (int, float)):
            duration = round((t_completed - t_created) / 1000.0, 2)

        # Parse parts
        parts_raw = msg.get("parts", [])
        if not isinstance(parts_raw, list):
            parts_raw = []

        parts = []
        tool_calls = []
        errors = 0
        has_reasoning = False
        text_preview = ""

        for p in parts_raw:
            if not isinstance(p, dict):
                continue
            ptype = p.get("type", "unknown")

            if ptype == "text":
                txt = p.get("text", "")
                parts.append({"type": "text", "text": txt})
                if not text_preview:
                    text_preview = txt

            elif ptype == "reasoning":
                parts.append({"type": "reasoning", "text": p.get("text", "")})
                has_reasoning = True
                if not text_preview:
                    text_preview = p.get("text", "")

            elif ptype in ("tool_call", "tool"):
                state = p.get("state", {})
                if not isinstance(state, dict):
                    state = {"status": str(state)}
                tool_name = p.get("tool_name", p.get("name", "?"))
                status = state.get("status", "?")
                tc = {
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_id": p.get("tool_id", p.get("id", "")),
                    "status": status,
                    "title": state.get("title", ""),
                    "input": state.get("input", p.get("input", {})),
                    "output": state.get("output", p.get("output", "")),
                    "error": p.get("error") or state.get("error") or None,
                    "time_start": safe_get(state, "time", "start", default=None),
                    "time_end": safe_get(state, "time", "end", default=None),
                    "metadata": state.get("metadata", {}),
                }
                parts.append(tc)
                tool_calls.append(tc)
                if status == "error":
                    errors += 1
                if not text_preview:
                    text_preview = f"[Tool: {tool_name}] {tc['title']}"

            elif ptype in ("step_start", "step-start"):
                parts.append({"type": "step_start", "name": p.get("name", "")})

            elif ptype in ("step_finish", "step-finish"):
                parts.append({"type": "step_finish", "name": p.get("name", "")})

            elif ptype == "snapshot":
                parts.append({"type": "snapshot", "data": p.get("data", p.get("snapshot", {}))})

            elif ptype == "patch":
                patch_raw = p.get("raw", p)
                if not isinstance(patch_raw, dict):
                    patch_raw = {}
                parts.append({
                    "type": "patch",
                    "hash": patch_raw.get("hash", ""),
                    "files": patch_raw.get("files", []),
                    "id": patch_raw.get("id", ""),
                    "session_id": patch_raw.get("sessionID", ""),
                    "message_id": patch_raw.get("messageID", ""),
                })

            else:
                parts.append({"type": ptype, "raw": p})

        finish = safe_get(info, "finish", default="")
        path_info = safe_get(info, "path", default={})
        if not isinstance(path_info, dict):
            path_info = {}
        steps.append({
            "index": idx,
            "role": role,
            "tokens": tokens,
            "duration": duration,
            "parts": parts,
            "tool_calls": tool_calls,
            "tool_call_count": len(tool_calls),
            "error_count": errors,
            "has_reasoning": has_reasoning,
            "text_preview": text_preview,
            "finish": finish,
            "cost": safe_get(info, "cost", default=0) or 0,
            "model_id": safe_get(info, "modelID", default=""),
            "provider_id": safe_get(info, "providerID", default=""),
            "time_created_ms": t_created,
            "time_completed_ms": t_completed,
            "agent": safe_get(info, "agent", default=""),
            "mode": safe_get(info, "mode", default=""),
            "message_id": message_id,
            "id": safe_get(info, "id", default=""),
            "parent_id": safe_get(info, "parentID", default=""),
            "session_id": safe_get(info, "sessionID", default=""),
            "cwd": path_info.get("cwd", ""),
            "root": path_info.get("root", ""),
        })

    return steps


def build_message_metrics(steps: list[dict]) -> list[dict]:
    """Build per-message metrics used for diagnostics tables and charts."""
    rows: list[dict] = []
    for s in steps:
        tokens = s.get("tokens", {})
        tok_total = tokens.get("total", 0) or 0
        tok_input = tokens.get("input", 0) or 0
        tok_output = tokens.get("output", 0) or 0
        cache_read = tokens.get("cache_read", 0) or 0
        non_cache = max(0, tok_input - cache_read)
        duration = s.get("duration")

        tool_time_sum = 0.0
        for tc in s.get("tool_calls", []):
            ts = tc.get("time_start")
            te = tc.get("time_end")
            if isinstance(ts, (int, float)) and isinstance(te, (int, float)) and te >= ts:
                tool_time_sum += (te - ts) / 1000.0

        part_counts: dict[str, int] = {}
        for p in s.get("parts", []):
            pt = p.get("type", "unknown")
            part_counts[pt] = part_counts.get(pt, 0) + 1

        rows.append({
            "index": s.get("index", 0),
            "role": s.get("role", "?"),
            "agent": s.get("agent", ""),
            "model_id": s.get("model_id", ""),
            "finish": s.get("finish", ""),
            "duration": duration,
            "cost": s.get("cost", 0),
            "tokens_total": tok_total,
            "tokens_input": tok_input,
            "tokens_output": tok_output,
            "cache_read": cache_read,
            "non_cache_tokens": non_cache,
            "cache_ratio": (cache_read / tok_total) if tok_total else 0.0,
            "tokens_per_sec": (tok_total / duration) if duration and duration > 0 else None,
            "non_cache_per_sec": (non_cache / duration) if duration and duration > 0 else None,
            "output_input_ratio": (tok_output / max(1, tok_input)),
            "tool_calls": s.get("tool_call_count", 0),
            "errors": s.get("error_count", 0),
            "tool_time_sum": tool_time_sum,
            "tool_time_share": (tool_time_sum / duration) if duration and duration > 0 else 0.0,
            "reasoning_parts": part_counts.get("reasoning", 0),
            "text_parts": part_counts.get("text", 0),
            "patch_parts": part_counts.get("patch", 0),
        })
    return rows


def _build_hotspots_md(rows: list[dict]) -> str:
    """Build markdown tables for top latency/token/cost/cache-miss hotspots."""
    with_dur = [r for r in rows if r.get("duration") is not None]
    top_d = sorted(with_dur, key=lambda r: r["duration"], reverse=True)[:5]
    top_t = sorted(rows, key=lambda r: r["tokens_total"], reverse=True)[:5]

    def fmt_table(items: list[dict], value_field: str, value_header: str, value_fmt: str) -> str:
        if not items:
            return "*No data*"
        lines = [
            f"| Step | Role | {value_header} | Tokens | Tool calls |",
            "|---:|---|---:|---:|---:|",
        ]
        for r in items:
            v = r[value_field]
            if isinstance(v, (int, float)):
                v_str = format(v, value_fmt)
            else:
                v_str = str(v)
            lines.append(
                f"| {r['index']} | `{r['role']}` | {v_str} | "
                f"{r['tokens_total']:,} | {r['tool_calls']} |"
            )
        return "\n".join(lines)

    sections = [
        "### Message Hotspots\n\n"
        "**Top latency steps**\n\n"
        + fmt_table(top_d, "duration", "Duration (s)", ".2f")
        + "\n\n**Top token-load steps**\n\n"
        + fmt_table(top_t, "tokens_total", "Tokens", ",")
    ]

    # Cost hotspots
    with_cost = [r for r in rows if (r.get("cost") or 0) > 0]
    if with_cost:
        top_c = sorted(with_cost, key=lambda r: r["cost"], reverse=True)[:5]
        sections.append(
            "\n\n**Most expensive steps**\n\n"
            + fmt_table(top_c, "cost", "Cost ($)", ".4f")
        )

    # Lowest cache ratio (assistant steps with tokens, excluding 0-token steps)
    asst_with_tok = [r for r in rows
                     if r.get("role") == "assistant" and r["tokens_total"] > 0]
    if asst_with_tok:
        low_cache = sorted(asst_with_tok, key=lambda r: r["cache_ratio"])[:5]
        lines = [
            "| Step | Role | Cache % | Fresh Input | Tokens |",
            "|---:|---|---:|---:|---:|",
        ]
        for r in low_cache:
            lines.append(
                f"| {r['index']} | `{r['role']}` | {r['cache_ratio'] * 100:.1f}% | "
                f"{r['non_cache_tokens']:,} | {r['tokens_total']:,} |"
            )
        sections.append(
            "\n\n**Lowest cache ratio steps** (optimization targets)\n\n"
            + "\n".join(lines)
        )

    return "".join(sections)


def _build_per_message_md(rows: list[dict], limit: int = 80) -> str:
    """Build a compact per-message diagnostics table."""
    if not rows:
        return "*No messages parsed.*"

    has_agent = any(r.get("agent") for r in rows)

    lines = ["### Per-Message Diagnostics", ""]
    if has_agent:
        lines.append(
            "| Step | Role | Agent | Finish | Dur(s) | Tokens | Tok/s | Cache % | Non-cache | Out/In | Tool calls | Tool wait % | Cost | Parts (R/T) |"
        )
        lines.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    else:
        lines.append(
            "| Step | Role | Finish | Dur(s) | Tokens | Tok/s | Cache % | Non-cache | Out/In | Tool calls | Tool wait % | Cost | Parts (R/T) |"
        )
        lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    for r in rows[:limit]:
        dur = "N/A" if r["duration"] is None else f"{r['duration']:.2f}"
        tokps = "N/A" if r["tokens_per_sec"] is None else f"{r['tokens_per_sec']:.1f}"
        parts = f"{r['reasoning_parts']}/{r['text_parts']}"
        cost = r.get("cost", 0) or 0
        cost_str = f"${cost:.4f}" if cost else "-"
        if has_agent:
            agent = r.get("agent", "") or "-"
            lines.append(
                f"| {r['index']} | `{r['role']}` | `{agent}` | `{r['finish'] or '-'}` | {dur} | "
                f"{r['tokens_total']:,} | {tokps} | {r['cache_ratio'] * 100:.1f}% | "
                f"{r['non_cache_tokens']:,} | {r['output_input_ratio']:.2f} | {r['tool_calls']} | "
                f"{r['tool_time_share'] * 100:.2f}% | {cost_str} | {parts} |"
            )
        else:
            lines.append(
                f"| {r['index']} | `{r['role']}` | `{r['finish'] or '-'}` | {dur} | "
                f"{r['tokens_total']:,} | {tokps} | {r['cache_ratio'] * 100:.1f}% | "
                f"{r['non_cache_tokens']:,} | {r['output_input_ratio']:.2f} | {r['tool_calls']} | "
                f"{r['tool_time_share'] * 100:.2f}% | {cost_str} | {parts} |"
            )
    if len(rows) > limit:
        lines.append(f"\n*Showing first {limit} / {len(rows)} messages.*")
    return "\n".join(lines)


def compute_metrics(steps: list[dict], raw: dict, message_rows: list[dict] | None = None) -> dict:
    """Aggregate metrics from parsed steps and raw trajectory."""
    if message_rows is None:
        message_rows = build_message_metrics(steps)

    total_duration = 0.0
    durations = []
    total_tokens = {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                    "cache_read": 0, "cache_write": 0}
    total_cost = 0.0
    tool_count = 0
    tool_breakdown: dict[str, int] = {}
    tool_status_breakdown: dict[str, int] = {}
    agent_breakdown: dict[str, int] = {}
    model_breakdown: dict[str, int] = {}
    tool_success = 0
    tool_fail = 0
    roles: dict[str, int] = {}
    finish_breakdown: dict[str, int] = {}
    reasoning_parts = 0
    text_parts = 0
    snapshot_parts = 0
    tool_durations: list[float] = []

    for s in steps:
        d = s.get("duration")
        if d is not None:
            total_duration += d
            durations.append(d)
        for k in total_tokens:
            total_tokens[k] += s["tokens"].get(k, 0)
        total_cost += s.get("cost", 0)
        tool_count += s["tool_call_count"]
        for tc in s["tool_calls"]:
            name = tc["tool_name"]
            tool_breakdown[name] = tool_breakdown.get(name, 0) + 1
            status = tc.get("status", "unknown")
            tool_status_breakdown[status] = tool_status_breakdown.get(status, 0) + 1
            if status in {"error", "failed", "failure", "cancelled", "canceled", "timeout", "timed_out"}:
                tool_fail += 1
            elif status in {"completed", "success", "succeeded", "ok"}:
                tool_success += 1
            ts = tc.get("time_start")
            te = tc.get("time_end")
            if isinstance(ts, (int, float)) and isinstance(te, (int, float)) and te >= ts:
                tool_durations.append((te - ts) / 1000.0)
        roles[s["role"]] = roles.get(s["role"], 0) + 1
        agent = s.get("agent", "")
        if agent:
            agent_breakdown[agent] = agent_breakdown.get(agent, 0) + 1
        model = s.get("model_id", "")
        if model:
            model_breakdown[model] = model_breakdown.get(model, 0) + 1
        # Finish state breakdown (only meaningful for assistant messages)
        finish = s.get("finish", "")
        if finish:
            finish_breakdown[finish] = finish_breakdown.get(finish, 0) + 1
        # Count part types
        for p in s.get("parts", []):
            pt = p.get("type", "")
            if pt == "reasoning":
                reasoning_parts += 1
            elif pt == "text":
                text_parts += 1
            elif pt == "snapshot":
                snapshot_parts += 1

    avg_duration = (total_duration / len(durations)) if durations else 0
    median_duration = statistics.median(durations) if durations else 0
    p95_duration = _percentile(durations, 0.95) if durations else 0
    max_duration = max(durations) if durations else 0

    # Patch info from raw
    output = raw.get("output", {}) if isinstance(raw.get("output"), dict) else {}
    timing = raw.get("timing", {}) if isinstance(raw.get("timing"), dict) else {}

    assistant_rows = [r for r in message_rows if r.get("role") == "assistant"]
    assistant_tokens = [r["tokens_total"] for r in assistant_rows]
    token_rates = [r["tokens_per_sec"] for r in assistant_rows if r.get("tokens_per_sec") is not None]
    cache_ratios = [r["cache_ratio"] for r in assistant_rows if r["tokens_total"] > 0]
    non_cache_total = sum(r["non_cache_tokens"] for r in message_rows)
    tool_time_total = sum(r["tool_time_sum"] for r in message_rows)
    cache_dominant_steps = sum(1 for r in assistant_rows if r["tokens_total"] > 0 and r["cache_ratio"] >= 0.90)
    multi_tool_steps = sum(1 for r in assistant_rows if r["tool_calls"] >= 2)
    no_tool_assistant_steps = sum(1 for r in assistant_rows if r["tool_calls"] == 0)
    patch_steps = sum(1 for r in assistant_rows if r["patch_parts"] > 0)
    avg_tool_duration = statistics.mean(tool_durations) if tool_durations else 0
    p95_tool_duration = _percentile(tool_durations, 0.95) if tool_durations else 0
    max_tool_duration = max(tool_durations) if tool_durations else 0

    return {
        "total_steps": len(steps),
        "total_duration": round(total_duration, 2),
        "avg_duration": round(avg_duration, 2),
        "median_duration": round(median_duration, 2),
        "p95_duration": round(p95_duration, 2),
        "max_duration": round(max_duration, 2),
        "wall_clock": timing.get("total_duration", total_duration),
        "tokens": total_tokens,
        "non_cache_tokens": non_cache_total,
        "non_cache_ratio": round((non_cache_total / total_tokens["total"] * 100), 1) if total_tokens["total"] else 0,
        "total_cost": round(total_cost, 6),
        "avg_tokens_per_step": round(total_tokens["total"] / len(steps)) if steps else 0,
        "tokens_per_second": round(total_tokens["total"] / total_duration, 1) if total_duration else 0,
        "output_input_ratio": round(total_tokens["output"] / max(1, total_tokens["input"]), 3),
        "median_step_tokens": round(statistics.median(assistant_tokens)) if assistant_tokens else 0,
        "p95_step_tokens": round(_percentile(assistant_tokens, 0.95)) if assistant_tokens else 0,
        "median_tokens_per_second": round(statistics.median(token_rates), 1) if token_rates else 0,
        "avg_cache_ratio": round(statistics.mean(cache_ratios) * 100, 1) if cache_ratios else 0,
        "cache_dominant_steps": cache_dominant_steps,
        "assistant_steps": len(assistant_rows),
        "tool_call_count": tool_count,
        "tool_breakdown": tool_breakdown,
        "tool_status_breakdown": tool_status_breakdown,
        "tool_success": tool_success,
        "tool_fail": tool_fail,
        "tool_success_rate": round(tool_success / tool_count * 100, 1) if tool_count else 0,
        "tokens_per_tool": round(total_tokens["total"] / tool_count) if tool_count else 0,
        "tool_time_total": round(tool_time_total, 2),
        "tool_wait_share": round(tool_time_total / total_duration * 100, 1) if total_duration else 0,
        "avg_tool_duration": round(avg_tool_duration, 3),
        "p95_tool_duration": round(p95_tool_duration, 3),
        "max_tool_duration": round(max_tool_duration, 3),
        "multi_tool_steps": multi_tool_steps,
        "no_tool_assistant_steps": no_tool_assistant_steps,
        "patch_steps": patch_steps,
        "messages_breakdown": roles,
        "agent_breakdown": agent_breakdown,
        "model_breakdown": model_breakdown,
        "finish_breakdown": finish_breakdown,
        "reasoning_parts": reasoning_parts,
        "text_parts": text_parts,
        "snapshot_parts": snapshot_parts,
        "patch_lines": output.get("patch_lines", 0),
        "has_patch": output.get("has_patch", False),
        "patch_error": output.get("error"),
    }


def discover_trajectory_files(base_dir: str) -> list[str]:
    """Glob for trajectory JSON files under base_dir.

    Searches all ``**/*.json`` files recursively and keeps only those
    that look like trajectory files (contain a top-level "trajectory" key).
    """
    pattern = os.path.join(base_dir, "**", "*.json")
    candidates = sorted(glob.glob(pattern, recursive=True))
    files: list[str] = []
    for fp in candidates:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                # Peek at the first 4 KB to check for the "trajectory" key
                # without parsing the entire file.
                head = f.read(4096)
            if '"trajectory"' in head:
                files.append(fp)
        except OSError:
            continue
    return files


def _fmt_dict_as_table(d: dict, key_header: str = "Key", val_header: str = "Count") -> str:
    """Format a dict as a markdown table."""
    if not d:
        return "*None*"
    lines = [f"| {key_header} | {val_header} |", "|---|---|"]
    for k, v in sorted(d.items(), key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {v} |")
    return "\n".join(lines)
