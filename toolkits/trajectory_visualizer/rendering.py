"""HTML/code rendering, card styles, and workflow HTML generation."""

import html
import json
import re
from datetime import datetime, timezone

from pygments import highlight as _pygments_highlight
from pygments.formatters import HtmlFormatter as _HtmlFormatter
from pygments.lexers import get_lexer_by_name as _get_lexer, TextLexer as _TextLexer


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
_pygments_css = _HtmlFormatter(style="github-dark").get_style_defs(".wf-code-hl")


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

    css = """
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
        cost_info = ''
        step_cost = step.get("cost", 0) or 0
        if step_cost > 0:
            cost_info = f'<span style="color:#059669">${step_cost:.4f}</span>'

        role = step["role"]
        role_style = _ROLE_BADGE_STYLES.get(role, "background:#6b7280;color:white;")
        role_label = role.title()

        card = f"""
        <div class="wf-card" id="wf-card-{i}" data-step-idx="{i}"
             style="background:{bg};border-color:{border};">
            <div class="wf-header">
                <span class="wf-badge" style="background:{border};color:white;">#{i}</span>
                <span class="wf-badge" style="{role_style}">{role_label}</span>
                <span class="wf-badge" style="background:transparent;color:{border};border:1px solid {border};">{label}</span>
                {agent_badge}
                <span class="wf-icons">{icon_str}</span>
            </div>
            <div class="wf-meta">
                <span>{dur}</span>
                <span>{tok} tok</span>
                {cost_info}{tc_info}{err_info}
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


