"""Per-leg verdict cut in the M20 exit-head trainer.

WHY THESE EXIST. `train_exit_head.py` trains and evaluates per FAMILY — one E0
dir pools every symbol in it. That is correct for training (it is what breaks
the label-count wall the exit-head program doc describes) and wrong as a
verdict unit, because `docs/research/exit-refinement-coverage.json` carries one
row per LEG.

Writing a pooled family verdict into each of that family's leg rows is
`BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS` — the bug the matrix rows
were exploded per-leg to kill, reappearing one layer up where it is harder to
see. `per_leg_summary` is the guard against that, so it needs tests that fail
when the guard stops guarding.

Each test below was verified against a planted defect: the assertion was
confirmed to FAIL when the behaviour it names is removed. A test that passes
against a broken implementation is worse than no test.
"""
from __future__ import annotations

import copy
import json
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

np = pytest.importorskip("numpy")


def _load():
    spec = importlib.util.spec_from_file_location(
        "train_exit_head", REPO / "scripts" / "ml" / "train_exit_head.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


teh = _load()


def block(n, auc, best_net, best_dd, actual_net, actual_dd, hard_net):
    """One leg's block inside one fold, in `eval_split`'s shape."""
    return {
        "n_trades": n, "auc": auc,
        "actual": {"net_r": actual_net, "max_dd_r": actual_dd},
        "stale_8_0": {"net_r": hard_net, "max_dd_r": 5.0},
        "giveback_1_1": {"net_r": hard_net - 1, "max_dd_r": 5.0},
        "model": {"tau_0.10": {"net_r": best_net, "max_dd_r": best_dd}},
    }


@pytest.fixture()
def folds():
    """Two folds, three legs with deliberately different truths.

    legA fat + head wins · legB fat + head loses · legC thin + head "wins".
    Pooled, these are one number (146 OOS trades, mixed) — which is precisely
    the conflation under test.
    """
    return [
        {"year": year, "per_leg": {
            "legA": block(30, 0.62, 10.0, 4.0, 5.0, 6.0, 7.0),
            "legB": block(40, 0.51, 2.0, 9.0, 8.0, 4.0, 7.0),
            "legC": block(3, 0.70, 9.0, 1.0, 1.0, 5.0, 2.0),
        }}
        for year in (2024, 2025)
    ]


def test_the_floor_is_single_homed_to_the_fleet_sweep():
    """The floor must be IMPORTED, not mirrored.

    Two copies of a threshold governing one matrix is how they drift apart.
    """
    from m20_fleet_exit_sweep import MIN_OOS_TRADES as sweep_floor
    assert teh.MIN_OOS_TRADES == sweep_floor == 25


def test_each_leg_gets_its_own_verdict_not_the_family_number(folds):
    s = teh.per_leg_summary(folds, teh.MIN_OOS_TRADES)
    assert s["legA"]["verdict"] == "candidate"
    assert s["legB"]["verdict"] == "honest_negative"
    # Three legs, three different verdicts, from ONE pooled model+fold set.
    assert len({s[leg]["verdict"] for leg in s}) == 3


def test_a_thin_leg_is_insufficient_base_and_not_a_negative(folds):
    """`insufficient_base` must stay distinct from `honest_negative`.

    Folding a too-thin book into the failure bucket makes "we could not judge"
    indistinguishable from "we judged and the lever lost" — opposite claims.
    """
    s = teh.per_leg_summary(folds, teh.MIN_OOS_TRADES)
    legc = s["legC"]
    assert legc["oos_trades"] == 6 < teh.MIN_OOS_TRADES
    assert legc["verdict"] == "insufficient_base"
    assert legc["verdict"] != "honest_negative"
    # The counterfactual keeps the floor's effect auditable.
    assert legc["would_have_been"] == "candidate"
    assert str(teh.MIN_OOS_TRADES) in legc["insufficient_base_why"]


def test_an_unimportable_floor_withholds_verdicts_rather_than_inventing_one(folds):
    """floor=None is a THIRD state, not a default.

    Grading against a locally invented number would silently produce verdicts
    under a threshold no operator set.
    """
    s = teh.per_leg_summary(folds, None)
    assert {v["verdict"] for v in s.values()} == {"ungraded_no_floor"}
    assert all("would_have_been" not in v for v in s.values())
    assert all(v["min_oos_trades_floor"] is None for v in s.values())


def test_a_leg_absent_from_a_fold_reduces_usable_folds_rather_than_losing_it(folds):
    """A leg that did not trade in a fold cannot vote either way.

    Counting its absence as a loss would make a leg that traded in one fold
    look like a leg that failed in two.
    """
    f = copy.deepcopy(folds)
    del f[0]["per_leg"]["legA"]
    s = teh.per_leg_summary(f, teh.MIN_OOS_TRADES)
    assert s["legA"]["usable_folds"] == 1
    assert s["legA"]["beats_actual_folds"] == 1


def test_worse_drawdown_fails_even_when_net_r_improves(folds):
    """net_R alone is not the gate — the maxDD clause is load-bearing.

    Dropping it is how a lever that buys return with risk ships looking clean.
    """
    f = copy.deepcopy(folds)
    for fold in f:
        fold["per_leg"]["legA"]["model"]["tau_0.10"]["max_dd_r"] = 99.0
    s = teh.per_leg_summary(f, teh.MIN_OOS_TRADES)
    assert s["legA"]["verdict"] == "honest_negative"


def test_beating_actual_but_not_the_hard_lever_is_not_a_candidate(folds):
    """The gate is vs the best HARD rule, not vs doing nothing.

    A head that only beats the un-levered book has not earned a place over the
    stale/giveback stop that already exists.
    """
    f = copy.deepcopy(folds)
    for fold in f:
        fold["per_leg"]["legA"]["stale_8_0"]["net_r"] = 50.0
    s = teh.per_leg_summary(f, teh.MIN_OOS_TRADES)
    assert s["legA"]["beats_hard_folds"] == 0
    assert s["legA"]["verdict"] == "honest_negative"


def test_split_by_leg_partitions_on_the_strategy_field():
    trades = {
        "t1": [{"strategy": "ict_scalp_sol_15m", "age_bars": 0}],
        "t2": [{"strategy": "ict_scalp_xrp_15m", "age_bars": 0}],
        "t3": [{"strategy": "ict_scalp_sol_15m", "age_bars": 0}],
    }
    out = teh.split_by_leg(trades)
    assert set(out) == {"ict_scalp_sol_15m", "ict_scalp_xrp_15m"}
    assert set(out["ict_scalp_sol_15m"]) == {"t1", "t3"}


# --------------------------------------------------------------- vintage cut
#
# The coverage headline is computed over a population whose verdicts were
# largely measured against a TP geometry production does not run
# (BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP). These pin the
# caveat's scoping, because a caveat that over-claims becomes alarm fatigue and
# one that under-claims hides the defect.

def _rollup():
    spec = importlib.util.spec_from_file_location(
        "m20_coverage_rollup",
        REPO / "scripts" / "research" / "m20_coverage_rollup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rollup = _rollup()


def _matrix(rows):
    return {"lever_columns": ["stale_stop"], "legend": {}, "rows": rows}


def _row(strategy, status="honest_negative", ref="sweep 2026-07-12"):
    return {"strategy": strategy, "symbol": "X", "tf": "1h",
            "execution": "live", "stale_stop": {"status": status, "ref": ref}}


def test_the_scalp_family_is_not_swept_into_the_geometry_caveat():
    """ict_scalp's harness MODELS its TP, so its verdicts are not stale.

    Blanket-flagging every family would make the caveat unusable — the
    desensitized-alarm failure the repo treats as its own P1.
    """
    v = rollup.evidence_vintage(_matrix([_row("ict_scalp_sol_15m")]))
    assert v["classifier_available"]
    assert v["affected_legs"] == 0 and v["clean_legs"] == 1
    assert v["pre_cutover"] == 0


def test_an_affected_family_leg_with_old_evidence_is_counted_stale():
    v = rollup.evidence_vintage(_matrix([_row("trend_donchian_eth_4h")]))
    assert v["affected_legs"] == 1
    assert v["pre_cutover"] == 1
    assert v["stale_cells"][0][4] == "2026-07-12"


def test_evidence_at_or_after_the_cutover_is_not_stale():
    v = rollup.evidence_vintage(
        _matrix([_row("trend_donchian_eth_4h", ref="re-swept 2026-08-11")]))
    assert v["pre_cutover"] == 0 and v["post_cutover"] == 1


def test_an_undated_ref_is_its_own_bucket_not_silently_clean():
    """No date is 'we cannot tell', which is not the same as 'current'."""
    v = rollup.evidence_vintage(
        _matrix([_row("trend_donchian_eth_4h", ref="no date here")]))
    assert v["undated"] == 1
    assert v["pre_cutover"] == 0 and v["post_cutover"] == 0


def test_an_open_cell_is_not_counted_as_stale_evidence():
    """A pending cell owes a measurement regardless — counting it as stale
    would double-count it against the done-condition."""
    v = rollup.evidence_vintage(
        _matrix([_row("trend_donchian_eth_4h", status="pending", ref="x 2026-07-12")]))
    assert v["pre_cutover"] == 0 and v["undated"] == 0


def test_the_vintage_denominator_equals_the_declared_lever_set():
    """Guards the comment's claim that the lever filter currently filters
    nothing — if a lever is dropped from the sensitive set, the caveat's
    denominator silently shrinks and the staleness reads better than it is."""
    matrix = json.loads(
        (REPO / "docs" / "research" / "exit-refinement-coverage.json").read_text())
    assert set(matrix["lever_columns"]) <= rollup.GEOMETRY_SENSITIVE_LEVERS
