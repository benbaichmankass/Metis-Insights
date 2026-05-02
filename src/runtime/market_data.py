"""Market-data fetcher — keeps OHLCV out of the pipeline signal builders.

S-033 (architecture-audit-2026-05-02 § P1-8). Pre-PR
``turtle_soup_signal_builder`` and ``vwap_signal_builder`` in
``src/runtime/pipeline.py`` instantiated a ``BybitConnector`` /
``BinanceConnector`` and called ``get_ohlcv()`` inline. That coupled
**signal generation** (a strategy concern) to **exchange reachability**
(an infrastructure concern). Per CLAUDE.md § Architecture rules § 2 the
strategy units should be pure — given candles + config, they emit a
package; they shouldn't decide where the candles come from.

This module owns the fetch + the DataFrame normalisation so the
builders can call a single helper and stay focused on signal logic.
The two existing builders both did the same shape of work:

  1. Pick the connector based on ``settings["EXCHANGE"]``.
  2. Honour ``BYBIT_TESTNET`` / ``BINANCE_TESTNET``.
  3. Call ``get_ohlcv(symbol, timeframe, limit=N)``.
  4. Convert list-of-rows → ``pandas.DataFrame`` with the canonical
     column ordering.
  5. Cast OHLCV columns to numeric.

``fetch_candles`` does (1)–(5) in one place. The builders accept the
returned DataFrame (or ``None`` when the fetch failed) and decide how
to react; this module never inspects strategy state.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


_OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def _build_exchange_client(settings: Dict[str, Any]):
    """Return a connector instance for the configured exchange.

    Logic preserved verbatim from the legacy
    ``pipeline._build_killzone_exchange`` so the runtime behaviour is
    bit-for-bit identical post-PR.
    """
    exchange_name = str(
        settings.get("EXCHANGE", settings.get("exchange", "bybit"))
    ).strip().lower()
    bybit_testnet_raw = str(
        os.environ.get("BYBIT_TESTNET", "true")
    ).strip().lower()
    testnet = bybit_testnet_raw not in {"false", "0", "no"}

    if exchange_name == "binance":
        from src.exchange.binance_connector import BinanceConnector
        return BinanceConnector(
            api_key=settings.get("BINANCE_API_KEY"),
            api_secret=settings.get("BINANCE_API_SECRET"),
            testnet=testnet,
        )

    if exchange_name == "bybit":
        from src.exchange.bybit_connector import BybitConnector
        return BybitConnector(
            api_key=settings.get("BYBIT_API_KEY"),
            api_secret=settings.get("BYBIT_API_SECRET"),
            testnet=testnet,
        )

    raise ValueError(f"Unsupported EXCHANGE value: {exchange_name}")


def fetch_candles(
    symbol: str,
    timeframe: str,
    *,
    settings: Optional[Dict[str, Any]] = None,
    limit: int,
    exchange_client: Any = None,
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV candles for *symbol* / *timeframe* and return a DataFrame.

    Parameters
    ----------
    symbol : str
        Exchange-native symbol (e.g. ``"BTCUSDT"`` for Bybit).
    timeframe : str
        Exchange-native timeframe (e.g. ``"5m"``, ``"15m"``).
    settings : dict, optional
        Pipeline settings — used to pick the connector + read API
        creds when ``exchange_client`` is not provided. Fields
        consulted: ``EXCHANGE`` / ``exchange`` (default bybit),
        ``BYBIT_API_KEY``, ``BYBIT_API_SECRET``, ``BINANCE_API_KEY``,
        ``BINANCE_API_SECRET``.
    limit : int
        Number of candles to fetch.
    exchange_client : object, optional
        Pre-built connector. When provided, ``fetch_candles`` skips
        the construction step and uses this client directly. The
        pipeline builders use this to keep the existing
        ``monkeypatch.setattr(pipeline, "_build_killzone_exchange",
        …)`` test fixtures working — the builder constructs the
        client (through the shim the tests patch) and passes it in.

    Returns
    -------
    pandas.DataFrame | None
        DataFrame with columns
        ``["timestamp", "open", "high", "low", "close", "volume"]``
        (numeric where applicable), or ``None`` when the exchange
        returned no rows. Never raises; on a configuration or network
        error logs and returns ``None`` so the caller can decide how
        to react. The legacy builders raised ``RuntimeError`` on a
        missing fetch — they keep that behaviour by checking the
        return value and raising themselves.
    """
    if exchange_client is None:
        try:
            exchange_client = _build_exchange_client(settings or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_candles: connector init failed (%s)", exc)
            return None

    try:
        candles_raw = exchange_client.get_ohlcv(symbol, timeframe, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch_candles: get_ohlcv failed for symbol=%s timeframe=%s (%s)",
            symbol, timeframe, exc,
        )
        return None

    if candles_raw is None:
        return None
    if hasattr(candles_raw, "__len__") and len(candles_raw) == 0:
        return None

    if isinstance(candles_raw, pd.DataFrame):
        candles_df = candles_raw.copy()
    else:
        candles_df = pd.DataFrame(candles_raw, columns=list(_OHLCV_COLUMNS))

    for col in ("open", "high", "low", "close", "volume"):
        if col in candles_df.columns:
            candles_df[col] = pd.to_numeric(candles_df[col], errors="coerce")

    return candles_df
