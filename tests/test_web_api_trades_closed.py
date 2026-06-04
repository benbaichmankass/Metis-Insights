"""S-557 — GET /api/bot/trades/closed tests.

Tier-1 read endpoint. Reads ``trade_journal.db::trades`` rows with
``status='closed'`` joined to ``order_packages`` for the closed_at
proxy. The dashboard's Journals tab consumes this; until it deploys
the dashboard falls back to log-derived rows.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import trades_closed as trades_closed_router

# S-067 follow-up #1: shared canonical-schema fixture.
from tests.fixtures.real_schema_db import (
    insert_order_package as _insert_package,
    insert_trade as _insert_trade,
    make_canonical_db,
)


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "trade_journal.db"
    make_canonical_db(path)
    monkeypatch.setattr(trades_closed_router, "_DB_PATH", path)
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_closed_trade_with_full_shape(db, client):
    trade_id = _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z",
        symbol="BTCUSDT",
        direction="long",
        entry_price=62000.0,
        exit_price=62150.0,
        position_size=0.001,
        exit_reason="tp",
        pnl=0.15,
        pnl_percent=0.0024,
        status="closed",
        is_backtest=0,
        strategy_name="turtle_soup",
        account_id="bybit_2",
    )
    _insert_package(
        db,
        order_package_id="pkg-1",
        linked_trade_id=trade_id,
        updated_at="2026-05-08T10:42:00Z",
        close_reason="tp",
    )

    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row == {
        "id": str(trade_id),
        "account": "bybit_2",
        "symbol": "BTCUSDT",
        "side": "buy",  # long → buy
        "pattern": "turtle_soup",
        "qty": 0.001,
        "entryPrice": 62000.0,
        "exitPrice": 62150.0,
        "realizedPnl": 0.15,
        "realizedPnlPct": 0.0024,
        "openedAt": "2026-05-08T10:00:00Z",
        "closedAt": "2026-05-08T10:42:00Z",  # from order_packages.updated_at
        "closeReason": "tp",
    }


def test_excludes_open_trades(db, client):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z",
        symbol="BTCUSDT",
        direction="long",
        entry_price=62000.0,
        position_size=0.001,
        status="open",  # NOT closed
        is_backtest=0,
        account_id="bybit_2",
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    assert resp.json() == []


def test_excludes_backtest_trades(db, client):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z",
        symbol="BTCUSDT",
        direction="long",
        entry_price=62000.0,
        exit_price=62150.0,
        position_size=0.001,
        status="closed",
        is_backtest=1,  # SYNTHETIC
        account_id="backtest",
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    assert resp.json() == []


def test_orders_newest_first_by_closed_at(db, client):
    """When op.updated_at is present, ordering is by op.updated_at DESC.
    Otherwise it falls back to t.timestamp DESC (also asserted)."""
    older_id = _insert_trade(
        db,
        timestamp="2026-05-07T08:00:00Z",
        symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2", strategy_name="vwap",
    )
    newer_id = _insert_trade(
        db,
        timestamp="2026-05-08T08:00:00Z",
        symbol="ETHUSDT", direction="short",
        entry_price=3000.0, exit_price=2950.0,
        position_size=0.1, status="closed", is_backtest=0,
        account_id="bybit_2", strategy_name="turtle_soup",
    )
    _insert_package(db, order_package_id="p-old", linked_trade_id=older_id,
                    updated_at="2026-05-07T09:00:00Z")
    _insert_package(db, order_package_id="p-new", linked_trade_id=newer_id,
                    updated_at="2026-05-08T09:00:00Z")
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [str(newer_id), str(older_id)]


# ---------------------------------------------------------------------------
# Side normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "direction,expected_side",
    [("long", "buy"), ("short", "sell"), ("buy", "buy"), ("sell", "sell")],
)
def test_side_mapping(db, client, direction, expected_side):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction=direction, entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2",
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.json()[0]["side"] == expected_side


# ---------------------------------------------------------------------------
# closeReason normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exit_reason,expected_close_reason",
    [
        ("tp", "tp"),
        ("sl", "sl"),
        ("manual", "manual"),
        ("reconciler_filled", "reconciler"),
        ("reconciler_orphaned", "reconciler"),
        ("trail_hit", "other"),
        ("", None),
        (None, None),
    ],
)
def test_close_reason_normalisation(db, client, exit_reason, expected_close_reason):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, exit_reason=exit_reason,
        status="closed", is_backtest=0, account_id="bybit_2",
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.json()[0]["closeReason"] == expected_close_reason


# ---------------------------------------------------------------------------
# closed_at fallback chain
# ---------------------------------------------------------------------------


def test_closed_at_falls_back_to_notes_when_no_package(db, client):
    """Reconciler-close path: no order_package row, but closed_at lives
    in the trade's notes JSON (per order_monitor._close_trade_from_order_status)."""
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2",
        notes=json.dumps({"closed_at": "2026-05-08T10:42:00Z",
                          "closed_by": "monitor_reconciler"}),
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.json()[0]["closedAt"] == "2026-05-08T10:42:00Z"


def test_closed_at_null_when_no_package_and_no_notes(db, client):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2", notes=None,
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.json()[0]["closedAt"] is None


