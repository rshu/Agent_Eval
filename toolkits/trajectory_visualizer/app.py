"""Gradio UI for trajectory visualization."""

import base64
import html
import json
import os
import re

import gradio as gr
import plotly.graph_objects as go

from .data import (
    load_trajectory, parse_steps, build_message_metrics, compute_metrics,
    _build_hotspots_md,
    _build_per_message_md, compute_health_verdict,
    format_session_md, format_performance_md,
    format_behavioral_md, format_output_md,
    extract_agent_info,
    wall_clock_fmt, format_banner_html,
)
from .analytics import compute_step_analytics, detect_phases, generate_insights
from .charts import (
    build_token_chart, build_duration_chart, build_tool_chart,
    build_cache_ratio_chart, build_efficiency_chart,
    build_analytics_heatmap, build_phase_chart,
    build_context_growth_chart,
    build_tool_duration_chart,
)
from .rendering import render_workflow_html, format_step_detail
from .styles import APP_CSS

_DETAIL_PLACEHOLDER = "<div id='wf-detail-content'><em>Click a step card to inspect details.</em></div>"


def _prerender_step_details(steps: list[dict]) -> str:
    """Pre-render all step details as HTML and return a base64-encoded JSON blob.

    ``format_step_detail()`` now returns styled HTML directly, so no
    markdown-it pass is needed.
    """
    details = {}
    for step in steps:
        details[str(step['index'])] = format_step_detail(step)
    b64 = base64.b64encode(json.dumps(details).encode()).decode()
    return f'<div data-b64="{b64}" style="display:none"></div>'


def _map_insights_to_sections(insights: list[str]) -> dict[str, list[str]]:
    """Categorize insight strings into Performance / Efficiency / Tools sections."""
    sections: dict[str, list[str]] = {"performance": [], "efficiency": [], "tools": []}
    perf_kw = ("slow turn", "latency", "duration", "token turn", "token count", "largest token")
    eff_kw = ("context escalation", "cache behavior", "cache read", "non-decreasing")
    tool_kw = ("tool-heavy", "tool_time", "tool repetition", "retrying", "stuck")
    for ins in insights:
        low = ins.lower()
        if any(k in low for k in perf_kw):
            sections["performance"].append(ins)
        elif any(k in low for k in eff_kw):
            sections["efficiency"].append(ins)
        elif any(k in low for k in tool_kw):
            sections["tools"].append(ins)
        else:
            sections["performance"].append(ins)
    return sections


def _compute_anomalies(metrics: dict, message_rows: list[dict]) -> list[dict]:
    """Return a list of anomaly dicts (type, step_idx, value_str) from metrics."""
    anomalies: list[dict] = []
    if not message_rows:
        return anomalies

    # Longest step by duration
    with_dur = [r for r in message_rows if r.get("duration") is not None]
    if with_dur:
        longest = max(with_dur, key=lambda r: r["duration"])
        anomalies.append({
            "type": "Slowest",
            "step_idx": longest["index"],
            "value": f"{longest['duration']:.1f}s",
        })

    # Highest token step
    if message_rows:
        highest_tok = max(message_rows, key=lambda r: r["tokens_total"])
        if highest_tok["tokens_total"] > 0:
            anomalies.append({
                "type": "Most Tokens",
                "step_idx": highest_tok["index"],
                "value": f"{highest_tok['tokens_total']:,} tok",
            })

    # Lowest cache ratio (assistant steps with tokens)
    asst_with_tok = [r for r in message_rows
                     if r.get("role") == "assistant" and r["tokens_total"] > 0]
    if asst_with_tok:
        lowest_cache = min(asst_with_tok, key=lambda r: r["cache_ratio"])
        anomalies.append({
            "type": "Lowest Cache",
            "step_idx": lowest_cache["index"],
            "value": f"{lowest_cache['cache_ratio'] * 100:.1f}%",
        })

    # Most tool calls
    with_tools = [r for r in message_rows if r["tool_calls"] > 0]
    if with_tools:
        most_tools = max(with_tools, key=lambda r: r["tool_calls"])
        anomalies.append({
            "type": "Most Tools",
            "step_idx": most_tools["index"],
            "value": f"{most_tools['tool_calls']} calls",
        })

    # Error steps
    error_steps = [r for r in message_rows if r.get("error_count", 0) > 0]
    if error_steps:
        anomalies.append({
            "type": "Errors",
            "step_idx": error_steps[0]["index"],
            "value": f"{len(error_steps)} step(s)",
        })

    return anomalies[:5]


