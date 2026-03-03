"""HTML/code rendering, card styles, and workflow HTML generation."""

import html
import json
import re
from datetime import datetime, timezone

from pygments import highlight as _pygments_highlight
from pygments.formatters import HtmlFormatter as _HtmlFormatter
from pygments.lexers import get_lexer_by_name as _get_lexer, TextLexer as _TextLexer

from .styles import WORKFLOW_CSS


_ROLE_COLORS = {
    "user": ("#dbeafe", "#1e40af", "User"),
    "assistant": ("#fef3c7", "#92400e", "Assistant"),
}

_ROLE_BADGE_STYLES = {
    "user": "background:#2563eb;color:white;",
    "assistant": "background:#d97706;color:white;",
    "system": "background:#6b7280;color:white;",
    "tool": "background:#7c3aed;color:white;",
}


def _card_style(step: dict) -> tuple[str, str, str]:
    """Return (bg_color, border_color, label) for a step card."""
    role = step["role"]
    if step["error_count"] > 0:
        return "#fee2e2", "#dc2626", "Error"
    if step.get("finish") == "stop" or step.get("finish") == "end_turn":
        return "#d1fae5", "#059669", "Final"
    if step["tool_call_count"] > 0:
        return "#fef3c7", "#d97706", "Tool Calls"
    if step["has_reasoning"] and role == "assistant":
        return "#ede9fe", "#7c3aed", "Reasoning"
    bg, border, label = _ROLE_COLORS.get(role, ("#f3f4f6", "#6b7280", role.title()))
    return bg, border, label


_CODE_FENCE_RE = re.compile(
    r"```(\w*)\n(.*?)```",
    re.DOTALL,
)


_pygments_formatter = _HtmlFormatter(nowrap=True, style="github-dark")


def _highlight_code(code: str, lang: str) -> str:
    """Syntax-highlight a code string using Pygments."""
    try:
        lexer = _get_lexer(lang, stripall=True)
    except Exception:
        lexer = _TextLexer(stripall=True)
    return _pygments_highlight(code, lexer, _pygments_formatter)


def _md_to_html_preview(text: str) -> str:
    """Convert text with markdown fenced code blocks to HTML.

    Code fences (```lang ... ```) become syntax-highlighted <pre><code> blocks.
    Everything else is html-escaped.
    """
    parts: list[str] = []
    last_end = 0
    for m in _CODE_FENCE_RE.finditer(text):
        before = text[last_end:m.start()]
        if before:
            parts.append(html.escape(before))
        lang = m.group(1) or "text"
        code = m.group(2).rstrip("\n")
        highlighted = _highlight_code(code, lang)
        lang_escaped = html.escape(lang)
        parts.append(
            f'<div class="wf-code-block">'
            f'<span class="wf-code-lang">{lang_escaped}</span>'
            f'<pre class="wf-code-hl"><code>{highlighted}</code></pre>'
            f'</div>'
        )
        last_end = m.end()
    tail = text[last_end:]
    if tail:
        parts.append(html.escape(tail))
    return "".join(parts) if parts else html.escape(text)


