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
    "user": ("var(--wf-bg-user)", "var(--wf-border-user)", "User"),
    "assistant": ("var(--wf-bg-assistant)", "var(--wf-border-assistant)", "Assistant"),
}

_ROLE_BADGE_STYLES = {
    "user": "background:var(--wf-border-user);color:white;",
    "assistant": "background:var(--wf-border-assistant);color:white;",
    "system": "background:var(--wf-border-default);color:white;",
    "tool": "background:var(--wf-border-reasoning);color:white;",
}


def _card_style(step: dict) -> tuple[str, str, str]:
    """Return (bg_color, border_color, label) for a step card.

    Colors are CSS variable references so they adapt to the active theme.
    """
    role = step["role"]
    if step["error_count"] > 0:
        return "var(--wf-bg-error)", "var(--wf-border-error)", "Error"
    if step.get("finish") == "stop" or step.get("finish") == "end_turn":
        return "var(--wf-bg-final)", "var(--wf-border-final)", "Final"
    if step["tool_call_count"] > 0:
        return "var(--wf-bg-tool)", "var(--wf-border-tool)", "Tool Calls"
    if step["has_reasoning"] and role == "assistant":
        return "var(--wf-bg-reasoning)", "var(--wf-border-reasoning)", "Reasoning"
    bg, border, label = _ROLE_COLORS.get(role, ("var(--wf-bg-default)", "var(--wf-border-default)", role.title()))
    return bg, border, label


_CODE_FENCE_RE = re.compile(
    r"```(\w*)\n(.*?)```",
    re.DOTALL,
)

# Matches runs of 3+ backticks that were NOT consumed by _CODE_FENCE_RE.
# These are unbalanced/orphaned fences (e.g. from truncated model output)
# that would otherwise open a code block in markdown-it and swallow content.
_ORPHAN_FENCE_RE = re.compile(r"`{3,}")


_pygments_formatter = _HtmlFormatter(nowrap=True, style="github-dark")


def _highlight_code(code: str, lang: str) -> str:
    """Syntax-highlight a code string using Pygments."""
    try:
        lexer = _get_lexer(lang, stripall=True)
    except Exception:
        lexer = _TextLexer(stripall=True)
    return _pygments_highlight(code, lexer, _pygments_formatter)


def _neutralize_orphan_fences(text: str) -> str:
    """Replace runs of 3+ backticks with single backtick-escaped equivalents.

    Turns e.g. ````` into `` `​`​` `` (backticks separated by zero-width
    spaces) so they render visibly but never open a code fence in markdown-it.
    Only call this on segments already known to be *outside* balanced fences.
    """
    return _ORPHAN_FENCE_RE.sub(
        lambda m: "\u200b".join("`" for _ in range(len(m.group()))),
        text,
    )


def _escape_html_outside_fences(text: str) -> str:
    """Escape HTML tags in *text* but leave fenced-code blocks untouched.

    This prevents HTML-like fragments in assistant output (e.g. ``<thinking>``,
    ``<result>``) from being treated as real DOM when the markdown is later
    rendered with ``html=True`` (needed for ``<details>`` tags elsewhere).
    Content inside code fences is left as-is so markdown-it can handle it.

    Any unbalanced/orphaned backtick fences (3+) in non-fence segments are
    neutralized so they cannot open a spurious code block that swallows
    subsequent HTML (``<details>`` etc.).
    """
    parts: list[str] = []
    last_end = 0
    for m in _CODE_FENCE_RE.finditer(text):
        segment = html.escape(text[last_end:m.start()])
        parts.append(_neutralize_orphan_fences(segment))
        parts.append(m.group(0))          # fence untouched
        last_end = m.end()
    segment = html.escape(text[last_end:])
    parts.append(_neutralize_orphan_fences(segment))
    return "".join(parts)


