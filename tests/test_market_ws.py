"""P2b — read-only market-data WebSocket ``/ws/market``.

Verifies the endpoint accepts a connection, streams the ``hello`` frame, then
pushes ``candles`` (one per subscribed symbol) + a ``positions`` frame, and
degrades gracefully when the underlying fetchers return nothing (no exchange
creds / empty DB in CI). No order path — a pure read surface.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.api.main import app


def test_ws_market_streams_hello_candles_positions():
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/market?symbols=BTCUSDT,MES&interval=5m&limit=50"
        ) as ws:
            frames = [ws.receive_json() for _ in range(5)]

    # First frame is always the hello, echoing the subscription.
    assert frames[0]["type"] == "hello"
    assert frames[0]["symbols"] == ["BTCUSDT", "MES"]
    assert frames[0]["interval"] == "5m"

    types = {f["type"] for f in frames}
    assert "candles" in types, "expected at least one candle frame"
    assert "positions" in types, "expected a positions frame"

    for f in frames:
        if f["type"] == "candles":
            assert f["symbol"] in ("BTCUSDT", "MES")
            assert f["interval"] == "5m"
            assert isinstance(f["candles"], list)  # possibly empty (no creds in CI)
        if f["type"] == "positions":
            assert isinstance(f["positions"], list)


def test_ws_market_empty_symbols_still_streams_positions():
    """No symbols → no candle frames, but the positions stream still runs."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["symbols"] == []
            nxt = ws.receive_json()
            assert nxt["type"] == "positions"
            assert isinstance(nxt["positions"], list)