def format_step_detail(step: dict) -> str:
    """Format detail panel for a selected step as a single markdown string."""
    _, _, label = _card_style(step)

    # --- Build header rows conditionally ---
    rows: list[tuple[str, str]] = []
    rows.append(("Role", f"`{step['role']}`"))

    if step.get("agent"):
        rows.append(("Agent", f"`{step['agent']}`"))
    if step.get("mode"):
        rows.append(("Mode", f"`{step['mode']}`"))
    if step.get("model_id"):
        rows.append(("Model", f"`{step['model_id']}`"))
    if step.get("provider_id"):
        rows.append(("Provider", f"`{step['provider_id']}`"))
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
    if step.get("cost"):
        rows.append(("Cost", f"${step['cost']:.4f}"))
    if step["tool_call_count"] > 0:
        rows.append(("Tool calls", str(step["tool_call_count"])))
    if step["error_count"] > 0:
        rows.append(("Errors", str(step["error_count"])))
    if step.get("id"):
        rows.append(("ID", f"`{step['id']}`"))
    if step.get("parent_id"):
        rows.append(("Parent ID", f"`{step['parent_id']}`"))
    if step.get("session_id"):
        rows.append(("Session", f"`{step['session_id']}`"))
    if step.get("cwd"):
        rows.append(("CWD", f"`{step['cwd']}`"))
    if step.get("root") and step.get("root") != step.get("cwd"):
        rows.append(("Root", f"`{step['root']}`"))
    if step.get("message_id"):
        rows.append(("Message ID", f"`{step['message_id']}`"))

    table_lines = [
        f"### Step {step['index']} \u2014 {label}",
        "| Field | Value |",
        "|-------|-------|",
    ]
    for field, value in rows:
        table_lines.append(f"| {field} | {value} |")
    header = "\n".join(table_lines) + "\n"

    tok = step["tokens"]
    tokens_str = (
        "| Input | Output | Reasoning | Cache Read | Cache Write | Total |\n"
        "|------:|-------:|----------:|-----------:|------------:|------:|\n"
        f"| {tok.get('input', 0):,} | {tok.get('output', 0):,} | "
        f"{tok.get('reasoning', 0):,} | {tok.get('cache_read', 0):,} | "
        f"{tok.get('cache_write', 0):,} | {tok.get('total', 0):,} |"
    )

    # --- Content parts ---
    content_parts = []
    for p in step["parts"]:
        ptype = p.get("type", "unknown")
        if ptype == "text":
            content_parts.append(f"#### Text\n\n{p.get('text', '')}\n")
        elif ptype == "reasoning":
            content_parts.append(f"#### Reasoning\n\n{p.get('text', '')}\n")
        elif ptype == "tool_call":
            inp = p.get("input", {})
            out = p.get("output", "")
            if isinstance(inp, dict):
                inp_str = json.dumps(inp, indent=2, ensure_ascii=False)
            else:
                inp_str = str(inp)
            if isinstance(out, str) and len(out) > 2000:
                out = out[:2000] + "\n... (truncated)"
            elif isinstance(out, dict):
                out = json.dumps(out, indent=2, ensure_ascii=False)
                if len(out) > 2000:
                    out = out[:2000] + "\n... (truncated)"

            tc_dur = ""
            if p.get("time_start") and p.get("time_end"):
                tc_dur = f" \u2014 {round((p['time_end'] - p['time_start']) / 1000, 2)}s"

            # Metadata line: tool_id + metadata dict entries
            meta_parts: list[str] = []
            tool_id = p.get("tool_id", "")
            if tool_id:
                meta_parts.append(f"`{tool_id}`")
            tc_meta = p.get("metadata") or {}
            # Keys already rendered or that duplicate state-level fields
            _handled_meta_keys = {"output", "input", "preview"}
            if isinstance(tc_meta, dict):
                if tc_meta.get("sessionId"):
                    sid = str(tc_meta["sessionId"])
                    meta_parts.append(f"Session: `{sid[:16]}\u2026`" if len(sid) > 16 else f"Session: `{sid}`")
                    _handled_meta_keys.add("sessionId")
                meta_model = tc_meta.get("model")
                if isinstance(meta_model, dict):
                    mid = meta_model.get("modelID", "")
                    pid = meta_model.get("providerID", "")
                    if mid:
                        meta_parts.append(f"Model: `{mid}`")
                    if pid:
                        meta_parts.append(f"Provider: `{pid}`")
                    _handled_meta_keys.add("model")
                elif meta_model:
                    meta_parts.append(f"Model: `{meta_model}`")
                    _handled_meta_keys.add("model")
                if tc_meta.get("truncated"):
                    meta_parts.append("truncated")
                _handled_meta_keys.add("truncated")
                # Show remaining metadata keys generically (scalars only)
                for mk, mv in tc_meta.items():
                    if mk in _handled_meta_keys:
                        continue
                    if mv is None or mv == "" or mv == {} or mv == []:
                        continue
                    # Skip complex structures — they're in Input/Output
                    if isinstance(mv, (list, dict)):
                        continue
                    # Truncate long strings to keep metadata line readable
                    if isinstance(mv, str) and len(mv) > 60:
                        mv = mv[:57] + "..."
                    meta_parts.append(f"{mk}: `{mv}`")
            # subagent_type from input
            if isinstance(inp, dict) and inp.get("subagent_type"):
                meta_parts.append(f"Subagent: `{inp['subagent_type']}`")

            meta_line = ""
            if meta_parts:
                meta_line = " \u00b7 ".join(meta_parts) + "\n\n"

            error_block = ""
            tc_error = p.get("error")
            if tc_error:
                err_str = tc_error if isinstance(tc_error, str) else json.dumps(tc_error, indent=2, ensure_ascii=False)
                error_block = f"\n\n<details><summary>Error</summary>\n\n```\n{err_str}\n```\n</details>\n"

            content_parts.append(
                f"#### Tool: `{p.get('tool_name', '?')}` \u2014 "
                f"`{p.get('status', '?')}`{tc_dur}\n\n"
                f"**{p.get('title') or 'Untitled'}**\n\n"
                f"{meta_line}"
                f"<details><summary>Input</summary>\n\n```json\n{inp_str}\n```\n</details>\n\n"
                f"<details><summary>Output</summary>\n\n```\n{out}\n```\n</details>"
                f"{error_block}\n"
            )
        elif ptype in ("step_start", "step_finish"):
            pass  # skip noise in detail view
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
    return header + "\n" + tokens_str + "\n\n" + content
