"""S-014 M0 PR #1 — GET /api/pnl/history.

S-063 (2026-05-09): no-session endpoint, returns a flat
``PnlHistoryPoint[]`` matching the dashboard's TypeScript contract
(``[{date, pnl, trades}, ...]``). Field rename: ``realized_usd`` →
``pnl``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import pnl as pnl_router
from src.web.api.routers import pnl_history as pnl_history_router


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


def _make_journal(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            position_size REAL,
            pnl REAL,
            status TEXT,
            is_backtest INTEGER DEFAULT 0,
            account_id TEXT NOT NULL DEFAULT 'live',
            is_demo BOOLEAN DEFAULT 0,
            account_class TEXT,
            strategy_name TEXT,
            created_at TEXT,
            closed_at TEXT,
            reconcile_status TEXT
        );
        -- /pnl/history now buckets on CLOSE-time COALESCE(closed_at,
        -- op.updated_at, timestamp) and LEFT JOINs order_packages; an empty
        -- table keeps the join a no-op (close-time falls back to timestamp).
        CREATE TABLE order_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linked_trade_id INTEGER,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return path


def _insert(path: Path, row: dict) -> None:
    conn = sqlite3.connect(str(path))
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO trades ({cols}) VALUES ({placeholders})", list(row.values()))
    conn.commit()
    conn.close()


@pytest.fixture
def journal(tmp_path, monkeypatch):
    db = _make_journal(tmp_path / "trade_journal.db")
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: db)
    return db


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 4, 30, 12, 0, 0, tzinfo=tz or timezone.utc)


def test_history_default_window_is_seven_with_data(journal, client, monkeypatch):
    monkeypatch.setattr(pnl_history_router, "datetime", _FrozenDatetime)
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 50_000, "position_size": 0.01, "pnl": 1.0,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-30T08:00:00Z",
    })
    resp = client.get("/api/pnl/history")
    assert resp.status_code == 200
    points = resp.json()
    assert isinstance(points, list)
    assert len(points) == 7
    # Window: 2026-04-24 .. 2026-04-30 (inclusive, contiguous).
    assert points[0]["date"] == "2026-04-24"
    assert points[-1]["date"] == "2026-04-30"
    # Per-row contract: every row carries date + pnl (+ trades count).
    for p in points:
        assert set(p.keys()) >= {"date", "pnl", "trades"}


def test_history_empty_journal_returns_empty_array(journal, client, monkeypatch):
    monkeypatch.setattr(pnl_history_router, "datetime", _FrozenDatetime)
    resp = client.get("/api/pnl/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_aggregates_realized_per_day_ignores_open_and_backtest(
    journal, client, monkeypatch
):
    monkeypatch.setattr(pnl_history_router, "datetime", _FrozenDatetime)
    # 2026-04-30: two closed trades net +10.25, one open (ignored).
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 50_000, "position_size": 0.01, "pnl": 12.50,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-30T08:00:00Z",
    })
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "ETHUSDT", "direction": "short",
        "entry_price": 3_000, "position_size": 0.10, "pnl": -2.25,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-30T09:30:00Z",
    })
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "ETHUSDT", "direction": "short",
        "entry_price": 3_000, "position_size": 0.10, "pnl": -99.0,
        "status": "open", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-30T11:00:00Z",
    })
    # 2026-04-28: one closed -7.00.
    _insert(journal, {
        "timestamp": "2026-04-28", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 49_000, "position_size": 0.01, "pnl": -7.00,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-28T14:00:00Z",
    })
    # Backtest row on 2026-04-29 — must be ignored.
    _insert(journal, {
        "timestamp": "2026-04-29", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 1, "position_size": 1, "pnl": 999.0,
        "status": "closed", "is_backtest": 1, "account_id": "main",
        "created_at": "2026-04-29T09:00:00Z",
    })
    # Pre-window row (8 days ago) — must be excluded from a 7-day window.
    _insert(journal, {
        "timestamp": "2026-04-23", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 1, "position_size": 1, "pnl": 500.0,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-23T09:00:00Z",
    })

    resp = client.get("/api/pnl/history?days=7")
    assert resp.status_code == 200
    by_date = {p["date"]: p for p in resp.json()}

    assert by_date["2026-04-30"]["pnl"] == pytest.approx(10.25)
    assert by_date["2026-04-30"]["trades"] == 2
    assert by_date["2026-04-29"] == {"date": "2026-04-29", "pnl": 0.0, "trades": 0}
    assert by_date["2026-04-28"]["pnl"] == pytest.approx(-7.00)
    assert by_date["2026-04-28"]["trades"] == 1
    assert "2026-04-23" not in by_date


def test_history_custom_days_param(journal, client, monkeypatch):
    monkeypatch.setattr(pnl_history_router, "datetime", _FrozenDatetime)
    _insert(journal, {
        "timestamp": "2026-04-01", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 1, "position_size": 1, "pnl": 1.0,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-01T00:00:00Z",
    })
    resp = client.get("/api/pnl/history?days=30")
    assert resp.status_code == 200
    points = resp.json()
    assert len(points) == 30
    assert points[0]["date"] == "2026-04-01"
    assert points[-1]["date"] == "2026-04-30"


def test_history_days_clamped_low(client):
    resp = client.get("/api/pnl/history?days=0")
    assert resp.status_code == 422


def test_history_days_clamped_high(client):
    resp = client.get("/api/pnl/history?days=91")
    assert resp.status_code == 422


def test_history_missing_db_returns_empty_array_not_503(tmp_path, monkeypatch, client):
    monkeypatch.setattr(
        pnl_router, "_resolve_db_path", lambda: tmp_path / "does-not-exist.db"
    )
    resp = client.get("/api/pnl/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_corrupt_db_returns_503(tmp_path, monkeypatch, client):
    bogus = tmp_path / "bogus.db"
    bogus.write_text("not a sqlite database")
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: bogus)
    resp = client.get("/api/pnl/history")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "pnl_history_unavailable"


# ---------------------------------------------------------------------------
# S-063: no-session contract. The Vercel dashboard hits this endpoint without
# a JWT (option (a) — drop the gate on the read-only path only). Every
# mutating route keeps `require_session`. See docs/api-tier-policy.md.
# ---------------------------------------------------------------------------


def test_history_without_session_returns_200(journal, client, monkeypatch):
    """Tier-1 read surface: no Authorization header, still 200."""
    monkeypatch.setattr(pnl_history_router, "datetime", _FrozenDatetime)
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 50_000, "position_size": 0.01, "pnl": 3.50,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": "2026-04-30T08:00:00Z",
    })
    resp = client.get("/api/pnl/history?days=30")
    assert resp.status_code == 200
    points = resp.json()
    assert isinstance(points, list)
    assert len(points) == 30
    by_date = {p["date"]: p for p in points}
    assert by_date["2026-04-30"]["pnl"] == pytest.approx(3.50)
    # No auth env vars were configured for this test — the route must not
    # touch the auth machinery at all.
