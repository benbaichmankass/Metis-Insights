"""
Tests for _inject_runtime_counters in src.runtime.pipeline.

Verifies that CURRENT_OPEN_POSITIONS and CURRENT_DAILY_LOSS_USD are
correctly injected into the settings dict before safe_place_order is called.

All tests are network-free and DB-free (in-memory SQLite where needed).

Tests:
  1. No exchange, no DB   — counters absent, original settings unchanged.
  2. Exchange 0 positions — CURRENT_OPEN_POSITIONS = "0".
  3. Exchange N positions — CURRENT_OPEN_POSITIONS = "3".
  4. Exchange error       — counter absent, no exception propagated.
  5. Daily loss below cap — CURRENT_DAILY_LOSS_USD = "50.0" (negative pnl).
  6. Daily loss positive  — CURRENT_DAILY_LOSS_USD = "0.0" (profit day → 0 loss).
  7. Backtest exclusion   — is_backtest=1 row ignored; is_backtest=0 row counted.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date
from unittest.mock import MagicMock


from src.runtime.risk_counters import inject_runtime_counters as _inject_runtime_counters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_with_trades(rows: list[dict]) -> str:
    """Create a temp SQLite DB with a trades table and return its path."""
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
            pnl REAL,
            status TEXT DEFAULT 'open',
            is_backtest BOOLEAN DEFAULT 1
        )"""
    )
    today = date.today().isoformat()
    for r in rows:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, pnl, status, is_backtest) "
            "VALUES (?, 'BTCUSDT', 'long', 50000, ?, ?, ?)",
            (r.get("timestamp", today + "T12:00:00"), r["pnl"], r["status"], r["is_backtest"]),
        )
    conn.commit()
    conn.close()
    return path


def _fake_exchange(positions: list | None = None, raise_on_call: bool = False):
    ex = MagicMock()
    if raise_on_call:
        ex.get_positions.side_effect = RuntimeError("network error")
    else:
        ex.get_positions.return_value = positions or []
    return ex


# ---------------------------------------------------------------------------
# Test 1: no exchange, no DB → counters absent
# ---------------------------------------------------------------------------

def test_no_exchange_no_db_leaves_settings_unchanged():
    settings = {"SYMBOL": "BTCUSDT"}
    result = _inject_runtime_counters(settings, exchange_client=None)
    assert "CURRENT_OPEN_POSITIONS" not in result
    assert "CURRENT_DAILY_LOSS_USD" not in result
    assert result["SYMBOL"] == "BTCUSDT"


def test_original_settings_dict_not_mutated():
    settings = {"SYMBOL": "BTCUSDT"}
    _inject_runtime_counters(settings, exchange_client=None)
    assert "CURRENT_OPEN_POSITIONS" not in settings


# ---------------------------------------------------------------------------
# Test 2: exchange returns 0 positions
# ---------------------------------------------------------------------------

def test_exchange_zero_positions_injects_zero():
    result = _inject_runtime_counters({}, exchange_client=_fake_exchange([]))
    assert result["CURRENT_OPEN_POSITIONS"] == "0"


# ---------------------------------------------------------------------------
# Test 3: exchange returns N positions
# ---------------------------------------------------------------------------

def test_exchange_three_positions_injects_three():
    positions = [{"contracts": 1}, {"contracts": 2}, {"contracts": 0.5}]
    result = _inject_runtime_counters({}, exchange_client=_fake_exchange(positions))
    assert result["CURRENT_OPEN_POSITIONS"] == "3"


# ---------------------------------------------------------------------------
# Test 4: exchange error → counter absent, no exception
# ---------------------------------------------------------------------------

def test_exchange_error_no_counter_no_raise():
    result = _inject_runtime_counters({}, exchange_client=_fake_exchange(raise_on_call=True))
    assert "CURRENT_OPEN_POSITIONS" not in result


def test_exchange_missing_method_no_counter():
    ex = object()  # no get_positions attribute
    result = _inject_runtime_counters({}, exchange_client=ex)
    assert "CURRENT_OPEN_POSITIONS" not in result


# ---------------------------------------------------------------------------
# Test 5: daily loss — negative pnl → abs value injected
# ---------------------------------------------------------------------------

def test_daily_loss_negative_pnl():
    path = _db_with_trades([{"pnl": -50.0, "status": "closed", "is_backtest": 0}])
    try:
        result = _inject_runtime_counters({"TRADE_JOURNAL_DB": path}, exchange_client=None)
        assert result["CURRENT_DAILY_LOSS_USD"] == "50.0"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 6: positive pnl day → CURRENT_DAILY_LOSS_USD = "0.0"
# ---------------------------------------------------------------------------

def test_daily_loss_positive_pnl_is_zero():
    path = _db_with_trades([{"pnl": 200.0, "status": "closed", "is_backtest": 0}])
    try:
        result = _inject_runtime_counters({"TRADE_JOURNAL_DB": path}, exchange_client=None)
        assert result["CURRENT_DAILY_LOSS_USD"] == "0.0"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 7: backtest exclusion (the critical correctness test)
# ---------------------------------------------------------------------------

def test_backtest_rows_excluded_from_daily_loss():
    """is_backtest=1 pnl=-9999 must be ignored; is_backtest=0 pnl=-50 is counted."""
    today = date.today().isoformat() + "T10:00:00"
    rows = [
        {"pnl": -9999.0, "status": "closed", "is_backtest": 1, "timestamp": today},
        {"pnl": -50.0,   "status": "closed", "is_backtest": 0, "timestamp": today},
    ]
    path = _db_with_trades(rows)
    try:
        result = _inject_runtime_counters({"TRADE_JOURNAL_DB": path}, exchange_client=None)
        assert result["CURRENT_DAILY_LOSS_USD"] == "50.0"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Additional: DB error → counter absent, no exception
# ---------------------------------------------------------------------------

def test_db_error_no_counter_no_raise():
    result = _inject_runtime_counters(
        {"TRADE_JOURNAL_DB": "/nonexistent/path/that/cannot/exist.db"},
        exchange_client=None,
    )
    assert "CURRENT_DAILY_LOSS_USD" not in result


def test_open_trades_excluded_from_daily_loss():
    """status='open' rows must not contribute to the daily loss figure."""
    path = _db_with_trades([{"pnl": -300.0, "status": "open", "is_backtest": 0}])
    try:
        result = _inject_runtime_counters({"TRADE_JOURNAL_DB": path}, exchange_client=None)
        # No closed trades → SUM is NULL → loss = 0.0
        assert result["CURRENT_DAILY_LOSS_USD"] == "0.0"
    finally:
        os.unlink(path)