def _build_card_jump_onclick(idx) -> str:
    """Return a JS onclick string that switches to the Workflow tab,
    scrolls step card *idx* into view, and clicks it."""
    return (
        f"(function(){{"
        f"var tabs=document.querySelectorAll('.tab-nav button');"
        f"if(tabs.length>1)tabs[1].click();"
        f"setTimeout(function(){{"
        f"var c=document.getElementById('wf-card-{idx}');"
        f"if(c){{c.scrollIntoView({{behavior:'smooth',block:'center'}});c.click();}}"
        f"}},200);"
        f"}})()"
    )


def _build_anomaly_strip_html(anomalies: list[dict]) -> str:
    """Render clickable anomaly badges with data-step-idx attributes."""
    if not anomalies:
        return ""
    badges = []
    for a in anomalies:
        idx = a["step_idx"]
        onclick = _build_card_jump_onclick(idx)
        badges.append(
            f"<span class='anomaly-badge' data-step-idx='{idx}'"
            f" onclick=\"{onclick}\" style='cursor:pointer;'>"
            f"{html.escape(a['type'])}: #{idx} ({html.escape(a['value'])})"
            f"</span>"
        )
    return "<div class='anomaly-strip'>" + "".join(badges) + "</div>"



_STEP_REF_RE = re.compile(r"\bstep (\d+)\b")


def _linkify_step_refs(escaped_text: str) -> str:
    """Replace 'step N' in html-escaped text with clickable spans that navigate to that workflow card."""
    def _repl(m: re.Match) -> str:
        idx = m.group(1)
        onclick = _build_card_jump_onclick(idx)
        return (
            f"<span class='insight-step-link' onclick=\"{onclick}\">"
            f"step {idx}</span>"
        )
    return _STEP_REF_RE.sub(_repl, escaped_text)


def _build_insight_callout_html(insights: list[str], max_items: int = 2) -> str:
    """Render up to *max_items* insight callouts as styled HTML."""
    if not insights:
        return ""
    items = []
    for ins in insights[:max_items]:
        text_html = _linkify_step_refs(html.escape(ins))
        items.append(
            f"<div class='insight-callout'>"
            f"<span class='insight-icon'>&#9432;</span> "
            f"<span class='insight-text'>{text_html}</span>"
            f"</div>"
        )
    return "".join(items)



def _build_overview_kpi_html(metrics: dict, wall_fmt: str,
                             verdicts: list[dict] | None = None) -> str:
    """Build at-a-glance KPI card strip for Overview tab.

    When *verdicts* is provided, matching KPI cards get a colored left border
    and a tooltip with the verdict detail string.
    """
    # Build verdict lookup: metric label -> (status, detail)
    _verdict_map: dict[str, tuple[str, str]] = {}
    if verdicts:
        # Map verdict metric names to KPI card labels
        _metric_to_kpi = {
            "Cache Efficiency": "Cache Read %",
            "Tool Success": "Tool Success",
            "Throughput": "Tokens",
            "Token Efficiency": "Tokens",
            "Errors": "Steps",
        }
        for v in verdicts:
            kpi_label = _metric_to_kpi.get(v["metric"], "")
            if kpi_label:
                _verdict_map[kpi_label] = (v["status"], v["detail"])

    _status_colors = {
        "good": "#059669",
        "warn": "#d97706",
        "bad": "#dc2626",
    }

    cards = [
        ("Steps", f"{metrics.get('total_steps', 0):,}",
         f"{metrics.get('assistant_steps', 0)} assistant"),
        ("Wall-Clock", wall_fmt,
         f"P95 {metrics.get('p95_duration', 0)}s"),
        ("Tokens", f"{metrics.get('tokens', {}).get('total', 0):,}",
         f"{metrics.get('tokens_per_second', 0):,} tok/s"),
        ("Tool Success", f"{metrics.get('tool_success_rate', 0)}%",
         f"{metrics.get('tool_call_count', 0):,} calls"),
        ("Cache Read %", f"{metrics.get('avg_cache_ratio', 0)}%",
         f"{metrics.get('cache_dominant_steps', 0)} dominant steps"),
        ("Fresh Input", f"{metrics.get('non_cache_ratio', 0)}%",
         f"{metrics.get('non_cache_tokens', 0):,} tokens"),
    ]
    card_html = []
    for label, value, sub in cards:
        verdict_info = _verdict_map.get(label)
        extra_style = ""
        title_attr = ""
        data_attr = ""
        if verdict_info:
            status, detail = verdict_info
            border_color = _status_colors.get(status, "#6b7280")
            extra_style = f" style='border-left:4px solid {border_color};'"
            title_attr = f" title='{html.escape(detail)}'"
            data_attr = f" data-status='{html.escape(status)}'"
        card_html.append(
            f"<div class='ov-kpi-card'{extra_style}{title_attr}{data_attr}>"
            f"<div class='ov-kpi-label'>{html.escape(str(label))}</div>"
            f"<div class='ov-kpi-value'>{html.escape(str(value))}</div>"
            f"<div class='ov-kpi-sub'>{html.escape(str(sub))}</div>"
            "</div>"
        )
    return "<div class='ov-kpi-grid'>" + "".join(card_html) + "</div>"


