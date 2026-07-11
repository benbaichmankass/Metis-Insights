"""Tests for S11 (M11): attribution API endpoints.

Validates:
  - GET /api/bot/positions/net — net positions from trade journal
  - GET /api/bot/strategy/attribution — per-strategy trade stats
  - Empty DB, missing DB, and populated DB cases
  - Wire shape contract (field names, types, nullability)

Note: Tests call router functions directly (no TestClient) to avoid
the broken cryptography/cffi dependency in this environment.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# DB fixture helpers
# ---------------------------------------------------------------------------

def _init_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                account_id TEXT,
                symbol TEXT,
                direction TEXT,
                strategy_name TEXT,
                position_size REAL,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                pnl_percent REAL,
                timestamp TEXT,
                exit_reason TEXT,
                setup_type TEXT,
                notes TEXT,
                status TEXT,
                is_backtest INTEGER DEFAULT 0,
                is_demo INTEGER DEFAULT 0,
                account_class TEXT,
                reconcile_status TEXT
            )"""
        )
        conn.commit()


def _insert_trade(
    path: str,
    *,
    account_id: str = "bybit_1",
    symbol: str = "BTCUSDT",
    direction: str = "long",
    strategy_name: str = "vwap",
    position_size: float = 0.5,
    pnl: float = 50.0,
    status: str = "open",
    is_backtest: int = 0,
    is_demo: int = 0,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO trades (account_id, symbol, direction, strategy_name, "
            "position_size, pnl, status, is_backtest, is_demo, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
            (account_id, symbol, direction, strategy_name, position_size,
             pnl, status, is_backtest, is_demo),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# GET /api/bot/positions/net — tested via router function
# ---------------------------------------------------------------------------

class TestNetPositionsEndpoint:
    def _call(self, db_path: str):
        from src.web.api.routers.attribution import get_net_positions
        return get_net_positions(db_path=db_path)

    def test_missing_db_returns_empty(self, tmp_path):
        data = self._call(str(tmp_path / "nonexistent.db"))
        assert data["positions"] == []
        assert data["count"] == 0

    def test_empty_db_returns_empty(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        data = self._call(db)
        assert data["positions"] == []

    def test_single_long_position(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, symbol="BTCUSDT", direction="long", position_size=0.5)
        data = self._call(db)
        assert data["count"] == 1
        pos = data["positions"][0]
        assert pos["symbol"] == "BTCUSDT"
        assert pos["net_qty"] == pytest.approx(0.5)

    def test_short_position_is_negative(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, symbol="BTCUSDT", direction="short", position_size=1.0)
        data = self._call(db)
        assert data["positions"][0]["net_qty"] == pytest.approx(-1.0)

    def test_aggregates_across_accounts(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, account_id="bybit_1", symbol="BTCUSDT", direction="long", position_size=0.3)
        _insert_trade(db, account_id="bybit_2", symbol="BTCUSDT", direction="long", position_size=0.2)
        data = self._call(db)
        assert data["count"] == 1
        assert data["positions"][0]["net_qty"] == pytest.approx(0.5)

    def test_multiple_symbols(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, symbol="BTCUSDT", direction="long", position_size=1.0)
        _insert_trade(db, symbol="ETHUSDT", direction="short", position_size=2.0)
        data = self._call(db)
        assert data["count"] == 2
        symbols = {p["symbol"]: p["net_qty"] for p in data["positions"]}
        assert symbols["BTCUSDT"] == pytest.approx(1.0)
        assert symbols["ETHUSDT"] == pytest.approx(-2.0)

    def test_backtest_excluded(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, symbol="BTCUSDT", direction="long", position_size=5.0, is_backtest=1)
        data = self._call(db)
        assert data["positions"] == []

    def test_closed_positions_excluded(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, symbol="BTCUSDT", direction="long", position_size=1.0, status="closed")
        data = self._call(db)
        assert data["positions"] == []


# ---------------------------------------------------------------------------
# GET /api/bot/strategy/attribution — tested via router function
# ---------------------------------------------------------------------------

class TestStrategyAttributionEndpoint:
    def _call(self, db_path: str):
        from src.web.api.routers.attribution import get_strategy_attribution
        return get_strategy_attribution(db_path=db_path)

    def test_missing_db_returns_empty_strategies(self, tmp_path):
        data = self._call(str(tmp_path / "nonexistent.db"))
        assert data["strategies"] == []
        assert "generated_at" in data

    def test_empty_db_returns_empty_strategies(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        data = self._call(db)
        assert data["strategies"] == []

    def test_single_strategy_winning_trade(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="vwap", pnl=100.0, status="closed")
        data = self._call(db)
        assert len(data["strategies"]) == 1
        s = data["strategies"][0]
        assert s["strategy"] == "vwap"
        assert s["closed_trades"] == 1
        assert s["winning_trades"] == 1
        assert s["losing_trades"] == 0
        assert s["win_rate"] == pytest.approx(100.0)
        assert s["total_pnl"] == pytest.approx(100.0)

    def test_win_rate_calculation(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        for pnl in [50.0, 30.0, -20.0, -10.0]:
            _insert_trade(db, strategy_name="turtle_soup", pnl=pnl, status="closed")
        data = self._call(db)
        s = data["strategies"][0]
        assert s["closed_trades"] == 4
        assert s["winning_trades"] == 2
        assert s["win_rate"] == pytest.approx(50.0)
        assert s["total_pnl"] == pytest.approx(50.0)

    def test_open_trades_counted(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="vwap", pnl=50.0, status="closed")
        _insert_trade(db, strategy_name="vwap", pnl=0.0, status="open")
        data = self._call(db)
        s = data["strategies"][0]
        assert s["open_trades"] == 1
        assert s["closed_trades"] == 1

    def test_multiple_strategies(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="vwap", pnl=100.0, status="closed")
        _insert_trade(db, strategy_name="ict_scalp_5m", pnl=-20.0, status="closed")
        data = self._call(db)
        strategies = {s["strategy"]: s for s in data["strategies"]}
        assert "vwap" in strategies
        assert "ict_scalp_5m" in strategies
        assert strategies["vwap"]["total_pnl"] == pytest.approx(100.0)
        assert strategies["ict_scalp_5m"]["total_pnl"] == pytest.approx(-20.0)

    def test_backtest_excluded(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="vwap", pnl=9999.0, status="closed", is_backtest=1)
        data = self._call(db)
        assert data["strategies"] == []

    def test_strategy_with_only_open_trades_included(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="new_strategy", status="open")
        data = self._call(db)
        strategies = {s["strategy"]: s for s in data["strategies"]}
        assert "new_strategy" in strategies
        s = strategies["new_strategy"]
        assert s["open_trades"] == 1
        assert s["closed_trades"] == 0

    def test_generated_at_is_iso8601(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        data = self._call(db)
        datetime.fromisoformat(data["generated_at"])  # must not raise

    def test_wire_shape_fields_present(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="vwap", pnl=10.0, status="closed")
        data = self._call(db)
        s = data["strategies"][0]
        required = {"strategy", "open_trades", "closed_trades", "winning_trades",
                    "losing_trades", "win_rate", "total_pnl"}
        assert required.issubset(s.keys())

    def test_losing_trade_has_pnl_zero_counted_as_loss(self, tmp_path):
        db = str(tmp_path / "journal.db")
        _init_db(db)
        _insert_trade(db, strategy_name="vwap", pnl=0.0, status="closed")
        data = self._call(db)
        s = data["strategies"][0]
        assert s["losing_trades"] == 1
        assert s["winning_trades"] == 0
