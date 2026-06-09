"""Position-netting guard — pure helpers (Option A, BL-20260608-DEMOPNL).

Covers the two shared helpers in ``src/runtime/positions.py`` that gate
the guard:

  * ``position_netting_guard_enabled`` — the single env kill-switch
    (default OFF) that gates BOTH halves of the fix (monocle +
    reconciler), so the operator can roll the whole thing back with one
    env flip + restart.
  * ``has_open_trade_for_strategy`` — the strategy-scoped open-trade read
    the monocle uses to decide whether a same-direction re-entry would
    net into an existing position.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime.positions import (
    has_open_trade_for_strategy,
    position_netting_guard_accounts,
    position_netting_guard_active_for,
    position_netting_guard_enabled,
)


def _init_trade_journal(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                position_size REAL NOT NULL,
                status TEXT DEFAULT 'open',
                is_backtest INTEGER DEFAULT 0,
                strategy_name TEXT,
                account_id TEXT NOT NULL DEFAULT 'live'
            )
            """
        )


def _insert(path, *, account_id, symbol, strategy_name, direction="long",
            status="open", is_backtest=0, position_size=0.01):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
            "position_size, status, is_backtest, strategy_name, account_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("2026-06-08T00:00:00Z", symbol, direction, 60_000.0,
             position_size, status, is_backtest, strategy_name, account_id),
        )


class TestGuardSwitch:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("POSITION_NETTING_GUARD_ENABLED", raising=False)
        assert position_netting_guard_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_values_enable(self, monkeypatch, val):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", val)
        assert position_netting_guard_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "garbage"])
    def test_falsy_values_disable(self, monkeypatch, val):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", val)
        assert position_netting_guard_enabled() is False


class TestGuardAccountScope:
    """``POSITION_NETTING_GUARD_ACCOUNTS`` narrows the (master-gated) scope
    for the demo-only soak. Defaults permissive (unset → all accounts when
    the master is on) so it is never a second default-off enable gate."""

    def test_accounts_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("POSITION_NETTING_GUARD_ACCOUNTS", raising=False)
        assert position_netting_guard_accounts() is None

    def test_accounts_blank_is_none(self, monkeypatch):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "  ")
        assert position_netting_guard_accounts() is None

    def test_accounts_single(self, monkeypatch):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        assert position_netting_guard_accounts() == frozenset({"bybit_1"})

    def test_accounts_csv_trimmed(self, monkeypatch):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", " bybit_1 , bybit_2 ")
        assert position_netting_guard_accounts() == frozenset({"bybit_1", "bybit_2"})

    def test_active_for_master_off_is_false(self, monkeypatch):
        monkeypatch.delenv("POSITION_NETTING_GUARD_ENABLED", raising=False)
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        assert position_netting_guard_active_for("bybit_1") is False

    def test_active_for_no_allowlist_applies_to_all(self, monkeypatch):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.delenv("POSITION_NETTING_GUARD_ACCOUNTS", raising=False)
        assert position_netting_guard_active_for("bybit_1") is True
        assert position_netting_guard_active_for("bybit_2") is True

    def test_active_for_allowlist_scopes(self, monkeypatch):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        assert position_netting_guard_active_for("bybit_1") is True
        assert position_netting_guard_active_for("bybit_2") is False

    def test_active_for_none_account_with_allowlist_is_false(self, monkeypatch):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        assert position_netting_guard_active_for(None) is False


class TestHasOpenTradeForStrategy:
    def test_no_db_returns_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is False

    def test_matching_open_trade_true(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_1", symbol="BTCUSDT",
                strategy_name="htf_pullback")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is True

    def test_different_strategy_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_1", symbol="BTCUSDT",
                strategy_name="vwap")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is False

    def test_different_symbol_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_1", symbol="ETHUSDT",
                strategy_name="htf_pullback")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is False

    def test_different_account_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_2", symbol="BTCUSDT",
                strategy_name="htf_pullback")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is False

    def test_closed_trade_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_1", symbol="BTCUSDT",
                strategy_name="htf_pullback", status="closed")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is False

    def test_backtest_trade_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_1", symbol="BTCUSDT",
                strategy_name="htf_pullback", is_backtest=1)
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", "htf_pullback", db_path=path,
        ) is False

    def test_none_strategy_false(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert(path, account_id="bybit_1", symbol="BTCUSDT",
                strategy_name="htf_pullback")
        assert has_open_trade_for_strategy(
            "bybit_1", "BTCUSDT", None, db_path=path,
        ) is False
