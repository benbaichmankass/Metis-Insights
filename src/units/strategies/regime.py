"""Market-regime classifier — shared between live signal builder and
backtest evaluator.

A 'regime' is a coarse label (`<trend>/<volatility>`) that captures
the recent market character without overfitting to specific price
levels. Strategies use regime labels to look up adaptive policy
(see ``src/units/strategies/vwap_policy.py``).

Why a shared module
-------------------
The backtest at ``src/backtest/run_backtest_vwap.py`` classifies
historical windows; the live signal builder needs to classify the
*current* market in real time. Both must use the SAME logic, or the
backtest is no longer a faithful simulation of live behavior — a
classic source of strategy regressions where a strategy that
backtested well loses money live because the regime classifier
disagrees on what regime we're in.

Lifted from the original ``classify_window_regime`` in the backtest
(PR #1466) and made callable by both layers.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd


# Trend bucket thresholds in fractional % change over the lookback.
# Symmetric around 0; mirror values for down/up keep the policy
# symmetric by default.
_TREND_STRONG = 0.05   # > +/- 5%
_TREND_WEAK = 0.01    # > +/- 1% but < +/- 5%
# (between -1% and +1% → sideways)

# Volatility bucket thresholds in basis points of close per bar
# (avg per-bar high-low range / close).
_VOL_LOW = 15.0    # < 15 bps  → low
_VOL_HIGH = 35.0   # > 35 bps  → high
# (15-35 bps → medium)


def classify_regime(candles_df: pd.DataFrame) -> Dict[str, Any]:
    """Label a candle window by trend + volatility regime.

    Operator directive 2026-05-18 after issue #1474's 365-day
    backtest revealed regime-dependent edge: same threshold wins
    in one regime, loses in another. The adaptive policy needs a
    classifier that runs both at backtest time AND live signal
    time so backtest and live agree on what regime we're in.

    Trend bucket — total % move from open to close of the window:
      strong-down  : < -5%
      weak-down    : -5% .. -1%
      sideways     : -1% .. +1%
      weak-up      : +1% .. +5%
      strong-up    : > +5%

    Volatility bucket — mean per-bar high-low range as bps of close:
      low     : < 15 bps  (slow tape)
      medium  : 15-35 bps
      high    : > 35 bps  (volatile tape)

    Returns
    -------
    dict with keys
      trend          : str (one of the buckets above, or "unknown")
      volatility     : str
      regime         : str — combined "<trend>/<volatility>"
      pct_change     : float — total move %, 2 dp
      avg_range_bps  : float — mean per-bar range, 2 dp

    For empty / malformed input returns the ``"unknown"`` label
    rather than raising; callers can fall back to default policy.
    """
    if (
        candles_df is None
        or not isinstance(candles_df, pd.DataFrame)
        or "close" not in candles_df.columns
        or len(candles_df) < 10
    ):
        return {
            "trend": "unknown", "volatility": "unknown",
            "regime": "unknown",
            "pct_change": 0.0, "avg_range_bps": 0.0,
        }
    close = candles_df["close"].astype(float)
    open_px = float(close.iloc[0])
    close_px = float(close.iloc[-1])
    if open_px <= 0:
        return {
            "trend": "unknown", "volatility": "unknown",
            "regime": "unknown",
            "pct_change": 0.0, "avg_range_bps": 0.0,
        }
    pct_change = (close_px - open_px) / open_px

    if pct_change < -_TREND_STRONG:
        trend = "strong-down"
    elif pct_change < -_TREND_WEAK:
        trend = "weak-down"
    elif pct_change < _TREND_WEAK:
        trend = "sideways"
    elif pct_change < _TREND_STRONG:
        trend = "weak-up"
    else:
        trend = "strong-up"

    high = candles_df["high"].astype(float)
    low = candles_df["low"].astype(float)
    bar_range_bps = ((high - low) / close * 10_000).mean()

    if bar_range_bps < _VOL_LOW:
        volatility = "low"
    elif bar_range_bps < _VOL_HIGH:
        volatility = "medium"
    else:
        volatility = "high"

    return {
        "trend": trend,
        "volatility": volatility,
        "regime": f"{trend}/{volatility}",
        "pct_change": round(pct_change * 100, 2),
        "avg_range_bps": round(float(bar_range_bps), 2),
    }
