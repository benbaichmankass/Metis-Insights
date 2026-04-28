"""
Higher-timeframe (HTF) trend confluence helpers for the ICT signal path.

These are *pure* functions: they never mutate the input DataFrame and they
never read from disk, the network, or the runtime pipeline. They exist so
the FVG/Order-Block strategy can later filter signals to only fire in the
direction of the dominant trend (one of the M7 Phase 2 confluence filters
listed in ``docs/sprint-plans/sprint-plan-2026-04-28.md``).

Wiring into ``src/runtime/pipeline.py`` is intentionally **not** done in
this checkpoint \u2014 see CP-2026-04-28-11 in
``docs/claude/checkpoints/CHECKPOINT_LOG.md``.

Public API
----------

``htf_trend_bias(df, fast=20, slow=50, source="close")``
    Return ``"bullish"``, ``"bearish"``, or ``"neutral"`` from the
    relationship between two EMAs computed on *df*.

``ema(series, length)``
    Vanilla exponential moving average. Exposed so tests and downstream
    callers can verify the same numerics this module uses.

Design notes
------------

* "HTF" in this module means *whatever timeframe the caller passes in*.
  The helpers do not resample. The "higher-timeframe" naming is preserved
  because that is how the sprint plan refers to the filter (\u00a7 M7 Phase 2,
  "higher-timeframe trend filter"). In production the caller would feed
  this function a 1h or 4h frame while the strategy itself runs on 5m.
* "Neutral" is returned when the two EMAs are within ``eps`` of each other
  (default ``1e-9``). This avoids flipping bias on numerical noise when
  prices are flat.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd


TrendBias = Literal["bullish", "bearish", "neutral"]


def ema(series: "pd.Series", length: int) -> "pd.Series":
    """
    Exponential moving average.

    Uses ``pandas.Series.ewm(span=length, adjust=False).mean()`` which
    matches the convention used by most charting platforms (TradingView,
    Bybit's TA library) where the most recent value is weighted more
    heavily and there is no lookback bias from ``adjust=True``.

    Parameters
    ----------
    series : pd.Series
        Numeric series. ``NaN`` values are propagated by ewm.
    length : int
        EMA span. Must be >= 1.

    Returns
    -------
    pd.Series
        EMA, same index as *series*.
    """
    if length < 1:
        raise ValueError(f"ema length must be >= 1, got {length}")
    return series.ewm(span=length, adjust=False).mean()


def htf_trend_bias(
    df: "pd.DataFrame",
    fast: int = 20,
    slow: int = 50,
    source: str = "close",
    eps: float = 1e-9,
) -> TrendBias:
    """
    Classify the HTF trend bias from a fast/slow EMA crossover state.

    Returns
    -------
    "bullish"
        ``ema(fast)`` is strictly above ``ema(slow)`` on the most recent bar.
    "bearish"
        ``ema(fast)`` is strictly below ``ema(slow)`` on the most recent bar.
    "neutral"
        The two EMAs are within ``eps``, the input is too short to seed
        either EMA, or the latest values are NaN.

    The function only inspects the **last** row. Callers that want a
    per-bar bias series should call ``ema()`` directly and compare.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV (or any frame containing *source*).
    fast, slow : int
        EMA spans. Must satisfy ``1 <= fast < slow``.
    source : str
        Column name to compute the EMAs on. Defaults to ``"close"``.
    eps : float
        Tolerance for the "neutral" band; helps avoid bias flipping on
        floating-point noise in flat markets.

    Notes
    -----
    Returns ``"neutral"`` rather than raising when *df* is empty or
    *source* is missing values \u2014 this matches how downstream pipeline code
    treats "no information" cases (skip, do not crash).
    """
    if fast < 1 or slow < 1:
        raise ValueError(f"EMA spans must be >= 1, got fast={fast} slow={slow}")
    if fast >= slow:
        raise ValueError(
            f"fast must be < slow, got fast={fast} slow={slow}"
        )
    if source not in df.columns:
        raise KeyError(f"source column {source!r} not in dataframe")

    if len(df) == 0:
        return "neutral"

    series = df[source].astype(float)
    # Treat a missing latest value as "no information" — same posture as
    # downstream strategy code which skips ticks with bad data rather than
    # acting on stale EMA state.
    if pd.isna(series.iloc[-1]):
        return "neutral"
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)

    fast_last = fast_ema.iloc[-1]
    slow_last = slow_ema.iloc[-1]

    # NaN guard: ``ewm`` with ``adjust=False`` never produces NaN unless
    # the input itself contains NaN, but be defensive.
    if pd.isna(fast_last) or pd.isna(slow_last):
        return "neutral"

    diff = float(fast_last) - float(slow_last)
    if abs(diff) <= eps:
        return "neutral"
    return "bullish" if diff > 0 else "bearish"


__all__ = ["TrendBias", "ema", "htf_trend_bias"]
