"""Unit tests for the ExitPlan materializer (P2, observe-only).

Proves ``materialize_exit_plan`` translates a static ExitPlan into concrete,
ordered, direction-resolved exit instructions — and, like its P1 siblings,
never raises and returns ``None`` rather than a malformed structure on bad
input.
"""
from __future__ import annotations

import json

import pytest

from src.runtime.exit_plan import EXIT_PLAN_VERSION, build_exit_plan_from_legacy
from src.runtime.exit_plan_materializer import (
    MATERIALIZED_EXIT_VERSION,
    materialize_exit_plan,
)


def _plan(rungs, final, stop=90.0, trailing_stop=None, time_decay_minutes=None):
    return {
        "version": EXIT_PLAN_VERSION,
        "rungs": rungs,
        "final": final,
        "stop": {"price": stop},
        "trailing_stop": trailing_stop,
        "time_decay_minutes": time_decay_minutes,
    }


# --------------------------------------------------------------------------- #
# Happy path — single fixed target
# --------------------------------------------------------------------------- #

def test_single_fixed_long():
    plan = _plan([], {"kind": "fixed", "price": 110.0})
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=90.0)
    assert out is not None
    assert out["version"] == MATERIALIZED_EXIT_VERSION
    assert out["direction"] == "long"
    assert out["risk"] == 10.0
    assert len(out["targets"]) == 1
    t = out["targets"][0]
    assert t["kind"] == "final"
    assert t["price"] == 110.0
    assert t["qty_pct"] == 1.0
    assert t["qty"] == 1.0          # fractional (qty_total defaults to 1.0)
    assert t["reach_r"] == 1.0
    assert out["stop"] == {"price": 90.0, "qty": 1.0}
    assert out["residual_qty"] == pytest.approx(0.0)


def test_single_fixed_short_reach_is_positive_below_entry():
    plan = _plan([], {"kind": "fixed", "price": 90.0}, stop=110.0)
    out = materialize_exit_plan(plan, direction="short", entry=100.0, stop=110.0)
    assert out is not None
    assert out["direction"] == "short"
    assert out["targets"][0]["price"] == 90.0
    assert out["targets"][0]["reach_r"] == 1.0   # profit-direction reach


# --------------------------------------------------------------------------- #
# Ladder — turtle TP1/TP2 generalization
# --------------------------------------------------------------------------- #

def test_two_rung_ladder_orders_near_to_far_long():
    plan = _plan(
        [{"price": 110.0, "qty_pct": 0.25}],
        {"kind": "fixed", "price": 120.0},
    )
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=90.0)
    assert out is not None
    prices = [t["price"] for t in out["targets"]]
    assert prices == [110.0, 120.0]               # near → far
    kinds = [t["kind"] for t in out["targets"]]
    assert kinds == ["rung", "final"]
    # rung banks 25%, final takes the 75% remainder
    assert out["targets"][0]["qty_pct"] == 0.25
    assert out["targets"][1]["qty_pct"] == pytest.approx(0.75)
    assert out["residual_qty"] == pytest.approx(0.0)


def test_ladder_orders_far_to_near_for_short():
    # short: profit is downward, so "near" is the higher price
    plan = _plan(
        [{"price": 90.0, "qty_pct": 0.5}],
        {"kind": "fixed", "price": 80.0},
        stop=110.0,
    )
    out = materialize_exit_plan(plan, direction="short", entry=100.0, stop=110.0)
    assert out is not None
    prices = [t["price"] for t in out["targets"]]
    assert prices == [90.0, 80.0]                 # near (90) before far (80)


def test_legacy_derived_plan_round_trips_through_materializer():
    # turtle_soup TP1/TP2 via the P1 derivation → 2-rung ladder → materialized
    plan = build_exit_plan_from_legacy({
        "strategy_name": "turtle_soup",
        "entry": 100.0, "sl": 90.0, "tp": 110.0,
        "meta": {"tp2": 120.0},
    })
    assert plan is not None
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=90.0)
    assert out is not None
    assert [t["price"] for t in out["targets"]] == [110.0, 120.0]


# --------------------------------------------------------------------------- #
# Trailing final — no fixed price, residual rides the trail
# --------------------------------------------------------------------------- #

def test_trailing_final_surfaces_rule_and_residual():
    plan = _plan(
        [{"price": 110.0, "qty_pct": 0.4}],
        {"kind": "trailing", "trail_r": 1.5, "activate_r": 1.0, "floor": "breakeven"},
    )
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=90.0)
    assert out is not None
    # only the rung is a resting fixed target
    assert [t["kind"] for t in out["targets"]] == ["rung"]
    assert out["final_trailing"] == {
        "trail_r": 1.5, "activate_r": 1.0, "floor": "breakeven",
    }
    # 60% of the position rides the trailing final
    assert out["residual_qty"] == pytest.approx(0.6)


# --------------------------------------------------------------------------- #
# Realism clamp is applied before resting
# --------------------------------------------------------------------------- #

