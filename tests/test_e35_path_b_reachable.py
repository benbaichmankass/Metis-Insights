"""Path B must be REACHABLE in the e35 bracket sweep.

`BL-20260823-E35-PATH-B-UNREACHABLE-RAW-RUNCELL-DICT`. The gate passed the raw
`run_cell` output where `fleet.is_path_b_candidate` reads
`d_net_r_per_capital_day` -- a key `run_cell` never emits. `.get` returned
None, `_up(None)` is False, and the predicate answered **False for every cell
ever gated**, whatever its numbers. Every such cell short-circuited to
`is_oos_fail` before any walk-forward ran, so the population a drawdown
tolerance would be argued from was never generated.

A unit test on the predicate alone cannot catch this -- the predicate was
always correct. The defect is the SHAPE of the argument the caller builds, so
these tests assert on the caller's contract:

  1. the key the predicate reads is absent from a `run_cell`-shaped dict
     (the premise; if this ever becomes false the bug is impossible and this
     file should be revisited rather than deleted);
  2. `capital_delta` produces that key, so the fixed call site can pass it;
  3. the predicate says True on real both-halves-better numbers when given a
     `capital_delta` dict and False when given a raw one -- the regression
     itself;
  4. the gate source passes `capital_delta`, not a bare `run_cell` result.

A positive control runs in every case: a test that can only ever fail is not
evidence that the thing works.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLEET = ROOT / "scripts" / "research" / "m20_fleet_exit_sweep.py"
E35 = ROOT / "scripts" / "research" / "e35_bracket_geometry_sweep.py"

# trend_donchian `sm2`, measured 2026-08-23: net_R up in BOTH halves, which is
# exactly the Path B population. Real numbers, so the test fails if the
# predicate's own thresholds ever move under it.
G_IS = {"d_net_r": 15.0732}
G_OOS = {"d_net_r": 3.8773}


@pytest.fixture(scope="module")
def fleet():
    spec = importlib.util.spec_from_file_location("_fleet", FLEET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cell_shaped() -> dict:
    """A dict with the keys `run_cell` actually returns (rate, not delta)."""
    return {"net_total_r": 51.06, "max_drawdown_r": 25.23, "total_trades": 292,
            "net_r_per_capital_day": 0.0512, "net_r_per_position_day": 0.0731,
            "mean_bars_held": 41.2, "capital_days": 997.0}


def test_run_cell_shape_lacks_the_delta_key_the_predicate_reads():
    """The premise of the bug: the rate is emitted, the DELTA is not."""
    d = _run_cell_shaped()
    assert "net_r_per_capital_day" in d
    assert "d_net_r_per_capital_day" not in d


def test_capital_delta_supplies_the_key(fleet):
    cap = fleet.capital_delta(_run_cell_shaped(),
                              {**_run_cell_shaped(), "net_r_per_capital_day": 0.04})
    assert "d_net_r_per_capital_day" in cap
    assert cap["d_net_r_per_capital_day"] is not None


def test_predicate_is_reachable_with_a_capital_delta_dict(fleet):
    """POSITIVE CONTROL. If this fails, the rest proves nothing."""
    cap = fleet.capital_delta(_run_cell_shaped(),
                              {**_run_cell_shaped(), "net_r_per_capital_day": 0.04})
    assert fleet.is_path_b_candidate(G_IS, G_OOS, cap) is True


def test_predicate_is_unreachable_with_a_raw_run_cell_dict(fleet):
    """The regression itself: the same numbers, the wrong argument shape."""
    assert fleet.is_path_b_candidate(G_IS, G_OOS, _run_cell_shaped()) is False


def test_unmeasurable_rate_still_fails_and_is_not_fabricated_as_zero(fleet):
    """'We could not look' must not become 'the rate did not improve' via 0.0."""
    cap = fleet.capital_delta({**_run_cell_shaped(), "net_r_per_capital_day": None},
                              _run_cell_shaped())
    assert cap["d_net_r_per_capital_day"] is None
    assert fleet.is_path_b_candidate(G_IS, G_OOS, cap) is False


def test_e35_gate_passes_capital_delta_not_a_raw_run_cell():
    """Source-level: the call site must not regress to the raw dict.

    Parsed, not grepped -- a comment mentioning `c_oos` must not satisfy it.
    """
    tree = ast.parse(E35.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "is_path_b_candidate"]
    assert calls, "no is_path_b_candidate call found in the e35 gate"
    for call in calls:
        third = call.args[2]
        # Accept a Name bound to capital_delta(...) or the call inline; refuse
        # a bare run_cell result.
        if isinstance(third, ast.Call):
            assert getattr(third.func, "attr", None) == "capital_delta"
        else:
            assert isinstance(third, ast.Name), ast.dump(third)
            assert third.id != "c_oos", (
                "is_path_b_candidate is being passed the raw run_cell dict "
                "again -- Path B is unreachable "
                "(BL-20260823-E35-PATH-B-UNREACHABLE-RAW-RUNCELL-DICT)")
            # the bound name must come from a capital_delta call
            assigns = [n for n in ast.walk(tree)
                       if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == third.id
                               for t in n.targets)]
            assert assigns, f"{third.id} is never assigned"
            assert any(isinstance(a.value, ast.Call)
                       and getattr(a.value.func, "attr", None) == "capital_delta"
                       for a in assigns), (
                f"{third.id} is not bound to a capital_delta(...) result")
