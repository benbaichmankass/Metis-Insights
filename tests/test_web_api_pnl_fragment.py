"""S-014 M3 PR #2 — GET /ui/fragments/pnl."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main
from src.web.api.routers import pnl as pnl_router

_ALLOWED_EMAIL = "ben.baichmankass@gmail.com"
_PASSWORD_HASH = hashlib.sha256(b"correct horse battery staple").hexdigest()
_SIGNING_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("ALLOWED_EMAIL", _ALLOWED_EMAIL)
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", _PASSWORD_HASH)


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


def _bearer(email: str = _ALLOWED_EMAIL) -> dict:
    return {"Authorization": f"Bearer {auth_module.issue_token(email)}"}


def _make_journal(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, direction TEXT,
            entry_price REAL, position_size REAL, pnl REAL,
            status TEXT, is_backtest INTEGER DEFAULT 0,
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
def journal(tmp_path, monkeypatch):
    db = _make_journal(tmp_path / "trade_journal.db")
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: db)
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        "accounts:\n  bybit_1: {}\n  bybit_2: {}\n", encoding="utf-8"
    )
    monkeypatch.setattr(pnl_router, "_resolve_accounts_yaml", lambda: accounts_yaml)
    return db


def test_pnl_fragment_renders_per_account_cards(journal, client):
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 50_000, "position_size": 0.01, "pnl": 12.50,
        "status": "closed", "is_backtest": 0, "account_id": "bybit_1",
        "created_at": "2026-04-30T08:00:00Z",
    })
    _insert(journal, {
        "timestamp": "2026-04-30", "symbol": "ETHUSDT", "direction": "short",
        "entry_price": 3_000, "position_size": 0.10, "pnl": -7.25,
        "status": "open", "is_backtest": 0, "account_id": "bybit_2",
        "created_at": "2026-04-30T09:00:00Z",
    })
    resp = client.get("/ui/fragments/pnl", headers=_bearer())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    for needle in ("bybit_1", "bybit_2", "$12.50", "$-7.25",
                   "realised", "unrealised", "trades today",
                   'class="metric positive"', 'class="metric negative"'):
        assert needle in body, needle


def test_pnl_fragment_zero_state(journal, client):
    """Empty journal must show all-zero per-account cards (no exception)."""
    resp = client.get("/ui/fragments/pnl", headers=_bearer())
    assert resp.status_code == 200
    body = resp.text
    assert "bybit_1" in body and "bybit_2" in body
    assert "$0.00" in body


def test_pnl_fragment_503_when_db_corrupt(tmp_path, monkeypatch, client):
    bogus = tmp_path / "bogus.db"
    bogus.write_text("not a sqlite database")
    monkeypatch.setattr(pnl_router, "_resolve_db_path", lambda: bogus)
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text("accounts:\n  bybit_1: {}\n", encoding="utf-8")
    monkeypatch.setattr(pnl_router, "_resolve_accounts_yaml", lambda: accounts_yaml)
    resp = client.get("/ui/fragments/pnl", headers=_bearer())
    assert resp.status_code == 503
    assert "P&amp;L not yet available" in resp.text


def test_pnl_fragment_without_token_returns_401(journal, client):
    resp = client.get("/ui/fragments/pnl")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_session"


def test_pnl_fragment_off_allowlist_returns_403(journal, client):
    resp = client.get("/ui/fragments/pnl", headers=_bearer("attacker@example.com"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "email_not_allowlisted"
