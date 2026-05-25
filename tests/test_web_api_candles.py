"""GET /api/bot/candles — OHLCV from the bot's own exchange feed.

Tier-1 read. The real fetch goes through src.runtime.market_data
(connector_for_symbol + fetch_candles); here we monkeypatch the router's
`_fetch_candles` so the test is exchange-independent and asserts the wire
shape, interval validation, the empty/fallback path, and the cache.
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import candles as candles_router


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_cache():
    candles_router._CACHE.clear()
    candles_router._CONNECTOR_CACHE.clear()
    yield
    candles_router._CACHE.clear()
    candles_router._CONNECTOR_CACHE.clear()


def test_happy_path_wire_shape(client, monkeypatch):
    rows = [
        {"time": 1_700_000_000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        {"time": 1_700_000_300, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 12.0},
    ]
    monkeypatch.setattr(candles_router, "_fetch_candles", lambda s, i, lim: rows)
    body = client.get("/api/bot/candles?symbol=BTCUSDT&interval=5m&limit=200").json()
    assert body["symbol"] == "BTCUSDT"
    assert body["interval"] == "5m"
    assert body["source"] == "bot-exchange"
    assert body["count"] == 2
    assert body["error"] is None
    assert body["candles"][0]["close"] == 1.5


def test_bad_interval_rejected(client, monkeypatch):
    monkeypatch.setattr(candles_router, "_fetch_candles",
                        lambda s, i, lim: (_ for _ in ()).throw(AssertionError("must not fetch")))
    body = client.get("/api/bot/candles?interval=7m").json()
    assert body["candles"] == [] and body["count"] == 0
    assert "unsupported interval" in body["error"]


def test_empty_fetch_sets_error(client, monkeypatch):
    monkeypatch.setattr(candles_router, "_fetch_candles", lambda s, i, lim: [])
    body = client.get("/api/bot/candles?symbol=MES&interval=15m").json()
    assert body["candles"] == [] and body["error"] == "no_data"


def test_fetch_exception_is_swallowed(client, monkeypatch):
    def boom(s, i, lim):
        raise RuntimeError("IB gateway unreachable")
    monkeypatch.setattr(candles_router, "_fetch_candles", boom)
    body = client.get("/api/bot/candles?symbol=MES&interval=5m").json()
    assert body["candles"] == [] and body["error"] == "no_data"


def test_cache_avoids_refetch(client, monkeypatch):
    calls = {"n": 0}

    def counting(s, i, lim):
        calls["n"] += 1
        return [{"time": 1_700_000_000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    monkeypatch.setattr(candles_router, "_fetch_candles", counting)
    client.get("/api/bot/candles?symbol=BTCUSDT&interval=5m&limit=50")
    client.get("/api/bot/candles?symbol=BTCUSDT&interval=5m&limit=50")
    assert calls["n"] == 1  # second call served from cache


def test_epoch_s_handles_formats():
    assert candles_router._epoch_s(pd.Timestamp("2026-05-25T00:00:00Z")) == 1779667200
    assert candles_router._epoch_s("2026-05-25T00:00:00Z") == 1779667200
    # naive timestamp is treated as UTC
    assert candles_router._epoch_s(pd.Timestamp("2026-05-25T00:00:00")) == 1779667200
    assert candles_router._epoch_s(None) is None
    assert candles_router._epoch_s("not-a-date") is None


def _fake_df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime([1_700_000_000], unit="s"),
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0],
    })


def test_connector_built_once_and_reused(monkeypatch):
    """_fetch_candles reuses the cached connector instead of rebuilding it."""
    import src.runtime.market_data as md
    builds = {"n": 0}

    def fake_connector(symbol, settings):
        builds["n"] += 1
        return object()

    monkeypatch.setattr(md, "connector_for_symbol", fake_connector)
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: _fake_df())

    candles_router._fetch_candles("BTCUSDT", "5m", 10)
    candles_router._fetch_candles("BTCUSDT", "1h", 10)
    candles_router._fetch_candles("BTCUSDT", "5m", 10)
    assert builds["n"] == 1  # one connector for the symbol across calls/intervals


def test_connector_evicted_on_empty_fetch(monkeypatch):
    """An empty fetch drops the cached connector so it rebuilds next call."""
    import src.runtime.market_data as md
    builds = {"n": 0}

    def fake_connector(symbol, settings):
        builds["n"] += 1
        return object()

    monkeypatch.setattr(md, "connector_for_symbol", fake_connector)
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: None)  # empty

    candles_router._fetch_candles("BTCUSDT", "5m", 10)
    candles_router._fetch_candles("BTCUSDT", "5m", 10)
    assert builds["n"] == 2  # rebuilt because the prior empty fetch evicted it
