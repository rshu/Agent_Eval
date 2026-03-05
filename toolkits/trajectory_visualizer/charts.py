"""Plotly chart builders for trajectory visualization."""

import statistics

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .data import infer_non_cache_input


def _plotly_step_color(step: dict) -> str:
    """Return a hex bar-color for a step (Plotly can't use CSS variables)."""
    if step["error_count"] > 0:
        return "#dc2626"
    if step.get("finish") in ("stop", "end_turn"):
        return "#059669"
    if step["tool_call_count"] > 0:
        return "#d97706"
    if step["has_reasoning"] and step["role"] == "assistant":
        return "#7c3aed"
    return {"user": "#1e40af", "assistant": "#92400e"}.get(step["role"], "#6b7280")


# -- Layout helpers -------------------------------------------------------

_TPL = "plotly_white"


def _empty_figure(height: int = 380, message: str | None = None) -> go.Figure:
    """Return a blank Plotly figure, optionally with a centered message."""
    fig = go.Figure()
    if message:
        fig.add_annotation(text=message, xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_size=16)
    fig.update_layout(template=_TPL, height=height)
    return fig


def _apply_chart_layout(fig: go.Figure, title: str,
                         xaxis: str | None = None, yaxis: str | None = None,
                         height: int = 380, **kwargs) -> None:
    """Apply standard chart layout (template + margins)."""
    layout = dict(
        title=title,
        template=_TPL,
        height=height,
        margin=dict(t=50, b=40, l=60, r=20),
    )
    if xaxis:
        layout["xaxis_title"] = xaxis
    if yaxis:
        layout["yaxis_title"] = yaxis
    layout.update(kwargs)
    fig.update_layout(**layout)


def _add_legend_hint(fig: go.Figure) -> None:
    """Add a subtle 'click legend to toggle' hint at the bottom-right."""
    fig.add_annotation(
        text="Click legend items to show/hide series",
        xref="paper", yref="paper", x=1.0, y=-0.12,
        showarrow=False, font=dict(size=9, color="#9ca3af"),
        xanchor="right",
    )


# -- Annotation utilities ------------------------------------------------

_PHASE_COLORS = {"Boot": "rgba(59,130,246,0.10)", "Steady": "rgba(16,185,129,0.08)",
                 "Closeout": "rgba(245,158,11,0.10)", "Full Run": "rgba(107,114,128,0.06)"}
_PHASE_LINE_COLORS = {"Boot": "#3b82f6", "Steady": "#10b981",
                      "Closeout": "#f59e0b", "Full Run": "#6b7280"}


def _detect_outliers(values: list[float], threshold: float = 2.0) -> list[tuple[int, float, str]]:
    """Return (index, value, label) for values exceeding *threshold* σ from mean.

    Returns empty list if fewer than 10 values or no outliers found.
    """
    if len(values) < 10:
        return []
    clean = [v for v in values if v is not None and v > 0]
    if len(clean) < 5:
        return []
    mean = statistics.mean(clean)
    stdev = statistics.stdev(clean) if len(clean) > 1 else 0
    if stdev == 0:
        return []
    outliers = []
    for i, v in enumerate(values):
        if v is not None and v > 0 and (v - mean) > threshold * stdev:
            outliers.append((i, v, "spike"))
    return outliers


def add_phase_overlays(fig: go.Figure, phases: list[dict] | None,
                       step_count: int) -> None:
    """Draw semi-transparent vertical regions for each detected phase."""
    if not phases or len(phases) <= 1:
        return
    for p in phases:
        color = _PHASE_COLORS.get(p["name"], "rgba(107,114,128,0.06)")
        label_color = _PHASE_LINE_COLORS.get(p["name"], "#6b7280")
        fig.add_vrect(
            x0=p["start_idx"] - 0.5, x1=p["end_idx"] + 0.5,
            fillcolor=color, layer="below", line_width=0,
        )
        fig.add_annotation(
            x=(p["start_idx"] + p["end_idx"]) / 2, y=1.0,
            yref="paper", text=p["name"],
            showarrow=False, font=dict(size=10, color=label_color),
            yanchor="bottom",
        )


def _add_outlier_annotations(fig: go.Figure, outliers: list[tuple[int, float, str]],
                             fmt: str = ",.0f", suffix: str = "") -> None:
    """Add annotation arrows for detected outlier points."""
    for idx, val, label in outliers[:5]:  # cap at 5 to avoid clutter
        fig.add_annotation(
            x=idx, y=val,
            text=f"{label}: {val:{fmt}}{suffix}",
            showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=1,
            arrowcolor="#dc2626", font=dict(size=9, color="#dc2626"),
            ax=0, ay=-30,
        )


# -- Chart builders -------------------------------------------------------

