#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — the **point-in-time expectation model** for economic releases.

Pre-registered per the operator-approved 2026-07-30 gate change: M1's "surprise"
may be measured against archived published consensus **or** against a PIT
expectation model validated on the captured-consensus overlap. This module is that
model.

Why it exists
-------------
Archived *survey* consensus (Reuters/Bloomberg/WSJ polls) is not available
historically at zero cost, and the captured window is only ~6 months deep — which
had been read as "wait until mid-September for n=12." But the research question M2
actually asks is not *"does published consensus predict returns"*; it is **"does the
UNANTICIPATED COMPONENT of a release predict returns."** Survey consensus is one
proxy for the market's expectation, not the only valid one. A model expectation fit
strictly on pre-release data answers the same economic question and is
backfillable over **decades** of FRED history.

    surprise(D) = actual(D) − expectation(D | data strictly before D)

The specification (pre-registered — do not tune on the outcome)
---------------------------------------------------------------
An expanding-window OLS refit at every release, with features chosen to capture
what actually drives these series:

* ``lag_1``            — the previous release (level persistence)
* ``lag_seasonal``     — the release one full seasonal period ago (52 for weekly,
                         12 for monthly), which is the dominant term for
                         inventory/storage series
* ``sin/cos`` harmonics of the seasonal phase (``--harmonics`` pairs, default 2)
                       — smooth within-year seasonality that ``lag_seasonal``
                         alone can't express
* an intercept

Fit by least squares on every usable row **before** the target release; predict the
target. Deliberately small and linear: the point is an honest, hard-to-overfit
baseline for the *anticipated* component, not a forecasting contest. A richer model
would raise exactly the overfitting question the M1 gate exists to avoid.

Leakage safety — the one hard property
--------------------------------------
``expectation_at(history, i, ...)`` may read ``history[:i]`` and nothing else. Both
the training rows and the prediction row's features are drawn from indices ``< i``.
This is enforced by construction (the function is handed a slice) and pinned by
``test_econ_expectation.py::TestLeakageSafety``, which mutates every future value
and asserts the expectation is bit-identical.

Vintage caveat (carried, not hidden)
-----------------------------------
Keyless FRED (``fredgraph.csv``) serves the **current** vintage, not first prints.
For the weekly EIA/claims headline series revisions are small-to-nil, so it is a
defensible first pass — but a caller MUST stamp which basis it used
(``pit_basis``), and ALFRED (``realtime_start``, free key) is the upgrade path if an
edge proves revision-sensitive. Never present revised data as point-in-time.

**Stdlib-only** (mirrors the other macro/ops scripts) so it runs on a bare
GitHub-hosted runner with no repo install and is unit-testable without network.
The OLS is solved via normal equations + Gaussian elimination with partial
pivoting, plus a tiny ridge term (see ``_RIDGE``) for numerical stability — part
of the pre-registered spec, not a tuned hyper-parameter. No order path, no DB
write, no live-VM touch.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

# Normal equations are mildly ill-conditioned; a fixed relative ridge keeps the
# solve stable without materially biasing a 7-parameter fit. Scaled by the mean
# diagonal of X'X so it is unit-invariant. Pre-registered — do not tune.
_RIDGE = 1e-9

# Seasonal period in OBSERVATIONS (not days), by release cadence.
SEASONAL_PERIOD: dict[str, int] = {
    "weekly": 52,
    "monthly": 12,
    "quarterly": 4,
}
DEFAULT_HARMONICS = 2
# Enough rows to identify intercept + lag_1 + lag_seasonal + 2*harmonics params
# with slack. Below this the fit is not trustworthy and we return None rather than
# a fabricated expectation.
DEFAULT_MIN_TRAIN = 24

SPEC_VERSION = "seasonal_ar_ols_v1"


def _design_row(values: Sequence[float], j: int, period: int, harmonics: int) -> Optional[list[float]]:
    """Feature row for observation ``j``, using only indices strictly < ``j``.

    ``None`` when ``j`` lacks its lags (the warm-up head of the series).
    """
    if j - period < 0 or j - 1 < 0:
        return None
    lag_1 = values[j - 1]
    lag_s = values[j - period]
    if not (math.isfinite(lag_1) and math.isfinite(lag_s)):
        return None
    row = [1.0, float(lag_1), float(lag_s)]
    # Seasonal phase of the TARGET observation. This is calendar position, known
    # ex ante for a scheduled release — not information about its value.
    phase = 2.0 * math.pi * (j % period) / float(period)
    for k in range(1, harmonics + 1):
        row.append(math.sin(k * phase))
        row.append(math.cos(k * phase))
    return row


