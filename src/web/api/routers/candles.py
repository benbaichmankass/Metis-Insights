"""Tier-1 read: GET /api/bot/candles — OHLCV from the SAME exchange the
strategies trade the symbol on.

Routes per instrument via ``src.runtime.market_data.connector_for_symbol``
(BTCUSDT → Bybit, MES → Interactive Brokers, per ``config/instruments.yaml``)
and fetches through the canonical ``fetch_candles`` the signal builders use —
so the dashboard's chart shows exactly the candles the bot sees, instead of a
separate (and from Streamlit Cloud, flaky) Yahoo Finance feed.

Best-effort: any connector/fetch failure (e.g. MES when the IB account has no
``ib_port``) returns an empty ``candles`` list so the dashboard falls back to
its yfinance source. Results are cached briefly so repeated dashboard polls
don't hammer the exchange. Tier 1 — no auth, no secrets in the response.

Env dependency: the Bybit path honours ``BYBIT_TESTNET`` (default *true* in the
market-data builder). The ``ict-web-api`` unit must carry ``BYBIT_TESTNET=false``
(+ the Bybit creds) to return mainnet candles — otherwise the fetch is empty and
the dashboard falls back to yfinance.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
# Native exchange timeframes the chart offers. Kept in sync with the
# dashboard's interval selector.
_ALLOWED_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}

# Short in-process cache so a 10 s dashboard poll loop doesn't issue a fresh
# exchange call every tick. Keyed by (symbol, interval, limit).
_CACHE: Dict[tuple, tuple[float, List[dict]]] = {}
_CACHE_TTL_S = 10.0

# Reuse one exchange connector per symbol instead of building a fresh ccxt
# client (which loads markets) on every uncached request. With multiple polling
# dashboards (production + preview) hitting several intervals, rebuilding the
# client each call was needless load on the bot. Evicted on an empty fetch so a
# dead connector self-heals on the next request.
_CONNECTOR_CACHE: Dict[str, Any] = {}


def _connector(symbol: str, settings: Dict[str, Any]):
    client = _CONNECTOR_CACHE.get(symbol)
    if client is None:
        from src.runtime.market_data import connector_for_symbol
        client = connector_for_symbol(symbol, settings)  # may raise (e.g. IB w/o ib_port)
        _CONNECTOR_CACHE[symbol] = client
    return client


def _settings() -> Dict[str, Any]:
    """Connector settings from the process env (same vars the trader uses)."""
    return {
        "EXCHANGE": os.environ.get("EXCHANGE", "bybit"),
        "BYBIT_API_KEY": os.environ.get("BYBIT_API_KEY"),
        "BYBIT_API_SECRET": os.environ.get("BYBIT_API_SECRET"),
        "BINANCE_API_KEY": os.environ.get("BINANCE_API_KEY"),
        "BINANCE_API_SECRET": os.environ.get("BINANCE_API_SECRET"),
    }


def _epoch_s(ts: Any) -> Optional[int]:
    """Normalise a candle timestamp (datetime / ISO str / epoch ms|s) → epoch seconds."""
    try:
        t = pd.Timestamp(ts)
    except (ValueError, TypeError):
        return None
    if pd.isna(t):
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return int(t.timestamp())


def _fetch_candles(symbol: str, interval: str, limit: int) -> List[dict]:
    """Fetch via the canonical market-data path; return Lightweight-Charts rows."""
    from src.runtime.market_data import fetch_candles
    settings = _settings()
    client = _connector(symbol, settings)  # cached per symbol; may raise (IB w/o ib_port)
    df = fetch_candles(symbol, interval, settings=settings, limit=limit,
                        exchange_client=client)
    if df is None or len(df) == 0:
        # Drop the cached connector so a stale/broken client rebuilds next call.
        _CONNECTOR_CACHE.pop(symbol, None)
        return []
    out: List[dict] = []
    for _, row in df.iterrows():
        t = _epoch_s(row.get("timestamp"))
        if t is None:
            continue
        try:
            out.append({
                "time": t,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0.0),
            })
        except (TypeError, ValueError, KeyError):
            continue
    out.sort(key=lambda c: c["time"])
    return out


@router.get("/candles")
async def get_candles(
    symbol: str = Query("BTCUSDT", max_length=20),
    interval: str = Query("5m", max_length=8),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Dict[str, Any]:
    """OHLCV for *symbol* / *interval* from the bot's own exchange feed.

    Wire shape: ``{symbol, interval, source, candles: [{time, open, high, low,
    close, volume}], count, error}``. ``time`` is epoch seconds (Lightweight
    Charts native). ``candles`` is empty (with ``error`` set) on any failure so
    the dashboard can fall back to its yfinance feed without a 5xx.
    """
    if interval not in _ALLOWED_INTERVALS:
        return {"symbol": symbol, "interval": interval, "source": "bot-exchange",
                "candles": [], "count": 0,
                "error": f"unsupported interval (allowed: {sorted(_ALLOWED_INTERVALS)})"}

    key = (symbol, interval, limit)
    now = time.time()
    cached = _CACHE.get(key)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_S:
        candles = cached[1]
    else:
        try:
            candles = _fetch_candles(symbol, interval, limit)
        except Exception as exc:  # allow-silent: best-effort market-data read; logs + empty so the dashboard falls back to yfinance
            logger.warning("candles: fetch failed for %s/%s: %s", symbol, interval, exc)
            candles = []
        _CACHE[key] = (now, candles)

    return {
        "symbol": symbol,
        "interval": interval,
        "source": "bot-exchange",
        "candles": candles,
        "count": len(candles),
        "error": None if candles else "no_data",
    }
