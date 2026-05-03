"""Tests for src/units/ui/telegram_format.py.

Pin the contract: every Telegram message in the codebase routes through
this module, so the operator's "uniform format with collapsable
sections" requirement only holds if the formatter behaves predictably:

- HTML escaping happens once and only on user-supplied content.
- ``<blockquote expandable>`` is the wrapper for section bodies in
  HTML mode.
- Sections are ordered by ``priority`` (lower = first).
- Plain-text rendering matches the same payload section-for-section.
"""
from __future__ import annotations

from src.units.ui.telegram_format import (
    Section,
    bullet_list,
    html_escape,
    kv_block,
    render_html,
    render_plain,
)


def test_html_escape_replaces_amp_lt_gt_only():
    # Quotes pass through; & < > are escaped exactly once.
    assert html_escape("a & b < c > d 'e' \"f\"") == "a &amp; b &lt; c &gt; d 'e' \"f\""


def test_html_escape_handles_none():
    assert html_escape(None) == ""


def test_render_html_wraps_each_section_in_expandable_blockquote():
    body = render_html(
        header="Pipeline result: status=submitted | strategy=vwap",
        sections=[
            Section(summary="Strategy — vwap", body="Symbol: BTCUSDT\nSide: buy"),
            Section(summary="Order package — generated", body="Entry: 50000"),
        ],
    )
    assert "<b>Pipeline result: status=submitted | strategy=vwap</b>" in body
    # One blockquote per section.
    assert body.count("<blockquote expandable>") == 2
    assert body.count("</blockquote>") == 2
    assert "<b>Strategy — vwap</b>" in body
    assert "<b>Order package — generated</b>" in body


def test_render_html_orders_sections_by_priority():
    body = render_html(
        header="Hdr",
        sections=[
            Section(summary="late", body="z", priority=99),
            Section(summary="first", body="a", priority=1),
            Section(summary="middle", body="m", priority=50),
        ],
    )
    first = body.index("<b>first</b>")
    mid = body.index("<b>middle</b>")
    late = body.index("<b>late</b>")
    assert first < mid < late


def test_render_html_escapes_html_in_user_content():
    body = render_html(
        header="A & B",
        sections=[Section(summary="<script>", body="x>y & z<w")],
    )
    # Header escaped, summary escaped, body escaped.
    assert "<b>A &amp; B</b>" in body
    assert "&lt;script&gt;" in body
    assert "x&gt;y &amp; z&lt;w" in body
    # No raw injection.
    assert "<script>" not in body


def test_render_plain_renders_inline_indented_sections():
    txt = render_plain(
        header="Hdr",
        sections=[
            Section(summary="Performance — 0 errors",
                    body="Ticks ok: 4\nTicks err: 0"),
        ],
    )
    assert "Hdr" in txt
    assert "• Performance — 0 errors" in txt
    # Body lines indented two spaces.
    assert "  Ticks ok: 4" in txt
    assert "  Ticks err: 0" in txt


def test_render_html_truncates_overlong_payloads():
    # Build a section with a 5000-char body; expect a truncation marker.
    body = render_html(
        header="H",
        sections=[Section(summary="big", body="x" * 5000)],
    )
    assert len(body) <= 4096
    assert "…" in body or "truncated" in body


def test_kv_block_formats_label_value_lines():
    txt = kv_block([("Strategy", "vwap"), ("Confidence", None), ("Entry", 50000)])
    assert txt == "Strategy: vwap\nConfidence: —\nEntry: 50000"


def test_bullet_list_handles_empty_and_populated():
    assert bullet_list([]) == "(none)"
    assert bullet_list(["a", "b"]) == "- a\n- b"
    assert bullet_list([], empty="(no entries)") == "(no entries)"


def test_section_with_empty_body_shows_placeholder_in_blockquote():
    """Operator can still tap-to-expand; sees ``(no entries)`` rather
    than a blank line. Makes the collapsable UX honest."""
    body = render_html(
        header="H",
        sections=[Section(summary="Errors — 0", body="")],
    )
    assert "<blockquote expandable>(no entries)</blockquote>" in body
