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
    /* Component-level light tokens */
    --ov-card-bg: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    --ov-card-shadow: rgba(15, 23, 42, 0.04);
    --ov-banner-bg: linear-gradient(135deg, #eaf2ff 0%, #effbf4 55%, #fffaf0 100%);
    --ov-banner-border: #c7d7f6;
    --ov-code-bg: #eef3ff;
    --ov-code-border: #dbe5ff;
    --ov-body-text: #1f2937;
    --ov-insight-bg: #f0f4ff;
    --ov-insight-border: #dbe5ff;
    --ov-insight-text: #374151;
    --ov-link-hover-bg: #eef3ff;
    --ov-anomaly-bg: #fef3c7;
    --ov-anomaly-border: #f59e0b;
    --ov-anomaly-text: #92400e;
    --ov-anomaly-hover: #fde68a;
    --ov-chart-ctrl-bg: #f3f6ff;
    --ov-table-header-bg: #f1f5f9;
    --ov-nav-bg: #f8fafc;
    --ov-acc-bg: #ffffff;
    /* Workflow card palette */
    --wf-bg-user: #dbeafe;
    --wf-border-user: #1e40af;
    --wf-bg-assistant: #fef3c7;
    --wf-border-assistant: #92400e;
    --wf-bg-error: #fee2e2;
    --wf-border-error: #dc2626;
    --wf-bg-final: #d1fae5;
    --wf-border-final: #059669;
    --wf-bg-tool: #fef3c7;
    --wf-border-tool: #d97706;
    --wf-bg-reasoning: #ede9fe;
    --wf-border-reasoning: #7c3aed;
    --wf-bg-default: #f3f4f6;
    --wf-border-default: #6b7280;
    --wf-card-border: #e5e7eb;
    --wf-meta-color: #6b7280;
    --wf-preview-color: #374151;
    --wf-connector-from: #d1d5db;
    --wf-connector-to: #e5e7eb;
    --wf-scroll-thumb: #cbd5e1;
}
/* Type scale */
h2, .section-header { font-size: 18px; font-weight: 700; }
h3, .card-header { font-size: 14px; font-weight: 600; }
body, p, td, li { font-size: 13px; font-weight: 400; }
.muted, .ov-kpi-sub, .wf-meta { font-size: 12px; }

.summary-banner {
    background: var(--ov-banner-bg);
    border: 1px solid var(--ov-banner-border);
    border-radius: 14px;
    box-shadow: 0 8px 20px rgba(24, 47, 89, 0.08);
    padding: 16px 24px;
    margin-bottom: 20px;
}
.summary-banner p { margin: 2px 0 !important; }
.overview-card {
    background: var(--ov-card-bg);
    border: 1px solid var(--ov-border);
    border-radius: 12px;
    box-shadow: 0 2px 8px var(--ov-card-shadow);
    padding: 14px 16px;
    min-height: 120px;
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
    color: var(--ov-body-text);
}
.overview-card code {
    background: var(--ov-code-bg);
    border: 1px solid var(--ov-code-border);
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
    transition: transform 0.15s, box-shadow 0.15s;
}
.ov-kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 14px rgba(15, 23, 42, 0.10);
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

/* Insight callouts */
.insight-callout {
    display: inline-flex;
    align-items: flex-start;
    gap: 6px;
    background: var(--ov-insight-bg);
    border: 1px solid var(--ov-insight-border);
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
    color: var(--ov-insight-text);
}
.insight-step-link {
    color: var(--ov-accent);
    text-decoration: underline;
    text-decoration-style: dotted;
    cursor: pointer;
    font-weight: 600;
}
.insight-step-link:hover {
    text-decoration-style: solid;
    background: var(--ov-link-hover-bg);
    border-radius: 3px;
    padding: 0 2px;
}
/* Workflow count */
.wf-count {
    font-size: 12px;
    color: var(--ov-muted);
    padding: 4px 0 8px 0;
    font-weight: 600;
}
.chart-control {
    background: var(--ov-chart-ctrl-bg);
    border: 1px solid var(--ov-insight-border);
    border-radius: 10px;
    padding: 10px 12px 0;
    margin: 2px 0 8px;
}
.per-message-acc {
    border: 1px solid var(--ov-border);
    border-radius: 12px;
    background: var(--ov-acc-bg);
    margin-bottom: 20px;
    transition: background 0.15s, box-shadow 0.15s;
}
.per-message-acc:hover {
    box-shadow: 0 2px 10px rgba(15, 23, 42, 0.06);
}
.detail-panel {
    position: sticky; top: 12px;
    max-height: 78vh; overflow-y: auto;
    scrollbar-width: thin;
}
/* Insight sidebar in accordion two-column layouts */
.insight-sidebar {
    position: sticky; top: 12px;
    min-height: auto;
}
/* Anomaly strip */
.anomaly-strip {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin: 0 0 16px 0;
}
.anomaly-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: var(--ov-anomaly-bg);
    border: 1px solid var(--ov-anomaly-border);
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
    color: var(--ov-anomaly-text);
    cursor: pointer;
    transition: box-shadow 0.15s, background 0.15s;
}
.anomaly-badge:hover {
    background: var(--ov-anomaly-hover);
    box-shadow: 0 2px 6px rgba(245,158,11,0.25);
}

