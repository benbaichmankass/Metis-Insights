"""Breakout confirmation strategy — units layer adapter (S-008 PR #121).

Wraps the ML breakout model (``src.strategies_manager.StrategyManager``) to
produce an ``OrderPackage``-compatible dict.

The breakout model only generates long (``"buy"``) signals; the strategy is
directionally biased upward.

Entry / SL / TP derivation
--------------------------
The breakout model does not emit price levels.  We use simple ATR-based
sizing when ATR is available in the candles, and fall back to ±2 % / 4 %
percentages otherwise.

* entry = last_close
* sl    = entry - ATR  (or entry * 0.98)
* tp    = entry + 2*ATR  (or entry * 1.04)

Confidence is mapped from the model's ``signal`` field:

  "STRONG_CONFIRM" → 0.9
  "CONFIRM"        → 0.7
  anything else    → 0.0  (not actionable → ValueError)
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

_CONFIDENCE_MAP = {
    "STRONG_CONFIRM": 0.9,
    "CONFIRM": 0.7,
}


def _atr(candles_df: pd.DataFrame, period: int = 14) -> Optional[float]:
    try:
        high = candles_df["high"]
        low = candles_df["low"]
        close = candles_df["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = float(tr.rolling(period).mean().iloc[-1])
        return atr if atr > 0 else None
    except Exception:
        return None


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a breakout-confirmation OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml.
    candles_df : pd.DataFrame
        OHLCV frame with at least 14 rows for ATR.  Required.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles_df is absent or model signal is not CONFIRM/STRONG_CONFIRM.
    """
    candles_df = require_candles(candles_df, "breakout_confirmation")

    from src.strategies_manager import StrategyManager

    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"
    candles_df = candles_df.copy()
    if "datetime_utc" not in candles_df.columns:
        candles_df["datetime_utc"] = pd.to_datetime(
            candles_df.index if hasattr(candles_df.index, "freq") or candles_df.index.dtype != "int64"
            else range(len(candles_df)),
            utc=True,
        )

    manager = StrategyManager()
    model_signal = manager.get_signal("breakout_confirmation", candles_df)

    signal_label = model_signal.get("signal", "")
    confidence = _CONFIDENCE_MAP.get(signal_label, 0.0)

    if confidence == 0.0:
        raise ValueError(
            f"Breakout model returned non-actionable signal '{signal_label}' for {symbol}; "
            "no trade."
        )

    # Breakout is always a long signal.
    direction = "long"
    entry = last_close(candles_df)

    atr = _atr(candles_df)
    if atr:
        sl = entry - atr
        tp = entry + 2 * atr
    else:
        sl, tp = derive_sl_tp(entry, direction)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "confidence": confidence,
        "meta": {"model_signal": model_signal, "atr": atr},
    }
