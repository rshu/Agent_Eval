"""Data loading, parsing, and aggregate metrics."""

import json
import os
import statistics
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


def infer_non_cache_input(
    total_tokens: int,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cache_read_tokens: int,
) -> int:
    """Infer fresh/non-cache input tokens across token-schema variants.

    Some traces report:
    - total = input + output + reasoning + cache_read  (input is already fresh)
    Others report:
    - total = input + output + reasoning               (input includes cache)
    """
    base = (input_tokens or 0) + (output_tokens or 0) + (reasoning_tokens or 0)
    total = total_tokens or 0
    cache_read = cache_read_tokens or 0

    # Pick the interpretation whose implied total is closer to observed total.
    dist_fresh_input = abs(total - (base + cache_read))
    dist_cached_input = abs(total - base)
    if dist_fresh_input <= dist_cached_input:
        return max(0, input_tokens or 0)
    return max(0, (input_tokens or 0) - cache_read)


def _parse_parts(parts_raw: list) -> tuple[list, list, int, bool, str]:
    """Parse raw parts into structured parts, tool calls, error count, reasoning flag, and preview."""
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
                "type": "tool_call", "tool_name": tool_name,
                "tool_id": p.get("tool_id", p.get("id", "")), "status": status,
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
                "type": "patch", "hash": patch_raw.get("hash", ""),
                "files": patch_raw.get("files", []), "id": patch_raw.get("id", ""),
                "session_id": patch_raw.get("sessionID", ""),
                "message_id": patch_raw.get("messageID", ""),
            })
        else:
            parts.append({"type": ptype, "raw": p})

    return parts, tool_calls, errors, has_reasoning, text_preview


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

        t_created = safe_get(info, "time", "created", default=None)
        t_completed = safe_get(info, "time", "completed", default=None)
        duration = None
        if isinstance(t_created, (int, float)) and isinstance(t_completed, (int, float)):
            duration = round((t_completed - t_created) / 1000.0, 2)

        raw_parts = msg.get("parts", [])
        if not isinstance(raw_parts, list):
            raw_parts = []
        parts, tool_calls, errors, has_reasoning, text_preview = _parse_parts(raw_parts)

        finish = safe_get(info, "finish", default="")
        path_info = safe_get(info, "path", default={})
        if not isinstance(path_info, dict):
            path_info = {}
        steps.append({
            "index": idx, "role": role, "tokens": tokens, "duration": duration,
            "parts": parts, "tool_calls": tool_calls,
            "tool_call_count": len(tool_calls), "error_count": errors,
            "has_reasoning": has_reasoning, "text_preview": text_preview,
            "finish": finish,
            "model_id": safe_get(info, "modelID", default=""),
            "provider_id": safe_get(info, "providerID", default=""),
            "time_created_ms": t_created, "time_completed_ms": t_completed,
            "agent": safe_get(info, "agent", default=""),
            "mode": safe_get(info, "mode", default=""),
            "message_id": msg.get("message_id", ""),
            "id": safe_get(info, "id", default=""),
            "parent_id": safe_get(info, "parentID", default=""),
            "session_id": safe_get(info, "sessionID", default=""),
            "cwd": path_info.get("cwd", ""), "root": path_info.get("root", ""),
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
        tok_reasoning = tokens.get("reasoning", 0) or 0
        cache_read = tokens.get("cache_read", 0) or 0
        non_cache = infer_non_cache_input(
            total_tokens=tok_total,
            input_tokens=tok_input,
            output_tokens=tok_output,
            reasoning_tokens=tok_reasoning,
            cache_read_tokens=cache_read,
        )
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
    """Build markdown tables for top latency/token/cache-miss hotspots."""
    with_dur = [r for r in rows if r.get("duration") is not None]
    top_d = sorted(with_dur, key=lambda r: r["duration"], reverse=True)[:5]
    top_t = sorted(rows, key=lambda r: r["tokens_total"], reverse=True)[:5]

    def fmt_table(items: list[dict], value_field: str, value_header: str,
                  value_fmt: str, extra_cols: list[tuple[str, str, str]] | None = None) -> str:
        if not items:
            return "*No data*"
        extra = extra_cols or [("tokens_total", "Tokens", ","), ("tool_calls", "Tool Calls", "")]
        hdr = " | ".join(h for _, h, _ in extra)
        lines = [
            f"| Step | Role | {value_header} | {hdr} |",
            "|---:|---|" + "---:|" * (1 + len(extra)),
        ]
        for r in items:
            v = r[value_field]
            v_str = format(v, value_fmt) if isinstance(v, (int, float)) else str(v)
            extras = " | ".join(
                format(r[f], ef) if isinstance(r[f], (int, float)) and ef else str(r[f])
                for f, _, ef in extra
            )
            lines.append(f"| {r['index']} | `{r['role']}` | {v_str} | {extras} |")
        return "\n".join(lines)

    sections = [
        "### Message Hotspots\n\n"
        "**Top latency steps**\n\n"
        + fmt_table(top_d, "duration", "Duration (s)", ".2f")
        + "\n\n**Top token-load steps**\n\n"
        + fmt_table(top_t, "tokens_total", "Tokens", ",",
                    extra_cols=[("tool_calls", "Tool Calls", ""),
                                ("duration", "Duration (s)", ".2f")])
    ]

    # Lowest cache ratio (assistant steps with tokens, excluding 0-token steps)
    asst_with_tok = [r for r in rows
                     if r.get("role") == "assistant" and r["tokens_total"] > 0]
    if asst_with_tok:
        low_cache = sorted(asst_with_tok, key=lambda r: r["cache_ratio"])[:5]
        lines = [
            "| Step | Role | Cache Read % | Fresh Input | Tokens |",
            "|---:|---|---:|---:|---:|",
        ]
        for r in low_cache:
            lines.append(
                f"| {r['index']} | `{r['role']}` | {r['cache_ratio'] * 100:.1f}% | "
                f"{r['non_cache_tokens']:,} | {r['tokens_total']:,} |"
            )
        sections.append(
            "\n\n**Lowest cache read steps** (optimization targets)\n\n"
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
            "| Step | Role | Agent | Finish | Duration (s) | Tokens | Tok/s | Cache Read % | Fresh Input | Out/In Ratio | Tool Calls | Tool Wait % | Parts (R/T) |"
        )
        lines.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    else:
        lines.append(
            "| Step | Role | Finish | Duration (s) | Tokens | Tok/s | Cache Read % | Fresh Input | Out/In Ratio | Tool Calls | Tool Wait % | Parts (R/T) |"
        )
        lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    for r in rows[:limit]:
        dur = "N/A" if r["duration"] is None else f"{r['duration']:.2f}"
        tokps = "N/A" if r["tokens_per_sec"] is None else f"{r['tokens_per_sec']:.1f}"
        parts = f"{r['reasoning_parts']}/{r['text_parts']}"
        finish = _friendly_finish(r['finish']) or '-'
        if has_agent:
            agent = r.get("agent", "") or "-"
            lines.append(
                f"| {r['index']} | `{r['role']}` | `{agent}` | `{finish}` | {dur} | "
                f"{r['tokens_total']:,} | {tokps} | {r['cache_ratio'] * 100:.1f}% | "
                f"{r['non_cache_tokens']:,} | {r['output_input_ratio']:.2f} | {r['tool_calls']} | "
                f"{r['tool_time_share'] * 100:.2f}% | {parts} |"
            )
        else:
            lines.append(
                f"| {r['index']} | `{r['role']}` | `{finish}` | {dur} | "
                f"{r['tokens_total']:,} | {tokps} | {r['cache_ratio'] * 100:.1f}% | "
                f"{r['non_cache_tokens']:,} | {r['output_input_ratio']:.2f} | {r['tool_calls']} | "
                f"{r['tool_time_share'] * 100:.2f}% | {parts} |"
            )
    if len(rows) > limit:
        lines.append(f"\n*Showing first {limit} / {len(rows)} messages.*")
    return "\n".join(lines)


def _compute_command_metrics(steps: list[dict]) -> dict:
    """Compute command execution success rate from tool calls with exit codes."""
    cmd_total = 0
    cmd_failures = 0
    for s in steps:
        for tc in s.get("tool_calls", []):
            meta = tc.get("metadata", {})
            if not isinstance(meta, dict):
                continue
            if "exit" in meta:
                cmd_total += 1
                if meta["exit"] != 0:
                    cmd_failures += 1
    if cmd_total == 0:
        return {"command_success_rate": None, "command_call_count": None, "command_failures": None}
    return {
        "command_success_rate": round((cmd_total - cmd_failures) / cmd_total, 4),
        "command_call_count": cmd_total,
        "command_failures": cmd_failures,
    }


def _compute_timing_metrics(steps: list[dict], total_output_tokens: int) -> dict:
    """Compute throughput timing: TTFT, output tok/s, TTLT."""
    first_user_created = None
    first_asst_completed = None
    last_asst_completed = None
    total_asst_duration = 0.0

    for s in steps:
        role = s.get("role", "")
        t_created = s.get("time_created_ms")
        t_completed = s.get("time_completed_ms")
        if role == "user" and first_user_created is None and isinstance(t_created, (int, float)):
            first_user_created = t_created
        if role == "assistant":
            if isinstance(t_completed, (int, float)):
                if first_asst_completed is None:
                    first_asst_completed = t_completed
                last_asst_completed = t_completed
            d = s.get("duration")
            if d is not None:
                total_asst_duration += d

    result: dict = {}
    if first_user_created is not None and first_asst_completed is not None:
        result["time_to_first_token"] = round((first_asst_completed - first_user_created) / 1000, 3)
    else:
        result["time_to_first_token"] = None

    result["output_tokens_per_sec"] = (
        round(total_output_tokens / total_asst_duration, 1)
        if total_asst_duration > 0 and total_output_tokens > 0 else None
    )

    if first_user_created is not None and last_asst_completed is not None:
        result["time_to_last_token"] = round((last_asst_completed - first_user_created) / 1000, 3)
    else:
        result["time_to_last_token"] = None

    return result


def _compute_plan_metrics(steps: list[dict]) -> dict:
    """Compute plan tracking from todo snapshot parts."""
    snapshots = []
    for s in steps:
        for p in s.get("parts", []):
            if p.get("type") == "snapshot":
                data = p.get("data", {})
                if isinstance(data, dict) and ("todos" in data or "items" in data):
                    snapshots.append(data)
    if not snapshots:
        return {"plan_items": None, "plan_completion_ratio": None, "plan_update_count": None}

    # First snapshot for plan_items count
    first = snapshots[0]
    items = first.get("todos", first.get("items", []))
    plan_items = len(items) if isinstance(items, list) else 0

    # Last snapshot for completion ratio
    last = snapshots[-1]
    last_items = last.get("todos", last.get("items", []))
    if isinstance(last_items, list) and last_items:
        completed = sum(
            1 for item in last_items
            if (isinstance(item, dict) and item.get("completed", False))
            or (isinstance(item, dict) and item.get("status") in ("completed", "done"))
        )
        plan_completion_ratio = round(completed / len(last_items), 4)
    else:
        plan_completion_ratio = None

    return {
        "plan_items": plan_items,
        "plan_completion_ratio": plan_completion_ratio,
        "plan_update_count": len(snapshots),
    }


def _compute_token_stats(total_tokens, total_duration, steps, message_rows, raw):
    """Token breakdown, throughput, and cache metrics."""
    output = raw.get("output", {}) if isinstance(raw.get("output"), dict) else {}
    session_raw = raw.get("session_raw", {}) if isinstance(raw.get("session_raw"), dict) else {}
    summary = session_raw.get("summary") if isinstance(session_raw.get("summary"), dict) else None

    assistant_rows = [r for r in message_rows if r.get("role") == "assistant"]
    assistant_tokens = [r["tokens_total"] for r in assistant_rows]
    token_rates = [r["tokens_per_sec"] for r in assistant_rows if r.get("tokens_per_sec") is not None]
    cache_ratios = [r["cache_ratio"] for r in assistant_rows if r["tokens_total"] > 0]
    non_cache_total = sum(r["non_cache_tokens"] for r in message_rows)
    cache_dominant = sum(1 for r in assistant_rows if r["tokens_total"] > 0 and r["cache_ratio"] >= 0.90)
    total_io = total_tokens["input"] + total_tokens["output"]
    churn = (summary["additions"] + summary["deletions"]) if summary and "additions" in summary and "deletions" in summary else 0
    return {
        "tokens": total_tokens,
        "non_cache_tokens": non_cache_total,
        "non_cache_ratio": round(non_cache_total / total_tokens["total"] * 100, 1) if total_tokens["total"] else 0,
        "avg_tokens_per_step": round(total_tokens["total"] / len(steps)) if steps else 0,
        "tokens_per_second": round(total_tokens["total"] / total_duration, 1) if total_duration else 0,
        "output_input_ratio": round(total_tokens["output"] / max(1, total_tokens["input"]), 3),
        "median_step_tokens": round(statistics.median(assistant_tokens)) if assistant_tokens else 0,
        "p95_step_tokens": round(_percentile(assistant_tokens, 0.95)) if assistant_tokens else 0,
        "median_tokens_per_second": round(statistics.median(token_rates), 1) if token_rates else 0,
        "avg_cache_ratio": round(statistics.mean(cache_ratios) * 100, 1) if cache_ratios else 0,
        "cache_dominant_steps": cache_dominant,
        "assistant_steps": len(assistant_rows),
        "input_tokens": total_tokens["input"],
        "output_tokens": total_tokens["output"],
        "cache_read_tokens": total_tokens["cache_read"],
        "cache_utilization_ratio": (
            round(total_tokens["cache_read"] / (total_tokens["cache_read"] + total_tokens["input"]), 4)
            if (total_tokens["cache_read"] + total_tokens["input"]) > 0 and total_tokens["cache_read"] > 0 else None
        ),
        "tokens_per_patch_line": round(total_io / output.get("patch_lines", 0), 1) if output.get("patch_lines", 0) > 0 else None,
        "tokens_per_churn_line": round(total_io / churn, 1) if churn > 0 else None,
    }


def _compute_tool_stats(steps, total_tokens_total, total_duration, message_rows):
    """Tool frequency, success rate, duration, and load metrics."""
    tool_count = 0
    tool_breakdown: dict[str, int] = {}
    tool_status_breakdown: dict[str, int] = {}
    tool_success = 0
    tool_fail = 0
    tool_durations: list[float] = []

    for s in steps:
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
            ts, te = tc.get("time_start"), tc.get("time_end")
            if isinstance(ts, (int, float)) and isinstance(te, (int, float)) and te >= ts:
                tool_durations.append((te - ts) / 1000.0)

    assistant_rows = [r for r in message_rows if r.get("role") == "assistant"]
    tool_time_total = sum(r["tool_time_sum"] for r in message_rows)
    avg_td = statistics.mean(tool_durations) if tool_durations else 0
    return {
        "tool_call_count": tool_count,
        "tool_breakdown": tool_breakdown,
        "tool_status_breakdown": tool_status_breakdown,
        "tool_success": tool_success,
        "tool_fail": tool_fail,
        "tool_success_rate": round(tool_success / tool_count * 100, 1) if tool_count else 0,
        "tokens_per_tool": round(total_tokens_total / tool_count) if tool_count else 0,
        "tool_time_total": round(tool_time_total, 2),
        "tool_wait_share": round(tool_time_total / total_duration * 100, 1) if total_duration else 0,
        "avg_tool_duration": round(avg_td, 3),
        "p95_tool_duration": round(_percentile(tool_durations, 0.95), 3) if tool_durations else 0,
        "max_tool_duration": round(max(tool_durations), 3) if tool_durations else 0,
        "multi_tool_steps": sum(1 for r in assistant_rows if r["tool_calls"] >= 2),
        "no_tool_assistant_steps": sum(1 for r in assistant_rows if r["tool_calls"] == 0),
        "patch_steps": sum(1 for r in assistant_rows if r["patch_parts"] > 0),
        "tool_calls_per_min": round(tool_count / (total_duration / 60), 2) if total_duration > 0 else None,
        "tool_time_fraction": round(tool_time_total / total_duration, 4) if total_duration > 0 else None,
        "tool_system_failure_rate": round(tool_fail / tool_count, 4) if tool_count > 0 else None,
    }


def _compute_efficiency_stats(steps, message_rows, raw):
    """Behavioral, structural, and change-scope metrics."""
    roles: dict[str, int] = {}
    agent_breakdown: dict[str, int] = {}
    model_breakdown: dict[str, int] = {}
    finish_breakdown: dict[str, int] = {}
    reasoning_parts = text_parts = snapshot_parts = 0
    for s in steps:
        roles[s["role"]] = roles.get(s["role"], 0) + 1
        agent = s.get("agent", "")
        if agent:
            agent_breakdown[agent] = agent_breakdown.get(agent, 0) + 1
        model = s.get("model_id", "")
        if model:
            model_breakdown[model] = model_breakdown.get(model, 0) + 1
        finish = s.get("finish", "")
        if finish:
            finish_breakdown[finish] = finish_breakdown.get(finish, 0) + 1
        for p in s.get("parts", []):
            pt = p.get("type", "")
            if pt == "reasoning":
                reasoning_parts += 1
            elif pt == "text":
                text_parts += 1
            elif pt == "snapshot":
                snapshot_parts += 1

    output = raw.get("output", {}) if isinstance(raw.get("output"), dict) else {}
    session_raw = raw.get("session_raw", {}) if isinstance(raw.get("session_raw"), dict) else {}
    summary = session_raw.get("summary") if isinstance(session_raw.get("summary"), dict) else None
    file_status_raw = raw.get("file_status")
    asst_durs = [s["duration"] for s in steps if s.get("role") == "assistant" and s.get("duration") is not None]
    user_n, asst_n = roles.get("user", 0), roles.get("assistant", 0)
    return {
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
        "files_changed": (
            summary.get("files") if summary and "files" in summary
            else len(file_status_raw) if isinstance(file_status_raw, list) else None
        ),
        "additions": summary.get("additions") if summary else None,
        "deletions": summary.get("deletions") if summary else None,
        "churn": (summary["additions"] + summary["deletions"]) if summary and "additions" in summary and "deletions" in summary else None,
        "net_change": (summary["additions"] - summary["deletions"]) if summary and "additions" in summary and "deletions" in summary else None,
        "user_turns": user_n,
        "assistant_turns": asst_n,
        "autonomy_ratio": round(asst_n / (user_n + asst_n), 4) if (user_n + asst_n) > 0 else None,
        "p50_duration": round(_percentile(asst_durs, 0.50), 2) if asst_durs else None,
        "p90_duration": round(_percentile(asst_durs, 0.90), 2) if asst_durs else None,
        "p99_duration": round(_percentile(asst_durs, 0.99), 2) if asst_durs else None,
    }


def compute_metrics(steps: list[dict], raw: dict, message_rows: list[dict] | None = None) -> dict:
    """Aggregate metrics from parsed steps and raw trajectory."""
    if message_rows is None:
        message_rows = build_message_metrics(steps)

    # Duration stats
    durations = [s["duration"] for s in steps if s.get("duration") is not None]
    total_duration = sum(durations)
    total_tokens = {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                    "cache_read": 0, "cache_write": 0}
    for s in steps:
        for k in total_tokens:
            total_tokens[k] += s["tokens"].get(k, 0)

    timing = raw.get("timing", {}) if isinstance(raw.get("timing"), dict) else {}

    return {
        "total_steps": len(steps),
        "total_duration": round(total_duration, 2),
        "avg_duration": round(total_duration / len(durations), 2) if durations else 0,
        "median_duration": round(statistics.median(durations), 2) if durations else 0,
        "p95_duration": round(_percentile(durations, 0.95), 2) if durations else 0,
        "max_duration": round(max(durations), 2) if durations else 0,
        "wall_clock": timing.get("total_duration", total_duration),
        **_compute_token_stats(total_tokens, total_duration, steps, message_rows, raw),
        **_compute_tool_stats(steps, total_tokens["total"], total_duration, message_rows),
        **_compute_efficiency_stats(steps, message_rows, raw),
        **_compute_command_metrics(steps),
        **_compute_timing_metrics(steps, total_tokens["output"]),
        **_compute_plan_metrics(steps),
    }


def compute_health_verdict(metrics: dict, step_analytics: list[dict]) -> list[dict]:
    """Compute a health verdict with color-coded status for key metrics."""
    verdicts = []

    # Cache efficiency
    avg_cache = metrics.get("avg_cache_ratio", 0)
    if avg_cache >= 60:
        status, detail = "good", f"Avg cache read {avg_cache}% — strong cache reuse"
    elif avg_cache >= 30:
        status, detail = "warn", f"Avg cache read {avg_cache}% — moderate cache reuse"
    else:
        status, detail = "bad", f"Avg cache read {avg_cache}% — most input tokens are fresh"
    verdicts.append({"metric": "Cache Efficiency", "status": status, "label": f"{avg_cache}%", "detail": detail})

    # Tool success rate
    tool_rate = metrics.get("tool_success_rate", 0)
    tool_count = metrics.get("tool_call_count", 0)
    if tool_count == 0:
        verdicts.append({"metric": "Tool Success", "status": "good", "label": "N/A", "detail": "No tool calls"})
    elif tool_rate >= 95:
        verdicts.append({"metric": "Tool Success", "status": "good", "label": f"{tool_rate}%", "detail": f"{tool_rate}% success across {tool_count} calls"})
    elif tool_rate >= 80:
        verdicts.append({"metric": "Tool Success", "status": "warn", "label": f"{tool_rate}%", "detail": f"{tool_rate}% success — {metrics.get('tool_fail', 0)} failures out of {tool_count} calls"})
    else:
        verdicts.append({"metric": "Tool Success", "status": "bad", "label": f"{tool_rate}%", "detail": f"{tool_rate}% success — high failure rate across {tool_count} calls"})

    # Token efficiency (tok/s)
    tok_per_s = metrics.get("tokens_per_second", 0)
    if tok_per_s >= 50:
        status, detail = "good", f"{tok_per_s} tok/s — strong throughput"
    elif tok_per_s >= 20:
        status, detail = "warn", f"{tok_per_s} tok/s — moderate throughput"
    else:
        status, detail = "bad", f"{tok_per_s} tok/s — low throughput"
    verdicts.append({"metric": "Throughput", "status": status, "label": f"{tok_per_s} tok/s", "detail": detail})

    # Error rate
    error_steps = sum(1 for a in step_analytics if any(
        p.get("type") == "tool_call" and p.get("status") in ("error", "failed", "failure")
        for s in ([a] if "parts" in a else [])
        for p in s.get("parts", [])
    ))
    # Fallback: count from steps with error_count info
    if error_steps == 0:
        error_steps = metrics.get("tool_fail", 0)
    if error_steps == 0:
        status, detail = "good", "No error steps detected"
    elif error_steps <= 2:
        status, detail = "warn", f"{error_steps} error step(s) detected"
    else:
        status, detail = "bad", f"{error_steps} error steps — agent may be struggling"
    verdicts.append({"metric": "Errors", "status": status, "label": str(error_steps), "detail": detail})

    return verdicts



def format_session_md(timing: dict, metadata: dict, retry: dict,
                      *, model_id: str = "", provider_id: str = "",
                      agent_id: str = "") -> str:
    """Format session & environment metadata as a markdown table."""
    started = timing.get("started_at", "N/A")
    finished = timing.get("finished_at", "N/A")
    if isinstance(started, str) and len(started) > 19:
        started = started[:19].replace("T", " ")
    else:
        started = str(started)
    if isinstance(finished, str) and len(finished) > 19:
        finished = finished[:19].replace("T", " ")
    else:
        finished = str(finished)

    retry_info = ""
    if retry:
        retry_info = (
            f"| Attempts | {retry.get('total_attempts', '?')}"
            f" / {retry.get('max_retries', '?')} |"
        )

    md = metadata
    return f"""### Session & Environment
| Field | Value |
|-------|-------|
| Model | `{model_id or md.get('model') or 'N/A'}` |
| Provider | `{provider_id or 'N/A'}` |
| Agent | `{agent_id or md.get('agent', 'N/A')}` |
| Start time | `{started}` |
| End time | `{finished}` |
| Duration | `{timing.get('total_duration', 'N/A')}s` |
| Session | `{md.get('session_id', 'N/A')}` |
| Branch | `{md.get('branch', 'N/A')}` |
| Baseline commit | `{(md.get('baseline_commit') or 'N/A')[:12]}` |
| Directory | `{md.get('directory_name', 'N/A')}` |
| Server version | `{md.get('server_version', 'N/A')}` |
| Hostname | `{md.get('hostname', 'N/A')}` |
| Platform | `{(md.get('platform') or 'N/A')[:50]}` |
| Python | `{md.get('python_version', 'N/A')}` |
{retry_info}
"""


def format_performance_md(metrics: dict, wall_fmt: str) -> str:
    """Format performance & token metrics as a markdown table."""
    agent_section = ""
    if metrics.get("agent_breakdown"):
        agent_section = (
            "\n**Agent breakdown**\n\n"
            + _fmt_dict_as_table(metrics["agent_breakdown"], "Agent", "Steps")
            + "\n"
        )
    model_section = ""
    if metrics.get("model_breakdown"):
        model_section = (
            "\n**Model breakdown**\n\n"
            + _fmt_dict_as_table(metrics["model_breakdown"], "Model", "Steps")
            + "\n"
        )
    return f"""### Performance & Tokens
| Metric | Value |
|--------|------:|
| Total steps | {metrics['total_steps']} |
| Wall-clock time | {wall_fmt} |
| Avg step duration | {metrics['avg_duration']}s |
| Median / P95 duration | {metrics['median_duration']}s / {metrics['p95_duration']}s |
| Max step duration | {metrics['max_duration']}s |
| Total tokens | {metrics['tokens']['total']:,} |
| \u2003Input | {metrics['tokens']['input']:,} |
| \u2003Output | {metrics['tokens']['output']:,} |
| \u2003Reasoning | {metrics['tokens']['reasoning']:,} |
| \u2003Cache read | {metrics['tokens']['cache_read']:,} |
| \u2003Cache write | {metrics['tokens']['cache_write']:,} |
| Fresh input tokens | {metrics['non_cache_tokens']:,} ({metrics['non_cache_ratio']}%) |
| Avg tokens / step | {metrics['avg_tokens_per_step']:,} |
| Tokens / second | {metrics['tokens_per_second']:,} |
| Median tokens / second | {metrics['median_tokens_per_second']:,} |
| Output/Input token ratio | {metrics['output_input_ratio']} |
| Tokens / tool call | {metrics['tokens_per_tool']:,} |

**Tool calls** ({metrics['tool_call_count']} total, {metrics['tool_success_rate']}% success)

{_fmt_dict_as_table(metrics['tool_breakdown'], 'Tool', 'Count')}
{agent_section}{model_section}"""


def format_behavioral_md(metrics: dict) -> str:
    """Format behavioral diagnostics as a markdown table."""
    return f"""### Behavioral Diagnostics
| Indicator | Value |
|-----------|------:|
| Assistant steps | {metrics['assistant_steps']} |
| Multi-tool assistant steps | {metrics['multi_tool_steps']} |
| No-tool assistant steps | {metrics['no_tool_assistant_steps']} |
| Median assistant step tokens | {metrics['median_step_tokens']:,} |
| P95 assistant step tokens | {metrics['p95_step_tokens']:,} |
| Avg cache read % | {metrics['avg_cache_ratio']}% |
| Cache-dominant assistant steps (\u226590%) | {metrics['cache_dominant_steps']} |
| Tool execution time (sum) | {metrics['tool_time_total']}s |
| Tool-wait share of step time | {metrics['tool_wait_share']}% |
| Avg / P95 / Max tool duration | {metrics['avg_tool_duration']}s / {metrics['p95_tool_duration']}s / {metrics['max_tool_duration']}s |
"""


def format_output_md(output: dict, metadata: dict, summary: dict,
                     metrics: dict) -> str:
    """Format output & agent stats as markdown."""
    # Finish breakdown
    finish_parts = []
    for fk, fv in sorted(metrics["finish_breakdown"].items(), key=lambda x: -x[1]):
        finish_parts.append(f"{fv} {fk}")
    finish_str = ", ".join(finish_parts) if finish_parts else "N/A"

    # Tool status breakdown
    tool_status_parts = []
    for sk, sv in sorted(metrics["tool_status_breakdown"].items(), key=lambda x: -x[1]):
        tool_status_parts.append(f"{sv} {sk}")
    tool_status_str = ", ".join(tool_status_parts) if tool_status_parts else "N/A"

    # Role breakdown
    role_parts = []
    for rk, rv in sorted(metrics["messages_breakdown"].items()):
        role_parts.append(f"{rv} {rk}")
    role_str = ", ".join(role_parts) if role_parts else "N/A"

    # Output detail rows
    output_rows: list[str] = []
    if output.get("has_patch"):
        output_rows.append(
            f"| Patch | {output.get('patch_lines', 0)} lines,"
            f" {output.get('patch_length', 0):,} chars |"
        )
    if summary:
        output_rows.append(f"| Files changed | {summary.get('files', 'N/A')} |")
        output_rows.append(f"| Additions | +{summary.get('additions', 0)} |")
        output_rows.append(f"| Deletions | -{summary.get('deletions', 0)} |")
    gt_patch = metadata.get("ground_truth_patch", "")
    if gt_patch:
        suffix = "..." if len(gt_patch) > 60 else ""
        output_rows.append(f"| Ground truth | `{gt_patch[:60]}{suffix}` |")
    if output.get("error"):
        output_rows.append(f"| Error | `{output['error']}` |")

    output_table = ""
    if output_rows:
        output_table = "| Field | Value |\n|-------|-------|\n" + "\n".join(output_rows)

    return f"""### Output & Agent Stats
{output_table}

| Indicator | Value |
|-----------|-------|
| Role breakdown | {role_str} |
| Assistant finish states | {finish_str} |
| Tool calls | {metrics['tool_call_count']} |
| Tool status | {tool_status_str} |
| Tool success rate | {metrics['tool_success_rate']}% |
| Reasoning parts | {metrics['reasoning_parts']} |
| Text parts | {metrics['text_parts']} |
"""


def wall_clock_fmt(metrics: dict) -> tuple[float, str]:
    """Return (wall_seconds, formatted_string) for wall-clock time."""
    wall = metrics["wall_clock"] if isinstance(metrics["wall_clock"], (int, float)) else metrics["total_duration"]
    fmt = f"{wall:.0f}s" if wall < 3600 else f"{wall / 60:.1f}m"
    return wall, fmt


def format_banner_html(filename: str, metrics: dict, wall_fmt: str) -> str:
    """Build the one-line HTML summary banner for the loaded trajectory."""
    import html as _html
    return (
        f"<strong>{_html.escape(filename)}</strong> &nbsp;&mdash;&nbsp; "
        f"{metrics['total_steps']} steps &middot; "
        f"{metrics['tool_call_count']} tool calls ({metrics['tool_success_rate']}% success) &middot; "
        f"{metrics['tokens']['total']:,} tokens &middot; "
        f"{wall_fmt} wall-clock &middot; "
        f"{metrics['tokens_per_second']} tok/s &middot; "
        f"{metrics['reasoning_parts']} reasoning"
    )


def extract_agent_info(steps: list[dict]) -> tuple[str, str, str]:
    """Return (model_id, provider_id, agent_id) from the first assistant step."""
    model_id = provider_id = agent_id = ""
    for s in steps:
        if s["role"] == "assistant" and s.get("model_id"):
            model_id = s["model_id"]
            provider_id = s.get("provider_id", "")
            if s.get("agent"):
                agent_id = s["agent"]
            break
    if not agent_id:
        for s in steps:
            if s.get("agent"):
                agent_id = s["agent"]
                break
    return model_id, provider_id, agent_id


_FINISH_LABELS = {
    "tool-calls": "Tool Call",
    "stop": "Completed",
    "end_turn": "End Turn",
}


def _friendly_finish(raw: str | None) -> str:
    """Map internal finish-reason enums to user-friendly labels."""
    if not raw:
        return ""
    return _FINISH_LABELS.get(raw, raw.replace("-", " ").replace("_", " ").title())


_PART_LABELS = {
    "text": "Text",
    "reasoning": "Reason",
    "tool_call": "Tool",
    "step_start": "Start",
    "step_finish": "Finish",
    "snapshot": "Snap",
    "patch": "Patch",
}


def _friendly_parts(part_mix: str) -> str:
    """Convert comma-separated part types to compact friendly labels."""
    if not part_mix:
        return ""
    types = [t.strip() for t in part_mix.split(",") if t.strip()]
    labels = [_PART_LABELS.get(t, t.replace("_", " ").title()) for t in types]
    if len(labels) <= 3:
        return " · ".join(labels)
    return " · ".join(labels[:2]) + f" +{len(labels) - 2}"


def build_analytics_dataframe(step_analytics: list[dict]) -> list[dict]:
    """Convert step analytics into flat rows suitable for a DataFrame."""
    has_agents = any(a.get("agent") for a in step_analytics)
    rows = []
    for a in step_analytics:
        row: dict[str, Any] = {"idx": a["index"], "role": a["role"]}
        if has_agents:
            row["agent"] = a.get("agent", "")
        row.update({
            "Duration (s)": a["duration_s"],
            "Total Tokens": a["tok_total"],
            "Tok/s": round(a["tok_per_s"]) if a["tok_per_s"] is not None else None,
            "Cache Read %": round(a["cache_ratio"] * 100, 1),
            "Fresh Input": a["non_cache_tok"],
            "Out/In Ratio": round(a["out_in_ratio"], 3) if a["out_in_ratio"] is not None else None,
            "Tool Calls": a["tool_calls"],
            "Tool Wait %": (round(a["tool_time_share"] * 100, 1)
                            if a["tool_time_share"] is not None else None),
            "Finish": _friendly_finish(a["finish"]),
            "Parts": _friendly_parts(a["part_mix"]),
            "Idle Gap (s)": a["idle_before_s"],
        })
        rows.append(row)
    return rows


def _fmt_dict_as_table(d: dict, key_header: str = "Key", val_header: str = "Count") -> str:
    """Format a dict as a markdown table."""
    if not d:
        return "*None*"
    lines = [f"| {key_header} | {val_header} |", "|---|---|"]
    for k, v in sorted(d.items(), key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {v} |")
    return "\n".join(lines)
