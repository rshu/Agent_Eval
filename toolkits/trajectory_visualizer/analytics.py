"""Per-step analytics, phase detection, and behavioral insights."""


def compute_step_analytics(steps: list[dict]) -> list[dict]:
    """Compute derived per-message metrics aligned 1:1 with steps."""
    analytics: list[dict] = []
    for i, step in enumerate(steps):
        duration_s = step["duration"]

        # Tool time: naive sum of individual tool call durations (may overcount parallel calls)
        tool_time_ms = 0
        for tc in step["tool_calls"]:
            ts = tc.get("time_start")
            te = tc.get("time_end")
            if isinstance(ts, (int, float)) and isinstance(te, (int, float)) and te >= ts:
                tool_time_ms += (te - ts)

        tool_time_share = None
        if duration_s is not None and duration_s > 0:
            tool_time_share = round(tool_time_ms / (duration_s * 1000), 4)

        tok_total = step["tokens"]["total"]
        cache_read = step["tokens"]["cache_read"]

        tok_per_s = None
        if duration_s is not None and duration_s > 0:
            tok_per_s = round(tok_total / duration_s, 1)

        cache_ratio = round(cache_read / tok_total, 4) if tok_total > 0 else 0.0
        input_tok = step["tokens"]["input"]
        output_tok = step["tokens"]["output"]
        non_cache_tok = max(0, input_tok - cache_read)
        out_in_ratio = round(output_tok / input_tok, 4) if input_tok > 0 else None

        # Sorted unique part types
        part_types = sorted({p.get("type", "") for p in step["parts"]} - {""})
        part_mix = ",".join(part_types)

        # Idle gap from previous step
        idle_before_s = None
        if i > 0:
            prev_completed = steps[i - 1].get("time_completed_ms")
            this_created = step.get("time_created_ms")
            if (isinstance(prev_completed, (int, float))
                    and isinstance(this_created, (int, float))):
                idle_before_s = round((this_created - prev_completed) / 1000, 2)

        analytics.append({
            "index": step["index"],
            "role": step["role"],
            "agent": step.get("agent", ""),
            "model_id": step.get("model_id", ""),
            "duration_s": duration_s,
            "tool_time_ms": tool_time_ms,
            "tool_time_share": tool_time_share,
            "tok_total": tok_total,
            "tok_per_s": tok_per_s,
            "cache_ratio": cache_ratio,
            "non_cache_tok": non_cache_tok,
            "out_in_ratio": out_in_ratio,
            "tool_calls": step["tool_call_count"],
            "finish": step["finish"],
            "part_mix": part_mix,
            "idle_before_s": idle_before_s,
        })

    return analytics


def detect_phases(analytics: list[dict]) -> list[dict]:
    """Automatic phase detection based on token/runtime share heuristics."""
    if len(analytics) < 3:
        return [{"name": "Full Run", "start_idx": 0,
                 "end_idx": max(len(analytics) - 1, 0),
                 "token_share": 100.0, "runtime_share": 100.0}]

    total_tok = sum(a["tok_total"] for a in analytics)
    total_rt = sum(a["duration_s"] or 0 for a in analytics)

    if total_tok == 0 or total_rt == 0:
        return [{"name": "Full Run", "start_idx": 0,
                 "end_idx": len(analytics) - 1,
                 "token_share": 100.0, "runtime_share": 100.0}]

    def _make_phase(name: str, start: int, end: int) -> dict:
        p_tok = sum(analytics[j]["tok_total"] for j in range(start, end + 1))
        p_rt = sum(analytics[j]["duration_s"] or 0 for j in range(start, end + 1))
        return {
            "name": name, "start_idx": start, "end_idx": end,
            "token_share": round(p_tok / total_tok * 100, 1) if total_tok else 0,
            "runtime_share": round(p_rt / total_rt * 100, 1) if total_rt else 0,
        }

    # Boot: cumulative token share < 15% but cumulative runtime share > 30%
    cum_tok, cum_rt, boot_end = 0, 0.0, None
    for i, a in enumerate(analytics):
        cum_tok += a["tok_total"]
        cum_rt += (a["duration_s"] or 0)
        tok_pct = cum_tok / total_tok * 100
        rt_pct = cum_rt / total_rt * 100
        if tok_pct >= 15 or i >= len(analytics) - 2:
            boot_end = i - 1 if i > 0 and rt_pct > 30 else None
            break
        if rt_pct > 30 and tok_pct < 15:
            boot_end = i

    # Closeout: trailing steps with finish=stop/end_turn or no tools + high tokens
    avg_tok = total_tok / len(analytics)
    closeout_start = None
    for i in range(len(analytics) - 1, max(0, (boot_end or 0) + 1) - 1, -1):
        a = analytics[i]
        is_close = (
            (a["finish"] in ("stop", "end_turn") or a["tool_calls"] == 0)
            and a["tok_total"] > avg_tok
        )
        if is_close:
            closeout_start = i
        else:
            break

    phases: list[dict] = []
    steady_start = 0
    if boot_end is not None and boot_end >= 0:
        phases.append(_make_phase("Boot", 0, boot_end))
        steady_start = boot_end + 1

    steady_end = len(analytics) - 1
    if closeout_start is not None and closeout_start > steady_start:
        steady_end = closeout_start - 1
    else:
        closeout_start = None

    if steady_start <= steady_end:
        phases.append(_make_phase("Steady", steady_start, steady_end))

    if closeout_start is not None:
        phases.append(_make_phase("Closeout", closeout_start, len(analytics) - 1))

    return phases if phases else [_make_phase("Full Run", 0, len(analytics) - 1)]


