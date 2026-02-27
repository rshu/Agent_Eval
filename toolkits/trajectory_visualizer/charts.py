"""Plotly chart builders for trajectory visualization."""

import statistics

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .rendering import _card_style


def build_token_chart(steps: list[dict], cumulative: bool = False) -> go.Figure:
    """Stacked bar of token breakdown over steps (non-overlapping segments).

    Segments: fresh_input (input - cache_read) + cache_read
              + net_output (output - reasoning) + reasoning = total
    """
    if not steps:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=380)
        return fig

    indices = list(range(len(steps)))
    cache_r = [s["tokens"]["cache_read"] for s in steps]
    fresh_input = [max(0, s["tokens"]["input"] - s["tokens"]["cache_read"]) for s in steps]
    reasoning_t = [s["tokens"]["reasoning"] for s in steps]
    net_output = [max(0, s["tokens"]["output"] - s["tokens"]["reasoning"]) for s in steps]

    if cumulative:
        for lst in (fresh_input, cache_r, net_output, reasoning_t):
            for i in range(1, len(lst)):
                lst[i] += lst[i - 1]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=indices, y=fresh_input, name="Fresh Input",
                         marker_color="#3b82f6"))
    fig.add_trace(go.Bar(x=indices, y=cache_r, name="Cache Read",
                         marker_color="#6ee7b7"))
    fig.add_trace(go.Bar(x=indices, y=net_output, name="Output",
                         marker_color="#f59e0b"))
    fig.add_trace(go.Bar(x=indices, y=reasoning_t, name="Reasoning",
                         marker_color="#8b5cf6"))

    fig.update_layout(
        barmode="stack",
        title="Token Usage by Step" + (" (Cumulative)" if cumulative else ""),
        xaxis_title="Step",
        yaxis_title="Tokens",
        template="plotly_white",
        height=380,
        margin=dict(t=50, b=40, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    return fig


def build_duration_chart(steps: list[dict]) -> go.Figure:
    """Bar chart of step durations with average line."""
    if not steps:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=380)
        return fig

    indices = list(range(len(steps)))
    durations = [s["duration"] if s["duration"] is not None else 0 for s in steps]
    colors = []
    for s in steps:
        _, border, _ = _card_style(s)
        colors.append(border)

    avg_d = sum(durations) / len(durations) if durations else 0

    fig = go.Figure()
    fig.add_trace(go.Bar(x=indices, y=durations, name="Duration", marker_color=colors,
                         showlegend=False))
    fig.add_hline(y=avg_d, line_dash="dash", line_color="#dc2626",
                  annotation_text=f"Avg: {avg_d:.1f}s")
    fig.update_layout(
        title="Step Duration",
        xaxis_title="Step",
        yaxis_title="Seconds",
        template="plotly_white",
        height=380,
        margin=dict(t=50, b=40, l=60, r=20),
    )
    return fig


def build_tool_chart(steps: list[dict]) -> go.Figure:
    """Horizontal bar chart of tool call frequency by name."""
    breakdown: dict[str, int] = {}
    for s in steps:
        for tc in s["tool_calls"]:
            name = tc["tool_name"]
            breakdown[name] = breakdown.get(name, 0) + 1

    if not breakdown:
        fig = go.Figure()
        fig.add_annotation(text="No tool calls", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(template="plotly_white", height=300)
        return fig

    sorted_items = sorted(breakdown.items(), key=lambda x: x[1])
    names = [x[0] for x in sorted_items]
    counts = [x[1] for x in sorted_items]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=names, x=counts, orientation="h", marker_color="#6366f1",
        text=counts, textposition="outside",
    ))
    fig.update_layout(
        title="Tool Call Frequency",
        xaxis_title="Count",
        template="plotly_white",
        height=max(250, 50 * len(names)),
        margin=dict(l=max(120, max(len(n) for n in names) * 8), r=40, t=50, b=40),
    )
    return fig


