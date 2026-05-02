"""S-031 PR2 regression tests
(architecture-audit-2026-05-02 P1-6).

Per CLAUDE.md § Architecture rules § 5: the UI unit owns the
"what to display" decision. Pre-PR ``src/bot/telegram_query_bot.py``
inlined ``_format_signal_row``, ``_SIGNAL_STATUS_EMOJI``, and
``_render_signals_block`` — duplicating logic that ``processor``
already partially owned (``get_recent_signals``).

Post-PR:
  * ``src.ui.processor.get_signals_block(strategy_filter, limit) → str``
    is the single rendering path.
  * The bot's ``_render_signals_block`` is a one-line wrapper.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.ui.processor import (
    get_signals_block,
    _format_signal_row,
    _SIGNAL_STATUS_EMOJI,
)


@pytest.fixture()
def tmp_audit(tmp_path, monkeypatch):
    audit = tmp_path / "signal_audit.jsonl"
    monkeypatch.setenv("SIGNAL_AUDIT_PATH", str(audit))
    return audit


def _write_records(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# _format_signal_row
# ---------------------------------------------------------------------------


class TestFormatSignalRow:
    def test_full_record_renders_clean(self):
        rec = {
            "logged_at_utc": "2026-05-02T21:30:15.123",
            "strategy": "vwap",
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.001,
            "status": "submitted",
            "reason": "",
        }
        out = _format_signal_row(rec)
        assert out.startswith("🟢 2026-05-02 21:30:15")
        assert "strategy=vwap" in out
        assert "BTCUSDT buy 0.0010" in out
        assert "→ submitted" in out

    def test_unknown_status_uses_default_bullet(self):
        rec = {
            "logged_at_utc": "2026-05-02T21:30:15",
            "strategy": "vwap", "symbol": "X",
            "side": "buy", "qty": 0.001, "status": "weird_status",
        }
        assert _format_signal_row(rec).startswith("• ")

    def test_long_reason_truncated(self):
        rec = {
            "logged_at_utc": "2026-05-02T21:30:15",
            "strategy": "vwap", "symbol": "X",
            "side": "buy", "qty": 0.001, "status": "skipped",
            "reason": "x" * 200,
        }
        out = _format_signal_row(rec)
        # Reason gets capped at 60 chars.
        assert "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" in out
        assert "x" * 200 not in out

    def test_no_underscores_in_markdown_unsafe_position(self):
        """The original bug: pipeline statuses like ``failed_validation``
        contain underscores that break Telegram's legacy Markdown parser.
        The formatter MUST emit plain text (no leading/trailing
        underscores wrapping the status)."""
        rec = {
            "logged_at_utc": "2026-05-02T21:30:15",
            "strategy": "vwap", "symbol": "X",
            "side": "buy", "qty": 0.001, "status": "failed_validation",
        }
        out = _format_signal_row(rec)
        # The status appears verbatim, not wrapped in *…* or _…_
        assert "→ failed_validation" in out
        assert "*failed_validation*" not in out
        assert "_failed_validation_" not in out


class TestEmojiMap:
    @pytest.mark.parametrize("status,emoji", [
        ("submitted", "🟢"), ("dry_run", "🟡"), ("skipped", "⚪️"),
        ("halted", "🛑"), ("failed_validation", "🔴"),
        ("failed_exchange", "❌"), ("refused", "🚫"),
        ("multi_account_dispatched", "🟢"),
    ])
    def test_known_statuses_have_emojis(self, status, emoji):
        assert _SIGNAL_STATUS_EMOJI[status] == emoji


# ---------------------------------------------------------------------------
# get_signals_block
# ---------------------------------------------------------------------------


class TestGetSignalsBlock:
    def test_empty_audit_returns_empty_state(self, tmp_audit):
        out = get_signals_block()
        assert "No signals logged" in out
        assert str(tmp_audit) in out  # operator gets the path

    def test_empty_audit_with_filter_mentions_filter(self, tmp_audit):
        out = get_signals_block(strategy_filter="vwap")
        assert "for vwap" in out

    def test_renders_records_newest_last(self, tmp_audit):
        _write_records(tmp_audit, [
            {"logged_at_utc": "2026-05-02T20:00:00",
             "strategy": "vwap", "symbol": "BTCUSDT",
             "side": "buy", "qty": 0.001, "status": "submitted"},
            {"logged_at_utc": "2026-05-02T20:05:00",
             "strategy": "vwap", "symbol": "BTCUSDT",
             "side": "sell", "qty": 0.002, "status": "skipped",
             "reason": "no_signal"},
        ])
        out = get_signals_block(limit=10)

        assert "Last 2 signals" in out
        assert "🟢 2026-05-02 20:00:00" in out
        assert "⚪️ 2026-05-02 20:05:00" in out
        assert "→ skipped — no_signal" in out

    def test_strategy_filter_works(self, tmp_audit):
        _write_records(tmp_audit, [
            {"logged_at_utc": "2026-05-02T20:00:00",
             "strategy": "vwap", "symbol": "X", "side": "buy",
             "qty": 0.001, "status": "submitted"},
            {"logged_at_utc": "2026-05-02T20:05:00",
             "strategy": "turtle_soup", "symbol": "Y", "side": "sell",
             "qty": 0.002, "status": "submitted"},
        ])
        out = get_signals_block(strategy_filter="vwap", limit=10)
        assert "Last 1 signals — vwap" in out
        assert "strategy=vwap" in out
        assert "strategy=turtle_soup" not in out

    def test_limit_caps_rendered_rows(self, tmp_audit):
        _write_records(tmp_audit, [
            {"logged_at_utc": f"2026-05-02T20:00:{i:02d}",
             "strategy": "vwap", "symbol": "X", "side": "buy",
             "qty": 0.001, "status": "submitted"}
            for i in range(20)
        ])
        out = get_signals_block(limit=5)
        # Header reflects rendered count.
        assert "Last 5 signals" in out
        # 5 emoji-prefixed rows.
        assert out.count("🟢 2026-05-02 20:00:") == 5


# ---------------------------------------------------------------------------
# Bot back-compat wrapper
# ---------------------------------------------------------------------------


class TestBotWrapperCallsProcessor:
    def test_render_signals_block_is_thin_wrapper(self):
        """Source-level grep: the bot's wrapper calls the processor
        and no longer reads the audit file directly."""
        bot_src = Path("src/bot/telegram_query_bot.py").read_text()
        start = bot_src.index("def _render_signals_block(")
        # Slice through the next def.
        after = bot_src.index("\ndef ", start + 1)
        wrapper_src = bot_src[start:after]
        assert "get_signals_block" in wrapper_src, (
            "_render_signals_block must delegate to "
            "processor.get_signals_block per Architecture rule § 5"
        )
        # The previous direct-audit-read code is gone.
        assert "_read_audit_tail(" not in wrapper_src
        assert "_format_signal_row(" not in wrapper_src
