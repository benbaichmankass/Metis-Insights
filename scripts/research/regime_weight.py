"""The ONE definition of the soft (continuous) regime-weight lever.

Refinement axis for SRQ-20260618-001/-002. The 2026-06-18 hard ADX gate
(``--adx-min``/``--adx-max`` in ``scripts/backtest_trend.py`` /
``scripts/backtest_pullback.py``) was REFUTED by its own holdout check the same
day: four per-cell-tuned thresholds passed every fold at the exact 5-fold /
0.40 train_frac split and failed at k=6, k=7, train_frac 0.35/0.45 — in-sample
artifacts, not a robust regime edge (see
``docs/claude/strategy-refinement-queue.json``, both items'
``resweep_results`` and the 2026-08-11 ``stale_field_correction`` note). What
was NOT refuted is the underlying signal — a flat ``adx_min=20`` genuinely
helped ETH and hurt XRP/SOL/AVAX, i.e. ADX carries real, cell-dependent
information — only the HARD one-bit-per-cell gate on top of it, which a 5-fold
sweep has enough freedom to overfit per cell.

This module is the shared, continuous alternative: instead of admitting or
rejecting a bar, it WEIGHTS the realized R of a trade by a linear ramp of the
entry-bar ADX between a floor and a ceiling. A trade entered in a weak-ADX bar
is sized down (never necessarily to zero); a trade entered at or above the
ceiling is sized at 1.0. This has one fewer degree of freedom to overfit than
a per-cell tuned hard threshold (two anchors instead of one point pick per
cell) and degrades gracefully instead of discarding the trade outright.

Lives under ``scripts/research/`` rather than ``src/research/`` (where its
sibling ``trail_levers.py`` lives) so it stays inside `TIER1_SURFACE`
(``scripts/ci/check_pr_landing.py``) — this is backtest-only tooling with zero
live-runtime import path, and ``src/**`` is Tier-2 by that guard regardless of
a module's actual content.

Pure, dependency-free (no pandas) so it is unit-testable in isolation and
shared verbatim by both harnesses — see ``src/research/trail_levers.py`` for
why a rule read in two places is a defect class in this repo, not a style
preference.
"""
from __future__ import annotations

from typing import Optional


def soft_weight_armed(floor: Optional[float], ceiling: Optional[float]) -> bool:
    """Is the soft regime-weight lever declared at all?

    Requires BOTH anchors — a floor with no ceiling (or vice versa) is a
    half-configured lever, the same reasoning `trail_levers.vol_trail_armed`
    applies to the vol-conditional trail. No-op (byte-identical) when either
    is missing.
    """
    return floor is not None and ceiling is not None


def soft_regime_weight(adx_value: Optional[float], floor: float, ceiling: float,
                       weight_min: float = 0.0) -> float:
    """The continuous regime weight for one trade, given its entry-bar ADX.

    ``adx_value`` NaN/None (warm-up, or ADX not computed) returns
    ``weight_min`` — the same fail-permissive-toward-caution posture the hard
    gate takes on an undefined regime (never admitted). ``ceiling <= floor``
    is degenerate input and is treated as a step function at ``floor`` rather
    than raising, so a misconfigured sweep arm reports a determinate (if
    uninteresting) result instead of crashing a batch run.

    weight = weight_min + (1 - weight_min) * clip((adx - floor) / (ceiling - floor), 0, 1)

    ``weight_min=0.0`` (the default) can flatten a trade to zero R contribution
    in the weakest regime — the continuous analogue of the hard gate's skip.
    ``weight_min>0.0`` sizes down rather than fully out, which is the genuinely
    new axis the hard gate could not express at all.
    """
    if adx_value is None:
        return weight_min
    try:
        adx_f = float(adx_value)
    except (TypeError, ValueError):
        return weight_min
    if adx_f != adx_f:  # NaN
        return weight_min
    span = ceiling - floor
    if span <= 0:
        ramp = 1.0 if adx_f >= floor else 0.0
    else:
        ramp = (adx_f - floor) / span
        ramp = max(0.0, min(1.0, ramp))
    return weight_min + (1.0 - weight_min) * ramp
