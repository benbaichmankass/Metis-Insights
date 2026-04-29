"""Shared helpers for the src/units/strategies/ adapters (S-008 PR #121).

Each strategy module exposes a single public function::

    def order_package(cfg: dict, candles_df=None) -> dict

The returned dict contains the fields required to build an
``OrderPackage`` in the Coordinator (all except ``strategy``, which the
Coordinator inserts itself):

    symbol     : str
    direction  : "long" | "short"
    entry      : float   (estimated entry price)
    sl         : float   (stop-loss price)
    tp         : float   (primary take-profit price)
    confidence : float   (0.0 – 1.0)
    meta       : dict    (raw signal data for logging)

Raises
------
ValueError
    When the signal is non-actionable (side="none") or candles are absent
    and cannot be substituted.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def side_to_direction(side: str) -> str:
    """Convert pipeline 'buy'/'sell' to 'long'/'short'. Raises on 'none'."""
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    raise ValueError(f"Non-actionable signal side: '{side}'")


def last_close(candles_df: pd.DataFrame) -> float:
    return float(candles_df["close"].iloc[-1])


def derive_sl_tp(
    entry: float,
    direction: str,
    sl_pct: float = 0.02,
    reward_ratio: float = 2.0,
) -> tuple[float, float]:
    """Simple percentage-based SL/TP when zone levels are not available.

    Returns (sl, tp).
    """
    if direction == "long":
        sl = entry * (1 - sl_pct)
        tp = entry * (1 + sl_pct * reward_ratio)
    else:
        sl = entry * (1 + sl_pct)
        tp = entry * (1 - sl_pct * reward_ratio)
    return round(sl, 8), round(tp, 8)


def require_candles(candles_df: Optional[pd.DataFrame], name: str) -> pd.DataFrame:
    """Raise ValueError when candles are absent or empty."""
    if candles_df is None or (hasattr(candles_df, "empty") and candles_df.empty):
        raise ValueError(
            f"Strategy '{name}': candles_df is required but was not provided. "
            "Pass a DataFrame with OHLCV columns."
        )
    return candles_df
