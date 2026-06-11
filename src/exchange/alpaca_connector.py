"""Alpaca market-data connector — data-only (M15 Phase 1).

Read path for US stocks/ETFs bars via Alpaca's Market Data REST API
(https://data.alpaca.markets). Deliberately mirrors the
``get_ohlcv(symbol, timeframe, limit) -> DataFrame | None`` contract of
``BybitConnector`` so ``src.runtime.market_data.fetch_candles`` can use
it unchanged. **No order path** — execution wiring is M15 Phase 2 and
goes through the ``new-broker`` checklist (account package, integrator,
executor branch), not this module.

Uses plain ``requests`` (no alpaca-py dependency). Credentials come
from ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY`` (free paper
account keys work for data). ``ALPACA_DATA_FEED`` selects the feed
(default ``iex`` — the free real-time feed; ``sip`` needs the paid
consolidated plan).
"""
from __future__ import annotations

import logging
import os

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_TIMEFRAME_MAP = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
    "2h": "2Hour",
    "4h": "4Hour",
    "1d": "1Day",
}


class AlpacaMarketData:
    """Read-only OHLCV fetcher for US stocks/ETFs via Alpaca Data v2."""

    def __init__(self, api_key=None, api_secret=None, base_url=None, feed=None,
                 timeout: float = 10.0):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        self.base_url = (base_url or os.environ.get(
            "ALPACA_DATA_URL", "https://data.alpaca.markets")).rstrip("/")
        self.feed = feed or os.environ.get("ALPACA_DATA_FEED", "iex")
        self.timeout = timeout

    def get_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 100):
        """Return the most recent *limit* bars as a canonical DataFrame.

        Columns: ``timestamp, open, high, low, close, volume`` —
        timestamps are tz-aware UTC. Returns ``None`` on any error or
        empty response (the ``fetch_candles`` contract).
        """
        tf = _TIMEFRAME_MAP.get(str(timeframe).lower())
        if tf is None:
            logger.warning("alpaca: unsupported timeframe %r", timeframe)
            return None
        try:
            resp = requests.get(
                f"{self.base_url}/v2/stocks/{symbol}/bars",
                params={
                    "timeframe": tf,
                    "limit": int(limit),
                    "adjustment": "split",
                    "feed": self.feed,
                    "sort": "desc",
                },
                headers={
                    "APCA-API-KEY-ID": self.api_key,
                    "APCA-API-SECRET-KEY": self.api_secret,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            bars = (resp.json() or {}).get("bars") or []
            if not bars:
                return None
            df = pd.DataFrame(
                [
                    {
                        "timestamp": b.get("t"),
                        "open": b.get("o"),
                        "high": b.get("h"),
                        "low": b.get("l"),
                        "close": b.get("c"),
                        "volume": b.get("v"),
                    }
                    for b in bars
                ]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            return (
                df.dropna(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alpaca: get_ohlcv failed for %s %s (%s)", symbol, timeframe, exc)
            return None

    def get_price(self, symbol: str):
        """Latest trade price, or ``None`` on error."""
        try:
            resp = requests.get(
                f"{self.base_url}/v2/stocks/{symbol}/trades/latest",
                params={"feed": self.feed},
                headers={
                    "APCA-API-KEY-ID": self.api_key,
                    "APCA-API-SECRET-KEY": self.api_secret,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            trade = (resp.json() or {}).get("trade") or {}
            price = trade.get("p")
            return float(price) if price is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("alpaca: get_price failed for %s (%s)", symbol, exc)
            return None
