"""VWAP strategy — units layer adapter (S-008 PR #121).

Wraps ``strategies.vwap_signal_builder.build_vwap_signal`` to produce an
``OrderPackage``-compatible dict.

Entry / SL / TP derivation
--------------------------
VWAP mean-reversion: the target is always the VWAP level itself.

* Long (price below VWAP): entry = last_close, tp = vwap, sl = entry - (tp - entry).
* Short (price above VWAP): entry = last_close, tp = vwap, sl = entry + (entry - tp).
* Fallback when std_dev is zero: ±2 % SL, 2:1 TP.

Confidence is ``min(|deviation| / ENTRY_STD_THRESHOLD, 1.0)`` where
deviation is the number of std-devs price is away from VWAP.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from src.units.strategies._base import (
    derive_sl_tp,
    last_close,
    require_candles,
    side_to_direction,
)

_ENTRY_STD_THRESHOLD = 1.0  # mirrors vwap_signal_builder.ENTRY_STD_THRESHOLD


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

    from strategies.vwap_signal_builder import build_vwap_signal, compute_vwap, ENTRY_STD_THRESHOLD

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
