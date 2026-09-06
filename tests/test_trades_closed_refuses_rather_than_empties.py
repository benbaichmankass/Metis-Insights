"""MI-144(c) — ``/api/bot/trades/closed`` SERVES the window or REFUSES it with a
reason. It never returns a bare ``[]`` to mean "something went wrong".

This is the route a performance review grades from, so an unreadable journal
rendering as *"no closed trades yet"* is a clean, confident, WRONG negative —
``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states" applied to a whole
endpoint: *we could not look* and *we looked and there is nothing* had one
representation.

⚠️ THE BRIEFED SYMPTOM DID NOT REPRODUCE, AND THAT IS RECORDED HERE SO IT IS NOT
RE-DERIVED. MEASURED against the live endpoint 2026-09-06
(``https://ict-bot.duckdns.org/api/bot/trades/closed``): limit 5→5, 100→100,
200→200 all HTTP 200; limit 201/400/800 → **HTTP 422** naming the cap
(``"Input should be less than or equal to 200"``); ``since=2026-09-01`` → 142
rows, ``since=2026-01-01`` → 200. So the route did NOT silently empty above the
cap — it refused, and a client coercing a 422 body to ``[]``/0 is what produced
the "400→0, 800→0" reading. The REAL instances of the class are (1) the
``except`` handlers below and (2) that the window above the cap was UNREACHABLE
because there was no ``offset``.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.api.routers import trades_closed as mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, direction TEXT,
            entry_price REAL, exit_price REAL, position_size REAL, pnl REAL,
            pnl_percent REAL, status TEXT, notes TEXT, is_backtest INTEGER,
            strategy_name TEXT, account_id TEXT, is_demo INTEGER,
            account_class TEXT, closed_at TEXT, exit_reason TEXT,
            reconcile_status TEXT
        );
        CREATE TABLE order_packages (
            order_package_id TEXT PRIMARY KEY, linked_trade_id INTEGER,
            updated_at TEXT
        );
        """
    )
    for i in range(1, 8):
        conn.execute(
            "INSERT INTO trades (id, timestamp, symbol, direction, entry_price,"
            " exit_price, position_size, pnl, status, is_backtest, account_id,"
            " is_demo, account_class, closed_at, exit_reason) VALUES"
            " (?,?,?,?,?,?,?,?, 'closed', 0, 'acct', 0, 'real_money', ?, 'tp')",
            (i, f"2026-09-0{i}T00:00:00", "BTCUSDT", "long", 100.0, 101.0,
             1.0, 1.0, f"2026-09-0{i}T01:00:00"),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr(mod, "_DB_PATH", db)
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_missing_journal_refuses_with_a_reason_and_is_not_an_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_DB_PATH", tmp_path / "does-not-exist.db")
    app = FastAPI()
    app.include_router(mod.router)
    resp = TestClient(app, raise_server_exceptions=False).get("/api/bot/trades/closed")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error"] == "journal_unavailable"
    # The REASON is machine-readable, because "db missing" and "db locked" have
    # different remedies and must not share one message.
    assert detail["reason"] == "db_file_missing"


def test_a_read_failure_refuses_and_names_which_failure(client, monkeypatch):
    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(mod, "_query_closed_trades", boom)
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "db_operational"

    def boom2(*_a, **_k):
        raise ValueError("something else entirely")
    monkeypatch.setattr(mod, "_query_closed_trades", boom2)
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "unexpected_error"


def test_an_empty_result_is_the_ONLY_thing_an_empty_list_may_mean(client):
    """A filter that genuinely matches nothing still returns 200 + []. The point
    of the refusals above is not to stop returning empty lists — it is that an
    empty list may only ever mean *we looked and there is nothing*."""
    resp = client.get("/api/bot/trades/closed?account_id=no-such-account")
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"
    assert resp.headers["X-Has-More"] == "false"


def test_truncation_is_distinguishable_from_exhaustion(client):
    """Without these headers a full page and a complete answer render
    identically — the silent half of the same defect."""
    resp = client.get("/api/bot/trades/closed?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3
    assert resp.headers["X-Total-Count"] == "7"
    assert resp.headers["X-Has-More"] == "true"

    resp = client.get("/api/bot/trades/closed?limit=100")
    assert len(resp.json()) == 7
    assert resp.headers["X-Has-More"] == "false"


def test_offset_makes_the_window_above_one_page_REACHABLE(client):
    """Raising the cap is NOT the fix and was never proposed as one. The cap is
    a legitimate refusal that already names its bound; what was missing is a way
    to serve the window anyway."""
    page1 = client.get("/api/bot/trades/closed?limit=3&offset=0").json()
    page2 = client.get("/api/bot/trades/closed?limit=3&offset=3").json()
    page3 = client.get("/api/bot/trades/closed?limit=3&offset=6").json()
    ids = [r["id"] for r in page1 + page2 + page3]
    assert ids == ["7", "6", "5", "4", "3", "2", "1"]   # newest-first, no gaps
    assert len(set(ids)) == 7                            # and no duplicates


def test_the_total_count_reflects_the_filters_not_the_whole_table(client):
    """A count computed over the wrong WHERE is the db_explorer defect
    (BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN): `total`
    came back as the entire table and read as "the filter matched everything"."""
    resp = client.get("/api/bot/trades/closed?limit=2&since=2026-09-05")
    assert resp.headers["X-Total-Count"] == "3"          # 05, 06, 07 — not 7
    assert resp.headers["X-Has-More"] == "true"


def test_the_cap_still_refuses_with_a_reason_rather_than_emptying(client):
    resp = client.get("/api/bot/trades/closed?limit=400")
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"][0]["type"] == "less_than_equal"
    assert body["detail"][0]["ctx"]["le"] == mod.MAX_LIMIT
