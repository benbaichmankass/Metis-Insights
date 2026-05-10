"""S-067 follow-up #6 — GET /api/bot/pnl/exchange tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.runtime import exchange_fills_store as store
from src.web.api import main as api_main


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def fills_db(tmp_path: Path, monkeypatch) -> Path:
    db = tmp_path / "exchange_fills.sqlite"
    monkeypatch.setenv("EXCHANGE_FILLS_DB", str(db))
    # The router calls aggregate_* which call get_fills_db_path() —
    # the env var override is enough to redirect.
    return db


def _row(**overrides):
    base = {
        "exec_id": "e-1",
        "account_id": "bybit_2",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "price": 60000.0,
        "qty": 0.001,
        "fee": 0.012,
        "fee_currency": "USDT",
        "exec_time": "2026-05-09T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_returns_zero_aggregates_when_db_missing(client, fills_db):
    """No fills DB yet (puller has never run) → 200 with zero values."""
    assert not fills_db.exists()
    resp = client.get("/api/bot/pnl/exchange?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == {"fill_count": 0, "total_fees": 0.0,
                                "symbol_count": 0, "window_days": 7}
    assert body["by_symbol"] == []


def test_returns_per_symbol_aggregates(client, fills_db, monkeypatch):
    store.upsert_fills(
        [
            _row(exec_id="a", symbol="BTC/USDT:USDT",
                 exec_time="2026-05-09T12:00:00+00:00",
                 qty=0.001, price=60000.0, fee=0.10),
            _row(exec_id="b", symbol="BTC/USDT:USDT",
                 exec_time="2026-05-09T13:00:00+00:00",
                 qty=0.002, price=60500.0, fee=0.20),
            _row(exec_id="c", symbol="ETH/USDT:USDT",
                 exec_time="2026-05-09T14:00:00+00:00",
                 qty=0.5, price=3000.0, fee=0.30),
        ],
        path=fills_db,
    )
    # Pin "now" so the day-window filter is deterministic.
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "datetime", _frozen_datetime(fixed_now))

    resp = client.get("/api/bot/pnl/exchange?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["fill_count"] == 3
    assert body["summary"]["symbol_count"] == 2
    assert abs(body["summary"]["total_fees"] - 0.60) < 1e-9
    sym_map = {r["symbol"]: r for r in body["by_symbol"]}
    assert sym_map["BTC/USDT:USDT"]["fill_count"] == 2
    assert sym_map["ETH/USDT:USDT"]["fill_count"] == 1


def test_days_param_clamps_at_max(client, fills_db):
    resp = client.get("/api/bot/pnl/exchange?days=91")
    assert resp.status_code == 422  # Query(le=90)


def test_days_param_rejects_zero(client, fills_db):
    resp = client.get("/api/bot/pnl/exchange?days=0")
    assert resp.status_code == 422  # Query(ge=1)


def test_default_days_is_seven(client, fills_db):
    resp = client.get("/api/bot/pnl/exchange")
    assert resp.status_code == 200
    assert resp.json()["summary"]["window_days"] == 7


def _frozen_datetime(fixed: datetime):
    """Return a datetime-like class whose ``now`` returns ``fixed``."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    return _Frozen
