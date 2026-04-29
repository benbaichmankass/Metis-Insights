"""ICT strategy — units layer adapter (S-008 PR #121).

Wraps ``src.runtime.strategies.ict.build_ict_signal`` to produce an
``OrderPackage``-compatible dict.

Entry / SL / TP derivation
--------------------------
ICT zones (FVG and OB) carry price levels.  We use them when present:

* Bullish FVG: entry = top of gap (``high``), sl = bottom of gap (``low``),
  tp = entry + 2 * (entry - sl).
* Bearish FVG: entry = bottom of gap (``low``), sl = top (``high``),
  tp = entry - 2 * (sl - entry).
* Order Block: same logic using ``ob_high`` / ``ob_low``.
* Fallback (no zone levels): entry = last_close, ±2 % SL, 2:1 TP.

Confidence is 0.8 when signal fires (FVG/OB aligned with HTF trend +
kill-zone gate), 0.0 otherwise.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import (
    derive_sl_tp,
    last_close,
    require_candles,
    side_to_direction,
)


def _zone_levels(trigger: Dict[str, Any], direction: str):
    """Extract (entry, sl, tp) from a FVG or OB trigger dict, or return None."""
    if not trigger:
        return None
    zone = trigger.get("zone") or {}
    kind = trigger.get("kind") or ""

    if kind == "fvg":
        high = zone.get("high") or zone.get("fvg_high")
        low = zone.get("low") or zone.get("fvg_low")
    elif kind == "ob":
        high = zone.get("ob_high") or zone.get("high")
        low = zone.get("ob_low") or zone.get("low")
    else:
        return None

    try:
        high = float(high)
        low = float(low)
    except (TypeError, ValueError):
        return None

    if high <= 0 or low <= 0 or high <= low:
        return None

    if direction == "long":
        entry = high
        sl = low
        risk = entry - sl
        tp = entry + 2 * risk
    else:
        entry = low
        sl = high
        risk = sl - entry
        tp = entry - 2 * risk

    return round(entry, 8), round(sl, 8), round(tp, 8)


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build an ICT OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml (passed through by Coordinator).
    candles_df : pd.DataFrame
        OHLCV frame with DatetimeIndex.  Required — raises ValueError when absent.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles_df is absent, or the ICT signal is non-actionable
        (side="none": neutral trend, inactive kill-zone, or no aligned zone).
    """
    candles_df = require_candles(candles_df, "ict")

    from src.runtime.strategies.ict import build_ict_signal

    settings = dict(cfg)
    symbol = settings.get("symbol") or settings.get("SYMBOL") or "BTCUSDT"
    settings.setdefault("SYMBOL", symbol)

    signal = build_ict_signal(candles_df, settings=settings)

    side = signal.get("side", "none")
    direction = side_to_direction(side)  # raises ValueError when side=="none"

    meta = signal.get("meta") or {}
    trigger = meta.get("trigger_zone") and {"kind": meta.get("trigger_kind"), "zone": meta.get("trigger_zone")}

    levels = _zone_levels(trigger, direction) if trigger else None
    if levels:
        entry, sl, tp = levels
    else:
        entry = last_close(candles_df)
        sl, tp = derive_sl_tp(entry, direction)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": 0.8,
        "meta": {**meta, "signal": signal},
    }