def build_token_chart(steps: list[dict], cumulative: bool = False,
                      phases: list[dict] | None = None) -> go.Figure:
    """Stacked bar of token breakdown over steps (non-overlapping segments).

    Segments: fresh_input + cache_read
              + net_output (output - reasoning) + reasoning = total
    """
    if not steps:
        return _empty_figure(380)

    indices = list(range(len(steps)))
    cache_r = [s["tokens"]["cache_read"] for s in steps]
    fresh_input = [
        infer_non_cache_input(
            total_tokens=s["tokens"]["total"],
            input_tokens=s["tokens"]["input"],
            output_tokens=s["tokens"]["output"],
            reasoning_tokens=s["tokens"]["reasoning"],
            cache_read_tokens=s["tokens"]["cache_read"],
        )
        for s in steps
    ]
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

    _apply_chart_layout(
        fig, "Token Usage by Step" + (" (Cumulative)" if cumulative else ""),
        xaxis="Step", yaxis="Tokens (count)", height=380,
        barmode="stack",
        legend=dict(orientation="h", yanchor="bottom", y=1.06, xanchor="center", x=0.5),
    )
    _add_legend_hint(fig)
    if not cumulative:
        totals = [s["tokens"]["total"] for s in steps]
        outliers = _detect_outliers(totals)
        _add_outlier_annotations(fig, outliers, fmt=",.0f", suffix=" tok")
    add_phase_overlays(fig, phases, len(steps))
    return fig


def build_duration_chart(steps: list[dict],
                         phases: list[dict] | None = None) -> go.Figure:
    """Bar chart of step durations with average line."""
    if not steps:
        return _empty_figure(380)

    indices = list(range(len(steps)))
    durations = [s["duration"] if s["duration"] is not None else 0 for s in steps]
    colors = [_plotly_step_color(s) for s in steps]

    avg_d = sum(durations) / len(durations) if durations else 0

    fig = go.Figure()
    fig.add_trace(go.Bar(x=indices, y=durations, name="Duration", marker_color=colors,
                         showlegend=False))
    fig.add_hline(y=avg_d, line_dash="dash", line_color="#dc2626",
                  annotation_text=f"Avg: {avg_d:.1f}s")
    _apply_chart_layout(fig, "Step Duration", xaxis="Step", yaxis="Duration (s)",
                         height=380)
    outliers = _detect_outliers(durations)
    _add_outlier_annotations(fig, outliers, fmt=".1f", suffix="s")
    add_phase_overlays(fig, phases, len(steps))
    return fig


def build_tool_chart(steps: list[dict]) -> go.Figure:
    """Horizontal bar chart of tool call frequency by name."""
    breakdown: dict[str, int] = {}
    for s in steps:
        for tc in s["tool_calls"]:
            name = tc.get("tool_name") or "(unnamed)"
            breakdown[name] = breakdown.get(name, 0) + 1

    if not breakdown:
        return _empty_figure(300, "No tool calls recorded in this trajectory.")

    sorted_items = sorted(breakdown.items(), key=lambda x: x[1])
    names = [x[0] for x in sorted_items]
    counts = [x[1] for x in sorted_items]
    # Truncate long tool names for display; show full name on hover
    display_names = [n if len(n) <= 30 else n[:27] + "..." for n in names]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=display_names, x=counts, orientation="h", marker_color="#6366f1",
        text=[str(c) for c in counts], textposition="outside",
        cliponaxis=False,
        customdata=names,
        hovertemplate="%{customdata}: %{x} call(s)<extra></extra>",
    ))
    max_label = max(len(n) for n in display_names)
    _apply_chart_layout(
        fig, "Tool Call Frequency", xaxis="Count",
        height=max(250, 50 * len(names)),
        margin=dict(l=max(140, max_label * 7 + 20), r=60, t=50, b=40),
    )
    return fig


def build_cache_ratio_chart(rows: list[dict],
                            phases: list[dict] | None = None) -> go.Figure:
    """Bar chart of cache-read ratio (%) per step."""
    if not rows:
        return _empty_figure(320)

    indices = [r["index"] for r in rows]
    ratios = [r["cache_ratio"] * 100 for r in rows]
    colors = ["#92400e" if r["role"] == "assistant" else "#1e40af" for r in rows]
    avg_ratio = statistics.mean(ratios) if ratios else 0

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=indices,
        y=ratios,
        marker_color=colors,
        name="Cache Read %",
        hovertemplate="Step %{x}<br>Cache Read: %{y:.1f}%<extra></extra>",
    ))
    fig.add_hline(y=avg_ratio, line_dash="dash", line_color="#dc2626",
                  annotation_text=f"Avg: {avg_ratio:.1f}%")
    _apply_chart_layout(fig, "Cache-Read Ratio by Step", xaxis="Step",
                         yaxis="Cache Read (%)", height=320)
    add_phase_overlays(fig, phases, len(rows))
    return fig