def expectation_at(
    values: Sequence[float],
    i: int,
    *,
    period: int = 52,
    harmonics: int = DEFAULT_HARMONICS,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> Optional[float]:
    """PIT expectation for ``values[i]`` using ONLY ``values[:i]``.

    Returns ``None`` — never a guess — when there is too little history to fit, or
    the design is degenerate. A ``None`` propagates to a ``None`` surprise, so a
    thin head of the series is honestly excluded rather than silently zeroed.
    """
    if i <= 0 or i > len(values):
        return None
    if period < 2 or harmonics < 0:
        return None

    rows: list[list[float]] = []
    targets: list[float] = []
    # Train on every row j < i that has full lags. Every feature index is < j < i,
    # so nothing at or beyond i is ever read.
    for j in range(1, i):
        r = _design_row(values, j, period, harmonics)
        if r is None:
            continue
        y = values[j]
        if not math.isfinite(y):
            continue
        rows.append(r)
        targets.append(float(y))

    if not rows or len(rows) < max(min_train, len(rows[0]) + 2):
        return None

    x_target = _design_row(values, i, period, harmonics)
    if x_target is None:
        return None

    coef = _solve_ols(rows, targets)
    if coef is None:
        return None
    pred = sum(x * c for x, c in zip(x_target, coef))
    return pred if math.isfinite(pred) else None


def _solve_ols(rows: Sequence[Sequence[float]], targets: Sequence[float]) -> Optional[list[float]]:
    """Least-squares fit via ridge-stabilised normal equations (stdlib only).

    Returns ``None`` on a singular/non-finite system rather than a garbage fit.
    """
    p = len(rows[0])
    # X'X and X'y
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for r, y in zip(rows, targets):
        for a in range(p):
            ra = r[a]
            xty[a] += ra * y
            xtx_a = xtx[a]
            for b_ in range(a, p):
                xtx_a[b_] += ra * r[b_]
    for a in range(p):          # mirror the symmetric upper triangle
        for b_ in range(a):
            xtx[a][b_] = xtx[b_][a]

    diag_mean = sum(xtx[a][a] for a in range(p)) / p
    if not math.isfinite(diag_mean) or diag_mean <= 0:
        return None
    for a in range(p):
        xtx[a][a] += _RIDGE * diag_mean

    # Gaussian elimination with partial pivoting on the augmented system.
    aug = [list(xtx[a]) + [xty[a]] for a in range(p)]
    for col in range(p):
        piv = max(range(col, p), key=lambda r_: abs(aug[r_][col]))
        if abs(aug[piv][col]) < 1e-12:
            return None
        aug[col], aug[piv] = aug[piv], aug[col]
        pv = aug[col][col]
        for r_ in range(col + 1, p):
            f = aug[r_][col] / pv
            if f == 0.0:
                continue
            for c_ in range(col, p + 1):
                aug[r_][c_] -= f * aug[col][c_]
    coef = [0.0] * p
    for r_ in range(p - 1, -1, -1):
        acc = aug[r_][p] - sum(aug[r_][c_] * coef[c_] for c_ in range(r_ + 1, p))
        coef[r_] = acc / aug[r_][r_]
    return coef if all(math.isfinite(c) for c in coef) else None


def surprise_series(
    values: Sequence[float],
    *,
    period: int = 52,
    harmonics: int = DEFAULT_HARMONICS,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> list[Optional[float]]:
    """``[actual_i − expectation_i]`` aligned to ``values``, ``None`` where unfit.

    Each element is computed with an expanding window: element ``i`` sees only
    ``values[:i]``. Walk-forward by construction — there is no single in-sample fit.
    """
    out: list[Optional[float]] = []
    for i, actual in enumerate(values):
        exp = expectation_at(values, i, period=period, harmonics=harmonics,
                             min_train=min_train)
        if exp is None or not math.isfinite(actual):
            out.append(None)
        else:
            out.append(float(actual) - exp)
    return out


def period_for_cadence(cadence: str, default: int = 52) -> int:
    """Seasonal period in observations for a named release cadence."""
    return SEASONAL_PERIOD.get(str(cadence or "").strip().lower(), default)
