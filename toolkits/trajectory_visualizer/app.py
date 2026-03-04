"""Gradio UI for trajectory visualization."""

import html
import json
import os

import gradio as gr
import plotly.graph_objects as go
import pandas as pd

from .data import (
    load_trajectory, parse_steps, build_message_metrics, compute_metrics,
    discover_trajectory_files, _build_hotspots_md,
    _build_per_message_md, compute_health_verdict,
    format_session_md, format_performance_md,
    format_behavioral_md, format_output_md,
    extract_agent_info, build_analytics_dataframe,
    wall_clock_fmt, format_banner_html,
)
from .analytics import compute_step_analytics, detect_phases, generate_insights
from .charts import (
    build_token_chart, build_duration_chart, build_tool_chart,
    build_cache_ratio_chart, build_efficiency_chart,
    build_analytics_heatmap, build_phase_chart,
    build_context_growth_chart,
    build_tool_duration_chart, build_idle_gap_chart,
)
from .rendering import render_workflow_html, format_step_detail
from .styles import APP_CSS


def _map_insights_to_sections(insights: list[str]) -> dict[str, list[str]]:
    """Categorize insight strings into Performance / Efficiency / Tools sections."""
    sections: dict[str, list[str]] = {"performance": [], "efficiency": [], "tools": []}
    perf_kw = ("slow turn", "latency", "duration", "token turn", "token count", "largest token")
    eff_kw = ("context escalation", "cache behavior", "cache_ratio", "non-decreasing")
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


def _build_insight_callout_html(insights: list[str], max_items: int = 2) -> str:
    """Render up to *max_items* insight callouts as styled HTML."""
    if not insights:
        return ""
    items = []
    for ins in insights[:max_items]:
        items.append(
            f"<div class='insight-callout'>"
            f"<span class='insight-icon'>&#9432;</span> "
            f"<span class='insight-text'>{html.escape(ins)}</span>"
            f"</div>"
        )
    return "".join(items)


def _build_health_verdict_html(verdicts: list[dict]) -> str:
    """Render health verdict as a horizontal strip of color-coded badges."""
    if not verdicts:
        return ""
    status_colors = {
        "good": ("#059669", "#d1fae5", "#065f46"),
        "warn": ("#d97706", "#fef3c7", "#92400e"),
        "bad": ("#dc2626", "#fee2e2", "#991b1b"),
    }
    badges = []
    for v in verdicts:
        bg, bg_light, text_color = status_colors.get(v["status"], ("#6b7280", "#f3f4f6", "#374151"))
        detail_escaped = html.escape(v["detail"])
        badges.append(
            f"<div class='hv-badge' style='background:{bg_light};border:1px solid {bg};' title='{detail_escaped}'>"
            f"<span class='hv-dot' style='background:{bg};'></span>"
            f"<span class='hv-metric' style='color:{text_color};'>{html.escape(v['metric'])}</span>"
            f"<span class='hv-label' style='color:{text_color};'>{html.escape(v['label'])}</span>"
            f"</div>"
        )
    return "<div class='hv-strip'>" + "".join(badges) + "</div>"


def _build_overview_kpi_html(metrics: dict, wall_fmt: str) -> str:
    """Build at-a-glance KPI card strip for Overview tab."""
    cards = [
        ("Steps", f"{metrics.get('total_steps', 0):,}",
         f"{metrics.get('assistant_steps', 0)} assistant"),
        ("Wall-Clock", wall_fmt,
         f"P95 {metrics.get('p95_duration', 0)}s"),
        ("Tokens", f"{metrics.get('tokens', {}).get('total', 0):,}",
         f"{metrics.get('tokens_per_second', 0):,} tok/s"),
        ("Tool Success", f"{metrics.get('tool_success_rate', 0)}%",
         f"{metrics.get('tool_call_count', 0):,} calls"),
        ("Cache Ratio", f"{metrics.get('avg_cache_ratio', 0)}%",
         f"{metrics.get('cache_dominant_steps', 0)} dominant steps"),
        ("Non-Cache", f"{metrics.get('non_cache_ratio', 0)}%",
         f"{metrics.get('non_cache_tokens', 0):,} tokens"),
    ]
    card_html = []
    for label, value, sub in cards:
        card_html.append(
            "<div class='ov-kpi-card'>"
            f"<div class='ov-kpi-label'>{html.escape(str(label))}</div>"
            f"<div class='ov-kpi-value'>{html.escape(str(value))}</div>"
            f"<div class='ov-kpi-sub'>{html.escape(str(sub))}</div>"
            "</div>"
        )
    return "<div class='ov-kpi-grid'>" + "".join(card_html) + "</div>"