def build_efficiency_chart(rows: list[dict],
                           phases: list[dict] | None = None) -> go.Figure:
    """Tokens/sec and tool-wait share per step (behavior efficiency view)."""
    if not rows:
        return _empty_figure(340)

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
            name="Fresh Input tok/s",
            line=dict(color="#059669", width=2, dash="dot"),
            marker=dict(size=5),
            hovertemplate="Step %{x}<br>Fresh Input tok/s: %{y:.1f}<extra></extra>",
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
    _apply_chart_layout(
        fig,
        "Per-Step Efficiency — Left axis: tok/s · Right axis: Tool Wait %",
        height=340, margin=dict(t=65, b=40, l=60, r=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, xanchor="center", x=0.5),
    )
    _add_legend_hint(fig)
    fig.update_xaxes(title_text="Step")
    fig.update_yaxes(title_text="Throughput (tok/s)", secondary_y=False)
    fig.update_yaxes(title_text="Tool Wait (%)", secondary_y=True)
    add_phase_overlays(fig, phases, len(rows))
    return fig


def build_analytics_heatmap(
    analytics: list[dict], phases: list[dict] | None = None,
) -> go.Figure:
    """Heatmap of per-step metrics normalized 0\u20131 per row."""
    if not analytics:
        return _empty_figure(300)

    metric_keys = [
        "cache_ratio", "tool_time_share", "tok_per_s", "out_in_ratio",
        "non_cache_tok", "idle_before_s",
    ]
    labels = [
        "Cache Read %", "Tool Time Share", "Tok/s", "Out/In Ratio",
        "Fresh Input Tokens", "Idle Gap (s)",
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

    _apply_chart_layout(fig, "Behavioral Heatmap (normalized per metric)",
                         xaxis="Step", height=360,
                         margin=dict(t=50, b=40, l=120, r=20))
    return fig


def build_phase_chart(
    phases: list[dict], analytics: list[dict],
) -> go.Figure:
    """Stacked horizontal bar showing phases proportional to step count."""
    if not phases:
        return _empty_figure(200)

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

    _apply_chart_layout(
        fig, "Phase Timeline", xaxis="Steps", height=200,
        barmode="stack", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="center", x=0.5),
    )
    return fig


def build_context_growth_chart(rows: list[dict],
                               phases: list[dict] | None = None) -> go.Figure:
    """Cumulative input tokens (context pressure) with cache-read overlay."""
    if not rows:
        return _empty_figure(340)

    indices = [r["index"] for r in rows]
    cum_input = []
    cum_fresh = []
    cum_cache = []
    ri, rf, rc = 0, 0, 0
    for r in rows:
        ri += r.get("tokens_input", 0)
        cache_read = r.get("cache_read", 0)
        # rows["non_cache_tokens"] is already schema-normalized in build_message_metrics()
        rf += r.get("non_cache_tokens", 0)
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
    _apply_chart_layout(
        fig, "Context Growth (Cumulative Input Tokens)",
        xaxis="Step", yaxis="Tokens (count)", height=340,
        legend=dict(orientation="h", yanchor="bottom", y=1.06, xanchor="center", x=0.5),
    )
    _add_legend_hint(fig)
    add_phase_overlays(fig, phases, len(rows))
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
        return _empty_figure(
            300,
            "No tool duration data. Requires time_start / time_end on tool call events.",
        )

    sorted_tools = sorted(tool_durs.keys(),
                          key=lambda t: statistics.mean(tool_durs[t]), reverse=True)
    names = sorted_tools
    avgs = [round(statistics.mean(tool_durs[t]), 3) for t in names]
    p95s = [round(sorted(tool_durs[t])[min(len(tool_durs[t]) - 1,
            int(len(tool_durs[t]) * 0.95))], 3) for t in names]
    maxs = [round(max(tool_durs[t]), 3) for t in names]

    fig = go.Figure()
    display_names = [n if len(n) <= 30 else n[:27] + "..." for n in names]
    fig.add_trace(go.Bar(y=display_names, x=avgs, name="Avg (s)", orientation="h",
                         marker_color="#3b82f6", text=[f"{v:.2f}s" for v in avgs],
                         textposition="outside", cliponaxis=False))
    fig.add_trace(go.Bar(y=display_names, x=p95s, name="P95 (s)", orientation="h",
                         marker_color="#f59e0b", text=[f"{v:.2f}s" for v in p95s],
                         textposition="outside", cliponaxis=False))
    fig.add_trace(go.Bar(y=display_names, x=maxs, name="Max (s)", orientation="h",
                         marker_color="#ef4444", text=[f"{v:.2f}s" for v in maxs],
                         textposition="outside", cliponaxis=False))
    max_label = max(len(n) for n in display_names)
    _apply_chart_layout(
        fig, "Tool Duration by Type (Avg / P95 / Max)",
        xaxis="Duration (s)", height=max(280, 60 * len(names)),
        barmode="group",
        margin=dict(l=max(140, max_label * 7 + 20), r=70, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    return fig
