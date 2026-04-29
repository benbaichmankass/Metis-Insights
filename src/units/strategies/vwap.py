"""VWAP strategy — units layer (S-008 PR #121, S-012 PR C5 self-contained).

Pure VWAP mean-reversion signal builder + ``order_package`` adapter. Before
S-012 PR C5 the helpers (``compute_vwap``, ``build_vwap_signal``,
``ENTRY_STD_THRESHOLD``) lived in ``strategies/vwap_signal_builder.py``.
That module has been removed; everything now lives here so the
production strategy directory is exactly one path.

Public surface
--------------
- ``ENTRY_STD_THRESHOLD`` — module constant; std-dev threshold for entry.
- ``compute_vwap(df)`` — pure VWAP scalar from an OHLCV frame.
- ``build_vwap_signal(df, symbol, qty)`` — pipeline-shape signal dict
  (``{symbol, side, qty, meta}``); never raises on bad data — returns
  side="none" with a logged reason. Used by the runtime pipeline.
- ``order_package(cfg, candles_df)`` — units-layer adapter conforming to
  the contract in ``src/units/strategies/_base.py``. Used by the
  Coordinator dispatch path.

Strategies are pure signal generators (see ``_base.py`` docstring): no
``dry_run`` flag, no execution awareness.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import (
    derive_sl_tp,
    last_close,
    require_candles,
    side_to_direction,
)

logger = logging.getLogger(__name__)

# Minimum candles needed for a meaningful VWAP reading.
MIN_CANDLES = 2

# Minimum standard-deviation bands required to call a reversion signal.
# Price must deviate at least this many std-devs from VWAP to be actionable.
ENTRY_STD_THRESHOLD = 1.0

# Internal alias retained for backwards-compatible imports.
_ENTRY_STD_THRESHOLD = ENTRY_STD_THRESHOLD


def compute_vwap(candles_df: pd.DataFrame) -> float:
    """Return VWAP for the supplied candle window.

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
    return float((typical_price * candles_df["volume"]).sum() / total_volume)


def _no_trade(symbol: str, reason: str) -> Dict[str, Any]:
    """Standardised no-trade signal for invalid or insufficient candle data."""
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
    """Compute a VWAP mean-reversion signal from OHLCV candle data.

    Returns a signal dict with keys: symbol, side, qty, meta.

    * side='buy'  when price is at least ENTRY_STD_THRESHOLD std-devs *below*
                  VWAP (mean-reversion long).
    * side='sell' when price is at least ENTRY_STD_THRESHOLD std-devs *above*
                  VWAP (mean-reversion short).
    * side='none' / qty=0 when price is near VWAP or data is insufficient.

    Invalid candle data (empty, missing volume column, zero/negative total
    volume) returns a no-trade signal instead of raising — the tick completes
    safely.
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if "volume" not in candles_df.columns:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if candles_df["volume"].sum() <= 0:
        return _no_trade(symbol, "VWAP skipped: total candle volume is zero or negative")

    vwap = compute_vwap(candles_df)
    current_price = float(candles_df["close"].iloc[-1])

    typical_price = (
        candles_df["high"] + candles_df["low"] + candles_df["close"]
    ) / 3.0
    std_dev = float(typical_price.std())

    deviation = (current_price - vwap) / std_dev if std_dev > 0 else 0.0

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


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a VWAP OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml.
    candles_df : pd.DataFrame
        OHLCV frame.  Required — raises ValueError when absent.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles_df is absent or signal is non-actionable (side="none").
    """
    candles_df = require_candles(candles_df, "vwap")

    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"
    qty = float(cfg.get("max_qty") or cfg.get("MAX_QTY") or 1.0)

    signal = build_vwap_signal(candles_df, symbol=symbol, qty=qty)

    side = signal.get("side", "none")
    direction = side_to_direction(side)  # raises ValueError when side=="none"

    entry = last_close(candles_df)

    # Attempt VWAP-to-price TP; fall back to percentage-based.
    try:
        vwap = compute_vwap(candles_df)
        typical_price = (candles_df["high"] + candles_df["low"] + candles_df["close"]) / 3.0
        std_dev = float(typical_price.std())

        if direction == "long":
            tp = vwap
            risk = entry - tp
            sl = entry + risk if risk > 0 else entry * 0.98
        else:
            tp = vwap
            risk = tp - entry
            sl = entry - risk if risk > 0 else entry * 1.02

        # Confidence: deviation in std-dev units capped at 1.0
        if std_dev > 0:
            deviation = abs(entry - vwap) / std_dev
            confidence = min(deviation / ENTRY_STD_THRESHOLD, 1.0)
        else:
            confidence = 0.5

    except Exception:
        sl, tp = derive_sl_tp(entry, direction)
        confidence = 0.5

    meta = signal.get("meta") or {}
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": round(confidence, 4),
        "meta": {**meta, "signal": signal},
    }
