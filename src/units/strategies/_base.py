"""Shared helpers for the src/units/strategies/ adapters (S-008 PR #121 / S-011 PR #2).

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

**Strategies are pure signal generators.**
They have no ``dry_run`` flag and no knowledge of whether orders will be
executed or simulated.  The dry/live execution decision lives entirely in
the Accounts layer (``TradingAccount.dry_run``).  This separation ensures
signal logic is never coupled to execution mode.

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


# ---------------------------------------------------------------------------
# monitor() contract — S-030 PR2 (architecture-audit-2026-05-02 P1-4)
# ---------------------------------------------------------------------------


def monitor_breakeven_sl(
    open_pkg: Dict[str, Any],
    candles_df: pd.DataFrame,
    *,
    one_r_threshold: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """Standard "trail SL to break-even after 1R" monitor rule.

    Both vwap and turtle_soup delegate here for the v1 monitor logic.
    Each strategy can layer its own logic on top by checking other
    conditions first; only when none match should it fall through to
    this helper.

    Algorithm:
      - 1R = abs(entry - sl).
      - For longs: when current_price ≥ entry + (one_r_threshold × 1R)
        AND current sl < entry, return ``{"sl": entry}`` so the caller
        moves the stop to break-even.
      - Symmetric for shorts.
      - Otherwise return ``None`` (no change).

    The caller (S-030 PR3 monitor loop) consumes the dict and writes
    it back via ``Database.update_order_package``.

    Returns
    -------
    None | dict
        ``None`` for "no change". The dict shape is one of:
          * ``{"sl": float}`` — move stop-loss to this level.
          * ``{"tp": float}`` — move take-profit to this level.
          * ``{"action": "close", "reason": str}`` — close now.
    """
    if candles_df is None or len(candles_df) == 0:
        return None
    try:
        current_price = float(candles_df["close"].iloc[-1])
    except (KeyError, IndexError, ValueError):
        return None
    try:
        entry = float(open_pkg["entry"])
        sl = float(open_pkg["sl"])
        direction = str(open_pkg["direction"]).lower()
    except (KeyError, TypeError, ValueError):
        return None

    one_r = abs(entry - sl)
    if one_r <= 0:
        return None

    if direction == "long":
        if current_price >= entry + (one_r_threshold * one_r) and sl < entry:
            return {"sl": entry}
        return None
    if direction == "short":
        if current_price <= entry - (one_r_threshold * one_r) and sl > entry:
            return {"sl": entry}
        return None
    return None
