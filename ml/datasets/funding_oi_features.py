"""Crypto funding-rate + open-interest features (S-MLOPT-S11, M14 Phase 2.3).

Perpetual-swap funding rate and open interest are cheap, high-value, currently
unused feature families for the BTCUSDT regime/decision heads. The research
nuance the roadmap bakes in (optimization-roadmap.md § Session 2.3):

> **Funding is mostly a TRAILING byproduct of momentum — its signal is in the
> EXTREMES, not the level.**

So the headline features are the funding-rate **z-score** and its **absolute
magnitude** (an extreme detector), not the raw level; open interest is fed as a
**change** (log change over a past window) + the z-score of that change, since
the OI level itself is non-stationary and exchange-scale-dependent.

All functions here are **pure** and operate on a window of already-aligned
observations (the caller is responsible for the as-of, past-only alignment —
same contract as `volatility_estimators.py`). Fed a past-only window
`[t-n+1 .. t]` the result is leakage-safe by construction. Best-effort:
``None`` is returned when the window is too short or degenerate (zero variance);
the feature-emit shape converts ``None`` → ``0.0`` via ``_finite_or_zero``.
"""
from __future__ import annotations

import math
import statistics
from typing import Sequence


def rolling_zscore(window: Sequence[float | None], *, min_n: int = 3) -> float | None:
    """Z-score of the LAST value vs the mean/stdev of ``window``.

    ``window`` is the inclusive past window ``[t-n+1 .. t]`` of the aligned
    series; the score is ``(window[-1] - mean) / stdev`` over the non-``None``
    entries. Returns ``None`` when fewer than ``min_n`` usable points, the last
    value is ``None``, or the window has ~zero variance (an undefined z).
    """
    vals = [v for v in window if v is not None]
    if len(vals) < min_n or window[-1] is None:
        return None
    mean = statistics.fmean(vals)
    stdev = statistics.pstdev(vals)
    if stdev <= 1e-12:
        return None
    return (float(window[-1]) - mean) / stdev


def extreme_magnitude(zscore: float | None) -> float | None:
    """``abs(zscore)`` — the "signal is in the extremes" feature.

    A large magnitude (in either direction) flags a funding extreme; the sign
    is carried separately by ``rolling_zscore``. ``None`` propagates.
    """
    if zscore is None:
        return None
    return abs(zscore)


def log_change(window: Sequence[float | None]) -> float | None:
    """``ln(window[-1] / first_positive)`` over the window — the OI-change feature.

    Uses the earliest **positive** value in the window as the base so a single
    missing/zero leading observation doesn't void the change. Returns ``None``
    when the last value is missing/non-positive or no positive base exists.
    """
    if not window or window[-1] is None or window[-1] <= 0:
        return None
    base = None
    for v in window:
        if v is not None and v > 0:
            base = v
            break
    if base is None or base <= 0:
        return None
    return math.log(float(window[-1]) / base)


def diffs(window: Sequence[float | None]) -> list[float]:
    """Consecutive first differences of the non-``None`` run (for change-z)."""
    vals = [v for v in window if v is not None]
    return [vals[i] - vals[i - 1] for i in range(1, len(vals))]


def change_zscore(window: Sequence[float | None], *, min_n: int = 3) -> float | None:
    """Z-score of the LATEST first-difference vs the window's first-differences.

    Captures "is the most recent open-interest move unusually large?" — the
    extreme-of-change reading that complements the level-change ``log_change``.
    Returns ``None`` when there are too few diffs or ~zero variance.
    """
    d = diffs(window)
    if len(d) < min_n:
        return None
    mean = statistics.fmean(d)
    stdev = statistics.pstdev(d)
    if stdev <= 1e-12:
        return None
    return (d[-1] - mean) / stdev


def _finite_or_zero(value: float | None) -> float:
    """A feature-emit shape: ``None`` / non-finite → ``0.0`` (neutral)."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)