/* KPI card verdict indicator */
.ov-kpi-card[data-status="good"] {
    border-left: 4px solid #059669;
}
.ov-kpi-card[data-status="warn"] {
    border-left: 4px solid #d97706;
}
.ov-kpi-card[data-status="bad"] {
    border-left: 4px solid #dc2626;
}
/* ===== Detail-panel (dp-*) ===== */
.dp-header {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    padding: 10px 14px;
    border-radius: 10px;
    margin-bottom: 12px;
    font-size: 14px;
    font-weight: 700;
    color: white;
}
.dp-header .dp-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 6px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: rgba(255,255,255,0.25);
}
.dp-meta-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    margin-bottom: 14px;
}
.dp-meta-table td {
    padding: 4px 10px;
    border-bottom: 1px solid var(--ov-border);
    color: var(--ov-body-text);
}
.dp-meta-table td:first-child {
    font-weight: 600;
    color: var(--ov-muted);
    white-space: nowrap;
    width: 110px;
}
.dp-meta-table code {
    background: var(--ov-code-bg);
    border: 1px solid var(--ov-code-border);
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 11px;
}
.dp-section {
    border-left: 3px solid var(--ov-border);
    background: var(--ov-card);
    border-radius: 0 8px 8px 0;
    padding: 10px 14px;
    margin-bottom: 8px;
}
.dp-section-title {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 0 0 6px 0;
    color: var(--ov-muted);
}
.dp-section-text { border-left-color: var(--wf-border-assistant); }
.dp-section-reasoning { border-left-color: var(--wf-border-reasoning); }
.dp-section-tool { border-left-color: var(--wf-border-tool); }
.dp-section-tool-error { border-left-color: var(--wf-border-error); }
.dp-section-patch { border-left-color: var(--wf-border-final); }
.dp-section-snapshot { border-left-color: var(--wf-border-default); }
.dp-details {
    background: var(--ov-code-bg);
    border: 1px solid var(--ov-code-border);
    border-radius: 8px;
    margin: 6px 0;
    font-size: 12px;
}
.dp-details summary {
    padding: 6px 12px;
    cursor: pointer;
    font-weight: 600;
    font-size: 12px;
    color: var(--ov-accent);
    user-select: none;
}
.dp-details[open] summary {
    border-bottom: 1px solid var(--ov-code-border);
}
.dp-details-body {
    padding: 8px 12px;
    overflow-x: auto;
    max-height: 400px;
    overflow-y: auto;
}
.dp-details-body pre {
    margin: 0;
    font-size: 11px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--ov-body-text);
}
.dp-tool-header {
    font-size: 13px;
    font-weight: 600;
    margin: 0 0 4px 0;
    color: var(--ov-text);
}
.dp-tool-meta {
    font-size: 11px;
    color: var(--ov-muted);
    margin-bottom: 6px;
}
.dp-content {
    font-size: 13px;
    line-height: 1.6;
    color: var(--ov-body-text);
    white-space: pre-wrap;
    word-break: break-word;
}
/* Filter pill styling for workflow checkbox group */
.gradio-container .checkbox-group label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--ov-insight-bg);
    border: 1px solid var(--ov-insight-border);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
    color: var(--ov-body-text);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
}
.gradio-container .checkbox-group label:hover {
    background: var(--ov-link-hover-bg);
    border-color: var(--ov-accent);
}
"""

WORKFLOW_CSS = """
<style>
.wf-scroll {
    max-height: 75vh; overflow-y: auto; padding: 8px 4px 8px 0;
    scrollbar-width: thin; scrollbar-color: var(--wf-scroll-thumb) transparent;
}
.wf-scroll::-webkit-scrollbar { width: 6px; }
.wf-scroll::-webkit-scrollbar-thumb { background: var(--wf-scroll-thumb); border-radius: 3px; }
.wf-container { font-family: system-ui, -apple-system, sans-serif; max-width: 680px; margin: 0 auto; }
.wf-card {
    border: 2px solid var(--wf-card-border); border-radius: 10px; padding: 12px 16px;
    cursor: pointer; transition: box-shadow 0.15s, transform 0.1s;
    position: relative;
}
.wf-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.10); transform: translateY(-1px); }
.wf-card.wf-active { border-left: 4px solid var(--ov-accent); box-shadow: 0 2px 10px rgba(29,78,216,0.15); }
.wf-connector {
    width: 2px; height: 20px; background: linear-gradient(to bottom, var(--wf-connector-from), var(--wf-connector-to));
    margin: 0 auto;
}
.wf-badge {
    display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 8px;
    border-radius: 6px; margin-right: 6px; text-transform: uppercase; letter-spacing: 0.5px;
}
.wf-header { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.wf-meta { font-size: 11px; color: var(--wf-meta-color); margin-top: 5px; display: flex; gap: 8px; flex-wrap: wrap; }
.wf-meta span { white-space: nowrap; }
.wf-preview {
    font-size: 12px; color: var(--wf-preview-color); margin-top: 6px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-height: 1.4em; line-height: 1.4;
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
.wf-icons { font-size: 11px; color: var(--wf-meta-color); }
</style>
<!-- Click handling via Gradio js_on_load + trigger() -->
"""
