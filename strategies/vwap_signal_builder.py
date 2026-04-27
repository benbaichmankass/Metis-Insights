"""
VWAP signal builder — pure computation, no exchange calls, no secrets.

Accepts a standard OHLCV DataFrame and returns a normalised signal dict
that the runtime pipeline can act on.  Designed to be offline-safe and
fully testable without live market data.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger(__name__)

# Minimum candles needed for a meaningful VWAP reading.
MIN_CANDLES = 2

# Minimum standard-deviation bands required to call a reversion signal.
# Price must deviate at least this many std-devs from VWAP to be actionable.
ENTRY_STD_THRESHOLD = 1.0


def compute_vwap(candles_df: pd.DataFrame) -> float:
    """
    Return VWAP for the supplied candle window.

    Raises ValueError with a clear, non-secret message if the data is
    insufficient or degenerate (zero volume).
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        raise ValueError(
            "VWAP computation requires a non-empty DataFrame. "
            "Check that market data was fetched correctly."
        )

    required = {"high", "low", "close", "volume"}
    missing = required - set(candles_df.columns)
    if missing:
        raise ValueError(
            f"VWAP computation: candle DataFrame is missing columns: {sorted(missing)}. "
            "Expected columns: high, low, close, volume."
        )

    if len(candles_df) < MIN_CANDLES:
        raise ValueError(
            f"VWAP requires at least {MIN_CANDLES} candles, got {len(candles_df)}. "
            "Ensure the timeframe and lookback window are configured correctly."
        )

    total_volume = candles_df["volume"].sum()
    if total_volume <= 0:
        raise ValueError(
            "VWAP cannot be computed: total volume across all candles is zero or negative. "
            "Check that the candle data contains valid volume information."
        )

    typical_price = (
        candles_df["high"] + candles_df["low"] + candles_df["close"]
    ) / 3.0
    vwap = float((typical_price * candles_df["volume"]).sum() / total_volume)
    return vwap


def _no_trade(symbol: str, reason: str) -> Dict[str, Any]:
    """Return a standardised no-trade signal for invalid or insufficient candle data."""
    logger.warning("VWAP no-trade for %s: %s", symbol, reason)
    return {
        "symbol": symbol,
        "side": "none",
        "qty": 0.0,
        "meta": {
            "strategy_name": "vwap",
            "reason": reason,
        },
    }


def build_vwap_signal(
    candles_df: pd.DataFrame,
    symbol: str,
    qty: float,
) -> Dict[str, Any]:
    """
    Compute a VWAP mean-reversion signal from OHLCV candle data.

    Returns a signal dict with keys: symbol, side, qty, meta.
    - side='buy'  when price is at least ENTRY_STD_THRESHOLD std-devs *below* VWAP
                  (mean-reversion long)
    - side='sell' when price is at least ENTRY_STD_THRESHOLD std-devs *above* VWAP
                  (mean-reversion short)
    - side='none' / qty=0 when price is near VWAP or data is insufficient for bands

    Invalid candle data (empty, missing volume column, zero/negative total volume)
    returns a no-trade signal instead of raising, so the tick completes safely.
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if "volume" not in candles_df.columns:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if candles_df["volume"].sum() <= 0:
        return _no_trade(symbol, "VWAP skipped: total candle volume is zero or negative")

    vwap = compute_vwap(candles_df)
    current_price = float(candles_df["close"].iloc[-1])

    # Compute typical-price std-dev for band width.
    typical_price = (
        candles_df["high"] + candles_df["low"] + candles_df["close"]
    ) / 3.0
    std_dev = float(typical_price.std())

    if std_dev > 0:
        deviation = (current_price - vwap) / std_dev
    else:
        deviation = 0.0

    if deviation <= -ENTRY_STD_THRESHOLD:
        side = "buy"
        reason = f"price {current_price:.4f} is {abs(deviation):.2f} std-devs below VWAP {vwap:.4f}"
    elif deviation >= ENTRY_STD_THRESHOLD:
        side = "sell"
        reason = f"price {current_price:.4f} is {deviation:.2f} std-devs above VWAP {vwap:.4f}"
    else:
        side = "none"
        qty = 0
        reason = f"price {current_price:.4f} within {ENTRY_STD_THRESHOLD} std-dev of VWAP {vwap:.4f} — no signal"

    logger.info(
        "VWAP signal: symbol=%s vwap=%.4f price=%.4f std=%.4f deviation=%.2f side=%s",
        symbol, vwap, current_price, std_dev, deviation, side,
    )

    return {
        "symbol": symbol,
        "side": side,
        "qty": float(qty) if side != "none" else 0.0,
        "meta": {
            "strategy_name": "vwap",
            "vwap": vwap,
            "current_price": current_price,
            "std_dev": std_dev,
            "deviation_std": deviation,
            "reason": reason,
        },
    }
