"""Unit tests for the ExitPlan layer (dynamic-take-profit consistency, P1).

Covers:
  - ``validate_exit_plan`` — accepts the canonical schema, rejects malformed.
  - ``build_exit_plan_from_legacy`` — derives a valid plan from package fields,
    including the TP1/TP2 ladder round-trip that mirrors turtle_soup's exit, and
    the single-target case; never raises on garbage.
  - ``exit_plan_realism.clamp_exit_plan`` — clamps out-of-reach rungs / final to
    the R ceiling, leaves in-bounds plans untouched, never raises, never mutates.
"""
from __future__ import annotations

import pytest

from src.runtime.exit_plan import (
    EXIT_PLAN_VERSION,
    build_exit_plan_from_legacy,
    validate_exit_plan,
)
from src.runtime.exit_plan_realism import (
    DEFAULT_MAX_REACH_R,
    clamp_exit_plan,
    reach_r,
)


def _valid_plan(**overrides):
    plan = {
        "version": EXIT_PLAN_VERSION,
        "rungs": [{"price": 110.0, "qty_pct": 0.25}],
        "final": {"kind": "fixed", "price": 120.0},
        "stop": {"price": 95.0},
        "trailing_stop": None,
        "time_decay_minutes": None,
        "meta": {"source": "t"},
    }
    plan.update(overrides)
    return plan


# ---------------------------------------------------------------------------
# validate_exit_plan
# ---------------------------------------------------------------------------

def test_valid_plan_passes():
    ok, reason = validate_exit_plan(_valid_plan())
    assert ok, reason


def test_empty_ladder_single_target_is_valid():
    ok, reason = validate_exit_plan(_valid_plan(rungs=[]))
    assert ok, reason


def test_trailing_final_and_trailing_stop_valid():
    plan = _valid_plan(
        final={"kind": "trailing", "trail_r": 1.0, "activate_r": 1.0, "floor": "breakeven"},
        trailing_stop={"activate_r": 1.0, "trail_kind": "be", "param": 0.0},
        time_decay_minutes=240.0,
    )
    ok, reason = validate_exit_plan(plan)
    assert ok, reason


@pytest.mark.parametrize("bad", [
    None,
    42,
    "x",
    {},
    {"version": 999, "rungs": [], "final": {"kind": "fixed", "price": 1}, "stop": {"price": 1}},
])
def test_non_dicts_and_wrong_version_rejected(bad):
    ok, _ = validate_exit_plan(bad)
    assert ok is False


def test_cumulative_qty_over_one_rejected():
    plan = _valid_plan(rungs=[{"price": 110.0, "qty_pct": 0.6},
                              {"price": 115.0, "qty_pct": 0.6}])
    ok, reason = validate_exit_plan(plan)
    assert ok is False and "cumulative" in reason


def test_cumulative_qty_exactly_one_ok():
    plan = _valid_plan(rungs=[{"price": 110.0, "qty_pct": 0.25},
                              {"price": 115.0, "qty_pct": 0.25},
                              {"price": 118.0, "qty_pct": 0.5}])
    ok, reason = validate_exit_plan(plan)
    assert ok, reason


@pytest.mark.parametrize("rung", [
    {"price": -1, "qty_pct": 0.5},
    {"price": 110.0, "qty_pct": 0},
    {"price": 110.0, "qty_pct": 1.5},
    {"price": 110.0, "qty_pct": True},
    {"qty_pct": 0.5},
])
def test_bad_rung_rejected(rung):
    ok, _ = validate_exit_plan(_valid_plan(rungs=[rung]))
    assert ok is False


def test_bad_stop_rejected():
    assert validate_exit_plan(_valid_plan(stop={"price": -1}))[0] is False
    assert validate_exit_plan(_valid_plan(stop={}))[0] is False


def test_bad_final_kind_rejected():
    assert validate_exit_plan(_valid_plan(final={"kind": "wat", "price": 1}))[0] is False


# ---------------------------------------------------------------------------
# build_exit_plan_from_legacy
# ---------------------------------------------------------------------------

def test_legacy_ladder_round_trip():
    """A package with a distinct meta.tp2 → two-leg ladder (TP1 partial, run to
    TP2), mirroring turtle_soup's monitor exit. Plan must validate."""
    pkg = {
        "strategy_name": "turtle_soup",
        "entry": 100.0, "sl": 95.0, "tp": 110.0,
        "meta": {"tp2": 120.0},
    }
    plan = build_exit_plan_from_legacy(pkg, {"partial_close_pct": 0.25})
    ok, reason = validate_exit_plan(plan)
    assert ok, reason
    assert plan["rungs"] == [{"price": 110.0, "qty_pct": 0.25}]
    assert plan["final"] == {"kind": "fixed", "price": 120.0}
    assert plan["stop"] == {"price": 95.0}