def test_closed_at_prefers_package_over_notes(db, client):
    trade_id = _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2",
        notes=json.dumps({"closed_at": "1999-01-01T00:00:00Z"}),
    )
    _insert_package(db, order_package_id="p-1", linked_trade_id=trade_id,
                    updated_at="2026-05-08T10:42:00Z")
    resp = client.get("/api/bot/trades/closed")
    # Package's updated_at wins; the stale notes value is ignored.
    assert resp.json()[0]["closedAt"] == "2026-05-08T10:42:00Z"


def test_malformed_notes_does_not_crash(db, client):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2", notes="not json {",
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    assert resp.json()[0]["closedAt"] is None


# ---------------------------------------------------------------------------
# Nullable / missing fields
# ---------------------------------------------------------------------------


def test_nullable_pnl_and_pattern(db, client):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, exit_reason=None, pnl=None,
        pnl_percent=None, status="closed", is_backtest=0,
        strategy_name=None, account_id="bybit_2",
    )
    row = client.get("/api/bot/trades/closed").json()[0]
    assert row["pattern"] is None
    # 2026-06-04 reporting-cleanup: NULL pnl now renders as null
    # (was 0.0 — that coercion misled the operator into reading every
    # "PnL-unknown" reconciler-incomplete row as a flat $0 trade).
    assert row["realizedPnl"] is None
    assert row["realizedPnlPct"] is None  # NULL pct stays null
    assert row["closeReason"] is None


def test_nullable_exit_price(db, client):
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=None,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2",
    )
    row = client.get("/api/bot/trades/closed").json()[0]
    assert row["exitPrice"] is None


# ---------------------------------------------------------------------------
# Query params
# ---------------------------------------------------------------------------


def test_limit_clamps_results(db, client):
    for i in range(5):
        _insert_trade(
            db,
            timestamp=f"2026-05-08T10:0{i}:00Z", symbol="BTCUSDT",
            direction="long", entry_price=60000.0, exit_price=60500.0,
            position_size=0.001, status="closed", is_backtest=0,
            account_id="bybit_2",
        )
    resp = client.get("/api/bot/trades/closed?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_limit_too_low_is_422(db, client):
    assert client.get("/api/bot/trades/closed?limit=0").status_code == 422


def test_limit_too_high_is_422(db, client):
    assert client.get("/api/bot/trades/closed?limit=201").status_code == 422


def test_since_filter(db, client):
    older_id = _insert_trade(
        db,
        timestamp="2026-05-07T08:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2",
    )
    newer_id = _insert_trade(
        db,
        timestamp="2026-05-08T08:00:00Z", symbol="ETHUSDT",
        direction="short", entry_price=3000.0, exit_price=2950.0,
        position_size=0.1, status="closed", is_backtest=0,
        account_id="bybit_2",
    )
    _insert_package(db, order_package_id="p-old", linked_trade_id=older_id,
                    updated_at="2026-05-07T09:00:00Z")
    _insert_package(db, order_package_id="p-new", linked_trade_id=newer_id,
                    updated_at="2026-05-08T09:00:00Z")
    resp = client.get("/api/bot/trades/closed?since=2026-05-08T00:00:00Z")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [str(newer_id)]


# ---------------------------------------------------------------------------
# Tier-1 contract — no session required
# ---------------------------------------------------------------------------


def test_isdemo_flag_and_include_demo_split(db, client):
    """2026-06-04 reporting-cleanup: every closed row carries an ``isDemo``
    flag; ``?include_demo=true`` returns both live and demo segments so
    the consumer can render them as separate sections. The default (no
    flag) preserves live-only behavior."""
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=62000.0, exit_price=62150.0,
        position_size=0.001, exit_reason="tp", pnl=0.15,
        status="closed", is_backtest=0, is_demo=0,
        account_id="bybit_2", strategy_name="turtle_soup",
    )
    _insert_trade(
        db,
        timestamp="2026-05-08T11:00:00Z", symbol="BTCUSDT",
        direction="short", entry_price=62200.0, exit_price=62100.0,
        position_size=0.002, exit_reason="tp", pnl=0.20,
        status="closed", is_backtest=0, is_demo=1,
        account_id="bybit_1", strategy_name="vwap",
    )

    # Default — live only, still tagged.
    body = client.get("/api/bot/trades/closed").json()
    assert len(body) == 1
    assert body[0]["account"] == "bybit_2"
    assert body[0]["isDemo"] is False

    # Opted in — both segments, each tagged.
    body = client.get("/api/bot/trades/closed?include_demo=true").json()
    assert len(body) == 2
    by_account = {r["account"]: r for r in body}
    assert by_account["bybit_2"]["isDemo"] is False
    assert by_account["bybit_1"]["isDemo"] is True


def test_no_session_returns_200(db, client):
    """Tier-1 — no Authorization header, no auth env vars; still 200."""
    _insert_trade(
        db,
        timestamp="2026-05-08T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, exit_price=60500.0,
        position_size=0.001, status="closed", is_backtest=0,
        account_id="bybit_2",
    )
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Best-effort error paths
# ---------------------------------------------------------------------------


def test_missing_db_returns_empty_list(tmp_path, monkeypatch, client):
    monkeypatch.setattr(trades_closed_router, "_DB_PATH", tmp_path / "missing.db")
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    assert resp.json() == []


def test_corrupt_db_returns_empty_list(tmp_path, monkeypatch, client):
    """A non-sqlite file at TRADE_JOURNAL_DB shouldn't 500 the API."""
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite file")
    monkeypatch.setattr(trades_closed_router, "_DB_PATH", bad)
    resp = client.get("/api/bot/trades/closed")
    assert resp.status_code == 200
    assert resp.json() == []
