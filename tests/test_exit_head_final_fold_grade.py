"""RQ-20260929-401's registered partition, pinned BEFORE the run."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "exit_head_final_fold_grade", REPO / "scripts/research/exit_head_final_fold_grade.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


def _rep(year=2026, n=80, actual=1.0, head=2.0, mode="years", arm=G.ARM):
    fold = {"year": year, "n_trades": n, "train_rows": 999, "auc": 0.55,
            "actual": {"trades": n, "net_r": actual},
            "model_cond": {arm: {"trades": n, "net_r": head}}}
    return {"fold_mode": mode, "folds": [{**fold, "year": 2025}, fold]}


def _s(**kw):
    return G.leg_stat(_rep(**kw), "leg", 2026)


def test_recovered_is_head_minus_baseline_on_same_trades():
    s = _s(actual=1.5, head=0.25)
    assert s["state"] == "ok" and s["recovered_r_oos"] == -1.25 and s["n_oos"] == 80


def test_missing_final_year_never_borrows_an_earlier_fold():
    rep = _rep()
    rep["folds"] = [f for f in rep["folds"] if f["year"] != 2026]
    s = G.leg_stat(rep, "leg", 2026)
    assert s["state"] == "final_fold_missing" and s["folds_present"] == [2025]


def test_wrong_fold_mode_and_wrong_arm_and_trade_mismatch_are_not_graded():
    assert _s(mode="trades")["state"] == "wrong_fold_mode"
    assert _s(arm="tau_0.1")["state"] == "malformed_fold"     # only the registered arm counts
    rep = _rep()
    rep["folds"][-1]["model_cond"][G.ARM]["trades"] = 79       # unpaired arms
    assert G.leg_stat(rep, "leg", 2026)["state"] == "malformed_fold"


def _ok(n, rec):
    return {"state": "ok", "n_oos": n, "recovered_r_oos": rec, "leg": "x"}


def test_partition_pass_refine_fail_and_floor():
    assert G.grade([_ok(100, 1), _ok(98, .1), _ok(84, -3)])["verdict"] == "pass"
    assert G.grade([_ok(100, 1), _ok(98, -.1), _ok(84, -3)])["verdict"] == "indeterminate"   # refine
    assert G.grade([_ok(100, -1), _ok(98, 0.0), _ok(84, -3)])["verdict"] == "fail"           # 0.0 is not > 0
    # a positive leg below the floor is excluded, and <2 graded legs is underpowered
    g = G.grade([_ok(63, 9), _ok(100, 1), _ok(98, -1)])
    assert g["verdict"] == "indeterminate" and g["n_legs_graded"] == 2 and g["n_legs_positive"] == 1
    g = G.grade([_ok(63, 9), _ok(62, 9), _ok(100, 1)])
    assert g["verdict"] == "indeterminate" and "underpowered" in g["why"]
    assert G.grade([{"state": "final_fold_missing"}, _ok(100, 1), _ok(90, 1)])["verdict"] == "pass"
