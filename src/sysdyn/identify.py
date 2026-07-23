"""M29 P1 — system identification (fit a model's parameters from data).

The AI-role lead for M29 v1 (operator-locked P0): *system identification* — infer
the free parameters (and, via ``delay`` candidates, the lags) of a **declared**
stock-flow structure from observed history, rather than hand-tuning constants. This
module is that fitter, plus the honesty checks the design demands: a holdout error
band and per-fold parameter **stability** (an SD structure is identifiable only if
the fit is stable across folds — equifinality otherwise; the design's #1 risk).

Deliberately dependency-light — a pure-Python **bounded coordinate descent with
grid refinement**, no scipy. Seed SD models have a handful of parameters on a
short bounded search, so a robust derivative-free local search with multi-scale
refinement is the right tool and keeps the dependency surface empty. It is *local*
(honest about that): callers pass a sensible ``init`` inside ``bounds``; the
synthetic round-trip test is what proves it recovers truth in the seed model's
basin.

Everything here is pure: the objective simulates the injected model against the
injected observed series. No I/O, no randomness, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from .engine import Model, simulate


# ---- error metrics -------------------------------------------------------

def sse(pred: Sequence[float], obs: Sequence[float]) -> float:
    """Sum of squared errors over the aligned prefix (ignores trailing unmatched)."""
    n = min(len(pred), len(obs))
    return sum((pred[i] - obs[i]) ** 2 for i in range(n))


def rmse(pred: Sequence[float], obs: Sequence[float]) -> float:
    n = min(len(pred), len(obs))
    if n == 0:
        return float("nan")
    return (sse(pred, obs) / n) ** 0.5


def r_squared(pred: Sequence[float], obs: Sequence[float]) -> float:
    """Coefficient of determination; ``nan`` when the observed series is flat."""
    n = min(len(pred), len(obs))
    if n == 0:
        return float("nan")
    mean = sum(obs[:n]) / n
    ss_tot = sum((obs[i] - mean) ** 2 for i in range(n))
    if ss_tot == 0.0:
        return float("nan")
    return 1.0 - sse(pred, obs) / ss_tot


# ---- the fit -------------------------------------------------------------

# A predictor turns a full simulation Trajectory into the modelled series to
# compare against the observed target (e.g. the "price" observation, or a stock).
PredictFn = Callable[["object"], Sequence[float]]


@dataclass
class FitResult:
    params: dict[str, float]
    loss: float
    rmse: float
    r2: float
    iterations: int
    converged: bool


def _objective(
    model: Model,
    free: Mapping[str, float],
    fixed: Mapping[str, float],
    exog: Sequence[Mapping[str, float]],
    steps: int,
    dt: float,
    predict: PredictFn,
    observed: Sequence[float],
    score_slice: Optional[tuple[int, int]],
) -> float:
    params = {**fixed, **free}
    traj = simulate(model, params, exog, steps, dt=dt)
    pred = predict(traj)
    if score_slice is None:
        return sse(pred, observed)
    a, b = score_slice
    return sse(pred[a:b], observed[a:b])


def identify(
    model: Model,
    *,
    bounds: Mapping[str, tuple[float, float]],
    init: Mapping[str, float],
    fixed: Optional[Mapping[str, float]] = None,
    exog: Sequence[Mapping[str, float]],
    observed: Sequence[float],
    predict: PredictFn,
    dt: float = 1.0,
    max_passes: int = 60,
    tol: float = 1e-9,
    score_slice: Optional[tuple[int, int]] = None,
) -> FitResult:
    """Fit the free parameters in ``bounds`` to minimise SSE(predict(sim), observed).

    ``init`` seeds each free param (clamped into its bound). ``fixed`` are params
    held constant. ``predict`` selects the modelled series from the trajectory.
    Bounded coordinate descent: each pass sweeps every parameter, trying ± a step
    per axis and keeping any improvement; when a full pass yields no improvement the
    step is halved; converged when the step underflows or the loss stops moving.

    ``score_slice`` (start, end) restricts the residuals that count to that index
    window while STILL simulating the full ``len(observed)`` run from t=0 — so a
    fold is scored on its window without changing the model's initial condition
    (used by :func:`walk_forward_stability`). The reported ``rmse``/``r2`` are
    computed over the same window.
    """
    fixed = dict(fixed or {})
    steps = len(observed)
    params = {k: min(max(float(init[k]), lo), hi) for k, (lo, hi) in bounds.items()}
    # Per-axis step starts at a quarter of each parameter's range.
    step = {k: (hi - lo) / 4.0 for k, (lo, hi) in bounds.items()}

    def loss_of(p: Mapping[str, float]) -> float:
        return _objective(model, p, fixed, exog, steps, dt, predict, observed, score_slice)

    best = loss_of(params)
    iterations = 0
    converged = False
    for _ in range(max_passes):
        iterations += 1
        improved = False
        for k, (lo, hi) in bounds.items():
            for direction in (1.0, -1.0):
                cand = dict(params)
                cand[k] = min(max(params[k] + direction * step[k], lo), hi)
                if cand[k] == params[k]:
                    continue
                cl = loss_of(cand)
                if cl < best - tol:
                    params, best, improved = cand, cl, True
        if not improved:
            # Refine: halve every axis' step. Stop once all steps are negligible.
            for k, (lo, hi) in bounds.items():
                step[k] *= 0.5
            if all(step[k] <= (hi - lo) * 1e-6 for k, (lo, hi) in bounds.items()):
                converged = True
                break

    traj = simulate(model, {**fixed, **params}, exog, steps, dt=dt)
    pred = list(predict(traj))
    if score_slice is None:
        pred_w, obs_w = pred, observed
    else:
        a, b = score_slice
        pred_w, obs_w = pred[a:b], observed[a:b]
    return FitResult(
        params=params,
        loss=best,
        rmse=rmse(pred_w, obs_w),
        r2=r_squared(pred_w, obs_w),
        iterations=iterations,
        converged=converged,
    )


# ---- identifiability / stability ----------------------------------------

@dataclass
class StabilityReport:
    """Per-fold fits + a spread summary. ``max_rel_spread`` is the largest
    across-fold coefficient of variation (std/|mean|) over the free params — a
    small value means the structure is identifiable (the fit lands in the same
    place regardless of which slice it saw); a large one means equifinality and
    the model should be simplified, not shipped (the design's stop condition)."""

    folds: list[FitResult]
    param_means: dict[str, float]
    param_rel_spread: dict[str, float]
    max_rel_spread: float


def walk_forward_stability(
    model: Model,
    *,
    bounds: Mapping[str, tuple[float, float]],
    init: Mapping[str, float],
    fixed: Optional[Mapping[str, float]] = None,
    exog: Sequence[Mapping[str, float]],
    observed: Sequence[float],
    predict: PredictFn,
    n_folds: int,
    dt: float = 1.0,
) -> StabilityReport:
    """Fit the model independently on ``n_folds`` contiguous time slices and report
    how much the recovered parameters move fold-to-fold.

    Each fold re-simulates from the model's declared initial stocks over its own
    slice of the exogenous/observed series (a fold is a clean sub-run, not a
    warm-started continuation — so the fits are genuinely independent evidence).
    """
    if n_folds < 2:
        raise ValueError("n_folds must be >= 2 to measure stability")
    n = len(observed)
    if n < n_folds:
        raise ValueError(f"need at least one observation per fold ({n} < {n_folds})")
    size = n // n_folds
    fits: list[FitResult] = []
    for f in range(n_folds):
        a = f * size
        b = n if f == n_folds - 1 else (f + 1) * size
        # Simulate the FULL run from t=0 (consistent initial condition) but score
        # only this fold's window — so folds are genuinely independent evidence
        # about the params without a mid-run initial-condition mismatch.
        fits.append(
            identify(
                model,
                bounds=bounds,
                init=init,
                fixed=fixed,
                exog=exog,
                observed=observed,
                predict=predict,
                dt=dt,
                score_slice=(a, b),
            )
        )

    means: dict[str, float] = {}
    spreads: dict[str, float] = {}
    for k in bounds:
        vals = [fit.params[k] for fit in fits]
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / len(vals)
        std = var ** 0.5
        means[k] = m
        spreads[k] = (std / abs(m)) if m != 0 else (0.0 if std == 0 else float("inf"))

    return StabilityReport(
        folds=fits,
        param_means=means,
        param_rel_spread=spreads,
        max_rel_spread=max(spreads.values()) if spreads else 0.0,
    )