def test_fantasy_target_is_clamped_to_reach_ceiling():
    # final at 200 = 10R away (entry 100, risk 10) — beyond the 5R ceiling
    plan = _plan([], {"kind": "fixed", "price": 200.0})
    out = materialize_exit_plan(
        plan, direction="long", entry=100.0, stop=90.0, max_reach_r=5.0,
    )
    assert out is not None
    assert out["targets"][0]["price"] == 150.0    # clamped to 5R = 100 + 5*10
    assert out["targets"][0]["reach_r"] == 5.0
    assert out["realism_notes"]                    # the clamp was recorded


# --------------------------------------------------------------------------- #
# Absolute (per-account) materialization + lot rounding
# --------------------------------------------------------------------------- #

def test_absolute_qty_with_lot_step_floors_each_order():
    plan = _plan(
        [{"price": 110.0, "qty_pct": 0.33}],
        {"kind": "fixed", "price": 120.0},
    )
    out = materialize_exit_plan(
        plan, direction="long", entry=100.0, stop=90.0,
        qty_total=10.0, qty_step=1.0,
    )
    assert out is not None
    # 0.33 * 10 = 3.3 → floored to 3 (reduce-only never over-closes)
    assert out["targets"][0]["qty"] == 3.0
    # final remainder 0.67 * 10 = 6.7 → floored to 6
    assert out["targets"][1]["qty"] == 6.0
    assert out["stop"]["qty"] == 10.0
    assert out["fractional"] is False


def test_qty_underflow_drops_the_order_with_a_note():
    # tiny rung pct against a coarse lot step → floors to 0 → dropped
    plan = _plan(
        [{"price": 110.0, "qty_pct": 0.05}],
        {"kind": "fixed", "price": 120.0},
    )
    out = materialize_exit_plan(
        plan, direction="long", entry=100.0, stop=90.0,
        qty_total=1.0, qty_step=1.0,
    )
    assert out is not None
    assert all(t["kind"] != "rung" for t in out["targets"])   # rung dropped
    assert any(n.get("reason") == "qty_underflow" for n in out["notes"])


# --------------------------------------------------------------------------- #
# Stop handling
# --------------------------------------------------------------------------- #

def test_stop_falls_back_to_plan_stop_when_arg_omitted():
    plan = _plan([], {"kind": "fixed", "price": 110.0}, stop=88.0)
    out = materialize_exit_plan(plan, direction="long", entry=100.0)
    assert out is not None
    assert out["stop"]["price"] == 88.0
    assert out["risk"] == 12.0


def test_explicit_stop_arg_overrides_plan_stop():
    plan = _plan([], {"kind": "fixed", "price": 110.0}, stop=88.0)
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=95.0)
    assert out is not None
    assert out["stop"]["price"] == 95.0           # ratcheted stop wins
    assert out["risk"] == 5.0


def test_trailing_stop_passes_through():
    ts = {"activate_r": 1.0, "trail_kind": "be", "param": 0.0}
    plan = _plan([], {"kind": "fixed", "price": 110.0}, trailing_stop=ts)
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=90.0)
    assert out is not None
    assert out["trailing_stop"] == ts


# --------------------------------------------------------------------------- #
# Rejection / robustness — None, never raises
# --------------------------------------------------------------------------- #

def test_invalid_plan_returns_none():
    assert materialize_exit_plan({"version": 999}, direction="long", entry=100.0, stop=90.0) is None
    assert materialize_exit_plan(None, direction="long", entry=100.0, stop=90.0) is None


def test_unknown_direction_returns_none():
    plan = _plan([], {"kind": "fixed", "price": 110.0})
    assert materialize_exit_plan(plan, direction="sideways", entry=100.0, stop=90.0) is None


def test_zero_risk_returns_none():
    plan = _plan([], {"kind": "fixed", "price": 110.0}, stop=100.0)
    assert materialize_exit_plan(plan, direction="long", entry=100.0, stop=100.0) is None


def test_missing_entry_returns_none():
    plan = _plan([], {"kind": "fixed", "price": 110.0})
    assert materialize_exit_plan(plan, direction="long", entry=None, stop=90.0) is None


@pytest.mark.parametrize("garbage", [
    "not a plan", 42, [], {"rungs": object()},
    {"version": 1, "rungs": "x", "final": {}, "stop": {}},
])
def test_never_raises_on_garbage(garbage):
    # returns None, never raises
    assert materialize_exit_plan(garbage, direction="long", entry=100.0, stop=90.0) is None


def test_result_is_json_serializable():
    plan = _plan(
        [{"price": 110.0, "qty_pct": 0.25}],
        {"kind": "fixed", "price": 120.0},
    )
    out = materialize_exit_plan(plan, direction="long", entry=100.0, stop=90.0, as_of="2026-06-17T20:00:00Z")
    assert out is not None
    # round-trips cleanly (no non-serializable objects leaked in)
    again = json.loads(json.dumps(out))
    assert again["as_of"] == "2026-06-17T20:00:00Z"
    assert again["targets"][0]["price"] == 110.0
