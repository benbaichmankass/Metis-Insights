"""/api/diag/audit_query — historical, time/event-filtered audit read.

Covers the DB-backed reader added 2026-06-01 so an off-VM session can pull an
arbitrary historical window (or every row of one event type, e.g.
``regime_shadow_gate``) without the ~1000-line tail cap on ``/audit`` and
``/log_file?name=audit``. The reader SELECTs ``trade_journal.db::signals``
(the audit dual-write), so this seeds that table directly.

The app is assembled from just the diag router (not the full app) so the test
doesn't pull the auth stack.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.api.routers import diag as diag_router

_TOKEN = "test-diag-token-not-a-real-secret"
_H = {"Authorization": f"Bearer {_TOKEN}"}


def _seed(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE signals(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "logged_at_utc TEXT NOT NULL, strategy TEXT, symbol TEXT, side TEXT, "
        "qty REAL, status TEXT, reason TEXT, meta TEXT)"
    )
    rows = [
        # ts (col format: isoformat +00:00 w/ micros), strategy, symbol, side, reason, extra
        ("2026-06-01T14:00:00.123456+00:00", "vwap", "BTCUSDT", "none", "no signal",
         {"event": "vwap_eval", "regime": "chop", "adx_14": 15.3, "regime_source": "adx-14"}),
        ("2026-06-01T14:05:00.500000+00:00", "vwap", "BTCUSDT", "long", "regime_gated_chop",
         {"event": "regime_shadow_gate", "regime": "chop", "cell": "off", "gated": True, "enforced": False}),
        ("2026-06-01T15:00:00.000000+00:00", "trend_donchian", "MES", "buy", "breakout",
         {"event": "trend_donchian_eval", "regime": "trending", "adx_14": 31.2}),
        ("2026-05-30T10:00:00.000000+00:00", "vwap", "BTCUSDT", "long", "regime_gated_trending",
         {"event": "regime_shadow_gate", "regime": "trending", "cell": "off", "gated": True, "enforced": False}),
    ]
    for ts, strat, sym, side, reason, extra in rows:
        payload = {"strategy": strat, "symbol": sym, "side": side, "status": None,
                   "reason": reason, "logged_at_utc": ts, **extra}
        conn.execute(
            "INSERT INTO signals(logged_at_utc, strategy, symbol, side, qty, status, reason, meta) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ts, strat, sym, side, None, None, reason, json.dumps(payload)),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    db_path = tmp_path / "trade_journal.db"
    _seed(db_path)
    monkeypatch.setattr(diag_router, "_DB_PATH", db_path)
    app = FastAPI()
    app.include_router(diag_router.router)
    return TestClient(app, raise_server_exceptions=False)


def _q(client, qs: str):
    return client.get("/api/diag/audit_query" + qs, headers=_H)


def test_event_filter_returns_only_that_event_with_meta_merged(client):
    r = _q(client, "?event=regime_shadow_gate&limit=50").json()
    assert r["count"] == 2
    assert r["dual_write_present"] is True
    assert all(row["event"] == "regime_shadow_gate" for row in r["rows"])
    # meta payload is merged in (enforced/cell come from the JSON blob)
    assert all(row.get("enforced") is False for row in r["rows"])
    assert all(row.get("cell") == "off" for row in r["rows"])


@pytest.mark.parametrize(
    "since,until",
    [
        ("2026-06-01T14:00:00Z", "2026-06-01T14:10:00Z"),          # Z suffix
        # explicit offset — `+` MUST be percent-encoded in a query string
        ("2026-06-01T14:00:00%2B00:00", "2026-06-01T14:10:00%2B00:00"),
        ("2026-06-01T14:00:00", "2026-06-01T14:10:00"),            # naive (assume UTC)
    ],
)
def test_window_filter_is_offset_format_agnostic(client, since, until):
    # The boundary row at 14:00:00.123456+00:00 must be included regardless of
    # whether the caller used Z / +00:00 / naive — the col stores +00:00.
    r = _q(client, f"?since={since}&until={until}").json()
    assert r["count"] == 2, [x["event"] for x in r["rows"]]
    events = {x["event"] for x in r["rows"]}
    assert events == {"vwap_eval", "regime_shadow_gate"}


def test_strategy_and_event_combine(client):
    r = _q(client, "?strategy=vwap&event=regime_shadow_gate").json()
    assert r["count"] == 2
    assert all(x["strategy"] == "vwap" for x in r["rows"])


def test_newest_first_ordering(client):
    ts = [x["logged_at_utc"] for x in _q(client, "?limit=50").json()["rows"]]
    assert ts == sorted(ts, reverse=True)


def test_offset_paging(client):
    page0 = _q(client, "?limit=1&offset=0").json()
    page1 = _q(client, "?limit=1&offset=1").json()
    assert page0["count"] == 1 and page1["count"] == 1
    assert page0["rows"][0]["logged_at_utc"] != page1["rows"][0]["logged_at_utc"]


def test_invalid_timestamp_rejected(client):
    assert _q(client, "?since=notadate").status_code == 400


def test_invalid_event_charset_rejected(client):
    # a LIKE wildcard must not be accepted into the event filter
    assert _q(client, "?event=foo%bar").status_code == 400


def test_requires_token(client):
    assert client.get("/api/diag/audit_query").status_code == 401


def test_absent_signals_table_is_non_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()  # exists but no `signals` table
    monkeypatch.setattr(diag_router, "_DB_PATH", empty)
    app = FastAPI()
    app.include_router(diag_router.router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/diag/audit_query", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [] and body["dual_write_present"] is False
    assert body.get("error") == "signals_table_absent"
