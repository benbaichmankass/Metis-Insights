"""The evidence-horizon model: the five classes, the interval, and the collapses it refuses.

⚠️ **THE LOAD-BEARING ASSERTIONS ARE THE NEGATIVE ONES.** A test suite that only
checks the happy projections would pass on a module that reports a rate of zero
for a leg that closed nothing, or a finite horizon for a shadow leg that can
never close a trade — which are precisely the readings this module exists to
refuse. Each such test names the wrong answer it is pinning out.
"""
from __future__ import annotations

import math

import pytest

from src.runtime.evidence_horizon import (
    FUNNEL_STAGES,
    GRADEABLE_NOW,
    HORIZON_CLASSES,
    REACHABLE,
    STAGE_CLOSING,
    STAGE_DECIDED_NOT_FILLED,
    STAGE_FILLED_NOT_CLOSED,
    STAGE_NO_DECISIONS,
    STAGE_UNKNOWN,
    STRUCTURALLY_UNGRADEABLE,
    UNBOUNDED_NO_CLOSES,
    UNKNOWN,
    classify_funnel_stage,
    evidence_horizon,
    poisson_rate_lower_bound,
    poisson_rate_upper_bound,
    summarize_horizons,
)

FLOOR = 20
W = 7.0


def h(n_closed, *, n_decisions=5, n_filled=5, execution="live", window_days=W):
    return evidence_horizon(
        floor=FLOOR, n_closed=n_closed, window_days=window_days,
        n_decisions=n_decisions, n_filled=n_filled, execution=execution,
    )


# ---------------------------------------------------------------------------
# The confidence limits, checked against closed-form values rather than against
# the implementation. `-ln(0.05) = 2.9957` is the rule of three; the k=1 lower
# limit solves `1 - e^-m = 0.05` exactly.
# ---------------------------------------------------------------------------

def test_zero_event_upper_bound_is_the_rule_of_three():
    assert poisson_rate_upper_bound(0, W) == pytest.approx(-math.log(0.05) / W, rel=1e-6)


def test_one_event_lower_bound_matches_the_closed_form():
    expected = -math.log(1 - 0.05) / W  # solves P(X >= 1 | m) = 0.05
    assert poisson_rate_lower_bound(1, W) == pytest.approx(expected, rel=1e-6)


def test_lower_bound_is_below_the_point_estimate_which_is_below_the_upper():
    """The bisection's DIRECTION flag is what this pins.

    Running the decreasing branch on the increasing function does not raise —
    it walks the bracket to its ceiling and returns a plausible-looking limit
    orders of magnitude wrong. Only an ordering assertion catches that.
    """
    for k in (1, 2, 4, 8, 19):
        lo = poisson_rate_lower_bound(k, W)
        hi = poisson_rate_upper_bound(k, W)
        point = k / W
        assert lo < point < hi, (k, lo, point, hi)


def test_no_rate_without_an_exposure():
    """A rate per day is undefined without days — and is not invented."""
    assert poisson_rate_upper_bound(3, 0) is None
    assert poisson_rate_upper_bound(3, None) is None
    assert poisson_rate_lower_bound(0, W) is None  # the limit IS zero -> unbounded


# ---------------------------------------------------------------------------
# The five classes.
# ---------------------------------------------------------------------------

def test_at_or_above_the_floor_is_gradeable_now():
    assert h(FLOOR)["horizon_class"] == GRADEABLE_NOW
    assert h(FLOOR + 100)["horizon_class"] == GRADEABLE_NOW


def test_a_closing_leg_is_reachable_with_a_finite_point_projection():
    out = h(8)
    assert out["horizon_class"] == REACHABLE
    assert out["days_to_floor_point"] == pytest.approx(17.5)
    assert out["observed_close_rate_per_day"] == pytest.approx(8 / W)


def test_zero_closes_reports_NO_RATE_rather_than_a_rate_of_zero():
    """⚠️ The wrong answer this pins out: `observed_close_rate_per_day: 0.0`.

    Zero closes in a window is an ABSENCE OF MEASUREMENT that bounds the rate
    from above; a stored 0.0 would make it arithmetically indistinguishable
    from a leg measured to close at a rate of zero, and would divide-by-zero
    or project to infinity in every consumer downstream.
    """
    out = h(0, n_decisions=0, n_filled=0)
    assert out["horizon_class"] == UNBOUNDED_NO_CLOSES
    assert out["observed_close_rate_per_day"] is None
    assert out["days_to_floor_point"] is None
    assert out["days_to_floor_conservative"] is None  # unbounded, not "soon"


def test_zero_closes_still_carries_a_defensible_lower_bound_on_the_wait():
    """`unbounded` must not degrade into "no information" — the rule of three holds."""
    out = h(0, n_decisions=0, n_filled=0)
    assert out["days_to_floor_optimistic"] == pytest.approx(FLOOR * W / -math.log(0.05), abs=0.1)
    assert out["days_to_floor_optimistic"] > W  # strictly longer than the window we looked at


