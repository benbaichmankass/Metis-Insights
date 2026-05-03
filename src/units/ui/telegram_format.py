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
    """

    summary: str
    body: str = ""
    priority: int = 50
    empty_body_text: str = "(no entries)"


def _truncate(text: str, limit: int = _TELEGRAM_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(_TRUNCATE_MARKER))] + _TRUNCATE_MARKER


def _section_html(section: Section) -> str:
    summary = html_escape(section.summary).strip() or "(unnamed section)"
    body = section.body or section.empty_body_text
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
