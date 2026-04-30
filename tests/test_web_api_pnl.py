"""S-013 M2 PR #2 — GET /api/pnl."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main
from src.web.api.routers import pnl as pnl_router


@pytest.fixture
def client():
    return TestClient(api_main.app)


def _write_accounts_yaml(path: Path, names: list[str]) -> Path:
    body = "accounts:\n" + "".join(f"  {n}: {{}}\n" for n in names)
    path.write_text(body, encoding="utf-8")
    return path


def _make_journal(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
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
            created_at TEXT
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
def fixtures(tmp_path, monkeypatch):
    db = _make_journal(tmp_path / "trade_journal.db")
    accounts = _write_accounts_yaml(tmp_path / "accounts.yaml", ["main", "prop_a"])
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: db)
    monkeypatch.setattr(pnl_router, "_resolve_accounts_yaml", lambda: accounts)
    return db, accounts


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 4, 30, 12, 0, 0, tzinfo=tz or timezone.utc)


def test_pnl_happy_path_aggregates_realised_unrealised_and_trades_today(
    fixtures, client, monkeypatch
):
    db, _ = fixtures
    today = "2026-04-30"
    yesterday = "2026-04-29"

    # main: realised winner today + open position today.
    _insert(db, {
        "timestamp": today, "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 50_000, "position_size": 0.01, "pnl": 12.50,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": f"{today}T08:00:00Z",
    })
    _insert(db, {
        "timestamp": today, "symbol": "ETHUSDT", "direction": "short",
        "entry_price": 3_000, "position_size": 0.10, "pnl": -2.25,
        "status": "open", "is_backtest": 0, "account_id": "main",
        "created_at": f"{today}T11:30:00Z",
    })
    # main: realised loser yesterday (counts toward realised, not trades_today).
    _insert(db, {
        "timestamp": yesterday, "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 49_000, "position_size": 0.01, "pnl": -7.00,
        "status": "closed", "is_backtest": 0, "account_id": "main",
        "created_at": f"{yesterday}T14:00:00Z",
    })
    # backtest row that must be ignored.
    _insert(db, {
        "timestamp": today, "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 1, "position_size": 1, "pnl": 999.0,
        "status": "closed", "is_backtest": 1, "account_id": "main",
        "created_at": f"{today}T09:00:00Z",
    })

    monkeypatch.setattr(pnl_router, "datetime", _FrozenDatetime)

    resp = client.get("/api/pnl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert set(body["accounts"].keys()) >= {"main", "prop_a"}

    main = body["accounts"]["main"]
    assert main["realized_usd"] == pytest.approx(5.50)        # 12.50 - 7.00
    assert main["unrealized_usd"] == pytest.approx(-2.25)
    assert main["trades_today"] == 2                          # only live rows from today

    prop = body["accounts"]["prop_a"]
    assert prop == {"realized_usd": 0.0, "unrealized_usd": 0.0, "trades_today": 0}


def test_pnl_empty_journal_returns_all_zeros(fixtures, client):
    resp = client.get("/api/pnl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["accounts"] == {
        "main":   {"realized_usd": 0.0, "unrealized_usd": 0.0, "trades_today": 0},
        "prop_a": {"realized_usd": 0.0, "unrealized_usd": 0.0, "trades_today": 0},
    }
    assert body["as_of_utc"].endswith("Z")


def test_pnl_missing_db_file_returns_zeros_not_503(tmp_path, monkeypatch, client):
    accounts = _write_accounts_yaml(tmp_path / "accounts.yaml", ["main"])
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: tmp_path / "does-not-exist.db")
    monkeypatch.setattr(pnl_router, "_resolve_accounts_yaml", lambda: accounts)
    resp = client.get("/api/pnl")
    assert resp.status_code == 200
    assert resp.json()["accounts"] == {
        "main": {"realized_usd": 0.0, "unrealized_usd": 0.0, "trades_today": 0},
    }


def test_pnl_db_error_returns_503(tmp_path, monkeypatch, client):
    bogus = tmp_path / "bogus.db"
    bogus.write_text("not a sqlite database")
    accounts = _write_accounts_yaml(tmp_path / "accounts.yaml", ["main"])
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: bogus)
    monkeypatch.setattr(pnl_router, "_resolve_accounts_yaml", lambda: accounts)
    resp = client.get("/api/pnl")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "pnl_unavailable"


def test_pnl_surfaces_legacy_account_ids_not_in_yaml(fixtures, client):
    db, _ = fixtures
    _insert(db, {
        "timestamp": "2026-01-01", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 1, "position_size": 1, "pnl": 1.23,
        "status": "closed", "is_backtest": 0, "account_id": "live",  # legacy default
        "created_at": "2026-01-01T00:00:00Z",
    })
    resp = client.get("/api/pnl")
    assert resp.status_code == 200
    body = resp.json()
    assert "live" in body["accounts"]
    assert body["accounts"]["live"]["realized_usd"] == pytest.approx(1.23)


def test_require_session_passthrough_on_pnl(fixtures, client):
    """M3 PR #2 regression guard: no Authorization → 200 today."""
    resp = client.get("/api/pnl")
    assert resp.status_code == 200
