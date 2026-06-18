"""Tests for the strategy readiness-tier classifier (2026-06-18).

Canonical criteria: docs/strategy-readiness-ladder.md. The classifier turns a
k-fold fold-report into reject / paper_ready / live_ready so the gate stops
discarding genuine-but-not-yet-robust edges.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "classify_strategy_tier.py"
_spec = importlib.util.spec_from_file_location("classify_strategy_tier", _MOD_PATH)
assert _spec and _spec.loader
cst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cst)
classify_tier = cst.classify_tier


def _report(total, double, all_folds_pos, fold_nets):
    return {
        "total_oos_net_r_base": total,
        "total_oos_net_r_double": double,
        "gate_2x_fee_headroom": (double is not None and double > 0),
        "gate_all_folds_positive": all_folds_pos,
        "folds": [{"net_r": x} for x in fold_nets],
    }


def test_live_ready_every_fold_positive():
    r = _report(30.0, 25.0, True, [5.0, 8.0, 6.0, 4.0, 7.0])
    assert classify_tier(r)["tier"] == "live_ready"


def test_paper_ready_net_positive_one_negative_fold():
    # The real WS-C alt shape: net +26.9R, 2x headroom, one recent fold negative.
    r = _report(26.89, 24.79, False, [10.0, 9.0, 8.0, 5.5, -5.59])
    res = classify_tier(r)
    assert res["tier"] == "paper_ready", res
    assert "not yet fold-robust" in res["reasons"][0]


def test_real_fold_report_shape_scalar_folds_does_not_shadow_list():
    """Regression (2026-06-18): a real m15_ws_b_fold_report.py report carries a
    scalar ``folds`` (the fold *count*) alongside the per-fold ``folds_base_fee``
    list. The classifier must read the list, not iterate the int — the scalar
    used to shadow it and raise TypeError (swallowed by the report's bare
    except), silently voiding the tier stamp on every real report."""
    r = {
        "total_oos_net_r_base": 26.89,
        "total_oos_net_r_double": 24.79,
        "gate_2x_fee_headroom": True,
        "gate_all_folds_positive": False,
        "folds": 5,  # scalar count — must NOT be iterated
        "folds_base_fee": [{"net_r": x} for x in (10.0, 9.0, 8.0, 5.5, -5.59)],
    }
    res = classify_tier(r)  # must not raise
    assert res["tier"] == "paper_ready", res
    assert res["metrics"]["worst_fold_net_r"] == -5.59
    assert res["metrics"]["n_folds"] == 5


def test_reject_net_negative():
    r = _report(-14.0, -28.0, False, [-2.0, -3.0, -9.0])
    assert classify_tier(r)["tier"] == "reject"


def test_reject_fee_bleed_positive_base_negative_double():
    # vwap failure mode: positive-ish at 7.5 bps but negative once 2x fees bite.
    r = _report(3.0, -4.0, False, [2.0, -1.0, 2.0])
    res = classify_tier(r)
    assert res["tier"] == "reject"
    assert "fee-bleed" in res["reasons"][0]


def test_reject_catastrophic_fold_despite_positive_total():
    # Net +4R overall but a single fold loses more than the whole edge → fragile.
    r = _report(4.0, 3.0, False, [12.0, 9.0, -18.0])
    res = classify_tier(r)
    assert res["tier"] == "reject"
    assert "catastrophic" in res["reasons"][0]


def test_paper_ready_small_total_uses_absolute_floor():
    # Small +0.5R total: an ordinary -2R fold is within the 3R absolute floor,
    # so it stays paper_ready rather than being disqualified by fold noise.
    r = _report(0.5, 0.2, False, [1.5, -2.0, 1.0])
    assert classify_tier(r)["tier"] == "paper_ready"


def test_headroom_derived_when_gate_absent():
    r = {
        "total_oos_net_r_base": 10.0,
        "total_oos_net_r_double": 6.0,
        "gate_all_folds_positive": False,
        "folds": [{"net_r": 4.0}, {"net_r": -1.0}, {"net_r": 7.0}],
    }
    assert classify_tier(r)["tier"] == "paper_ready"


def test_missing_total_is_reject():
    assert classify_tier({"folds": []})["tier"] == "reject"