def build_cache_ratio_chart(rows: list[dict]) -> go.Figure:
    """Bar chart of cache-read ratio (%) per step."""
    if not rows:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=320)
        return fig

    indices = [r["index"] for r in rows]
    ratios = [r["cache_ratio"] * 100 for r in rows]
    colors = ["#92400e" if r["role"] == "assistant" else "#1e40af" for r in rows]
    avg_ratio = statistics.mean(ratios) if ratios else 0

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=indices,
        y=ratios,
        marker_color=colors,
        name="Cache ratio",
        hovertemplate="Step %{x}<br>Cache ratio: %{y:.1f}%<extra></extra>",
    ))
    fig.add_hline(y=avg_ratio, line_dash="dash", line_color="#dc2626",
                  annotation_text=f"Avg: {avg_ratio:.1f}%")
    fig.update_layout(
        title="Cache-Read Ratio by Step",
        xaxis_title="Step",
        yaxis_title="Cache ratio (%)",
        template="plotly_white",
        height=320,
        margin=dict(t=50, b=40, l=60, r=20),
    )
    return fig


def build_efficiency_chart(rows: list[dict]) -> go.Figure:
    """Tokens/sec and tool-wait share per step (behavior efficiency view)."""
    if not rows:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=340)
        return fig

    indices = [r["index"] for r in rows]
    tok_s = [r["tokens_per_sec"] for r in rows]
    noncache_s = [r["non_cache_per_sec"] for r in rows]
    tool_wait_pct = [r["tool_time_share"] * 100 for r in rows]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=indices,
            y=tok_s,
            mode="lines+markers",
            name="Tokens/s",
            line=dict(color="#2563eb", width=2),
            marker=dict(size=6),
            hovertemplate="Step %{x}<br>Tokens/s: %{y:.1f}<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=indices,
            y=noncache_s,
            mode="lines+markers",
            name="Non-cache tok/s",
            line=dict(color="#059669", width=2, dash="dot"),
            marker=dict(size=5),
            hovertemplate="Step %{x}<br>Non-cache tok/s: %{y:.1f}<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(
            x=indices,
            y=tool_wait_pct,
            name="Tool-wait %",
            marker_color="#f59e0b",
            opacity=0.28,
            hovertemplate="Step %{x}<br>Tool-wait: %{y:.2f}%<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        title="Per-Step Efficiency (Throughput vs Tool Wait)",
        template="plotly_white",
        height=340,
        margin=dict(t=50, b=40, l=60, r=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    fig.update_xaxes(title_text="Step")
    fig.update_yaxes(title_text="Tokens / second", secondary_y=False)
    fig.update_yaxes(title_text="Tool-wait (%)", secondary_y=True)
    return fig


def build_analytics_heatmap(
    analytics: list[dict], phases: list[dict] | None = None,
) -> go.Figure:
    """Heatmap of per-step metrics normalized 0\u20131 per row."""
    if not analytics:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=300)
        return fig

    metric_keys = [
        "cache_ratio", "tool_time_share", "tok_per_s", "out_in_ratio",
        "non_cache_tok", "idle_before_s",
    ]
    labels = [
        "Cache Ratio", "Tool Time Share", "Tok/s", "Output/Input",
        "Fresh Input Tok", "Idle Gap (s)",
    ]

    z: list[list[float]] = []
    hover: list[list[str]] = []
    for mk, lab in zip(metric_keys, labels):
        row_raw = [a.get(mk) or 0 for a in analytics]
        max_v = max(row_raw) if row_raw else 1
        if max_v == 0:
            max_v = 1
        z.append([v / max_v for v in row_raw])

        row_h: list[str] = []
        for a in analytics:
            v = a.get(mk)
            if v is None:
                row_h.append(f"Step {a['index']} ({a['role']})<br>{lab}: N/A")
            elif mk in ("cache_ratio", "tool_time_share"):
                row_h.append(
                    f"Step {a['index']} ({a['role']})<br>{lab}: {v * 100:.1f}%")
            elif mk in ("tok_per_s", "non_cache_tok"):
                row_h.append(
                    f"Step {a['index']} ({a['role']})<br>{lab}: {v:,.0f}")
            elif mk == "idle_before_s":
                row_h.append(
                    f"Step {a['index']} ({a['role']})<br>{lab}: {v:.2f}s")
            else:
                row_h.append(
                    f"Step {a['index']} ({a['role']})<br>{lab}: {v:.3f}")
        hover.append(row_h)

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=[str(a["index"]) for a in analytics],
        y=labels,
        hovertext=hover,
        hoverinfo="text",
        colorscale="YlOrRd",
        showscale=True,
    ))

    if phases:
        for phase in phases:
            if phase["start_idx"] > 0:
                fig.add_vline(
                    x=phase["start_idx"] - 0.5,
                    line_dash="dash", line_color="#3b82f6", line_width=2,
                    annotation_text=phase["name"],
                    annotation_position="top",
                )

    fig.update_layout(
        title="Behavioral Heatmap (normalized per metric)",
        xaxis_title="Step",
        template="plotly_white",
        height=360,
        margin=dict(t=50, b=40, l=120, r=20),
    )
    return fig


