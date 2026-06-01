"""Bar-close debounce gate (PERF-20260601-001).

The open-package gate only blocks re-entry while a package is *open*; when one
closes mid-bar (the reconciler records an exchange-side SL/TP fire) the gate
frees and a bar-close strategy re-fires its still-valid breakout on the next
tick — within the SAME bar. On the 2 h trend_donchian this stacked 9 packages
in ~1 h on 2026-06-01 and flooded the journal with ``intent_noop`` rejection
rows, skewing per-strategy stats.

``_same_bar_entry_for_strategy`` suppresses a second actionable dispatch for the
same strategy+symbol inside the same timeframe bucket as the package it already
created this bar. Contracts under test:

1. Timeframe parsing (``2h`` → 7200 …).
2. A package created *this* bar → block (returns the package id + bar size).
3. A package created in a *previous* bar → no block.
4. Kill-switch ``STRATEGY_BAR_DEBOUNCE_DISABLED`` → no block.
5. Unknown / missing timeframe → no block (fail-open).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runtime.strategy_monocle import (
    _same_bar_entry_for_strategy,
    _timeframe_seconds,
)
from src.units.db.database import Database


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _cfg(monkeypatch, timeframe):
    """Pin the strategy's configured timeframe (the helper reads config)."""
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda *a, **k: {"trend_donchian": {"timeframe": timeframe}},
        raising=False,
    )


def _insert(db, *, pkg_id, created_at, strategy="trend_donchian",
            status="closed", symbol="BTCUSDT"):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": strategy,
        "symbol": symbol,
        "direction": "long",
        "entry": 73_000.0,
        "sl": 72_000.0,
        "tp": 80_000.0,
        "confidence": 0.5,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "meta": {},
    })


# ---------------------------------------------------------------------------
# 1. timeframe parsing
# ---------------------------------------------------------------------------
def test_timeframe_seconds_table_and_generic():
    assert _timeframe_seconds("2h") == 7200
    assert _timeframe_seconds("15m") == 900
    assert _timeframe_seconds("1d") == 86400
    assert _timeframe_seconds("90m") == 5400      # generic <int><unit> parse
    assert _timeframe_seconds("bogus") is None
    assert _timeframe_seconds(None) is None
    assert _timeframe_seconds("") is None


# ---------------------------------------------------------------------------
# 2. same bar → block
# ---------------------------------------------------------------------------
def test_same_bar_blocks(tmp_journal, monkeypatch):
    _cfg(monkeypatch, "2h")
    _insert(tmp_journal, pkg_id="td-now",
            created_at=datetime.now(timezone.utc).isoformat())
    r = _same_bar_entry_for_strategy("trend_donchian", symbol="BTCUSDT")
    assert r is not None
    assert r["order_package_id"] == "td-now"
    assert r["bar_seconds"] == 7200


# ---------------------------------------------------------------------------
# 3. previous bar → no block
# ---------------------------------------------------------------------------
def test_previous_bar_does_not_block(tmp_journal, monkeypatch):
    _cfg(monkeypatch, "2h")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _insert(tmp_journal, pkg_id="td-old", created_at=old)
    assert _same_bar_entry_for_strategy("trend_donchian", symbol="BTCUSDT") is None


def test_symbol_scoped(tmp_journal, monkeypatch):
    """A same-bar package on a different symbol must not block."""
    _cfg(monkeypatch, "2h")
    _insert(tmp_journal, pkg_id="td-mes", symbol="MES",
            created_at=datetime.now(timezone.utc).isoformat())
    assert _same_bar_entry_for_strategy("trend_donchian", symbol="BTCUSDT") is None


# ---------------------------------------------------------------------------
# 4. kill-switch
# ---------------------------------------------------------------------------
def test_kill_switch_disables(tmp_journal, monkeypatch):
    _cfg(monkeypatch, "2h")
    monkeypatch.setenv("STRATEGY_BAR_DEBOUNCE_DISABLED", "true")
    _insert(tmp_journal, pkg_id="td-now",
            created_at=datetime.now(timezone.utc).isoformat())
    assert _same_bar_entry_for_strategy("trend_donchian", symbol="BTCUSDT") is None


# ---------------------------------------------------------------------------
# 5. unknown / missing timeframe → fail-open
# ---------------------------------------------------------------------------
def test_unknown_timeframe_fails_open(tmp_journal, monkeypatch):
    _cfg(monkeypatch, "not-a-tf")
    _insert(tmp_journal, pkg_id="td-now",
            created_at=datetime.now(timezone.utc).isoformat())
    assert _same_bar_entry_for_strategy("trend_donchian", symbol="BTCUSDT") is None


def test_missing_strategy_name_bypasses():
    assert _same_bar_entry_for_strategy(None) is None
    assert _same_bar_entry_for_strategy("") is None