def build_ui() -> gr.Blocks:
    """Build the full Gradio Blocks UI."""

    with gr.Blocks(title="Trajectory Insight Finder") as app:
        # Per-session state via gr.State
        state_steps = gr.State([])

        gr.Markdown("# Trajectory Insight Finder\nLoad a trajectory JSON to inspect agent execution steps, token usage, and tool calls.")

        # -- File selection row (centered, compact) --
        with gr.Row(equal_height=True):
            gr.Column(scale=1)  # left spacer
            with gr.Column(scale=2, min_width=300):
                with gr.Row(equal_height=True):
                    file_upload = gr.File(
                        label="Upload trajectory JSON",
                        file_types=[".json"],
                        scale=3,
                    )
                    load_btn = gr.Button("Load", variant="primary", scale=0, min_width=60)
            gr.Column(scale=1)  # right spacer

        # Summary banner (appears after load)
        summary_banner = gr.HTML("", elem_classes=["summary-banner"])

        # Anomaly strip (clickable badges for notable steps)
        anomaly_strip_html = gr.HTML("")

        # -- Tabs --
        with gr.Tabs():
            # ===== Overview Tab (unified — includes former Analytics content) =====
            with gr.TabItem("Overview"):
                overview_kpi_html = gr.HTML("", elem_classes=["overview-kpi-strip"])

                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=260):
                        meta_md = gr.Markdown(
                            "*Select a trajectory file and click Load.*",
                            elem_classes=["overview-card"],
                        )
                        analytics_phase_md = gr.Markdown("", elem_classes=["overview-card"])
                    with gr.Column(scale=2, min_width=400):
                        output_md = gr.Markdown("", elem_classes=["overview-card"])

                with gr.Accordion("Performance — Token consumption, step latency, and phase timeline",
                                 open=True, elem_classes=["per-message-acc"]):
                    metrics_md = gr.Markdown("", elem_classes=["overview-card"])
                    perf_insights_html = gr.HTML("")
                    with gr.Row():
                        chart_toggle = gr.Radio(
                            choices=["Per-Step", "Cumulative"],
                            value="Per-Step",
                            label="Token chart mode",
                            scale=1,
                            elem_classes=["chart-control"],
                        )
                    with gr.Row(equal_height=True):
                        token_chart = gr.Plot(label="Token Usage")
                        duration_chart = gr.Plot(label="Step Duration")
                    with gr.Row(equal_height=True):
                        analytics_phase_chart = gr.Plot(label="Phase Timeline")

                with gr.Accordion("Efficiency — Context growth, cache behavior, and behavioral heatmap",
                                 open=False, elem_classes=["per-message-acc"]):
                    with gr.Row(equal_height=True):
                        context_growth_chart = gr.Plot(label="Context Growth")
                        analytics_heatmap = gr.Plot(label="Behavioral Heatmap")
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, min_width=400):
                            cache_chart = gr.Plot(label="Cache Read %")
                        with gr.Column(scale=1, min_width=200):
                            eff_insights_html = gr.HTML("")

                with gr.Accordion("Tools — Tool usage, throughput, duration, and idle gaps",
                                 open=False, elem_classes=["per-message-acc"]):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=3, min_width=400):
                            behavior_md = gr.Markdown("", elem_classes=["overview-card"])
                        with gr.Column(scale=1, min_width=200):
                            tools_insights_html = gr.HTML("")
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, min_width=560):
                            efficiency_chart = gr.Plot(label="Per-Step Efficiency")
                        with gr.Column(scale=1, min_width=340):
                            tool_chart = gr.Plot(label="Tool Call Frequency")
                    with gr.Row(equal_height=True):
                        tool_duration_chart = gr.Plot(label="Tool Duration by Type")

                with gr.Accordion("Per-Step Deep Dive", open=False, elem_classes=["per-message-acc"]):
                    hotspots_md = gr.Markdown("", elem_classes=["overview-card"])
                    per_message_md = gr.Markdown("", elem_classes=["overview-card"])

            # ===== Workflow Tab =====
            with gr.TabItem("Workflow"):
                with gr.Row(equal_height=True):
                    wf_filter_checks = gr.CheckboxGroup(
                        choices=["Assistant", "User", "Tool Calls", "Errors", "Reasoning"],
                        value=["Assistant", "User", "Tool Calls", "Errors", "Reasoning"],
                        label="Show steps",
                        scale=3,
                    )
                    wf_search = gr.Textbox(
                        label="Search", placeholder="Filter by keyword...",
                        scale=1,
                    )
                wf_count_html = gr.HTML("")
                with gr.Row(equal_height=False):
                    with gr.Column(scale=3, min_width=400):
                        workflow_html = gr.HTML(
                            "<div style='padding:3em;color:#9ca3af;text-align:center;"
                            "font-size:15px;'>Load a trajectory to see the step flow.</div>",
                            js_on_load="""
                            element.addEventListener('click', function(e) {
                                var card = e.target.closest('.wf-card');
                                if (!card) return;
                                element.querySelectorAll('.wf-card').forEach(function(c) {
                                    c.classList.remove('wf-active');
                                });
                                card.classList.add('wf-active');
                                var idx = card.dataset.stepIdx;
                                var storeEl = document.querySelector('#wf-detail-store [data-b64]');
                                var target = document.getElementById('wf-detail-content');
                                if (!storeEl) { console.warn('wf-click: detail store not found'); return; }
                                if (!target) { console.warn('wf-click: detail panel not found'); return; }
                                try {
                                    var details = JSON.parse(atob(storeEl.dataset.b64));
                                    if (details[idx] != null) {
                                        target.innerHTML = details[idx];
                                    }
                                } catch(ex) { console.error('wf-click:', ex); }
                            });
                            """,
                        )
                    with gr.Column(scale=2, min_width=300, elem_classes=["detail-panel"]):
                        detail_html = gr.HTML(
                            _DETAIL_PLACEHOLDER,
                            elem_id="wf-detail-panel",
                        )
                detail_store = gr.HTML("", elem_id="wf-detail-store")

            # ===== Raw Data Tab =====
            with gr.TabItem("Raw Data"):
                raw_json = gr.Code(
                    label="Full trajectory JSON",
                    language="json",
                    value="",
                    max_lines=50,
                )

        # -- Callbacks --

        _empty_fig = go.Figure()
        _empty_fig.update_layout(template="plotly_white", height=380)

        def _empty_result(banner="", detail="*No data*"):
            """Return the empty outputs tuple for error states."""
            f = _empty_fig
            return (
                [],              # state_steps
                banner,          # summary_banner
                "",              # anomaly_strip_html
                "",              # overview_kpi_html
                detail,          # meta_md
                "",              # output_md
                "",              # analytics_phase_md
                "",              # metrics_md
                "",              # perf_insights_html
                f, f,            # token_chart, duration_chart
                f,               # analytics_phase_chart
                "",              # eff_insights_html
                f, f,            # context_growth_chart, analytics_heatmap
                f,               # cache_chart
                "",              # behavior_md
                "",              # tools_insights_html
                f, f,            # efficiency_chart, tool_chart
                f,               # tool_duration_chart
                "",              # hotspots_md
                "",              # per_message_md
                "",              # wf_count_html
                "<div></div>",   # workflow_html
                "",              # detail_store
                _DETAIL_PLACEHOLDER,  # detail_html
                "",              # raw_json
            )

        def do_load(upload_obj):
            """Load trajectory from uploaded file."""
            file_path = None
            if upload_obj is not None:
                file_path = upload_obj if isinstance(upload_obj, str) else upload_obj.name

            if not file_path or not os.path.isfile(file_path):
                return _empty_result(detail="*No file selected or file not found.*")

            raw = load_trajectory(file_path)
            if "_error" in raw:
                err_banner = f"<p style='color:#dc2626;'>Error: {html.escape(raw['_error'])}</p>"
                return _empty_result(banner=err_banner, detail="*Error loading file.*")

            steps = parse_steps(raw)
            message_rows = build_message_metrics(steps)
            metrics = compute_metrics(steps, raw, message_rows=message_rows)
            _d = lambda k: raw.get(k, {}) if isinstance(raw.get(k), dict) else {}
            md, timing, outp = _d("metadata"), _d("timing"), _d("output")
            session_raw, retry = _d("session_raw"), _d("retry")

            model_id, provider_id, agent_id = extract_agent_info(steps)
            _, wfmt = wall_clock_fmt(metrics)
            banner = format_banner_html(os.path.basename(file_path), metrics, wfmt)
            # Verdicts computed early so KPI cards can show status indicators
            verdicts = compute_health_verdict(metrics,
                                              compute_step_analytics(steps) if steps else [])
            kpi_html = _build_overview_kpi_html(metrics, wfmt, verdicts=verdicts)

            # -- Overview markdown sections --
            meta_text = format_session_md(
                timing, md, retry,
                model_id=model_id, provider_id=provider_id, agent_id=agent_id,
            )
            metrics_text = format_performance_md(metrics, wfmt)
            behavior_text = format_behavioral_md(metrics)
            hotspots_text = _build_hotspots_md(message_rows)
            per_message_text = _build_per_message_md(message_rows)
            summary_info = session_raw.get("summary", {})
            outp_text = format_output_md(outp, md, summary_info, metrics)

            # -- Workflow tab --
            wf_html = render_workflow_html(steps)
            wf_count = f"<div class='wf-count'>Showing {len(steps)} of {len(steps)} steps</div>"
            detail_store_val = _prerender_step_details(steps)

            # -- Analytics (computed before charts so annotations can use phases) --
            step_analytics = compute_step_analytics(steps)
            phases = detect_phases(step_analytics)

            # -- Charts --
            tok_fig = build_token_chart(steps, cumulative=False, phases=phases)
            dur_fig = build_duration_chart(steps, phases=phases)
            tl_fig = build_tool_chart(steps)
            cache_fig = build_cache_ratio_chart(message_rows, phases=phases)
            eff_fig = build_efficiency_chart(message_rows, phases=phases)
            ctx_fig = build_context_growth_chart(message_rows, phases=phases)
            insights_list = generate_insights(step_analytics, phases, steps=steps)
            tool_dur_fig = build_tool_duration_chart(steps)

            phase_md = "### Phase Summary\n\n" + "\n\n".join(
                f"**{p['name']}** (idx {p['start_idx']}\u2013{p['end_idx']}): "
                f"{p['token_share']}% tokens, {p['runtime_share']}% time"
                for p in phases)
            heatmap_fig = build_analytics_heatmap(step_analytics, phases)
            phase_fig = build_phase_chart(phases, step_analytics)


            # -- Section insight callouts --
            section_insights = _map_insights_to_sections(insights_list)
            perf_callout = _build_insight_callout_html(section_insights["performance"])
            eff_callout = _build_insight_callout_html(section_insights["efficiency"])
            tools_callout = _build_insight_callout_html(section_insights["tools"])

            # -- Anomaly strip --
            anomalies = _compute_anomalies(metrics, message_rows)
            anomaly_html = _build_anomaly_strip_html(anomalies)

            # -- Raw data --
            raw_str = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
            if len(raw_str) > 500_000:
                raw_str = raw_str[:500_000] + "\n\n... (truncated at 500KB)"

            return (
                steps,              # state_steps
                banner,             # summary_banner
                anomaly_html,       # anomaly_strip_html
                kpi_html,           # overview_kpi_html
                meta_text,          # meta_md
                outp_text,          # output_md
                phase_md,           # analytics_phase_md
                metrics_text,       # metrics_md
                perf_callout,       # perf_insights_html
                tok_fig,            # token_chart
                dur_fig,            # duration_chart
                phase_fig,          # analytics_phase_chart
                eff_callout,        # eff_insights_html
                ctx_fig,            # context_growth_chart
                heatmap_fig,        # analytics_heatmap
                cache_fig,          # cache_chart
                behavior_text,      # behavior_md
                tools_callout,      # tools_insights_html
                eff_fig,            # efficiency_chart
                tl_fig,             # tool_chart
                tool_dur_fig,       # tool_duration_chart
                hotspots_text,      # hotspots_md
                per_message_text,   # per_message_md
                wf_count,           # wf_count_html
                wf_html,            # workflow_html
                detail_store_val,   # detail_store
                _DETAIL_PLACEHOLDER,  # detail_html
                raw_str,            # raw_json
            )

        all_outputs = [
            state_steps,              # 0
            summary_banner,           # 1
            anomaly_strip_html,       # 2
            overview_kpi_html,        # 3
            meta_md,                  # 4
            output_md,                # 5
            analytics_phase_md,       # 6
            metrics_md,               # 7
            perf_insights_html,       # 8
            token_chart,              # 9
            duration_chart,           # 10
            analytics_phase_chart,    # 11
            eff_insights_html,        # 12
            context_growth_chart,     # 13
            analytics_heatmap,        # 14
            cache_chart,              # 15
            behavior_md,              # 16
            tools_insights_html,      # 17
            efficiency_chart,         # 18
            tool_chart,               # 19
            tool_duration_chart,      # 20
            hotspots_md,              # 21
            per_message_md,           # 22
            wf_count_html,            # 25
            workflow_html,            # 26
            detail_store,             # 27
            detail_html,              # 29
            raw_json,                 # 30
        ]

        load_btn.click(
            fn=do_load,
            inputs=[file_upload],
            outputs=all_outputs,
        )

        # -- Chart toggle callback --
        def on_chart_toggle(mode, steps):
            sa = compute_step_analytics(steps or [])
            ph = detect_phases(sa) if sa else []
            return build_token_chart(steps or [], cumulative=(mode == "Cumulative"),
                                     phases=ph)

        chart_toggle.change(
            fn=on_chart_toggle,
            inputs=[chart_toggle, state_steps],
            outputs=[token_chart],
        )

        # -- Workflow filter callback --
        def _filter_workflow_steps(steps, active_filters, keyword):
            """Return indices of steps matching filters and keyword."""
            if not steps:
                return []
            keyword = (keyword or "").strip().lower()
            active = set(active_filters)
            # Separate role filters (gate) from content filters (narrow)
            role_filters = active & {"Assistant", "User"}
            content_filters = active & {"Tool Calls", "Errors", "Reasoning"}
            filtered = []
            for i, s in enumerate(steps):
                role = s["role"]
                # Role gate: step must match a checked role
                role_ok = (
                    ("Assistant" in role_filters and role == "assistant")
                    or ("User" in role_filters and role == "user")
                )
                if not role_ok:
                    continue
                # Content gate: only applies to steps that have filterable content
                if content_filters:
                    has_content = (s["tool_call_count"] > 0
                                   or s["error_count"] > 0
                                   or s["has_reasoning"])
                    if has_content:
                        content_ok = (
                            ("Tool Calls" in content_filters and s["tool_call_count"] > 0)
                            or ("Errors" in content_filters and s["error_count"] > 0)
                            or ("Reasoning" in content_filters and s["has_reasoning"])
                        )
                        if not content_ok:
                            continue
                # Keyword match
                if keyword:
                    text = (s.get("text_preview") or "").lower()
                    tool_names = " ".join(tc["tool_name"] for tc in s.get("tool_calls", [])).lower()
                    tool_args = " ".join(
                        str(tc.get("input", "")) for tc in s.get("tool_calls", [])
                    ).lower()
                    if keyword not in text and keyword not in tool_names and keyword not in tool_args:
                        continue
                filtered.append(i)
            return filtered

        def do_filter_workflow(steps, active_filters, keyword):
            """Re-render workflow HTML with filters applied."""
            if not steps:
                return (
                    "<div style='padding:3em;color:#9ca3af;text-align:center;"
                    "font-size:15px;'>Load a trajectory to see the step flow.</div>",
                    "",
                )
            if not active_filters:
                return (
                    "<div style='padding:2em;color:#9ca3af;text-align:center;'>"
                    "No filters selected &mdash; check at least one filter to see steps.</div>",
                    "<div class='wf-count'>Showing 0 of "
                    f"{len(steps)} steps</div>",
                )
            indices = _filter_workflow_steps(steps, active_filters, keyword)
            filtered_steps = [steps[i] for i in indices]
            wf_html = render_workflow_html(filtered_steps)
            count_html = (
                f"<div class='wf-count'>Showing {len(filtered_steps)} of "
                f"{len(steps)} steps</div>"
            )
            return wf_html, count_html

        wf_filter_checks.change(
            fn=do_filter_workflow,
            inputs=[state_steps, wf_filter_checks, wf_search],
            outputs=[workflow_html, wf_count_html],
        )
        wf_search.change(
            fn=do_filter_workflow,
            inputs=[state_steps, wf_filter_checks, wf_search],
            outputs=[workflow_html, wf_count_html],
        )

    return app