def build_phase_chart(
    phases: list[dict], analytics: list[dict],
) -> go.Figure:
    """Stacked horizontal bar showing phases proportional to step count."""
    if not phases:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=200)
        return fig

    colors = {
        "Boot": "#ef4444", "Steady": "#3b82f6",
        "Closeout": "#f59e0b", "Full Run": "#6b7280",
    }

    fig = go.Figure()
    for p in phases:
        width = p["end_idx"] - p["start_idx"] + 1
        fig.add_trace(go.Bar(
            y=["Phase"], x=[width], orientation="h",
            name=p["name"],
            marker_color=colors.get(p["name"], "#6b7280"),
            text=(f"{p['name']}<br>"
                  f"{p['token_share']}% tok, {p['runtime_share']}% time"),
            textposition="inside",
            hovertext=(
                f"{p['name']}: idx {p['start_idx']}\u2013{p['end_idx']}, "
                f"{p['token_share']}% tokens, {p['runtime_share']}% runtime"),
            hoverinfo="text",
        ))

    fig.update_layout(
        barmode="stack",
        title="Phase Timeline",
        xaxis_title="Steps",
        template="plotly_white",
        height=200,
        margin=dict(t=50, b=40, l=60, r=20),
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="center", x=0.5),
    )
    return fig