def test_legacy_single_target_when_no_tp2():
    pkg = {"strategy_name": "vwap", "entry": 100.0, "sl": 95.0, "tp": 110.0}
    plan = build_exit_plan_from_legacy(pkg)
    ok, reason = validate_exit_plan(plan)
    assert ok, reason
    assert plan["rungs"] == []
    assert plan["final"] == {"kind": "fixed", "price": 110.0}


def test_legacy_be_trailing_from_cfg():
    pkg = {"strategy_name": "x", "entry": 100.0, "sl": 95.0, "tp": 110.0}
    plan = build_exit_plan_from_legacy(pkg, {"be_at_r": 1.0})
    assert plan["trailing_stop"] == {"activate_r": 1.0, "trail_kind": "be", "param": 0.0}


def test_legacy_accepts_field_synonyms():
    pkg = {"entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0}
    plan = build_exit_plan_from_legacy(pkg)
    assert plan is not None and plan["stop"]["price"] == 95.0


def test_legacy_meta_as_json_string():
    pkg = {"entry": 100.0, "sl": 95.0, "tp": 110.0, "meta": '{"tp2": 120.0}'}
    plan = build_exit_plan_from_legacy(pkg, {})
    assert plan["final"] == {"kind": "fixed", "price": 120.0}


@pytest.mark.parametrize("pkg", [
    None, 42, {}, {"entry": 100.0}, {"sl": 95.0, "tp": 0}, {"sl": 0, "tp": 110.0},
])
def test_legacy_returns_none_on_unusable_input(pkg):
    assert build_exit_plan_from_legacy(pkg) is None


def test_legacy_never_raises_on_garbage_meta():
    pkg = {"entry": 100.0, "sl": 95.0, "tp": 110.0, "meta": "not json{{"}
    plan = build_exit_plan_from_legacy(pkg, {})
    assert plan is not None and plan["rungs"] == []  # tp2 unresolved → single target


# ---------------------------------------------------------------------------
# realism guard
# ---------------------------------------------------------------------------

def test_reach_r_long_and_short():
    assert reach_r(110.0, entry=100.0, stop=95.0, direction="long") == pytest.approx(2.0)
    assert reach_r(90.0, entry=100.0, stop=105.0, direction="short") == pytest.approx(2.0)
    assert reach_r(110.0, entry=100.0, stop=100.0, direction="long") is None  # zero risk
    assert reach_r("x", entry=100.0, stop=95.0, direction="long") is None


def test_clamp_leaves_in_bounds_plan_untouched():
    plan = _valid_plan()  # final at 120 = 4R on entry100/sl95 risk 5 → under 5R
    out, notes = clamp_exit_plan(plan, direction="long", entry=100.0, stop=95.0)
    assert notes == []
    assert out is plan  # same object, no copy made


def test_clamp_pulls_far_target_to_ceiling():
    # risk = 5; ceiling 5R → max price 125. Final at 200 = 20R must clamp to 125.
    plan = _valid_plan(rungs=[], final={"kind": "fixed", "price": 200.0})
    out, notes = clamp_exit_plan(plan, direction="long", entry=100.0, stop=95.0,
                                 max_reach_r=5.0)
    assert out is not plan  # copy made
    assert out["final"]["price"] == pytest.approx(125.0)
    assert plan["final"]["price"] == 200.0  # original not mutated
    assert len(notes) == 1 and notes[0]["target"] == "final"
    assert validate_exit_plan(out)[0]


def test_clamp_far_rung_short():
    # short: entry 100, stop 105, risk 5, ceiling 5R → min price 75.
    plan = _valid_plan(rungs=[{"price": 10.0, "qty_pct": 0.5}],
                       final={"kind": "fixed", "price": 70.0}, stop={"price": 105.0})
    out, notes = clamp_exit_plan(plan, direction="short", entry=100.0, stop=105.0,
                                 max_reach_r=5.0)
    assert out["rungs"][0]["price"] == pytest.approx(75.0)
    assert out["final"]["price"] == pytest.approx(75.0)
    assert {n["target"] for n in notes} == {"rung[0]", "final"}


def test_clamp_never_raises_on_garbage():
    out, notes = clamp_exit_plan("nope", direction=None, entry=None, stop=None)
    assert out == "nope" and notes == []


def test_default_ceiling_is_generous():
    assert DEFAULT_MAX_REACH_R >= 5.0
