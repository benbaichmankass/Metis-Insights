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


# ---------------------------------------------------------------------------
# Truncation must keep HTML well-formed — Telegram rejects unbalanced tags
# with "Can't parse entities: can't find end tag corresponding to start tag
# blockquote". This was the failure mode hit on /last5 when 5 trades with
# long entry_reason / notes pushed the rendered HTML past 4096 chars.
# ---------------------------------------------------------------------------


def _is_balanced(html: str) -> bool:
    """Cheap structural check — counts of opening and closing tags match."""
    return (
        html.count("<blockquote") == html.count("</blockquote>")
        and html.count("<b>") == html.count("</b>")
    )


def test_truncate_preserves_balanced_blockquotes_when_cut_at_section_seam():
    """Many small sections — truncation should land at a section
    boundary so every opened blockquote has its matching close."""
    sections = [
        Section(summary=f"Section {i}", body=("payload_%d " % i) * 80)
        for i in range(20)
    ]
    body = render_html(header="Hdr", sections=sections)
    assert len(body) <= 4096
    assert _is_balanced(body), (
        f"Truncated HTML must be balanced. Got "
        f"<blockquote opens={body.count('<blockquote')} "
        f"closes={body.count('</blockquote>')}\n"
        f"Tail: {body[-200:]!r}"
    )


def test_truncate_balances_blockquote_when_no_section_seam_in_budget():
    """One huge section whose body alone exceeds 4096 chars — there's
    no section seam to cut at. The truncate helper must still produce
    a balanced message by appending </blockquote>."""
    body = render_html(
        header="H",
        sections=[Section(summary="big", body="x" * 10000)],
    )
    assert len(body) <= 4096
    assert _is_balanced(body), (
        f"Mid-section truncation must close the open blockquote. "
        f"Tail: {body[-100:]!r}"
    )
    # Closing tag must be present at the tail.
    assert "</blockquote>" in body


def test_truncate_does_not_chop_mid_tag():
    """Force a cut that would otherwise land inside the literal text
    ``<blockquote expandable>``. The healer must roll back to before
    the unbalanced ``<``."""
    # Build a body with predictable inner content that would push the
    # cut into the closing tag of the second section.
    long_body = "a" * 4000
    sections = [
        Section(summary="s1", body=long_body),
        Section(summary="s2", body=long_body),
    ]
    body = render_html(header="H", sections=sections)
    assert len(body) <= 4096
    # No literal substring like "<blockq" or "<bloc" left dangling.
    # If the helper rolled back correctly, the last "<" must precede
    # a ">" somewhere.
    last_open = body.rfind("<")
    last_close = body.rfind(">")
    assert last_close > last_open, (
        f"Truncated HTML ends mid-tag. Tail: {body[-80:]!r}"
    )
    assert _is_balanced(body)


def test_truncate_real_world_last5_shape():
    """Reproduces the /last5 failure: 5 trade rows with realistic
    long fields (entry_reason / notes / setup_type)."""
    long_reason = (
        "ICT FVG mean-reversion long; bias=bullish from London open; "
        "killzone=NY AM open ; structure=BoS at 50k ; aligned with daily "
        "bias and 4h FVG cluster ; manual notes appended by operator "
        "spanning multiple lines for narrative trace."
    )
    sections = [
        Section(
            summary=f"Trade #{i} BTCUSDT long PnL +12.34 (closed)",
            body="\n".join([
                f"timestamp: 2026-05-03T1{i}:00:00",
                "entry: 50000.0 | SL: 49500.0",
                "TP1: 50500 | TP2: 51000 | TP3: 52000",
                "size: 0.001",
                "setup_type: FVG | bias: bullish | killzone: NY-AM",
                f"entry_reason: {long_reason}",
                "exit_reason: TP1 hit",
                "PnL: 12.34 (0.05%)",
                f"notes: {long_reason}",
            ]),
        )
        for i in range(5)
    ]
    body = render_html(header="📒 Last 5 trades", sections=sections)
    assert len(body) <= 4096
    assert _is_balanced(body), (
        f"Real-world /last5 shape failed balance check. "
        f"<blockquote opens={body.count('<blockquote')} "
        f"closes={body.count('</blockquote>')}\n"
        f"Tail: {body[-200:]!r}"
    )


def test_truncate_short_input_unchanged():
    """No truncation when the message fits the budget — no marker added."""
    body = render_html(
        header="H",
        sections=[Section(summary="tiny", body="ok")],
    )
    assert "…(truncated)" not in body
    assert "<blockquote expandable>ok</blockquote>" in body


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
