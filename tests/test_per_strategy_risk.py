"""
S-005 M2: Per-strategy risk caps.

Tests for:
  - inject_per_strategy_counters DB queries
  - safe_place_order refusal on MAX_POS_PER_STRATEGY
  - safe_place_order refusal on MAX_DAILY_LOSS_PER_STRATEGY_USD
  - orders without strategy_name are unaffected by per-strategy caps
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date

import pytest

from src.runtime.risk_counters import inject_per_strategy_counters
from src.runtime.orders import safe_place_order


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _make_db(rows: list[dict]) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            pnl REAL DEFAULT 0,
            status TEXT DEFAULT 'open',
            is_backtest BOOLEAN DEFAULT 0,
            strategy_name TEXT
        )"""
    )
    today = date.today().isoformat() + "T12:00:00"
    for r in rows:
        conn.execute(
            "INSERT INTO trades "
            "(timestamp, symbol, direction, entry_price, pnl, status, is_backtest, strategy_name) "
            "VALUES (?, 'BTCUSDT', 'long', 50000, ?, ?, ?, ?)",
            (r.get("timestamp", today), r.get("pnl", 0.0),
             r.get("status", "open"), r.get("is_backtest", 0),
             r.get("strategy_name", None)),
        )
    conn.commit()
    conn.close()
    return path


def _order(strategy="breakout", qty=0.4):
    return {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": qty,
        "meta": {"strategy_name": strategy},
    }


def _settings(**extra):
    return {"DRY_RUN": "true", "MAX_QTY": "10", **extra}


# ---------------------------------------------------------------------------
# inject_per_strategy_counters — DB queries
# ---------------------------------------------------------------------------

def test_inject_per_strategy_open_positions():
    path = _make_db([
        {"status": "open",   "is_backtest": 0, "strategy_name": "breakout"},
        {"status": "open",   "is_backtest": 0, "strategy_name": "breakout"},
        {"status": "open",   "is_backtest": 0, "strategy_name": "vwap"},    # different strategy
        {"status": "closed", "is_backtest": 0, "strategy_name": "breakout"},  # closed — excluded
    ])
    try:
        result = inject_per_strategy_counters(
            {"TRADE_JOURNAL_DB": path}, strategy_name="breakout"
        )
        assert result["STRATEGY_OPEN_POSITIONS"] == "2"
    finally:
        os.unlink(path)


def test_inject_per_strategy_daily_pnl():
    today = date.today().isoformat() + "T12:00:00"
    path = _make_db([
        {"pnl": -50.0, "status": "closed", "is_backtest": 0,
         "strategy_name": "breakout", "timestamp": today},
        {"pnl": -20.0, "status": "closed", "is_backtest": 0,
         "strategy_name": "vwap", "timestamp": today},  # different strategy
        {"pnl": -30.0, "status": "closed", "is_backtest": 1,
         "strategy_name": "breakout", "timestamp": today},  # backtest — excluded
    ])
    try:
        result = inject_per_strategy_counters(
            {"TRADE_JOURNAL_DB": path}, strategy_name="breakout"
        )
        assert float(result["STRATEGY_DAILY_PNL"]) == pytest.approx(-50.0)
    finally:
        os.unlink(path)


def test_inject_per_strategy_no_db_returns_unchanged():
    settings = {"SYMBOL": "BTCUSDT"}
    result = inject_per_strategy_counters(settings, strategy_name="breakout")
    assert "STRATEGY_OPEN_POSITIONS" not in result
    assert "STRATEGY_DAILY_PNL" not in result


def test_inject_per_strategy_missing_column_defaults_to_zero():
    """DB without strategy_name column must still inject 0 counters."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, pnl REAL, "
        "status TEXT, is_backtest INTEGER, timestamp TEXT)"
    )
    conn.commit()
    conn.close()
    try:
        result = inject_per_strategy_counters(
            {"TRADE_JOURNAL_DB": path}, strategy_name="breakout"
        )
        assert result["STRATEGY_OPEN_POSITIONS"] == "0"
        assert result["STRATEGY_DAILY_PNL"] == "0.0"
    finally:
        os.unlink(path)


def test_inject_per_strategy_db_error_no_raise():
    result = inject_per_strategy_counters(
        {"TRADE_JOURNAL_DB": "/nonexistent/path.db"}, strategy_name="breakout"
    )
    assert "STRATEGY_OPEN_POSITIONS" not in result


# ---------------------------------------------------------------------------
# safe_place_order — MAX_POS_PER_STRATEGY refusal (S-005 M2 core test)
# ---------------------------------------------------------------------------

def test_per_strategy_risk_refusal_open_positions():
    """Order refused when strategy's open positions reach MAX_POS_PER_STRATEGY."""
    settings = _settings(
        MAX_POS_PER_STRATEGY="2",
        STRATEGY_OPEN_POSITIONS="2",  # at cap
    )
    result = safe_place_order(_order("breakout"), settings, client=None)
    assert result["status"] == "refused"
    assert "MAX_POS_PER_STRATEGY" in result["reason"]
    assert "breakout" in result["reason"]


def test_per_strategy_risk_refusal_daily_loss():
    """Order refused when strategy's daily loss reaches MAX_DAILY_LOSS_PER_STRATEGY_USD."""
    settings = _settings(
        MAX_DAILY_LOSS_PER_STRATEGY_USD="100",
        STRATEGY_DAILY_PNL="-100.0",  # loss == cap
    )
    result = safe_place_order(_order("vwap"), settings, client=None)
    assert result["status"] == "refused"
    assert "MAX_DAILY_LOSS_PER_STRATEGY_USD" in result["reason"]
    assert "vwap" in result["reason"]


def test_per_strategy_below_cap_passes_to_dry_run():
    """Order proceeds to dry_run when strategy counters are below caps."""
    settings = _settings(
        MAX_POS_PER_STRATEGY="3",
        STRATEGY_OPEN_POSITIONS="1",  # below cap
    )
    result = safe_place_order(_order("breakout"), settings, client=None)
    assert result["status"] == "dry_run"


def test_per_strategy_caps_ignored_without_strategy_name():
    """An order with no meta.strategy_name must not be affected by strategy caps."""
    order = {"symbol": "BTCUSDT", "side": "buy", "qty": 0.4}
    settings = _settings(
        MAX_POS_PER_STRATEGY="0",       # would block if checked
        STRATEGY_OPEN_POSITIONS="999",
    )
    result = safe_place_order(order, settings, client=None)
    assert result["status"] == "dry_run"


def test_per_strategy_caps_only_check_when_counter_present():
    """MAX_POS_PER_STRATEGY set but counter absent → cap silently skipped."""
    settings = _settings(MAX_POS_PER_STRATEGY="2")  # no STRATEGY_OPEN_POSITIONS
    result = safe_place_order(_order("ict"), settings, client=None)
    assert result["status"] == "dry_run"


def test_per_strategy_positive_pnl_not_refused():
    """Positive daily PnL must not count as a loss and must not trigger refusal."""
    settings = _settings(
        MAX_DAILY_LOSS_PER_STRATEGY_USD="100",
        STRATEGY_DAILY_PNL="200.0",  # profit day
    )
    result = safe_place_order(_order("ict"), settings, client=None)
    assert result["status"] == "dry_run"
