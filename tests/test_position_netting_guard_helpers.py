"""Position-netting guard — pure helpers (Option A, BL-20260608-DEMOPNL).

Covers the shared helpers in ``src/runtime/positions.py``:

  * ``position_netting_guard_active_for`` — the single predicate both halves
    of the guard (monocle + reconciler) consult. **BASELINE (2026-06-17)**:
    the guard is unconditional, so this always returns True regardless of env
    (the default-off ``POSITION_NETTING_GUARD_ENABLED`` gate +
    ``POSITION_NETTING_GUARD_ACCOUNTS`` scope were removed — a required
    correctness capability must not sit behind a default-off flag).
  * ``has_open_trade_for_strategy`` — the strategy-scoped open-trade read
    the monocle uses to decide whether a same-direction re-entry would
    net into an existing position.
"""
from __future__ import annotations

import sqlite3

from src.runtime.positions import (
    has_open_trade_for_strategy,
    position_netting_guard_active_for,
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


class TestGuardBaseline:
    """The guard is BASELINE / unconditional — ``active_for`` always True,
    independent of any env (the gate + scope were removed 2026-06-17)."""

    def test_active_for_always_true_no_env(self, monkeypatch):
        monkeypatch.delenv("POSITION_NETTING_GUARD_ENABLED", raising=False)
        monkeypatch.delenv("POSITION_NETTING_GUARD_ACCOUNTS", raising=False)
        assert position_netting_guard_active_for("bybit_1") is True
        assert position_netting_guard_active_for("bybit_2") is True
        assert position_netting_guard_active_for("ib_paper") is True
        assert position_netting_guard_active_for(None) is True

    def test_active_for_ignores_legacy_gate_env(self, monkeypatch):
        # Leftover env from the soak era must not re-introduce gating.
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "false")
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        assert position_netting_guard_active_for("bybit_2") is True


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