def generate_insights(
    analytics: list[dict],
    phases: list[dict],
    steps: list[dict] | None = None,
) -> list[str]:
    """Generate human-readable behavioral insight strings."""
    asst = [a for a in analytics if a["role"] == "assistant"]
    if not asst:
        return ["No assistant steps found."]

    insights: list[str] = []

    # 1. Latency source
    high_tool = [(a["index"], a["tool_time_share"])
                 for a in asst
                 if a["tool_time_share"] is not None and a["tool_time_share"] > 0.5]
    if high_tool:
        ex = ", ".join(f"step {idx} ({s * 100:.0f}%)" for idx, s in high_tool[:3])
        insights.append(f"Tool-heavy steps (tool_time > 50% of step duration): {ex}.")
    else:
        insights.append(
            "Most latency is model-side, not tool-side. "
            "No step has tool_time_share > 50%.")

    # 2. Cache behavior
    crs = [a["cache_ratio"] for a in asst if a["tok_total"] > 0]
    if crs:
        sorted_crs = sorted(crs)
        med_cr = sorted_crs[len(sorted_crs) // 2]
        min_cr = sorted_crs[0]
        min_step = next(
            a["index"] for a in asst
            if a["tok_total"] > 0 and a["cache_ratio"] == min_cr)
        insights.append(
            f"Cache behavior: median cache_ratio = {med_cr * 100:.1f}%. "
            f"Lowest: step {min_step} ({min_cr * 100:.1f}%).")

    # 3. Slow-turn outliers (no tool waiting)
    slow = [(a["index"], a["duration_s"])
            for a in asst
            if a["duration_s"] is not None and a["duration_s"] > 30
            and (a["tool_time_share"] is None or a["tool_time_share"] < 0.3)]
    if slow:
        slow.sort(key=lambda x: -x[1])
        ex = ", ".join(f"step {idx} ({d:.1f}s)" for idx, d in slow[:3])
        insights.append(
            f"Slow turns without tool waiting: {ex} "
            "\u2014 likely long internal reasoning.")

    # 4. High-token turns near end
    sorted_by_tok = sorted(asst, key=lambda a: -a["tok_total"])
    top_tok = sorted_by_tok[:3]
    late = [a for a in top_tok if a["index"] >= len(analytics) * 0.7]
    if late:
        ex = ", ".join(
            f"step {a['index']} ({a['tok_total']:,} tok)" for a in late)
        insights.append(f"Largest token turns near end: {ex}.")

    # 5. Context escalation — monotonically increasing input tokens
    asst_toks = [a["tok_total"] for a in asst if a["tok_total"] > 0]
    if len(asst_toks) >= 4:
        increasing_run = 1
        max_run = 1
        for i in range(1, len(asst_toks)):
            if asst_toks[i] >= asst_toks[i - 1]:
                increasing_run += 1
                max_run = max(max_run, increasing_run)
            else:
                increasing_run = 1
        if max_run >= 4:
            ratio = asst_toks[-1] / asst_toks[0] if asst_toks[0] > 0 else 0
            insights.append(
                f"Context escalation: {max_run} consecutive steps with "
                f"non-decreasing token count. Last/first ratio: {ratio:.1f}x."
            )

    # 6. Tool retry / repetition detection
    if steps:
        from collections import Counter
        tool_targets: list[tuple[str, str]] = []
        for s in steps:
            for tc in s.get("tool_calls", []):
                name = tc.get("tool_name", "")
                inp = tc.get("input", {})
                # Build a target key from tool name + primary input field
                target = ""
                if isinstance(inp, dict):
                    for k in ("file_path", "command", "pattern", "url",
                              "path", "query", "prompt"):
                        if inp.get(k):
                            v = str(inp[k])
                            target = v[:80]
                            break
                if name and target:
                    tool_targets.append((name, target))
        if tool_targets:
            counts = Counter(tool_targets)
            repeats = [(k, v) for k, v in counts.items() if v >= 3]
            if repeats:
                repeats.sort(key=lambda x: -x[1])
                examples = []
                for (tool, target), cnt in repeats[:3]:
                    short_target = target[:40] + "..." if len(target) > 40 else target
                    examples.append(f"{tool}({short_target}) x{cnt}")
                insights.append(
                    f"Tool repetition detected: {', '.join(examples)}. "
                    "Agent may be retrying or stuck in a loop."
                )

    return insights if insights else ["Insufficient data for behavioral insights."]
