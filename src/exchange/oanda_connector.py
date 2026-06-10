"""OANDA v20 market-data connector — data-only (M15 Phase 1).

Read path for FX/metals candles via the OANDA v20 REST API
(practice or live host). Mirrors the
``get_ohlcv(symbol, timeframe, limit) -> DataFrame | None`` contract of
``BybitConnector`` so ``src.runtime.market_data.fetch_candles`` can use
it unchanged. **No order path** — execution wiring is M15 Phase 2 via
the ``new-broker`` checklist.

Credentials: ``OANDA_API_TOKEN`` (personal access token from the
account portal; a free practice-account token works). ``OANDA_ENV``
selects the host: ``practice`` (default) → api-fxpractice.oanda.com,
``live`` → api-fxtrade.oanda.com.

Symbol mapping: accepts both OANDA-native (``EUR_USD``) and compact
(``EURUSD`` / ``XAUUSD``) forms — a 6-char compact symbol is split 3+3.

Caveat for consumers: the ``volume`` column is OANDA's **tick count**,
not traded volume (FX has no consolidated volume). The current live
strategy roster is price-only, so this is informational.
"""
from __future__ import annotations

import logging
import os

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_GRANULARITY_MAP = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "2h": "H2",
    "4h": "H4",
    "1d": "D",
}

_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


def _to_instrument(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if "_" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}_{s[3:]}"
    return s


class OandaMarketData:
    """Read-only OHLCV fetcher for FX/metals via OANDA v20."""

    def __init__(self, api_token=None, env=None, base_url=None, timeout: float = 10.0):
        self.api_token = api_token or os.environ.get("OANDA_API_TOKEN", "")
        env_name = (env or os.environ.get("OANDA_ENV", "practice")).strip().lower()
        self.base_url = (base_url or _HOSTS.get(env_name, _HOSTS["practice"])).rstrip("/")
        self.timeout = timeout

    def get_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 100):
        """Return the most recent *limit* complete candles as a DataFrame.

        Columns: ``timestamp, open, high, low, close, volume`` (mid
        prices; tz-aware UTC timestamps; volume = tick count). Returns
        ``None`` on any error or empty response.
        """
        gran = _GRANULARITY_MAP.get(str(timeframe).lower())
        if gran is None:
            logger.warning("oanda: unsupported timeframe %r", timeframe)
            return None
        instrument = _to_instrument(symbol)
        try:
            resp = requests.get(
                f"{self.base_url}/v3/instruments/{instrument}/candles",
                params={"granularity": gran, "count": int(limit), "price": "M"},
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            candles = (resp.json() or {}).get("candles") or []
            rows = []
            for c in candles:
                if not c.get("complete", True):
                    continue
                mid = c.get("mid") or {}
                rows.append(
                    {
                        "timestamp": c.get("time"),
                        "open": mid.get("o"),
                        "high": mid.get("h"),
                        "low": mid.get("l"),
                        "close": mid.get("c"),
                        "volume": c.get("volume"),
                    }
                )
            if not rows:
                return None
            df = pd.DataFrame(rows)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return (
                df.dropna(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("oanda: get_ohlcv failed for %s %s (%s)", symbol, timeframe, exc)
            return None

    def get_price(self, symbol: str):
        """Latest mid close from the most recent M1 candle, or ``None``."""
        df = self.get_ohlcv(symbol, "1m", limit=2)
        if df is None or df.empty:
            return None
        try:
            return float(df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            return None
