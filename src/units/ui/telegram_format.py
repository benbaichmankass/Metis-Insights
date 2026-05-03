"""Unified Telegram message formatter — collapsable sections.

The operator wants every recurring Telegram message (pings, hourly
summaries, command responses) to follow one shape: a one-line summary
header per section, with the long detail collapsed inside an
expandable HTML blockquote so the chat stays scannable.

This module is the single entry point for that shape. It produces an
HTML-mode body (Telegram Bot API ``parse_mode="HTML"``) using
``<blockquote expandable>`` for the body of each section. Clients tap
the blockquote to expand it inline.

Design rules
------------

* **Stateless.** Every public function returns a string. No I/O, no
  side effects, no logging.
* **Safe by default.** All caller-supplied content is HTML-escaped
  before composition. Callers MUST NOT pass raw HTML in section
  ``summary`` / ``body`` lines unless they explicitly use
  ``raw_html_body=True`` (used internally for nested sections).
* **Plain-text fallback.** ``render_plain(...)`` produces a usable
  no-parse-mode rendering of the same payload (sections expanded
  inline) so callers that target ``parse_mode=None`` (legacy hourly
  report path, ``send_via_alert_manager``) keep delivering content
  even on Telegram clients that don't yet support expandable
  blockquotes.

Telegram quirks
---------------

* ``<blockquote expandable>`` is supported by official clients (Bot
  API 7.0+). Older clients fall back to a non-collapsable blockquote,
  which is still readable.
* Telegram caps message length at 4096 chars. ``render_html`` truncates
  long bodies with a ``…`` marker rather than splitting; callers that
  need pagination should slice section bodies before passing them in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence


_TELEGRAM_MAX_CHARS = 4096
_TRUNCATE_MARKER = "\n…(truncated)"


def html_escape(value: object) -> str:
    """HTML-escape *value* for Telegram parse_mode='HTML'.

    Mirrors the escape used by ``src.units.ui.processor._h`` so the
    two stay in sync. Telegram only requires ``&``, ``<``, ``>`` to be
    escaped — quotes are passed through verbatim.
    """
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


@dataclass
class Section:
    """One collapsable section of a Telegram message.

    ``summary`` is the always-visible one-liner ("Performance — 3
    errors in past hour"). ``body`` is the detail dropped inside
    ``<blockquote expandable>``; line breaks are preserved.

    ``priority`` lets the renderer order or filter sections; lower
    numbers render first. Default is 50 (mid).

    ``empty_body_text`` is shown inside the blockquote when ``body``
    is empty, so the operator can still expand the section and see
    "(no entries)" rather than a blank line.

    ``body_is_html``: when True, the renderer skips HTML-escaping the
    body and treats it as already-sanitised HTML — useful when the
    caller has produced trusted HTML by calling another processor
    renderer that already escaped untrusted content. Default False so
    user-supplied content is escaped.
    """

    summary: str
    body: str = ""
    priority: int = 50
    empty_body_text: str = "(no entries)"
    body_is_html: bool = False


def _truncate(text: str, limit: int = _TELEGRAM_MAX_CHARS) -> str:
    """Cap *text* at *limit* characters while keeping HTML well-formed.

    Telegram rejects messages whose tags don't balance (``Can't parse
    entities: can't find end tag corresponding to start tag
    "blockquote"``). Pre-fix this helper was a naive
    ``text[:budget]`` slice — when the cut landed inside a
    ``<blockquote expandable>...</blockquote>`` block, the closing tag
    was lost and the whole message was rejected. ``/last5`` triggered
    this whenever the cumulative trade-row HTML exceeded 4096 chars
    (5 trades × ~600 chars + a long ``entry_reason`` field).

    Strategy (in order):

    1. **Within budget** — return unchanged.
    2. **Section seam in budget** — the renderer joins sections with
       ``"\\n\\n"`` and each section ends with ``</blockquote>``;
       cutting just after that seam guarantees every opened tag has
       its matching close, no healing needed.
    3. **No seam in budget** — naive truncate with the budget reduced
       by the worst-case healing overhead (one ``</blockquote>`` plus
       one ``</b>``), then (a) roll back to before any incomplete
       ``<...`` so we never cut mid-tag, then (b) append the missing
       close tags. Final length is guaranteed ``≤ limit``.
    """
    if len(text) <= limit:
        return text

    marker_len = len(_TRUNCATE_MARKER)

    # (2) Prefer a clean section-boundary cut. No healing needed; only
    # the marker has to fit, so the seam may sit at ``limit - marker_len``.
    seam = "</blockquote>\n\n"
    seam_search_end = (limit - marker_len) - len("</blockquote>") + len(seam)
    if seam_search_end > 0:
        safe_cut = text.rfind(seam, 0, seam_search_end)
        if safe_cut >= 0:
            end = safe_cut + len("</blockquote>")
            return text[:end] + _TRUNCATE_MARKER

    # (3) Mid-cut path. Reserve room for the worst-case heal so the
    # final string can never overflow the limit.
    heal_reserve = len("</blockquote>") + len("</b>")
    budget = limit - marker_len - heal_reserve
    if budget <= 0:
        return _TRUNCATE_MARKER[:limit]

    truncated = text[:budget]

    # (3a) If the cut landed mid-tag (an unmatched ``<`` after the
    # last ``>``), roll back to before the broken tag.
    last_open = truncated.rfind("<")
    last_close = truncated.rfind(">")
    if last_open > last_close:
        truncated = truncated[:last_open]

    # (3b) Balance ``<blockquote ...>`` opens with closes. The opening
    # form has attributes ("<blockquote expandable>") so we count the
    # bare prefix; closes are always the literal "</blockquote>".
    bq_opens = truncated.count("<blockquote")
    bq_closes = truncated.count("</blockquote>")
    if bq_opens > bq_closes:
        truncated += "</blockquote>" * (bq_opens - bq_closes)

    # (3c) Same for ``<b>`` headers.
    b_opens = truncated.count("<b>")
    b_closes = truncated.count("</b>")
    if b_opens > b_closes:
        truncated += "</b>" * (b_opens - b_closes)

    return truncated + _TRUNCATE_MARKER


def _section_html(section: Section) -> str:
    summary = html_escape(section.summary).strip() or "(unnamed section)"
    body = section.body or section.empty_body_text
    if section.body_is_html and section.body:
        body_html = body.rstrip()
    else:
        body_html = html_escape(body).rstrip()
    if not body_html:
        body_html = html_escape(section.empty_body_text)
    return (
        f"<b>{summary}</b>\n"
        f"<blockquote expandable>{body_html}</blockquote>"
    )


def _section_plain(section: Section) -> str:
    summary = (section.summary or "").strip() or "(unnamed section)"
    body = (section.body or section.empty_body_text).rstrip()
    indented = "\n".join(f"  {ln}" if ln else "" for ln in body.split("\n"))
    return f"• {summary}\n{indented}"


def _ordered(sections: Iterable[Section]) -> List[Section]:
    return sorted(sections, key=lambda s: s.priority)


def render_html(
    *,
    header: str,
    sections: Sequence[Section],
    footer: Optional[str] = None,
) -> str:
    """Render *header* + *sections* + *footer* as a Telegram HTML body.

    The output is suitable for ``send_telegram_direct(text,
    parse_mode="HTML")``. ``header`` becomes the bold leading line.
    Each section renders as a bold summary line with its body
    collapsed inside an expandable blockquote.
    """
    parts: List[str] = []
    if header:
        parts.append(f"<b>{html_escape(header)}</b>")
    for sec in _ordered(sections):
        parts.append(_section_html(sec))
    if footer:
        parts.append(html_escape(footer))
    return _truncate("\n\n".join(p for p in parts if p))


def render_plain(
    *,
    header: str,
    sections: Sequence[Section],
    footer: Optional[str] = None,
) -> str:
    """Render the same payload as plain text.

    Used when the destination must run with ``parse_mode=None`` —
    e.g. ``send_via_alert_manager`` (which routes hourly summaries
    through the parse-mode-less channel to avoid Telegram's HTML
    parser rejecting characters like ``<= 15m``).

    Sections render as bullet+summary with the body indented two
    spaces. There is no collapsing on this surface; the operator sees
    everything inline.
    """
    parts: List[str] = []
    if header:
        parts.append(header)
    for sec in _ordered(sections):
        parts.append(_section_plain(sec))
    if footer:
        parts.append(footer)
    return _truncate("\n\n".join(p for p in parts if p))


def kv_block(rows: Sequence[tuple]) -> str:
    """Format ``[(label, value), ...]`` as one-key-per-line plain text.

    Convenience for building section bodies. ``None`` values render
    as ``—``.
    """
    out: List[str] = []
    for label, value in rows:
        rendered = "—" if value is None else str(value)
        out.append(f"{label}: {rendered}")
    return "\n".join(out)


def bullet_list(items: Sequence[str], empty: str = "(none)") -> str:
    """Format *items* as ``- item`` lines, or *empty* when the list is empty."""
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)