def _md_to_html_preview(text: str) -> str:
    """Convert text with markdown fenced code blocks to HTML.

    Code fences (```lang ... ```) become syntax-highlighted <pre><code> blocks.
    Everything else is html-escaped.  Orphan backtick fences are neutralized.
    """
    parts: list[str] = []
    last_end = 0
    for m in _CODE_FENCE_RE.finditer(text):
        before = text[last_end:m.start()]
        if before:
            parts.append(_neutralize_orphan_fences(html.escape(before)))
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
        parts.append(_neutralize_orphan_fences(html.escape(tail)))
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
        err_info = f'<span style="color:var(--wf-border-error)">{step["error_count"]} err</span>' if step["error_count"] else ''
        agent_badge = ''
        if step.get("agent"):
            agent_badge = (
                f'<span class="wf-badge" style="background:var(--wf-bg-user);color:var(--wf-border-user);'
                f'border:1px solid var(--wf-border-user);font-size:9px;">{html.escape(step["agent"])}</span>'
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
    """Build the styled HTML header banner and metadata table for a step detail panel."""
    bg, border, label = _card_style(step)
    role = step["role"]
    role_style = _ROLE_BADGE_STYLES.get(role, "background:#6b7280;color:white;")

    rows: list[tuple[str, str]] = [("Role", step['role'])]
    _optional = [
        ("agent", "Agent"), ("mode", "Mode"), ("model_id", "Model"),
        ("provider_id", "Provider"),
    ]
    for key, field in _optional:
        if step.get(key):
            rows.append((field, step[key]))
    if step.get("duration") is not None:
        rows.append(("Duration", f"{step['duration']}s"))

    created_str = _fmt_timestamp(step.get("time_created_ms"))
    if created_str:
        rows.append(("Created", created_str))
    completed_str = _fmt_timestamp(step.get("time_completed_ms"))
    if completed_str:
        rows.append(("Completed", completed_str))

    if step.get("finish"):
        rows.append(("Finish", step["finish"]))
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
            rows.append((field, step[key]))
    if step.get("root") and step.get("root") != step.get("cwd"):
        rows.append(("Root", step["root"]))

    # Banner
    banner = (
        f"<div class='dp-header' style='background:{border};'>"
        f"<span class='dp-badge'>#{step['index']}</span>"
        f"<span class='dp-badge' style='{role_style}'>{html.escape(role.title())}</span>"
        f"Step {step['index']} &mdash; {html.escape(label)}"
        f"</div>"
    )

    # Metadata table
    tr_parts = []
    for field, value in rows:
        escaped_val = html.escape(str(value))
        # Wrap code-like values
        if any(c in value for c in ("/", ".", "-")) and len(value) > 8:
            escaped_val = f"<code>{escaped_val}</code>"
        tr_parts.append(f"<tr><td>{html.escape(field)}</td><td>{escaped_val}</td></tr>")

    table = f"<table class='dp-meta-table'>{''.join(tr_parts)}</table>"
    return banner + table


def _safe_fence(text: str, lang: str = "") -> str:
    """Wrap *text* in a fenced code block whose delimiter is longer than any backtick run inside.

    CommonMark allows opening fences of 3+ backticks; the closing fence must be
    at least as long.  By choosing a delimiter longer than any run in *text* we
    guarantee the fence is never prematurely closed.
    """
    longest = 2                              # minimum fence is 3
    for m in re.finditer(r"`+", text):
        longest = max(longest, len(m.group()))
    fence = "`" * (longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def _format_tool_call_detail(p: dict) -> str:
    """Render a single tool_call part as a styled HTML block."""
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
        tc_dur = f" &mdash; {round((p['time_end'] - p['time_start']) / 1000, 2)}s"

    meta_parts: list[str] = []
    tool_id = p.get("tool_id", "")
    if tool_id:
        meta_parts.append(f"<code>{html.escape(tool_id)}</code>")
    tc_meta = p.get("metadata") or {}
    handled = {"output", "input", "preview"}
    if isinstance(tc_meta, dict):
        if tc_meta.get("sessionId"):
            sid = str(tc_meta["sessionId"])
            display = f"{sid[:16]}\u2026" if len(sid) > 16 else sid
            meta_parts.append(f"Session: <code>{html.escape(display)}</code>")
            handled.add("sessionId")
        meta_model = tc_meta.get("model")
        if isinstance(meta_model, dict):
            if meta_model.get("modelID"):
                meta_parts.append(f"Model: <code>{html.escape(str(meta_model['modelID']))}</code>")
            if meta_model.get("providerID"):
                meta_parts.append(f"Provider: <code>{html.escape(str(meta_model['providerID']))}</code>")
            handled.add("model")
        elif meta_model:
            meta_parts.append(f"Model: <code>{html.escape(str(meta_model))}</code>")
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
            meta_parts.append(f"{html.escape(mk)}: <code>{html.escape(str(mv))}</code>")
    if isinstance(inp, dict) and inp.get("subagent_type"):
        meta_parts.append(f"Subagent: <code>{html.escape(str(inp['subagent_type']))}</code>")

    meta_line = f"<div class='dp-tool-meta'>{' &middot; '.join(meta_parts)}</div>" if meta_parts else ""

    has_error = p.get("error") or p.get("status") == "error"
    section_cls = "dp-section dp-section-tool-error" if has_error else "dp-section dp-section-tool"

    tool_name = html.escape(p.get("tool_name", "?"))
    status = html.escape(p.get("status", "?"))
    title = html.escape(p.get("title") or "Untitled")

    # Input/Output details
    inp_detail = (
        f"<details class='dp-details'><summary>Input</summary>"
        f"<div class='dp-details-body'><pre>{html.escape(inp_str)}</pre></div>"
        f"</details>"
    )
    out_detail = (
        f"<details class='dp-details'><summary>Output</summary>"
        f"<div class='dp-details-body'><pre>{html.escape(str(out))}</pre></div>"
        f"</details>"
    )

    error_detail = ""
    tc_error = p.get("error")
    if tc_error:
        err_str = tc_error if isinstance(tc_error, str) else json.dumps(tc_error, indent=2, ensure_ascii=False)
        error_detail = (
            f"<details class='dp-details' open><summary>Error</summary>"
            f"<div class='dp-details-body'><pre>{html.escape(err_str)}</pre></div>"
            f"</details>"
        )

    return (
        f"<div class='{section_cls}'>"
        f"<div class='dp-section-title'>Tool</div>"
        f"<div class='dp-tool-header'><code>{tool_name}</code> &mdash; "
        f"<code>{status}</code>{tc_dur}</div>"
        f"<div style='font-weight:600;margin-bottom:4px;color:var(--ov-text);'>{title}</div>"
        f"{meta_line}"
        f"{inp_detail}{out_detail}{error_detail}"
        f"</div>"
    )


def _format_text_section(p: dict, section_type: str) -> str:
    """Render a text or reasoning part as a styled HTML section card."""
    cls = "dp-section-text" if section_type == "text" else "dp-section-reasoning"
    label = "Text" if section_type == "text" else "Reasoning"
    text = p.get("text", "")
    # Use the code-fence-aware HTML renderer for content with code blocks
    rendered = _md_to_html_preview(text) if text else ""
    return (
        f"<div class='dp-section {cls}'>"
        f"<div class='dp-section-title'>{label}</div>"
        f"<div class='dp-content'>{rendered}</div>"
        f"</div>"
    )


def _format_patch_section(p: dict) -> str:
    """Render a patch part as a styled HTML section card."""
    patch_hash = p.get("hash", "")
    patch_files = p.get("files", [])
    patch_id = p.get("id", "")
    meta_parts = []
    if patch_hash:
        meta_parts.append(f"<code>{html.escape(patch_hash[:12])}</code>")
    if patch_id:
        meta_parts.append(f"<code>{html.escape(patch_id)}</code>")
    meta_line = f"<div class='dp-tool-meta'>{' &middot; '.join(meta_parts)}</div>" if meta_parts else ""
    files_html = ""
    if patch_files:
        items = "".join(f"<li><code>{html.escape(f)}</code></li>" for f in patch_files)
        files_html = f"<div style='margin-top:4px;font-size:12px;'><strong>Files:</strong><ul style='margin:2px 0 0 16px;'>{items}</ul></div>"
    return (
        f"<div class='dp-section dp-section-patch'>"
        f"<div class='dp-section-title'>Patch</div>"
        f"{meta_line}{files_html}"
        f"</div>"
    )


def format_step_detail(step: dict) -> str:
    """Format detail panel for a selected step as a single HTML string."""
    header = _format_step_header(step)

    content_parts = []
    for p in step["parts"]:
        ptype = p.get("type", "unknown")
        if ptype == "text":
            content_parts.append(_format_text_section(p, "text"))
        elif ptype == "reasoning":
            content_parts.append(_format_text_section(p, "reasoning"))
        elif ptype == "tool_call":
            content_parts.append(_format_tool_call_detail(p))
        elif ptype in ("step_start", "step_finish"):
            pass
        elif ptype == "snapshot":
            content_parts.append(
                "<div class='dp-section dp-section-snapshot'>"
                "<div class='dp-section-title'>Snapshot</div>"
                "<em>data omitted</em></div>"
            )
        elif ptype == "patch":
            content_parts.append(_format_patch_section(p))
        else:
            content_parts.append(
                f"<div class='dp-section'>"
                f"<div class='dp-section-title'>{html.escape(ptype)}</div>"
                f"</div>"
            )

    content = "\n".join(content_parts) if content_parts else "<em>No content</em>"
    return header + content