def build_cost_chart(rows: list[dict]) -> go.Figure:
    """Per-step cost bar + cumulative cost line (dual-axis)."""
    if not rows:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=340)
        return fig

    indices = [r["index"] for r in rows]
    costs = [r.get("cost", 0) or 0 for r in rows]

    if not any(c > 0 for c in costs):
        fig = go.Figure()
        fig.add_annotation(text="No cost data", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(template="plotly_white", height=340)
        return fig

    cum_cost = []
    running = 0.0
    for c in costs:
        running += c
        cum_cost.append(round(running, 6))

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=indices, y=costs, name="Step Cost",
            marker_color="#f59e0b", opacity=0.6,
            hovertemplate="Step %{x}<br>Cost: $%{y:.4f}<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=indices, y=cum_cost, name="Cumulative",
            mode="lines+markers",
            line=dict(color="#dc2626", width=2),
            marker=dict(size=5),
            hovertemplate="Step %{x}<br>Total: $%{y:.4f}<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        title="Cost per Step",
        template="plotly_white",
        height=340,
        margin=dict(t=50, b=40, l=60, r=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    fig.update_yaxes(title_text="Step Cost ($)", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative ($)", secondary_y=True)
    fig.update_xaxes(title_text="Step")
    return fig


def build_context_growth_chart(rows: list[dict]) -> go.Figure:
    """Cumulative input tokens (context pressure) with cache-read overlay."""
    if not rows:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=340)
        return fig

    indices = [r["index"] for r in rows]
    cum_input = []
    cum_fresh = []
    cum_cache = []
    ri, rf, rc = 0, 0, 0
    for r in rows:
        ri += r.get("tokens_input", 0)
        cache_read = r.get("cache_read", 0)
        rf += max(0, r.get("tokens_input", 0) - cache_read)
        rc += cache_read
        cum_input.append(ri)
        cum_fresh.append(rf)
        cum_cache.append(rc)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=indices, y=cum_input, name="Cumulative Input",
        mode="lines+markers",
        line=dict(color="#2563eb", width=2),
        marker=dict(size=5),
        fill="tozeroy", fillcolor="rgba(37,99,235,0.08)",
        hovertemplate="Step %{x}<br>Cumul. Input: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=indices, y=cum_fresh, name="Cumul. Fresh Input",
        mode="lines+markers",
        line=dict(color="#dc2626", width=2, dash="dot"),
        marker=dict(size=4),
        hovertemplate="Step %{x}<br>Cumul. Fresh: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=indices, y=cum_cache, name="Cumul. Cache Read",
        mode="lines+markers",
        line=dict(color="#059669", width=2, dash="dash"),
        marker=dict(size=4),
        hovertemplate="Step %{x}<br>Cumul. Cache: %{y:,}<extra></extra>",
    ))
    fig.update_layout(
        title="Context Growth (Cumulative Input Tokens)",
        xaxis_title="Step",
        yaxis_title="Tokens",
        template="plotly_white",
        height=340,
        margin=dict(t=50, b=40, l=60, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    return fig


def build_tool_duration_chart(steps: list[dict]) -> go.Figure:
    """Grouped bar chart of avg / p95 / max duration per tool type."""
    from collections import defaultdict

    tool_durs: dict[str, list[float]] = defaultdict(list)
    for s in steps:
        for tc in s["tool_calls"]:
            ts = tc.get("time_start")
            te = tc.get("time_end")
            if isinstance(ts, (int, float)) and isinstance(te, (int, float)) and te >= ts:
                tool_durs[tc["tool_name"]].append((te - ts) / 1000.0)

    if not tool_durs:
        fig = go.Figure()
        fig.add_annotation(text="No tool duration data", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(template="plotly_white", height=300)
        return fig

    sorted_tools = sorted(tool_durs.keys(),
                          key=lambda t: statistics.mean(tool_durs[t]), reverse=True)
    names = sorted_tools
    avgs = [round(statistics.mean(tool_durs[t]), 3) for t in names]
    p95s = [round(sorted(tool_durs[t])[min(len(tool_durs[t]) - 1,
            int(len(tool_durs[t]) * 0.95))], 3) for t in names]
    maxs = [round(max(tool_durs[t]), 3) for t in names]

    fig = go.Figure()
    fig.add_trace(go.Bar(y=names, x=avgs, name="Avg", orientation="h",
                         marker_color="#3b82f6", text=[f"{v:.2f}s" for v in avgs],
                         textposition="outside"))
    fig.add_trace(go.Bar(y=names, x=p95s, name="P95", orientation="h",
                         marker_color="#f59e0b", text=[f"{v:.2f}s" for v in p95s],
                         textposition="outside"))
    fig.add_trace(go.Bar(y=names, x=maxs, name="Max", orientation="h",
                         marker_color="#ef4444", text=[f"{v:.2f}s" for v in maxs],
                         textposition="outside"))
    fig.update_layout(
        barmode="group",
        title="Tool Duration by Type (Avg / P95 / Max)",
        xaxis_title="Seconds",
        template="plotly_white",
        height=max(280, 60 * len(names)),
        margin=dict(l=max(120, max(len(n) for n in names) * 8), r=50, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    return fig


def build_idle_gap_chart(analytics: list[dict]) -> go.Figure:
    """Bar chart of idle gaps between consecutive steps."""
    if not analytics:
        fig = go.Figure()
        fig.update_layout(template="plotly_white", height=300)
        return fig

    indices = []
    gaps = []
    for a in analytics:
        g = a.get("idle_before_s")
        if g is not None:
            indices.append(a["index"])
            gaps.append(g)

    if not gaps or not any(g > 0 for g in gaps):
        fig = go.Figure()
        fig.add_annotation(text="No idle gap data", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(template="plotly_white", height=300)
        return fig

    avg_gap = statistics.mean(gaps) if gaps else 0

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=indices, y=gaps, name="Idle Gap",
        marker_color="#6366f1",
        hovertemplate="Before step %{x}<br>Gap: %{y:.2f}s<extra></extra>",
    ))
    fig.add_hline(y=avg_gap, line_dash="dash", line_color="#dc2626",
                  annotation_text=f"Avg: {avg_gap:.2f}s")
    fig.update_layout(
        title="Idle Gaps Between Steps",
        xaxis_title="Step",
        yaxis_title="Gap (seconds)",
        template="plotly_white",
        height=300,
        margin=dict(t=50, b=40, l=60, r=20),
    )
    return fig
