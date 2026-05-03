"""S-telegram-format Phase 3 — collapsable /signals + /last5 renderers.

The renderers themselves live in ``src/units/ui/processor.py``. This
file pins the contract a refactor mustn't break:

- ``get_signals_block(use_html=True)`` groups by status. Each bucket
  is one ``<blockquote expandable>`` section with the per-row detail
  inside; the summary line names the count so the operator's eye
  catches the distribution.
- ``render_recent_trades_collapsable`` returns ONE message with each
  trade as its own collapsable section (pre-PR the bot sent one
  message per trade — noisy).
- DB-sourced free-text fields (notes, entry_reason, exit_reason)
  pass through HTML escape, so the legacy parse-mode-Markdown
  BadRequest pattern (BUG-009 / BUG-030 / BUG-031) cannot recur on
  this surface.
"""
from __future__ import annotations

from unittest.mock import patch

from src.units.ui.processor import (
    get_signals_block,
    render_recent_trades_collapsable,
)


# ---------------------------------------------------------------------------
# /signals — HTML collapsable mode
# ---------------------------------------------------------------------------


_SAMPLE_SIGNALS = [
    {
        "logged_at_utc": "2026-05-03T09:00:00Z",
        "strategy": "vwap", "symbol": "BTCUSDT", "side": "buy",
        "qty": 0.001, "status": "failed_validation",
        "reason": "ALLOW_LIVE_TRADING=true is required for live submission",
    },
    {
        "logged_at_utc": "2026-05-03T08:45:00Z",
        "strategy": "vwap", "symbol": "BTCUSDT", "side": "sell",
        "qty": 0.001, "status": "failed_validation", "reason": "same",
    },
    {
        "logged_at_utc": "2026-05-03T08:30:00Z",
        "strategy": "turtle_soup", "symbol": "BTCUSDT", "side": "buy",
        "qty": 0.002, "status": "submitted", "reason": None,
    },
    {
        "logged_at_utc": "2026-05-03T08:00:00Z",
        "strategy": "turtle_soup", "symbol": "BTCUSDT", "side": "buy",
        "qty": 0.001, "status": "refused",
        "reason": "strategy daily loss reached",
    },
]


def test_signals_html_groups_by_status():
    with patch("src.units.ui.processor.get_recent_signals",
               return_value=list(_SAMPLE_SIGNALS)):
        body = get_signals_block(limit=10, use_html=True)
    # Header.
    assert "<b>📡 Last 4 signals</b>" in body
    # Three distinct status buckets → three blockquotes.
    assert body.count("<blockquote expandable>") == 3
    assert "failed_validation — 2 signals" in body
    assert "submitted — 1 signals" in body
    assert "refused — 1 signals" in body


def test_signals_html_failures_render_first():
    """Per-status priority: failed_validation/refused/error appear
    above submitted/dry_run so the operator's eye lands on actionable
    buckets first."""
    with patch("src.units.ui.processor.get_recent_signals",
               return_value=list(_SAMPLE_SIGNALS)):
        body = get_signals_block(limit=10, use_html=True)
    fail_pos = body.index("failed_validation")
    refused_pos = body.index("refused")
    submitted_pos = body.index("submitted")
    assert fail_pos < submitted_pos
    assert refused_pos < submitted_pos


def test_signals_html_empty_state_uses_collapsable_envelope():
    with patch("src.units.ui.processor.get_recent_signals", return_value=[]):
        body = get_signals_block(limit=10, use_html=True)
    assert "<b>📡 Recent signals</b>" in body
    assert "No signals logged yet" in body


def test_signals_legacy_plain_default_unchanged():
    with patch("src.units.ui.processor.get_recent_signals",
               return_value=list(_SAMPLE_SIGNALS)):
        body = get_signals_block(limit=10)
    # No HTML markup in legacy output.
    assert "<blockquote" not in body
    assert "📡 Last 4 signals" in body


# ---------------------------------------------------------------------------
# /last5 — collapsable trade renderer
# ---------------------------------------------------------------------------


def _trade_row(trade_id, **overrides):
    base = {
        "id": trade_id,
        "timestamp": "2026-05-03 09:00:00",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_price": 50_000.0,
        "stop_loss": 49_500.0,
        "take_profit_1": 51_000.0,
        "take_profit_2": None,
        "take_profit_3": None,
        "position_size": 0.01,
        "setup_type": "fvg",
        "bias": "bullish",
        "killzone": "london",
        "entry_reason": "test entry reason",
        "exit_reason": "test exit reason",
        "pnl": 25.50,
        "pnl_percent": 1.2,
        "status": "closed",
        "notes": "test notes",
        "is_backtest": False,
        "created_at": "2026-05-03 08:50:00",
    }
    base.update(overrides)
    return base


def test_recent_trades_collapsable_renders_one_section_per_trade():
    rows = [_trade_row(1), _trade_row(2), _trade_row(3, pnl=-5.20)]
    body = render_recent_trades_collapsable(rows, title="📒 Last 5 trades")
    assert "<b>📒 Last 5 trades — 3 rows</b>" in body
    assert body.count("<blockquote expandable>") == 3
    # Per-trade summary line carries id + symbol + direction + pnl.
    assert "Trade #1 — BTCUSDT long PnL $+25.50" in body
    assert "Trade #3 — BTCUSDT long PnL $-5.20" in body


def test_recent_trades_collapsable_handles_empty_input():
    body = render_recent_trades_collapsable([], title="📒 Last 5 trades")
    assert "<b>📒 Last 5 trades</b>" in body
    assert "No trades found" in body


def test_recent_trades_collapsable_escapes_freetext_fields():
    """DB-sourced fields routinely contain ``<``, ``&``, ``*``, ``_``.
    The formatter must HTML-escape so the message reaches Telegram
    instead of triggering ``BadRequest: Can't parse entities`` (the
    failure mode of BUG-009 / BUG-030 / BUG-031)."""
    rows = [_trade_row(
        42,
        notes="reason: <stop hit> & cleared",
        entry_reason="A & B <crossed>",
        exit_reason="profit > target",
    )]
    body = render_recent_trades_collapsable(rows)
    # User text appears with escaped angle brackets; raw ones don't
    # leak (would be markup tags otherwise).
    assert "&lt;stop hit&gt;" in body
    assert "A &amp; B" in body
    assert "profit &gt; target" in body
    # The structural <blockquote> tag is still present (not escaped).
    assert "<blockquote expandable>" in body


def test_recent_trades_collapsable_marks_backtest_rows():
    rows = [_trade_row(1, is_backtest=True)]
    body = render_recent_trades_collapsable(rows)
    assert "BACKTEST row" in body
