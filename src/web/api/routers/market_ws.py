"""Read-only market-data WebSocket — ``/ws/market`` (P2b).

Streams live **candle** + **open-position/uPnL** snapshots to a connected
client on a short server loop, reusing the *same* fetchers the REST routes use
(``candles._fetch_candles`` + ``dashboard.get_positions``). It exists so the
Android app can get live futures candles + live positions/uPnL (and crypto)
over **one persistent connection** instead of hammering the REST endpoints on a
timer — the poll moves off the phone and onto the server.

**Read-only.** No order path, no writes, no new fetch cadence beyond what the
REST routes already do — a symbol whose candles are unavailable just yields an
empty frame; a disconnect ends the loop. Runs under the single uvicorn worker,
so a per-connection loop is the whole story (no shared broadcast task needed).

Query params: ``symbols`` (CSV, required for candles), ``interval`` (default
``5m``), ``limit`` (candle count, default 200), ``include_paper`` (default
true). Messages pushed as JSON:

- ``{"type":"hello","symbols":[...],"interval":"5m"}`` once on connect
- ``{"type":"candles","symbol":"BTCUSDT","interval":"5m","candles":[{time,open,high,low,close,volume}, ...]}``
- ``{"type":"positions","positions":[<Position rows, same shape as /api/bot/positions>]}``

**Cadence** is IB-pacing-aware: the whole loop ticks every ``_POLL_SECONDS``
(positions pushed every tick — a cheap DB read → live uPnL), but candles for a
given symbol are only re-fetched once its per-asset TTL elapses — crypto every
``_CRYPTO_CANDLE_TTL`` (Bybit REST is cheap), futures/IBKR every
``_OTHER_CANDLE_TTL`` (a 2s IBKR ``reqHistoricalData`` loop would trip IB's
historical-data pacing limits and could wedge the gateway — the one thing we
must never do). Candle fetches share ``candles._FETCH_EXECUTOR`` (the single
worker that serialises IB access), so the WS never races the REST candles route
on the IB connection.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.web.api.routers.candles import _FETCH_EXECUTOR, _fetch_candles
from src.web.api.routers.dashboard import get_positions

router = APIRouter(tags=["market-ws"])

_POLL_SECONDS = 2.0
_CRYPTO_CANDLE_TTL = 2.0
_OTHER_CANDLE_TTL = 8.0  # futures/IBKR — respect reqHistoricalData pacing limits
_DEFAULT_LIMIT = 200
_MAX_SYMBOLS = 12


def _candle_ttl(symbol: str) -> float:
    """Crypto (Bybit) can refresh fast; IBKR futures must not (pacing)."""
    return _CRYPTO_CANDLE_TTL if symbol.upper().endswith("USDT") else _OTHER_CANDLE_TTL


@router.websocket("/ws/market")
async def market_ws(ws: WebSocket) -> None:
    await ws.accept()
    qp = ws.query_params
    symbols = [
        s.strip().upper()
        for s in (qp.get("symbols") or "").split(",")
        if s.strip()
    ][:_MAX_SYMBOLS]
    interval = (qp.get("interval") or "5m").strip()
    try:
        limit = max(10, min(1000, int(qp.get("limit") or _DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    include_paper = (qp.get("include_paper") or "true").lower() not in ("0", "false", "no")

    loop = asyncio.get_running_loop()
    last_fetch: dict[str, float] = {}

    await ws.send_json({"type": "hello", "symbols": symbols, "interval": interval})

    try:
        while True:
            now = time.monotonic()

            # ── candles per symbol (refreshed on a per-asset TTL) ──
            for sym in symbols:
                if now - last_fetch.get(sym, 0.0) < _candle_ttl(sym):
                    continue
                last_fetch[sym] = now
                try:
                    rows = await loop.run_in_executor(
                        _FETCH_EXECUTOR, _fetch_candles, sym, interval, limit
                    )
                except Exception:  # allow-silent: one symbol's fetch failing must not kill the live socket — push an empty frame (same graceful-degradation as the REST /candles route) and keep streaming the others
                    rows = []
                await ws.send_json({
                    "type": "candles",
                    "symbol": sym,
                    "interval": interval,
                    "candles": rows or [],
                })

            # ── positions / uPnL (every tick — cheap DB read) ──
            try:
                positions = await get_positions(include_paper=include_paper, include_demo=False)
            except Exception:  # allow-silent: a positions read hiccup must not kill the live socket — push an empty positions frame this tick and retry next loop
                positions = []
            if symbols:
                positions = [
                    p for p in positions
                    if str(p.get("symbol", "")).upper() in symbols
                ]
            await ws.send_json({"type": "positions", "positions": positions})

            await asyncio.sleep(_POLL_SECONDS)
    except WebSocketDisconnect:
        return
    except Exception:  # allow-silent: the client is gone (send on a closed socket etc.) — end the per-connection loop cleanly, nothing to report
        return