def test_a_shadow_leg_with_no_fills_is_structural_not_merely_slow():
    """⚠️ The wrong answer this pins out: a horizon in DAYS for a leg days cannot fix.

    A shadow leg does not reach the order path, so its closed-trade count stays
    0 for ever. Reporting `unbounded_no_closes` here would be true-but-useless;
    reporting a day count would be false.
    """
    out = h(0, n_decisions=11, n_filled=0, execution="shadow")
    assert out["horizon_class"] == STRUCTURALLY_UNGRADEABLE
    assert out["structural_reason"] == "shadow_execution_no_fills"
    assert out["days_to_floor_optimistic"] is None


def test_a_shadow_leg_that_IS_filling_is_not_reclassified_as_structural():
    """The execution-mismatch anomaly belongs to the generator's own override.

    Silently filing a shadow leg that is reaching the order path as "cannot
    trade by design" would hide the pipeline anomaly the packet exists to
    raise.
    """
    out = h(3, n_decisions=9, n_filled=3, execution="shadow")
    assert out["horizon_class"] == REACHABLE


@pytest.mark.parametrize("kwargs", [
    {"n_closed": None},
    {"window_days": None},
    {"window_days": 0},
])
def test_a_missing_input_is_unknown_and_never_folded_into_zero_closes(kwargs):
    """⚠️ The wrong answer this pins out: `unbounded_no_closes` for an unread input.

    That would turn "we did not read n_closed" into "we read it and it was
    zero" — the collapse this repo has a guard family for.
    """
    out = h(**{"n_closed": 0, **kwargs})
    assert out["horizon_class"] == UNKNOWN
    assert out["horizon_class"] != UNBOUNDED_NO_CLOSES
    assert out["shortfall"] is None


# ---------------------------------------------------------------------------
# The funnel.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dec,fill,clos,expected", [
    (21, 8, 8, STAGE_CLOSING),
    (2, 1, 0, STAGE_FILLED_NOT_CLOSED),
    (11, 0, 0, STAGE_DECIDED_NOT_FILLED),
    (0, 0, 0, STAGE_NO_DECISIONS),
    (None, None, None, STAGE_UNKNOWN),
    (5, None, 0, STAGE_UNKNOWN),   # closed nothing, but we cannot say WHY
])
def test_funnel_stage(dec, fill, clos, expected):
    assert classify_funnel_stage(dec, fill, clos) == expected


def test_unknown_funnel_inputs_do_not_manufacture_a_silent_leg():
    """⚠️ The wrong answer this pins out: `no_decisions` when n_decisions is absent.

    That asserts a silent strategy on evidence nobody has, and `no_decisions`
    is the stage that routes a leg toward retirement.
    """
    assert classify_funnel_stage(None, None, 0) == STAGE_UNKNOWN


# ---------------------------------------------------------------------------
# The roll-up.
# ---------------------------------------------------------------------------

def test_summary_carries_every_class_including_the_zeroes():
    """An ABSENT key would read as "no leg is structurally ungradeable"; it means
    "this summary predates the class"."""
    s = summarize_horizons([h(8), h(0, n_decisions=0, n_filled=0)])
    assert set(s["by_horizon_class"]) == set(HORIZON_CLASSES)
    assert set(s["by_funnel_stage"]) == set(FUNNEL_STAGES)
    assert s["by_horizon_class"][STRUCTURALLY_UNGRADEABLE] == 0


def test_summary_buckets_sum_to_the_leg_count_so_the_partition_is_checkable():
    legs = [h(8), h(0, n_decisions=0, n_filled=0),
            h(0, n_decisions=4, n_filled=0, execution="shadow"),
            h(FLOOR), h(None)]
    s = summarize_horizons(legs)
    assert sum(s["by_horizon_class"].values()) == len(legs) == s["n_legs"]
    assert sum(s["by_funnel_stage"].values()) == len(legs)


def test_summary_days_cover_only_the_reachable_legs():
    """Pooling an unbounded leg into a "days to grade the fleet" figure is the
    collapse the whole block exists to undo."""
    s = summarize_horizons([h(8), h(1), h(0, n_decisions=0, n_filled=0)])
    assert s["reachable_legs"] == 2
    assert s["days_to_grade_all_reachable_point"] == pytest.approx(140.0)


# ---------------------------------------------------------------------------
# The measured 2026-09-01 shape, so a regression in the model is visible as a
# change to the published numbers rather than only to an abstract class.
# Population: the four leg shapes that actually occurred that run.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_closed,point,optimistic,conservative", [
    (8, 17.5, 9.7, 35.2),
    (5, 28.0, 13.3, 71.1),
    (2, 70.0, 22.2, 394.0),
    (1, 140.0, 29.5, 2729.4),
])
def test_the_intervals_measured_on_the_20260901_run(n_closed, point, optimistic, conservative):
    out = h(n_closed)
    assert out["days_to_floor_point"] == pytest.approx(point)
    assert out["days_to_floor_optimistic"] == pytest.approx(optimistic, abs=0.1)
    assert out["days_to_floor_conservative"] == pytest.approx(conservative, rel=1e-3)


def test_the_n_equals_1_interval_spans_two_orders_of_magnitude():
    """The single most decision-relevant fact in the model.

    Eight of the 18 reachable legs on the 2026-09-01 run sat at n_closed=1. If
    the interval ever collapses toward the point estimate, a reader will set a
    5-month window believing they have bought gradeability — which is the trap
    this whole block exists to make visible.
    """
    out = h(1)
    assert out["days_to_floor_conservative"] / out["days_to_floor_optimistic"] > 50
