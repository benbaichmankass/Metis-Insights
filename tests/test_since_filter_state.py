"""``?since=`` is parsed or REFUSED -- never a silent empty result.

PI-20261005-MWOP8C4X-0002 (/api/bot/order-packages) and -0003
(/api/bot/trades/closed): an unparseable ``since`` -- most often an ISO offset
whose ``+`` the URL decoded to a space -- reached SQLite ``datetime(?)`` as NULL
and matched nothing, so "we could not parse your filter" and "nothing matches"
were the same ``200`` with zero rows.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api._since import parse_since
from src.web.api.routers import order_packages as op_router
from src.web.api.routers import trades_closed as tc_router
from tests.fixtures.real_schema_db import (
    insert_order_package, insert_trade, make_canonical_db,
)


@pytest.mark.parametrize("raw,expect", [
    ("2026-10-05T00:00:00Z", "2026-10-05 00:00:00"),
    ("2026-10-05T00:00:00+00:00", "2026-10-05 00:00:00"),
    # '+' decoded to a space by the query-string parser
    ("2026-10-05T00:00:00 00:00", "2026-10-05 00:00:00"),
    ("2026-10-05T02:00:00+02:00", "2026-10-05 00:00:00"),
    ("2026-10-05", "2026-10-05 00:00:00"),
    ("2026-10-05T01:02:03", "2026-10-05 01:02:03"),   # naive == UTC
])
def test_parseable_values_normalise_to_utc(raw, expect):
    assert parse_since(raw) == (expect, "parsed")


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_absent_is_none_state_not_parsed(raw):
    assert parse_since(raw) == (None, "none")


@pytest.mark.parametrize("raw", ["yesterday", "2026-13-45", "not-a-date", "123abc"])
def test_unparseable_is_a_422_naming_the_state(raw):
    with pytest.raises(HTTPException) as ei:
        parse_since(raw)
    assert ei.value.status_code == 422
    assert ei.value.detail["filter_state"] == "unparsed"
    assert ei.value.detail["error"] == "invalid_since"


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "trade_journal.db"
    make_canonical_db(path)
    monkeypatch.setattr(op_router, "_DB_PATH", path)
    monkeypatch.setattr(op_router, "_CLAUDE_SCORES", tmp_path / "scores.jsonl")
    monkeypatch.setattr(tc_router, "_DB_PATH", path)
    tid = insert_trade(
        path, timestamp="2026-10-05T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=1.0, position_size=1.0, status="closed",
        is_backtest=0, is_demo=0, closed_at="2026-10-05T11:00:00Z")
    insert_order_package(path, order_package_id="op1", linked_trade_id=tid,
                         created_at="2026-10-05T10:00:00Z")
    return path


def test_order_packages_plus_offset_matches_instead_of_silently_emptying(db, client):
    # raw '+' in a URL -> space server-side: this used to return ZERO rows
    body = client.get("/api/bot/order-packages?since=2026-10-01T00:00:00+00:00").json()
    assert body["filter_state"] == "parsed"
    assert body["count"] == 1


def test_order_packages_unparseable_since_is_422_not_empty(db, client):
    r = client.get("/api/bot/order-packages?since=garbage")
    assert r.status_code == 422
    assert r.json()["detail"]["filter_state"] == "unparsed"


def test_order_packages_without_since_reports_none(db, client):
    assert client.get("/api/bot/order-packages").json()["filter_state"] == "none"


def test_order_packages_since_after_row_is_a_real_empty(db, client):
    body = client.get("/api/bot/order-packages?since=2026-12-01T00:00:00Z").json()
    assert body["filter_state"] == "parsed" and body["count"] == 0


def test_closed_trades_unparseable_since_is_422_not_bare_list(db, client):
    r = client.get("/api/bot/trades/closed?since=garbage")
    assert r.status_code == 422
    assert r.json()["detail"]["filter_state"] == "unparsed"


def test_closed_trades_plus_offset_matches_and_reports_parsed(db, client):
    r = client.get("/api/bot/trades/closed?since=2026-10-01T00:00:00+00:00")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.headers["X-Filter-State"] == "parsed"


def test_closed_trades_without_since_reports_none(db, client):
    assert client.get("/api/bot/trades/closed").headers["X-Filter-State"] == "none"
