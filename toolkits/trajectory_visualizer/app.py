"""Gradio UI for trajectory visualization."""

import html
import json
import os

import gradio as gr
import plotly.graph_objects as go
import pandas as pd

from .data import (
    load_trajectory, parse_steps, build_message_metrics, compute_metrics,
    discover_trajectory_files, _fmt_dict_as_table, _build_hotspots_md,
    _build_per_message_md,
)
from .analytics import compute_step_analytics, detect_phases, generate_insights
from .charts import (
    build_token_chart, build_duration_chart, build_tool_chart,
    build_cache_ratio_chart, build_efficiency_chart,
    build_analytics_heatmap, build_phase_chart,
    build_cost_chart, build_context_growth_chart,
    build_tool_duration_chart, build_idle_gap_chart,
)
from .rendering import render_workflow_html, format_step_detail


APP_CSS = """
:root {
    --ov-bg: #f6f8fc;
    --ov-card: #ffffff;
    --ov-border: #dce3ef;
    --ov-text: #0f172a;
    --ov-muted: #5b6473;
    --ov-accent: #1d4ed8;
    --ov-success: #059669;
    --ov-warn: #b45309;
}
.summary-banner {
    background: linear-gradient(135deg, #eaf2ff 0%, #effbf4 55%, #fffaf0 100%);
    border: 1px solid #c7d7f6;
    border-radius: 14px;
    box-shadow: 0 8px 20px rgba(24, 47, 89, 0.08);
    padding: 16px 24px;
    margin-bottom: 14px;
}
.summary-banner p { margin: 2px 0 !important; }
.overview-card {
    background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    border: 1px solid var(--ov-border);
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
    padding: 14px 16px;
    min-height: 210px;
}
.overview-card h3,
.overview-card h4 {
    color: var(--ov-text);
    margin-top: 0.1em;
}
.overview-card p,
.overview-card li,
.overview-card td {
    color: #1f2937;
}
.overview-card code {
    background: #eef3ff;
    border: 1px solid #dbe5ff;
    border-radius: 5px;
    padding: 1px 5px;
}
.overview-kpi-strip {
    margin: 4px 0 14px 0;
}
.ov-kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
}
.ov-kpi-card {
    background: var(--ov-card);
    border: 1px solid var(--ov-border);
    border-radius: 12px;
    padding: 12px 12px 10px;
    box-shadow: 0 1px 6px rgba(15, 23, 42, 0.04);
}
.ov-kpi-label {
    color: var(--ov-muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    font-weight: 700;
    margin-bottom: 5px;
}
.ov-kpi-value {
    color: var(--ov-text);
    font-size: 23px;
    font-weight: 700;
    line-height: 1.1;
}
.ov-kpi-sub {
    color: var(--ov-muted);
    font-size: 12px;
    margin-top: 4px;
}
.chart-control {
    background: #f3f6ff;
    border: 1px solid #dbe5ff;
    border-radius: 10px;
    padding: 10px 12px 0;
    margin: 2px 0 8px;
}
.per-message-acc {
    border: 1px solid var(--ov-border);
    border-radius: 12px;
    background: #ffffff;
}
.detail-panel {
    position: sticky; top: 12px;
    max-height: 78vh; overflow-y: auto;
    scrollbar-width: thin;
}
"""


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
        ("Cost", f"${metrics.get('total_cost', 0):.4f}",
         f"{metrics.get('reasoning_parts', 0)} reasoning parts"),
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
            # ===== Overview & Charts Tab (merged) =====
            with gr.TabItem("Overview"):
                overview_kpi_html = gr.HTML("", elem_classes=["overview-kpi-strip"])

                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=320):
                        metrics_md = gr.Markdown("", elem_classes=["overview-card"])
                    with gr.Column(scale=1, min_width=320):
                        behavior_md = gr.Markdown("", elem_classes=["overview-card"])
                    with gr.Column(scale=1, min_width=320):
                        hotspots_md = gr.Markdown("", elem_classes=["overview-card"])

                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=320):
                        meta_md = gr.Markdown(
                            "*Select a trajectory file and click Load.*",
                            elem_classes=["overview-card"],
                        )
                    with gr.Column(scale=1, min_width=320):
                        output_md = gr.Markdown("", elem_classes=["overview-card"])

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
                    context_growth_chart = gr.Plot(label="Context Growth")
                    cost_chart = gr.Plot(label="Cost per Step")
                with gr.Row(equal_height=False):
                    with gr.Column(scale=2, min_width=560):
                        efficiency_chart = gr.Plot(label="Per-Step Efficiency")
                    with gr.Column(scale=1, min_width=340):
                        tool_chart = gr.Plot(label="Tool Call Frequency")
                        cache_chart = gr.Plot(label="Cache Ratio")

                with gr.Accordion("Per-Message Deep Dive", open=False, elem_classes=["per-message-acc"]):
                    per_message_md = gr.Markdown("", elem_classes=["overview-card"])

            # ===== Workflow Tab =====
            with gr.TabItem("Workflow"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=3, min_width=400):
                        workflow_html = gr.HTML(
                            "<div style='padding:3em;color:#9ca3af;text-align:center;"
                            "font-size:15px;'>Load a trajectory to see the step flow.</div>",
                            js_on_load="""
                            element.querySelectorAll('.wf-card').forEach(function(card) {
                                card.addEventListener('click', function() {
                                    element.querySelectorAll('.wf-card').forEach(function(c) {
                                        c.classList.remove('wf-active');
                                    });
                                    card.classList.add('wf-active');
                                    var idx = parseInt(card.dataset.stepIdx);
                                    if (!isNaN(idx)) {
                                        trigger('click', {step_index: idx});
                                    }
                                });
                            });
                            """,
                        )
                    with gr.Column(scale=2, min_width=300, elem_classes=["detail-panel"]):
                        detail_md = gr.Markdown("*Click a step card to inspect details.*")

            # ===== Analytics Tab =====
            with gr.TabItem("Analytics"):
                analytics_phase_md = gr.Markdown(
                    "*Load a trajectory to see analytics.*")
                analytics_insights_md = gr.Markdown("")
                with gr.Row(equal_height=True):
                    analytics_heatmap = gr.Plot(label="Behavioral Heatmap")
                    analytics_phase_chart = gr.Plot(label="Phase Timeline")
                with gr.Row(equal_height=True):
                    tool_duration_chart = gr.Plot(label="Tool Duration by Type")
                    idle_gap_chart = gr.Plot(label="Idle Gaps")
                analytics_table = gr.Dataframe(
                    label="Per-Step Metrics",
                    interactive=False,
                    wrap=True,
                )

            # ===== Raw Data Tab =====
            with gr.TabItem("Raw Data"):
                raw_json = gr.Code(
                    label="Full trajectory JSON",
                    language="json",
                    value="",
                    max_lines=50,
                )

        # -- Callbacks --

        def do_load(dropdown_val, upload_obj):
            """Load trajectory from dropdown or upload."""
            file_path = None
            if upload_obj is not None:
                file_path = upload_obj if isinstance(upload_obj, str) else upload_obj.name
            elif dropdown_val:
                file_path = os.path.join(trajectory_dir, dropdown_val)

            empty_fig = go.Figure()
            empty_fig.update_layout(template="plotly_white", height=380)

            if not file_path or not os.path.isfile(file_path):
                empty = "*No file selected or file not found.*"
                return (
                    [],
                    "",
                    "",
                    empty, empty, empty, empty, empty,
                    empty_fig, empty_fig, empty_fig, empty_fig,
                    empty_fig, empty_fig, empty_fig,
                    empty,
                    "<div></div>",
                    "*Click a step card to inspect details.*",
                    "",
                    empty, "", empty_fig, empty_fig,
                    empty_fig, empty_fig,
                    pd.DataFrame(),
                )

            raw = load_trajectory(file_path)
            if "_error" in raw:
                return (
                    [],
                    f"<p style='color:#dc2626;'>Error: {html.escape(raw['_error'])}</p>",
                    "",
                    "*Error loading file.*", "", "", "", "",
                    empty_fig, empty_fig, empty_fig, empty_fig,
                    empty_fig, empty_fig, empty_fig,
                    "",
                    "<div></div>",
                    "",
                    "",
                    "*Error*", "", empty_fig, empty_fig,
                    empty_fig, empty_fig,
                    pd.DataFrame(),
                )

            steps = parse_steps(raw)
            message_rows = build_message_metrics(steps)
            metrics = compute_metrics(steps, raw, message_rows=message_rows)
            md = raw.get("metadata", {}) if isinstance(raw.get("metadata"), dict) else {}
            timing = raw.get("timing", {}) if isinstance(raw.get("timing"), dict) else {}
            outp = raw.get("output", {}) if isinstance(raw.get("output"), dict) else {}
            session_raw = raw.get("session_raw", {}) if isinstance(raw.get("session_raw"), dict) else {}
            retry = raw.get("retry", {}) if isinstance(raw.get("retry"), dict) else {}

            # Extract model/provider/agent from first assistant step
            model_id = ""
            provider_id = ""
            agent_id = ""
            for s in steps:
                if s["role"] == "assistant" and s.get("model_id"):
                    model_id = s["model_id"]
                    provider_id = s.get("provider_id", "")
                    if not agent_id and s.get("agent"):
                        agent_id = s["agent"]
                    break
            if not agent_id:
                for s in steps:
                    if s.get("agent"):
                        agent_id = s["agent"]
                        break

            # -- Summary banner --
            fname = os.path.basename(file_path)
            wall = metrics["wall_clock"] if isinstance(metrics["wall_clock"], (int, float)) else metrics["total_duration"]
            wall_fmt = f"{wall:.0f}s" if wall < 3600 else f"{wall / 60:.1f}m"
            banner = (
                f"<strong>{html.escape(fname)}</strong> &nbsp;&mdash;&nbsp; "
                f"{metrics['total_steps']} steps &middot; "
                f"{metrics['tool_call_count']} tool calls ({metrics['tool_success_rate']}% success) &middot; "
                f"{metrics['tokens']['total']:,} tokens &middot; "
                f"{wall_fmt} wall-clock &middot; "
                f"{metrics['tokens_per_second']} tok/s &middot; "
                f"{metrics['reasoning_parts']} reasoning &middot; "
                f"${metrics['total_cost']:.4f}"
            )
            kpi_html = _build_overview_kpi_html(metrics, wall_fmt)

            # -- Overview: Session & Environment --
            started = timing.get("started_at", "N/A")
            finished = timing.get("finished_at", "N/A")
            # Format timestamps nicely (strip timezone suffix for readability)
            if isinstance(started, str) and len(started) > 19:
                started_short = started[:19].replace("T", " ")
            else:
                started_short = str(started)
            if isinstance(finished, str) and len(finished) > 19:
                finished_short = finished[:19].replace("T", " ")
            else:
                finished_short = str(finished)

            summary_info = session_raw.get("summary", {})
            retry_info = ""
            if retry:
                retry_info = f"| Attempts | {retry.get('total_attempts', '?')} / {retry.get('max_retries', '?')} |"

            meta_text = f"""### Session & Environment
| Field | Value |
|-------|-------|
| Model | `{model_id or md.get('model') or 'N/A'}` |
| Provider | `{provider_id or 'N/A'}` |
| Agent | `{agent_id or md.get('agent', 'N/A')}` |
| Start time | `{started_short}` |
| End time | `{finished_short}` |
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

            # -- Overview: Performance & Tokens --
            # Format finish breakdown string
            finish_parts = []
            for fk, fv in sorted(metrics["finish_breakdown"].items(), key=lambda x: -x[1]):
                finish_parts.append(f"{fv} {fk}")
            finish_str = ", ".join(finish_parts) if finish_parts else "N/A"

            # Format tool status breakdown string
            tool_status_parts = []
            for sk, sv in sorted(metrics["tool_status_breakdown"].items(), key=lambda x: -x[1]):
                tool_status_parts.append(f"{sv} {sk}")
            tool_status_str = ", ".join(tool_status_parts) if tool_status_parts else "N/A"

            # Format role breakdown string
            role_parts = []
            for rk, rv in sorted(metrics["messages_breakdown"].items()):
                role_parts.append(f"{rv} {rk}")
            role_str = ", ".join(role_parts) if role_parts else "N/A"

            metrics_text = f"""### Performance & Tokens
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
| Non-cache tokens | {metrics['non_cache_tokens']:,} ({metrics['non_cache_ratio']}%) |
| Avg tokens / step | {metrics['avg_tokens_per_step']:,} |
| Tokens / second | {metrics['tokens_per_second']:,} |
| Median tokens / second | {metrics['median_tokens_per_second']:,} |
| Output/Input token ratio | {metrics['output_input_ratio']} |
| Total cost | ${metrics['total_cost']:.4f} |
| Tokens / tool call | {metrics['tokens_per_tool']:,} |

