"""M5 P4 — GET /api/bot/backtests tests.

Tier-1 read endpoint. Reads ``trade_journal.db::backtest_results`` rows
written by the M5 backtest consumer + the standalone
``src/backtest/run_backtest.py`` harness. Both writers target the same
canonical table.

Contracts under test:

1. Empty / missing DB / missing-table → ``[]`` with 200 (never 5xx).
2. Newest-first ordering by ``created_at`` (ties broken by ``id`` DESC).
3. ``limit`` clamped to 1..200, default 50; ``strategy`` filter is
   exact match on ``strategy_version``; ``since`` filters on
   ``created_at`` (ISO-8601, datetime() comparison).
4. Wire-shape — every column maps to its camelCase name; numeric
   columns are coerced to float, integer counts to int, NULLs preserved.
5. Tier-1 — endpoint never raises into the response (sqlite errors,
   schema drift, malformed rows all surface as ``[]``).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import backtests as backtests_router


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


def _create_table(path: Path) -> None:
    """Create the canonical backtest_results table at *path*. Mirrors
    ``src/units/db/database.py::initialize_db`` — keep in sync if the
    schema gains a column.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_results (
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
        )
        conn.commit()
    finally:
        conn.close()


def _insert(path: Path, **kwargs) -> int:
    """Insert one row into backtest_results. Pass any subset of columns.
    Returns the row id."""
    conn = sqlite3.connect(str(path))
    try:
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        cur = conn.execute(
            f"INSERT INTO backtest_results ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "trade_journal.db"
    _create_table(path)
    monkeypatch.setattr(backtests_router, "_DB_PATH", path)
    return path


# ---------------------------------------------------------------------------
# Empty / missing
# ---------------------------------------------------------------------------


def test_missing_db_returns_empty_list_200(tmp_path, monkeypatch, client):
    """No DB file at all → 200 with []."""
    missing = tmp_path / "does-not-exist.db"
    monkeypatch.setattr(backtests_router, "_DB_PATH", missing)
    resp = client.get("/api/bot/backtests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_missing_table_returns_empty_list_200(tmp_path, monkeypatch, client):
    """DB exists but no backtest_results table (fresh install) → []."""
    path = tmp_path / "fresh.db"
    sqlite3.connect(str(path)).close()
    monkeypatch.setattr(backtests_router, "_DB_PATH", path)
    resp = client.get("/api/bot/backtests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_empty_table_returns_empty_list_200(db, client):
    resp = client.get("/api/bot/backtests")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Wire shape
# ---------------------------------------------------------------------------


def test_full_row_wire_shape(db, client):
    _insert(
        db,
        run_date="2026-05-10",
        strategy_version="vwap",
        start_date="2024-01-01",
        end_date="2026-04-30",
        total_trades=120,
        winning_trades=70,
        losing_trades=50,
        win_rate=0.583,
        profit_factor=1.42,
        expectancy=0.18,
        max_drawdown=-1200.50,
        max_drawdown_pct=-0.12,
        sharpe_ratio=1.05,
        total_pnl=3450.75,
        total_pnl_pct=0.345,
        avg_win=80.50,
        avg_loss=-45.20,
        largest_win=250.00,
        largest_loss=-180.00,
        created_at="2026-05-10T11:30:00",
    )
    resp = client.get("/api/bot/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row == {
        "id": "1",
        "runDate": "2026-05-10",
        "strategy": "vwap",
        "startDate": "2024-01-01",
        "endDate": "2026-04-30",
        "totalTrades": 120,
        "winningTrades": 70,
        "losingTrades": 50,
        "winRate": pytest.approx(0.583),
        "profitFactor": pytest.approx(1.42),
        "expectancy": pytest.approx(0.18),
        "maxDrawdown": pytest.approx(-1200.50),
        "maxDrawdownPct": pytest.approx(-0.12),
        "sharpeRatio": pytest.approx(1.05),
        "totalPnl": pytest.approx(3450.75),
        "totalPnlPct": pytest.approx(0.345),
        "avgWin": pytest.approx(80.50),
        "avgLoss": pytest.approx(-45.20),
        "largestWin": pytest.approx(250.00),
        "largestLoss": pytest.approx(-180.00),
        "createdAt": "2026-05-10T11:30:00",
    }


def test_null_columns_preserved_as_none(db, client):
    """Columns with NULL values come back as JSON null, not zero."""
    _insert(
        db,
        run_date="2026-05-10",
        strategy_version="vwap",
        total_trades=0,
        # everything else NULL
        created_at="2026-05-10T12:00:00",
    )
    resp = client.get("/api/bot/backtests")
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["totalTrades"] == 0
    assert row["winningTrades"] is None
    assert row["winRate"] is None
    assert row["sharpeRatio"] is None
    assert row["startDate"] is None


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_newest_first_by_created_at(db, client):
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-08T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-09T10:00:00")
    body = client.get("/api/bot/backtests").json()
    dates = [r["createdAt"] for r in body]
    assert dates == [
        "2026-05-10T10:00:00",
        "2026-05-09T10:00:00",
        "2026-05-08T10:00:00",
    ]


def test_tie_break_by_id_desc_when_created_at_equal(db, client):
    """Two rows with identical created_at → newer id sorts first."""
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    body = client.get("/api/bot/backtests").json()
    ids = [r["id"] for r in body]
    assert ids == ["2", "1"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_strategy_filter_exact_match(db, client):
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="turtle_soup",
            created_at="2026-05-10T11:00:00")
    body = client.get("/api/bot/backtests?strategy=vwap").json()
    assert [r["strategy"] for r in body] == ["vwap"]


def test_strategy_filter_no_match_returns_empty(db, client):
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    assert client.get("/api/bot/backtests?strategy=ghost").json() == []


def test_since_filter_inclusive(db, client):
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-08T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-09T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    body = client.get(
        "/api/bot/backtests?since=2026-05-09T00:00:00"
    ).json()
    assert [r["createdAt"] for r in body] == [
        "2026-05-10T10:00:00",
        "2026-05-09T10:00:00",
    ]


def test_strategy_and_since_combine(db, client):
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-08T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="vwap",
            created_at="2026-05-10T10:00:00")
    _insert(db, run_date="2026-05-10", strategy_version="turtle_soup",
            created_at="2026-05-10T11:00:00")
    body = client.get(
        "/api/bot/backtests?strategy=vwap&since=2026-05-09T00:00:00"
    ).json()
    assert len(body) == 1
    assert body[0]["strategy"] == "vwap"
    assert body[0]["createdAt"] == "2026-05-10T10:00:00"


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


def test_limit_default_is_50(db, client):
    for i in range(60):
        _insert(db, run_date="2026-05-10", strategy_version="vwap",
                created_at=f"2026-05-10T10:{i:02d}:00")
    body = client.get("/api/bot/backtests").json()
    assert len(body) == 50


def test_explicit_limit_respected(db, client):
    for i in range(20):
        _insert(db, run_date="2026-05-10", strategy_version="vwap",
                created_at=f"2026-05-10T10:{i:02d}:00")
    body = client.get("/api/bot/backtests?limit=5").json()
    assert len(body) == 5


def test_limit_clamped_to_max(client):
    # > 200 must 422 from the FastAPI Query validator (le=200).
    resp = client.get("/api/bot/backtests?limit=500")
    assert resp.status_code == 422


def test_limit_below_min_rejected(client):
    resp = client.get("/api/bot/backtests?limit=0")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tier-1 never-crash contract
# ---------------------------------------------------------------------------


def test_sqlite_error_returns_empty_not_500(tmp_path, monkeypatch, client):
    """If the DB read explodes mid-query, the endpoint returns [], not 500."""
    path = tmp_path / "broken.db"
    _create_table(path)
    monkeypatch.setattr(backtests_router, "_DB_PATH", path)

    def _boom(*args, **kwargs):
        raise sqlite3.DatabaseError("simulated read failure")

    monkeypatch.setattr(
        backtests_router, "_query_backtest_results", _boom,
    )
    resp = client.get("/api/bot/backtests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_unexpected_exception_returns_empty_not_500(tmp_path, monkeypatch, client):
    path = tmp_path / "unexpected.db"
    _create_table(path)
    monkeypatch.setattr(backtests_router, "_DB_PATH", path)

    def _boom(*args, **kwargs):
        raise RuntimeError("kapow")

    monkeypatch.setattr(
        backtests_router, "_query_backtest_results", _boom,
    )
    resp = client.get("/api/bot/backtests")
    assert resp.status_code == 200
    assert resp.json() == []
