"""Centralized CSS constants for the trajectory visualizer UI."""

from pygments.formatters import HtmlFormatter as _HtmlFormatter

_pygments_css = _HtmlFormatter(style="github-dark").get_style_defs(".wf-code-hl")

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
    --ov-bad: #dc2626;
}
/* Type scale */
h2, .section-header { font-size: 18px; font-weight: 700; }
h3, .card-header { font-size: 14px; font-weight: 600; }
body, p, td, li { font-size: 13px; font-weight: 400; }
.muted, .ov-kpi-sub, .wf-meta { font-size: 12px; }

.summary-banner {
    background: linear-gradient(135deg, #eaf2ff 0%, #effbf4 55%, #fffaf0 100%);
    border: 1px solid #c7d7f6;
    border-radius: 14px;
    box-shadow: 0 8px 20px rgba(24, 47, 89, 0.08);
    padding: 16px 24px;
    margin-bottom: 20px;
}
.summary-banner p { margin: 2px 0 !important; }
.overview-card {
    background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    border: 1px solid var(--ov-border);
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
    padding: 14px 16px;
    min-height: 210px;
    margin-bottom: 20px;
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
    margin: 4px 0 20px 0;
}
.ov-kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 14px;
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
/* Health verdict strip */
.hv-strip {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin: 0 0 20px 0;
}
.hv-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 8px;
    cursor: default;
    transition: box-shadow 0.15s;
}
.hv-badge:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.10); }
.hv-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
}
.hv-metric {
    font-size: 12px;
    font-weight: 600;
}
.hv-label {
    font-size: 12px;
    font-weight: 700;
}
/* Insight callouts */
.insight-callout {
    display: inline-flex;
    align-items: flex-start;
    gap: 6px;
    background: #f0f4ff;
    border: 1px solid #dbe5ff;
    border-radius: 8px;
    padding: 6px 12px;
    margin: 0 8px 8px 0;
    font-size: 12px;
    color: var(--ov-accent);
    line-height: 1.4;
}
.insight-icon {
    font-size: 14px;
    flex-shrink: 0;
}
.insight-text {
    color: #374151;
}
/* Workflow count */
.wf-count {
    font-size: 12px;
    color: var(--ov-muted);
    padding: 4px 0 8px 0;
    font-weight: 600;
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
    margin-bottom: 20px;
}
.detail-panel {
    position: sticky; top: 12px;
    max-height: 78vh; overflow-y: auto;
    scrollbar-width: thin;
}
"""

WORKFLOW_CSS = """
<style>
.wf-scroll {
    max-height: 75vh; overflow-y: auto; padding: 8px 4px 8px 0;
    scrollbar-width: thin; scrollbar-color: #cbd5e1 transparent;
}
.wf-scroll::-webkit-scrollbar { width: 6px; }
.wf-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
.wf-container { font-family: system-ui, -apple-system, sans-serif; max-width: 680px; margin: 0 auto; }
.wf-card {
    border: 2px solid #e5e7eb; border-radius: 10px; padding: 12px 16px;
    cursor: pointer; transition: box-shadow 0.15s, transform 0.1s;
    position: relative;
}
.wf-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.10); transform: translateY(-1px); }
.wf-card.wf-active { box-shadow: 0 0 0 3px rgba(59,130,246,0.4); }
.wf-connector {
    width: 2px; height: 20px; background: linear-gradient(to bottom, #d1d5db, #e5e7eb);
    margin: 0 auto;
}
.wf-badge {
    display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 8px;
    border-radius: 6px; margin-right: 6px; text-transform: uppercase; letter-spacing: 0.5px;
}
.wf-header { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.wf-meta { font-size: 11px; color: #6b7280; margin-top: 5px; display: flex; gap: 8px; flex-wrap: wrap; }
.wf-meta span { white-space: nowrap; }
.wf-preview {
    font-size: 12px; color: #374151; margin-top: 6px;
    white-space: pre-wrap; word-break: break-word;
    line-height: 1.4;
}
.wf-code-block {
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    margin: 6px 0; overflow-x: auto; position: relative;
}
.wf-code-lang {
    position: absolute; top: 4px; right: 8px;
    font-size: 10px; color: #8b949e; text-transform: uppercase;
    letter-spacing: 0.5px; user-select: none;
}
.wf-code-block pre {
    margin: 0; padding: 10px 14px; overflow-x: auto;
}
.wf-code-block code {
    font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 12px; line-height: 1.5; color: #c9d1d9;
    white-space: pre; display: block;
}
/* Pygments syntax highlighting (github-dark) */
""" + _pygments_css + """
.wf-icons { font-size: 11px; color: #9ca3af; }
</style>
<!-- Click handling via Gradio js_on_load + trigger() -->
"""
