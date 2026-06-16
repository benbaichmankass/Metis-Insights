"""Offline fitting of confidence calibrators + reliability assessment.

Uses sklearn/numpy (trainer/CI only). Produces the pure-Python ``Calibrator``
objects in ``calibrators.py`` (serialized to plain dicts), so the live trader
never imports sklearn. See design doc § 4a.

Method auto-selection by sample size (the small-sample problem is real — the
live closed-trade book is thin; backtest ``--emit-trades`` rows augment it):

* ``n >= min_isotonic``  -> isotonic (non-parametric monotone)
* ``n >= min_platt``     -> Platt (1-param sigmoid)
* ``n >= min_decile``    -> equal-frequency decile binning
* otherwise              -> constant (base win-rate; honest "no signal")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .calibrators import (
    Calibrator,
    ConstantCalibrator,
    DecileCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
)

# Sample-size thresholds (tunable; documented defaults).
MIN_ISOTONIC = 300
MIN_PLATT = 50
MIN_DECILE = 20


def fit_calibrator(
    x: Sequence[float],
    y: Sequence[int],
    *,
    method: str = "auto",
    n_bins: int = 10,
) -> Calibrator:
    """Fit a raw-score -> P(win) calibrator. ``y`` is 0/1 (won)."""
    xs = [float(v) for v in x]
    ys = [1 if int(v) > 0 else 0 for v in y]
    n = len(xs)
    if n == 0:
        return ConstantCalibrator(rate=0.5)
    base_rate = sum(ys) / n

    chosen = method
    if method == "auto":
        if n >= MIN_ISOTONIC:
            chosen = "isotonic"
        elif n >= MIN_PLATT:
            chosen = "platt"
        elif n >= MIN_DECILE:
            chosen = "decile"
        else:
            chosen = "constant"

    # Degenerate label (all wins / all losses) -> nothing to discriminate.
    if len(set(ys)) < 2:
        return ConstantCalibrator(rate=base_rate)

    if chosen == "isotonic":
        return _fit_isotonic(xs, ys)
    if chosen == "platt":
        return _fit_platt(xs, ys)
    if chosen == "decile":
        return _fit_decile(xs, ys, n_bins=n_bins)
    return ConstantCalibrator(rate=base_rate)


def _fit_isotonic(xs: list[float], ys: list[int]) -> Calibrator:
    from sklearn.isotonic import IsotonicRegression  # local import (offline)

    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    ir.fit(xs, ys)
    # X_thresholds_/y_thresholds_ are the compact breakpoint representation.
    bx = list(getattr(ir, "X_thresholds_", []))
    by = list(getattr(ir, "y_thresholds_", []))
    if not bx:  # extremely degenerate fit
        return ConstantCalibrator(rate=sum(ys) / len(ys))
    return IsotonicCalibrator(xs=bx, ys=by)


def _fit_platt(xs: list[float], ys: list[int]) -> Calibrator:
    from sklearn.linear_model import LogisticRegression  # local import (offline)

    X = [[v] for v in xs]
    lr = LogisticRegression(solver="lbfgs")
    lr.fit(X, ys)
    a = float(lr.coef_[0][0])
    b = float(lr.intercept_[0])
    return PlattCalibrator(a=a, b=b)


def _fit_decile(xs: list[float], ys: list[int], *, n_bins: int) -> Calibrator:
    import numpy as np  # local import (offline)

    arr = np.asarray(xs, dtype=float)
    yarr = np.asarray(ys, dtype=float)
    # equal-frequency edges via quantiles; dedupe collapsed edges
    qs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.unique(np.quantile(arr, qs))
    if len(edges) < 2:
        return ConstantCalibrator(rate=float(yarr.mean()))
    rates: list[float] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i == len(edges) - 2:
            mask = (arr >= lo) & (arr <= hi)
        else:
            mask = (arr >= lo) & (arr < hi)
        if mask.sum() == 0:
            rates.append(float(yarr.mean()))
        else:
            rates.append(float(yarr[mask].mean()))
    return DecileCalibrator(edges=[float(e) for e in edges], rates=rates)


# --------------------------------------------------------------------------- #
# reliability / quality metrics
# --------------------------------------------------------------------------- #
@dataclass
class ReliabilityBin:
    mean_pred: float
    frac_pos: float
    count: int


def reliability_curve(
    y_true: Sequence[int], y_pred: Sequence[float], *, bins: int = 10
) -> list[ReliabilityBin]:
    """Predicted-vs-realized win rate per equal-width prob bin."""
    out: list[ReliabilityBin] = []
    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        idx = [
            j
            for j, p in enumerate(y_pred)
            if (p >= lo and (p < hi or (i == bins - 1 and p <= hi)))
        ]
        if not idx:
            continue
        mp = sum(float(y_pred[j]) for j in idx) / len(idx)
        fp = sum(int(y_true[j]) for j in idx) / len(idx)
        out.append(ReliabilityBin(mean_pred=mp, frac_pos=fp, count=len(idx)))
    return out


def brier_score(y_true: Sequence[int], y_pred: Sequence[float]) -> float:
    n = len(y_true)
    if n == 0:
        return 0.0
    return sum((float(p) - int(t)) ** 2 for t, p in zip(y_true, y_pred)) / n


def expected_calibration_error(
    y_true: Sequence[int], y_pred: Sequence[float], *, bins: int = 10
) -> float:
    """Sum over bins of |frac_pos - mean_pred| weighted by bin population."""
    n = len(y_true)
    if n == 0:
        return 0.0
    curve = reliability_curve(y_true, y_pred, bins=bins)
    return sum(b.count / n * abs(b.frac_pos - b.mean_pred) for b in curve)
