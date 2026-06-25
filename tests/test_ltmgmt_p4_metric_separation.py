"""P4 — real/paper performance metric separation on /api/bot/stats.

Ref: docs/audits/live-trade-management-contract-2026-06-16.md § Design plan §4
(operator directive: real and paper performance kept strictly separate — never
blend); backlog BL-20260616-LTMGMT-P4METRICS.

These tests pin:
  1. /stats top-level (pnl24h, totalPnL, openTrades, winRate) is real-money-only
     (unchanged) — paper rows never leak into the headline numbers.
  2. /stats carries an additive ``paper`` sub-block + a distinct
     ``paperOpenTrades`` count computed over paper rows only.
  3. The two blocks are NOT a blended total (real openTrades != real+paper).
  4. The S-067 outage contract is preserved: missing DB → zeroes (incl. the
     paper block); broken schema → 503.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import dashboard as dashboard_router


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setattr(dashboard_router, "_DB_PATH", db)
    return db


def _seed(db: Path) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            created_at TEXT,
            closed_at TEXT,
            status TEXT,
            pnl REAL,
            is_backtest INTEGER DEFAULT 0,
            account_class TEXT,
            is_demo INTEGER DEFAULT 0,
            strategy_name TEXT,
            reconcile_status TEXT
        );
        -- pnl24h now joins order_packages for its close-time fallback
        -- (COALESCE(closed_at, op.updated_at, timestamp)); an empty table keeps
        -- the LEFT JOIN a no-op so the close-time falls back to timestamp.
        CREATE TABLE order_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linked_trade_id INTEGER,
            updated_at TEXT
        );
        """
    )
    rows = [
        # real-money: 1 open, 1 closed win (+10), 1 closed loss (-5)
        (today, today, "open", None, 0, "real_money", 0),
        (today, today, "closed", 10.0, 0, "real_money", 0),
        (today, today, "closed", -5.0, 0, "real_money", 0),
        # paper (account_class): 2 open, 1 closed win (+20)
        (today, today, "open", None, 0, "paper", 1),
        (today, today, "open", None, 0, "paper", 1),
        (today, today, "closed", 20.0, 0, "paper", 1),
        # paper via legacy is_demo only (account_class NULL): 1 closed loss (-3)
        (today, today, "closed", -3.0, 0, None, 1),
        # a backtest row that must be ignored entirely
        (today, today, "closed", 999.0, 1, "real_money", 0),
    ]
    conn.executemany(
        "INSERT INTO trades "
        "(timestamp, created_at, status, pnl, is_backtest, account_class, is_demo) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_stats_separates_real_and_paper(client: TestClient, isolated_db: Path):
    _seed(isolated_db)
    body = client.get("/api/bot/stats").json()

    # Real-money top level — 1 open; closed +10/-5 → total 5, win rate 50%.
    assert body["openTrades"] == 1
    assert body["totalPnL"] == 5.0
    assert body["winRate"] == 50.0
    assert body["pnl24h"] == 5.0

    # Paper block — 2 open; closed +20 (account_class) and -3 (is_demo legacy)
    # → total 17, win rate 50% (1 win of 2 closed).
    assert body["paperOpenTrades"] == 2
    paper = body["paper"]
    assert paper["openTrades"] == 2
    assert paper["totalPnL"] == 17.0
    assert paper["winRate"] == 50.0
    assert paper["pnl24h"] == 17.0


def test_stats_winrate_excludes_null_pnl_closed(
    client: TestClient, isolated_db: Path,
):
    """A closed real-money trade with NULL pnl (reconciler_incomplete) must NOT
    dilute the win-rate denominator — it carries no win/loss signal. The
    denominator counts RESOLVED closed trades only (pnl IS NOT NULL), matching
    /api/bot/performance. Regression for the stats-vs-performance winRate gap
    (live diag 2026-06-19: stats 6.3% while /performance read 25.6%)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(isolated_db))
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, created_at TEXT, closed_at TEXT, status TEXT, pnl REAL,
            is_backtest INTEGER DEFAULT 0, account_class TEXT,
            is_demo INTEGER DEFAULT 0, strategy_name TEXT, reconcile_status TEXT
        );
        CREATE TABLE order_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linked_trade_id INTEGER, updated_at TEXT
        );
        """
    )
    rows = [
        # real-money: 1 win (+10), 1 loss (-5), 3 closed with NULL pnl.
        (today, today, "closed", 10.0, 0, "real_money", 0),
        (today, today, "closed", -5.0, 0, "real_money", 0),
        (today, today, "closed", None, 0, "real_money", 0),
        (today, today, "closed", None, 0, "real_money", 0),
        (today, today, "closed", None, 0, "real_money", 0),
    ]
    conn.executemany(
        "INSERT INTO trades "
        "(timestamp, created_at, status, pnl, is_backtest, account_class, is_demo) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    body = client.get("/api/bot/stats").json()
    # 1 win of 2 RESOLVED closed = 50%, NOT 1/5 = 20% (NULL rows excluded).
    assert body["winRate"] == 50.0


def test_stats_blocks_are_not_blended(client: TestClient, isolated_db: Path):
    """The headline openTrades is real-only (1), NOT the merged real+paper (3)."""
    _seed(isolated_db)
    body = client.get("/api/bot/stats").json()
    assert body["openTrades"] == 1
    assert body["openTrades"] != body["openTrades"] + body["paperOpenTrades"]
    # totalPnL real (5) and paper (17) are never summed into one field.
    assert body["totalPnL"] == 5.0
    assert body["paper"]["totalPnL"] == 17.0


def test_stats_missing_db_zeroes_both_blocks(client: TestClient, isolated_db: Path):
    assert not isolated_db.exists()
    body = client.get("/api/bot/stats").json()
    assert body["openTrades"] == 0
    assert body["paperOpenTrades"] == 0
    assert body["paper"] == {
        "pnl24h": 0, "totalPnL": 0, "openTrades": 0, "winRate": 0,
    }


def test_stats_broken_schema_still_503(client: TestClient, isolated_db: Path):
    conn = sqlite3.connect(str(isolated_db))
    # trades table missing is_backtest / account_class → OperationalError.
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, pnl REAL);"
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/bot/stats")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "stats_unavailable"