def render_workflow_html(steps: list[dict]) -> str:
    """Render vertical card flow as self-contained HTML with scroll container."""
    if not steps:
        return "<div style='padding:2em;color:#888;text-align:center;'>No steps to display.</div>"

    css = WORKFLOW_CSS

    cards_html = []
    for i, step in enumerate(steps):
        bg, border, label = _card_style(step)
        dur = f"{step['duration']}s" if step["duration"] is not None else "\u2014"
        tok = f"{step['tokens']['total']:,}"
        preview = _md_to_html_preview(step["text_preview"]) if step["text_preview"] else "\u2014"

        part_icons = []
        for p in step["parts"]:
            t = p.get("type", "")
            if t == "reasoning":
                part_icons.append("thought")
            elif t == "tool_call":
                part_icons.append("tool")
            elif t == "text":
                part_icons.append("text")
        icon_str = " \u00b7 ".join(sorted(set(part_icons))) if part_icons else ""

        tc_info = f'<span>{step["tool_call_count"]} tool(s)</span>' if step["tool_call_count"] else ''
        err_info = f'<span style="color:#dc2626">{step["error_count"]} err</span>' if step["error_count"] else ''
        agent_badge = ''
        if step.get("agent"):
            agent_badge = (
                f'<span class="wf-badge" style="background:#dbeafe;color:#1e40af;'
                f'border:1px solid #93c5fd;font-size:9px;">{html.escape(step["agent"])}</span>'
            )
        role = step["role"]
        role_style = _ROLE_BADGE_STYLES.get(role, "background:#6b7280;color:white;")
        role_label = role.title()

        orig_idx = step.get("index", i)
        card = f"""
        <div class="wf-card" id="wf-card-{orig_idx}" data-step-idx="{orig_idx}"
             style="background:{bg};border-color:{border};">
            <div class="wf-header">
                <span class="wf-badge" style="background:{border};color:white;">#{orig_idx}</span>
                <span class="wf-badge" style="{role_style}">{role_label}</span>
                <span class="wf-badge" style="background:transparent;color:{border};border:1px solid {border};">{label}</span>
                {agent_badge}
                <span class="wf-icons">{icon_str}</span>
            </div>
            <div class="wf-meta">
                <span>{dur}</span>
                <span>{tok} tok</span>
                {tc_info}{err_info}
            </div>
            <div class="wf-preview">{preview}</div>
        </div>
        """
        cards_html.append(card)
        if i < len(steps) - 1:
            cards_html.append('<div class="wf-connector"></div>')

    return (
        css
        + '<div class="wf-scroll"><div class="wf-container">'
        + "\n".join(cards_html)
        + '</div></div>'
    )


def _fmt_timestamp(ms):
    """Convert epoch-milliseconds to readable ``YYYY-MM-DD HH:MM:SS`` (UTC)."""
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _format_step_header(step: dict) -> str:
    """Build the metadata table and token summary for a step detail panel."""
    _, _, label = _card_style(step)

    rows: list[tuple[str, str]] = [("Role", f"`{step['role']}`")]
    _optional = [
        ("agent", "Agent"), ("mode", "Mode"), ("model_id", "Model"),
        ("provider_id", "Provider"),
    ]
    for key, field in _optional:
        if step.get(key):
            rows.append((field, f"`{step[key]}`"))
    if step.get("duration") is not None:
        rows.append(("Duration", f"{step['duration']}s"))

    created_str = _fmt_timestamp(step.get("time_created_ms"))
    if created_str:
        rows.append(("Created", created_str))
    completed_str = _fmt_timestamp(step.get("time_completed_ms"))
    if completed_str:
        rows.append(("Completed", completed_str))

    if step.get("finish"):
        rows.append(("Finish", f"`{step['finish']}`"))
    if step["tool_call_count"] > 0:
        rows.append(("Tool calls", str(step["tool_call_count"])))
    if step["error_count"] > 0:
        rows.append(("Errors", str(step["error_count"])))

    _id_fields = [
        ("id", "ID"), ("parent_id", "Parent ID"), ("session_id", "Session"),
        ("cwd", "CWD"), ("message_id", "Message ID"),
    ]
    for key, field in _id_fields:
        if step.get(key):
            rows.append((field, f"`{step[key]}`"))
    if step.get("root") and step.get("root") != step.get("cwd"):
        rows.append(("Root", f"`{step['root']}`"))

    table_lines = [
        f"### Step {step['index']} \u2014 {label}",
        "| Field | Value |",
        "|-------|-------|",
    ]
    for field, value in rows:
        table_lines.append(f"| {field} | {value} |")

    tok = step["tokens"]
    tokens_str = (
        "| Input | Output | Reasoning | Cache Read | Cache Write | Total |\n"
        "|------:|-------:|----------:|-----------:|------------:|------:|\n"
        f"| {tok.get('input', 0):,} | {tok.get('output', 0):,} | "
        f"{tok.get('reasoning', 0):,} | {tok.get('cache_read', 0):,} | "
        f"{tok.get('cache_write', 0):,} | {tok.get('total', 0):,} |"
    )
    return "\n".join(table_lines) + "\n\n" + tokens_str


