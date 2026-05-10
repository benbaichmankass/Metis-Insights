"""M5 P4 — GET /api/bot/backtests tests.

Tier-1 read endpoint backed by ``trade_journal.db::backtest_results``
(populated by the M5 backtest consumer — one row per `/test <strategy>`
invocation). The dashboard's Backtests tab consumes this list.

Pins:

  * Wire-shape (camelCase keys, headline metrics, no raw config dump).
  * Newest-first ordering by ``id`` (matches consumer-write order).
  * Optional ``strategy`` filter (exact match against
    ``strategy_version``).
  * ``limit`` clamped 1..200; default 50.
  * Best-effort behaviour: missing DB / missing table / sqlite error
    all collapse to an empty list (the dashboard treats `[]` as "no
    backtests yet").
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import backtests as backtests_router


# ---------------------------------------------------------------------------
# Fixtures

_BACKTEST_RESULTS_SCHEMA = """
CREATE TABLE backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    strategy_version TEXT,
    start_date TEXT,
    end_date TEXT,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    expectancy REAL,
    max_drawdown REAL,
    max_drawdown_pct REAL,
    sharpe_ratio REAL,
    total_pnl REAL,
    total_pnl_pct REAL,
    avg_win REAL,
    avg_loss REAL,
    largest_win REAL,
    largest_loss REAL,
    config JSON,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    """Empty trade_journal.db with the backtest_results table."""
    path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_BACKTEST_RESULTS_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(backtests_router, "_DB_PATH", path)
    return path


def _insert(db_path: Path, **fields) -> int:
    """Insert one row; defaults represent a typical happy-path run."""
    defaults = {
        "run_date": "2026-05-09",
        "strategy_version": "vwap",
        "start_date": "2026-04-01",
        "end_date": "2026-05-08",
        "total_trades": 12,
        "winning_trades": 7,
        "losing_trades": 5,
        "win_rate": 58.3,
        "profit_factor": 1.45,
        "expectancy": 8.7,
        "max_drawdown": -120.0,
        "max_drawdown_pct": -3.2,
        "sharpe_ratio": 1.18,
        "total_pnl": 104.5,
        "total_pnl_pct": 1.05,
        "avg_win": 28.0,
        "avg_loss": -14.4,
        "largest_win": 75.0,
        "largest_loss": -38.0,
        "config": "{}",
        "created_at": "2026-05-09T12:00:00",
    }
    defaults.update(fields)
    conn = sqlite3.connect(str(db_path))
    try:
        cols = ", ".join(defaults.keys())
        placeholders = ", ".join(["?"] * len(defaults))
        cur = conn.execute(
            f"INSERT INTO backtest_results ({cols}) VALUES ({placeholders})",
            list(defaults.values()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Happy path — wire-shape + newest-first

class TestHappyPath:
    def test_returns_full_wire_shape(self, db, client):
        row_id = _insert(db)
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        row = body[0]
        assert row == {
            "id": row_id,
            "strategy": "vwap",
            "runDate": "2026-05-09",
            "startDate": "2026-04-01",
            "endDate": "2026-05-08",
            "totalTrades": 12,
            "winningTrades": 7,
            "losingTrades": 5,
            "winRate": 58.3,
            "profitFactor": 1.45,
            "expectancy": 8.7,
            "sharpeRatio": 1.18,
            "maxDrawdownPct": -3.2,
            "totalPnl": 104.5,
            "createdAt": "2026-05-09T12:00:00",
        }

    def test_newest_first_by_id(self, db, client):
        first = _insert(db, strategy_version="vwap")
        second = _insert(db, strategy_version="turtle_soup")
        third = _insert(db, strategy_version="vwap")
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        body = resp.json()
        assert [r["id"] for r in body] == [third, second, first]

    def test_empty_db_returns_empty_list(self, db, client):
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Strategy filter

class TestStrategyFilter:
    def test_exact_match_only(self, db, client):
        _insert(db, strategy_version="vwap")
        _insert(db, strategy_version="turtle_soup")
        _insert(db, strategy_version="vwap")
        resp = client.get("/api/bot/backtests?strategy=vwap")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert all(r["strategy"] == "vwap" for r in body)

    def test_unknown_strategy_returns_empty(self, db, client):
        _insert(db, strategy_version="vwap")
        resp = client.get("/api/bot/backtests?strategy=bogus")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_strategy_filter_case_sensitive(self, db, client):
        # Exact match — uppercase doesn't match the lowercase row.
        _insert(db, strategy_version="vwap")
        resp = client.get("/api/bot/backtests?strategy=VWAP")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Limit clamping

class TestLimit:
    def test_limit_caps_results(self, db, client):
        for _ in range(5):
            _insert(db)
        resp = client.get("/api/bot/backtests?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_limit_below_1_rejected(self, client):
        resp = client.get("/api/bot/backtests?limit=0")
        assert resp.status_code == 422

    def test_limit_above_max_rejected(self, client):
        # MAX_LIMIT is 200; 201 must be rejected by FastAPI's Query
        # validator before we touch sqlite.
        resp = client.get("/api/bot/backtests?limit=201")
        assert resp.status_code == 422

    def test_default_limit_is_50(self, db, client):
        # Insert 60 rows, default limit returns 50.
        for _ in range(60):
            _insert(db)
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        assert len(resp.json()) == 50


# ---------------------------------------------------------------------------
# Best-effort fallbacks

class TestBestEffort:
    def test_missing_db_returns_empty(self, tmp_path, client, monkeypatch):
        # No DB on disk — must collapse to [], not 500.
        monkeypatch.setattr(backtests_router, "_DB_PATH", tmp_path / "missing.db")
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_missing_table_returns_empty(self, tmp_path, client, monkeypatch):
        # DB exists but the M5 consumer never wrote — no
        # backtest_results table. Should be [], not 500.
        path = tmp_path / "trade_journal.db"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE other (id INTEGER)")
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(backtests_router, "_DB_PATH", path)
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Null-tolerance — early consumer-failure rows have NULL metrics

class TestNullTolerance:
    def test_null_metrics_round_trip_as_none(self, db, client):
        _insert(
            db,
            win_rate=None,
            profit_factor=None,
            sharpe_ratio=None,
            max_drawdown_pct=None,
            total_pnl=None,
        )
        resp = client.get("/api/bot/backtests")
        assert resp.status_code == 200
        row = resp.json()[0]
        assert row["winRate"] is None
        assert row["profitFactor"] is None
        assert row["sharpeRatio"] is None
        assert row["maxDrawdownPct"] is None
        assert row["totalPnl"] is None
        # Counts that came in NULL collapse to 0 (not None) — the
        # dashboard treats them as "no trades".
        _insert(db, total_trades=None, winning_trades=None, losing_trades=None)
        resp2 = client.get("/api/bot/backtests")
        new_row = resp2.json()[0]
        assert new_row["totalTrades"] == 0
        assert new_row["winningTrades"] == 0
        assert new_row["losingTrades"] == 0