**Tool calls** ({metrics['tool_call_count']} total, {metrics['tool_success_rate']}% success)

{_fmt_dict_as_table(metrics['tool_breakdown'], 'Tool', 'Count')}
{"" if not metrics.get('agent_breakdown') else chr(10) + "**Agent breakdown**" + chr(10) + chr(10) + _fmt_dict_as_table(metrics['agent_breakdown'], 'Agent', 'Steps') + chr(10)}{"" if not metrics.get('model_breakdown') else chr(10) + "**Model breakdown**" + chr(10) + chr(10) + _fmt_dict_as_table(metrics['model_breakdown'], 'Model', 'Steps') + chr(10)}"""

            behavior_text = f"""### Behavioral Diagnostics
| Indicator | Value |
|-----------|------:|
| Assistant steps | {metrics['assistant_steps']} |
| Multi-tool assistant steps | {metrics['multi_tool_steps']} |
| No-tool assistant steps | {metrics['no_tool_assistant_steps']} |
| Median assistant step tokens | {metrics['median_step_tokens']:,} |
| P95 assistant step tokens | {metrics['p95_step_tokens']:,} |
| Avg cache-read ratio | {metrics['avg_cache_ratio']}% |
| Cache-dominant assistant steps (\u226590%) | {metrics['cache_dominant_steps']} |
| Tool execution time (sum) | {metrics['tool_time_total']}s |
| Tool-wait share of step time | {metrics['tool_wait_share']}% |
| Avg / P95 / Max tool duration | {metrics['avg_tool_duration']}s / {metrics['p95_tool_duration']}s / {metrics['max_tool_duration']}s |
"""

            hotspots_text = _build_hotspots_md(message_rows)
            per_message_text = _build_per_message_md(message_rows)

            # -- Overview: Output & Results --
            output_rows: list[str] = []
            if outp.get("has_patch"):
                output_rows.append(f"| Patch | {outp.get('patch_lines', 0)} lines, {outp.get('patch_length', 0):,} chars |")
            if summary_info:
                output_rows.append(f"| Files changed | {summary_info.get('files', 'N/A')} |")
                output_rows.append(f"| Additions | +{summary_info.get('additions', 0)} |")
                output_rows.append(f"| Deletions | -{summary_info.get('deletions', 0)} |")
            gt_patch = md.get("ground_truth_patch", "")
            if gt_patch:
                output_rows.append(f"| Ground truth | `{gt_patch[:60]}{'...' if len(gt_patch) > 60 else ''}` |")
            if outp.get("error"):
                output_rows.append(f"| Error | `{outp['error']}` |")

            output_table = ""
            if output_rows:
                output_table = "| Field | Value |\n|-------|-------|\n" + "\n".join(output_rows)

            outp_text = f"""### Output & Agent Stats
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

            # -- Workflow tab --
            wf_html = render_workflow_html(steps)

            # -- Charts --
            tok_fig = build_token_chart(steps, cumulative=False)
            dur_fig = build_duration_chart(steps)
            tl_fig = build_tool_chart(steps)
            cache_fig = build_cache_ratio_chart(message_rows)
            eff_fig = build_efficiency_chart(message_rows)
            ctx_fig = build_context_growth_chart(message_rows)
            cost_fig = build_cost_chart(message_rows)

            # -- Analytics tab --
            step_analytics = compute_step_analytics(steps)
            phases = detect_phases(step_analytics)
            insights_list = generate_insights(step_analytics, phases, steps=steps)
            tool_dur_fig = build_tool_duration_chart(steps)
            idle_fig = build_idle_gap_chart(step_analytics)

            phase_lines = []
            for p in phases:
                phase_lines.append(
                    f"**{p['name']}** (idx {p['start_idx']}\u2013{p['end_idx']}): "
                    f"{p['token_share']}% tokens, {p['runtime_share']}% time")
            phase_md = "### Phase Summary\n\n" + "\n\n".join(phase_lines)

            insights_md = "### Behavioral Insights\n\n" + "\n".join(
                f"- {ins}" for ins in insights_list)

            heatmap_fig = build_analytics_heatmap(step_analytics, phases)
            phase_fig = build_phase_chart(phases, step_analytics)

            has_agents = any(a.get("agent") for a in step_analytics)
            df_rows = []
            for a in step_analytics:
                row = {
                    "idx": a["index"],
                    "role": a["role"],
                }
                if has_agents:
                    row["agent"] = a.get("agent", "")
                row.update({
                    "dur(s)": a["duration_s"],
                    "tok_total": a["tok_total"],
                    "tok/s": (round(a["tok_per_s"])
                              if a["tok_per_s"] is not None else None),
                    "cache%": round(a["cache_ratio"] * 100, 1),
                    "non_cache": a["non_cache_tok"],
                    "out/in": (round(a["out_in_ratio"], 3)
                               if a["out_in_ratio"] is not None else None),
                    "tool#": a["tool_calls"],
                    "tool_share%": (round(a["tool_time_share"] * 100, 1)
                                    if a["tool_time_share"] is not None
                                    else None),
                    "finish": a["finish"],
                    "parts": a["part_mix"],
                    "idle(s)": a["idle_before_s"],
                })
                df_rows.append(row)
            analytics_df = pd.DataFrame(df_rows)

            # -- Raw data --
            raw_str = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
            if len(raw_str) > 500_000:
                raw_str = raw_str[:500_000] + "\n\n... (truncated at 500KB)"

            return (
                steps,
                banner,
                kpi_html,
                meta_text, metrics_text, outp_text, behavior_text, hotspots_text,
                tok_fig, dur_fig, ctx_fig, cost_fig, tl_fig, cache_fig, eff_fig,
                per_message_text,
                wf_html,
                "*Click a step card to inspect details.*",
                raw_str,
                phase_md, insights_md, heatmap_fig, phase_fig,
                tool_dur_fig, idle_fig,
                analytics_df,
            )

        all_outputs = [
            state_steps,
            summary_banner,
            overview_kpi_html,
            meta_md, metrics_md, output_md, behavior_md, hotspots_md,
            token_chart, duration_chart, context_growth_chart, cost_chart,
            tool_chart, cache_chart, efficiency_chart,
            per_message_md,
            workflow_html,
            detail_md,
            raw_json,
            analytics_phase_md, analytics_insights_md, analytics_heatmap,
            analytics_phase_chart,
            tool_duration_chart, idle_gap_chart,
            analytics_table,
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
            return build_token_chart(steps or [], cumulative=(mode == "Cumulative"))

        chart_toggle.change(
            fn=on_chart_toggle,
            inputs=[chart_toggle, state_steps],
            outputs=[token_chart],
        )

    return app