def _format_tool_call_detail(p: dict) -> str:
    """Render a single tool_call part as a markdown block."""
    inp = p.get("input", {})
    out = p.get("output", "")
    inp_str = json.dumps(inp, indent=2, ensure_ascii=False) if isinstance(inp, dict) else str(inp)
    if isinstance(out, str) and len(out) > 2000:
        out = out[:2000] + "\n... (truncated)"
    elif isinstance(out, dict):
        out = json.dumps(out, indent=2, ensure_ascii=False)
        if len(out) > 2000:
            out = out[:2000] + "\n... (truncated)"

    tc_dur = ""
    if p.get("time_start") and p.get("time_end"):
        tc_dur = f" \u2014 {round((p['time_end'] - p['time_start']) / 1000, 2)}s"

    meta_parts: list[str] = []
    tool_id = p.get("tool_id", "")
    if tool_id:
        meta_parts.append(f"`{tool_id}`")
    tc_meta = p.get("metadata") or {}
    handled = {"output", "input", "preview"}
    if isinstance(tc_meta, dict):
        if tc_meta.get("sessionId"):
            sid = str(tc_meta["sessionId"])
            meta_parts.append(f"Session: `{sid[:16]}\u2026`" if len(sid) > 16 else f"Session: `{sid}`")
            handled.add("sessionId")
        meta_model = tc_meta.get("model")
        if isinstance(meta_model, dict):
            if meta_model.get("modelID"):
                meta_parts.append(f"Model: `{meta_model['modelID']}`")
            if meta_model.get("providerID"):
                meta_parts.append(f"Provider: `{meta_model['providerID']}`")
            handled.add("model")
        elif meta_model:
            meta_parts.append(f"Model: `{meta_model}`")
            handled.add("model")
        if tc_meta.get("truncated"):
            meta_parts.append("truncated")
        handled.add("truncated")
        for mk, mv in tc_meta.items():
            if mk in handled or mv is None or mv == "" or mv == {} or mv == []:
                continue
            if isinstance(mv, (list, dict)):
                continue
            if isinstance(mv, str) and len(mv) > 60:
                mv = mv[:57] + "..."
            meta_parts.append(f"{mk}: `{mv}`")
    if isinstance(inp, dict) and inp.get("subagent_type"):
        meta_parts.append(f"Subagent: `{inp['subagent_type']}`")

    meta_line = (" \u00b7 ".join(meta_parts) + "\n\n") if meta_parts else ""

    error_block = ""
    tc_error = p.get("error")
    if tc_error:
        err_str = tc_error if isinstance(tc_error, str) else json.dumps(tc_error, indent=2, ensure_ascii=False)
        error_block = f"\n\n<details><summary>Error</summary>\n\n```\n{err_str}\n```\n</details>\n"

    return (
        f"#### Tool: `{p.get('tool_name', '?')}` \u2014 "
        f"`{p.get('status', '?')}`{tc_dur}\n\n"
        f"**{p.get('title') or 'Untitled'}**\n\n"
        f"{meta_line}"
        f"<details><summary>Input</summary>\n\n```json\n{inp_str}\n```\n</details>\n\n"
        f"<details><summary>Output</summary>\n\n```\n{out}\n```\n</details>"
        f"{error_block}\n"
    )


def format_step_detail(step: dict) -> str:
    """Format detail panel for a selected step as a single markdown string."""
    header = _format_step_header(step)

    content_parts = []
    for p in step["parts"]:
        ptype = p.get("type", "unknown")
        if ptype == "text":
            content_parts.append(f"#### Text\n\n{p.get('text', '')}\n")
        elif ptype == "reasoning":
            content_parts.append(f"#### Reasoning\n\n{p.get('text', '')}\n")
        elif ptype == "tool_call":
            content_parts.append(_format_tool_call_detail(p))
        elif ptype in ("step_start", "step_finish"):
            pass
        elif ptype == "snapshot":
            content_parts.append("#### Snapshot\n\n*(data omitted)*\n")
        elif ptype == "patch":
            patch_hash = p.get("hash", "")
            patch_files = p.get("files", [])
            patch_id = p.get("id", "")
            lines = ["#### Patch"]
            meta = []
            if patch_hash:
                meta.append(f"`{patch_hash[:12]}`")
            if patch_id:
                meta.append(f"`{patch_id}`")
            if meta:
                lines.append(" \u00b7 ".join(meta))
            if patch_files:
                lines.append("\n**Files:**")
                for f in patch_files:
                    lines.append(f"- `{f}`")
            content_parts.append("\n".join(lines) + "\n")
        else:
            content_parts.append(f"#### {ptype}\n")

    content = "\n---\n".join(content_parts) if content_parts else "*No content*"
    return header + "\n\n" + content