def build_ui(trajectory_dir: str) -> gr.Blocks:
    """Build the full Gradio Blocks UI."""

    discovered = discover_trajectory_files(trajectory_dir)
    choices = []
    for fp in discovered:
        try:
            rel = os.path.relpath(fp, trajectory_dir)
        except ValueError:
            rel = fp
        choices.append(rel)

    with gr.Blocks(title="Trajectory Visualizer") as app:
        # Per-session state via gr.State
        state_steps = gr.State([])

        gr.Markdown("# Trajectory Profiler & Visualizer\nLoad a trajectory JSON to inspect agent execution steps, token usage, and tool calls.")

        # -- File selection row --
        with gr.Row(equal_height=True):
            file_dropdown = gr.Dropdown(
                choices=choices,
                label="Trajectory file",
                scale=4,
                interactive=True,
                value=choices[0] if choices else None,
            )
            file_upload = gr.File(
                label="Upload JSON",
                file_types=[".json"],
                scale=2,
            )
            load_btn = gr.Button("Load", variant="primary", scale=0, min_width=60)

        # Summary banner (appears after load)
        summary_banner = gr.HTML("", elem_classes=["summary-banner"])

        # -- Tabs --
        with gr.Tabs():
            # ===== Overview Tab (unified — includes former Analytics content) =====
            with gr.TabItem("Overview"):
                overview_kpi_html = gr.HTML("", elem_classes=["overview-kpi-strip"])
                health_verdict_html = gr.HTML("")

                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=320):
                        meta_md = gr.Markdown(
                            "*Select a trajectory file and click Load.*",
                            elem_classes=["overview-card"],
                        )
                    with gr.Column(scale=1, min_width=320):
                        output_md = gr.Markdown("", elem_classes=["overview-card"])

                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=320):
                        analytics_phase_md = gr.Markdown("", elem_classes=["overview-card"])
                    with gr.Column(scale=1, min_width=320):
                        analytics_insights_md = gr.Markdown("", elem_classes=["overview-card"])

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
                    eff_insights_html = gr.HTML("")
                    with gr.Row(equal_height=True):
                        context_growth_chart = gr.Plot(label="Context Growth")
                        analytics_heatmap = gr.Plot(label="Behavioral Heatmap")
                    with gr.Row(equal_height=True):
                        cache_chart = gr.Plot(label="Cache Ratio")

                with gr.Accordion("Tools — Tool usage, throughput, duration, and idle gaps",
                                 open=False, elem_classes=["per-message-acc"]):
                    behavior_md = gr.Markdown("", elem_classes=["overview-card"])
                    tools_insights_html = gr.HTML("")
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, min_width=560):
                            efficiency_chart = gr.Plot(label="Per-Step Efficiency")
                        with gr.Column(scale=1, min_width=340):
                            tool_chart = gr.Plot(label="Tool Call Frequency")
                    with gr.Row(equal_height=True):
                        tool_duration_chart = gr.Plot(label="Tool Duration by Type")
                        idle_gap_chart = gr.Plot(label="Idle Gaps")

                with gr.Accordion("Per-Step Deep Dive", open=False, elem_classes=["per-message-acc"]):
                    hotspots_md = gr.Markdown("", elem_classes=["overview-card"])
                    analytics_table = gr.Dataframe(
                        label="Per-Step Metrics",
                        interactive=False,
                        wrap=True,
                    )
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
                                var idx = parseInt(card.dataset.stepIdx);
                                if (!isNaN(idx)) {
                                    trigger('click', {step_index: idx});
                                }
                            });
                            """,
                        )
                    with gr.Column(scale=2, min_width=300, elem_classes=["detail-panel"]):
                        detail_md = gr.Markdown("*Click a step card to inspect details.*")

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
                "",              # overview_kpi_html
                "",              # health_verdict_html
                detail,          # meta_md
                "",              # output_md
                "",              # analytics_phase_md
                "",              # analytics_insights_md
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
                f, f,            # tool_duration_chart, idle_gap_chart
                "",              # hotspots_md
                pd.DataFrame(),  # analytics_table
                "",              # per_message_md
                "",              # wf_count_html
                "<div></div>",   # workflow_html
                "*Click a step card to inspect details.*",  # detail_md
                "",              # raw_json
            )

        def do_load(dropdown_val, upload_obj):
            """Load trajectory from dropdown or upload."""
            file_path = None
            if upload_obj is not None:
                file_path = upload_obj if isinstance(upload_obj, str) else upload_obj.name
            elif dropdown_val:
                file_path = os.path.join(trajectory_dir, dropdown_val)

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
            kpi_html = _build_overview_kpi_html(metrics, wfmt)

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

            # -- Analytics (computed before charts so annotations can use phases) --
            step_analytics = compute_step_analytics(steps)
            phases = detect_phases(step_analytics)

            # -- Charts --
            tok_fig = build_token_chart(steps, cumulative=False,
                                        step_analytics=step_analytics, phases=phases)
            dur_fig = build_duration_chart(steps, step_analytics=step_analytics, phases=phases)
            tl_fig = build_tool_chart(steps)
            cache_fig = build_cache_ratio_chart(message_rows,
                                                step_analytics=step_analytics, phases=phases)
            eff_fig = build_efficiency_chart(message_rows,
                                            step_analytics=step_analytics, phases=phases)
            ctx_fig = build_context_growth_chart(message_rows,
                                                step_analytics=step_analytics, phases=phases)
            insights_list = generate_insights(step_analytics, phases, steps=steps)
            tool_dur_fig = build_tool_duration_chart(steps)
            idle_fig = build_idle_gap_chart(step_analytics)

            phase_md = "### Phase Summary\n\n" + "\n\n".join(
                f"**{p['name']}** (idx {p['start_idx']}\u2013{p['end_idx']}): "
                f"{p['token_share']}% tokens, {p['runtime_share']}% time"
                for p in phases)
            insights_md = "### Behavioral Insights\n\n" + "\n".join(
                f"- {ins}" for ins in insights_list)

            heatmap_fig = build_analytics_heatmap(step_analytics, phases)
            phase_fig = build_phase_chart(phases, step_analytics)

            analytics_df = pd.DataFrame(build_analytics_dataframe(step_analytics))

            # -- Health verdict --
            verdicts = compute_health_verdict(metrics, step_analytics)
            verdict_html = _build_health_verdict_html(verdicts)

            # -- Section insight callouts --
            section_insights = _map_insights_to_sections(insights_list)
            perf_callout = _build_insight_callout_html(section_insights["performance"])
            eff_callout = _build_insight_callout_html(section_insights["efficiency"])
            tools_callout = _build_insight_callout_html(section_insights["tools"])

            # -- Raw data --
            raw_str = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
            if len(raw_str) > 500_000:
                raw_str = raw_str[:500_000] + "\n\n... (truncated at 500KB)"

            return (
                steps,              # state_steps
                banner,             # summary_banner
                kpi_html,           # overview_kpi_html
                verdict_html,       # health_verdict_html
                meta_text,          # meta_md
                outp_text,          # output_md
                phase_md,           # analytics_phase_md
                insights_md,        # analytics_insights_md
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
                idle_fig,           # idle_gap_chart
                hotspots_text,      # hotspots_md
                analytics_df,       # analytics_table
                per_message_text,   # per_message_md
                wf_count,           # wf_count_html
                wf_html,            # workflow_html
                "*Click a step card to inspect details.*",  # detail_md
                raw_str,            # raw_json
            )

        all_outputs = [
            state_steps,              # 0
            summary_banner,           # 1
            overview_kpi_html,        # 2
            health_verdict_html,      # 3
            meta_md,                  # 4
            output_md,                # 5
            analytics_phase_md,       # 6
            analytics_insights_md,    # 7
            metrics_md,               # 8
            perf_insights_html,       # 9
            token_chart,              # 10
            duration_chart,           # 11
            analytics_phase_chart,    # 12
            eff_insights_html,        # 13
            context_growth_chart,     # 14
            analytics_heatmap,        # 15
            cache_chart,              # 16
            behavior_md,              # 17
            tools_insights_html,      # 18
            efficiency_chart,         # 19
            tool_chart,               # 20
            tool_duration_chart,      # 21
            idle_gap_chart,           # 22
            hotspots_md,              # 23
            analytics_table,          # 24
            per_message_md,           # 25
            wf_count_html,            # 26
            workflow_html,            # 27
            detail_md,                # 28
            raw_json,                 # 29
        ]

        load_btn.click(
            fn=do_load,
            inputs=[file_dropdown, file_upload],
            outputs=all_outputs,
        )

        # Auto-load on dropdown change
        file_dropdown.change(
            fn=do_load,
            inputs=[file_dropdown, file_upload],
            outputs=all_outputs,
        )

        # -- Step click callback --
        def on_step_click(steps, evt: gr.EventData):
            if not steps:
                return "*Load a trajectory first.*"
            try:
                idx = int(evt.step_index)
            except (ValueError, TypeError, AttributeError):
                return "*Select a step from the workflow.*"
            if idx < 0 or idx >= len(steps):
                return f"*Step {idx} out of range.*"
            return format_step_detail(steps[idx])

        workflow_html.click(
            fn=on_step_click,
            inputs=[state_steps],
            outputs=[detail_md],
        )

        # -- Chart toggle callback --
        def on_chart_toggle(mode, steps):
            sa = compute_step_analytics(steps or [])
            ph = detect_phases(sa) if sa else []
            return build_token_chart(steps or [], cumulative=(mode == "Cumulative"),
                                     step_analytics=sa, phases=ph)

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
