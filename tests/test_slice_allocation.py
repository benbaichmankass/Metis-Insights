"""NETTED-SLICES: the allocation score is the EXISTING conviction blend, extended.

`allocator_ev.intent_allocation_score` runs `conviction.compute_conviction`
over the stamped c_strat/c_setup/c_wr/c_reg plus c_bt (backtest evidence) and
c_live (live track record). `positions.slice_qty` turns a weight into a qty.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.runtime import allocator_ev as ae
from src.runtime.conviction import DEFAULT_CONVICTION_WEIGHTS
from src.runtime.positions import account_uses_slices, slice_qty

REPO = Path(__file__).resolve().parents[1]


def test_pinned_defaults_equal_the_yaml():
    raw = yaml.safe_load((REPO / "config" / "slice_allocation.yaml").read_text())
    assert raw["weights"] == ae.DEFAULT_ALLOCATION_CONFIG["weights"]
    assert raw["live_window_trades"] == ae.DEFAULT_ALLOCATION_CONFIG["live_window_trades"]
    assert raw["symbol_budget_trades"] == ae.DEFAULT_ALLOCATION_CONFIG["symbol_budget_trades"]


def test_allocation_weights_extend_the_conviction_inputs_not_replace_them():
    w = ae.DEFAULT_ALLOCATION_CONFIG["weights"]
    assert set(DEFAULT_CONVICTION_WEIGHTS) <= set(w)
    assert set(w) - set(DEFAULT_CONVICTION_WEIGHTS) == {"c_bt", "c_live"}


def test_account_uses_slices():
    assert account_uses_slices({"position_model": "slices"})
    assert account_uses_slices({"position_model": "SLICES "})
    assert not account_uses_slices({})
    assert not account_uses_slices(None)


def test_weights_follow_score_and_never_the_name():
    w, basis = ae.allocation_weights({"zzz_b": 0.6, "aaa_a": 0.2})
    assert basis == "score"
    assert w["zzz_b"] == pytest.approx(0.75) and w["aaa_a"] == pytest.approx(0.25)
    w2, _ = ae.allocation_weights({"aaa_a": 0.6, "zzz_b": 0.2})
    assert w2["aaa_a"] == pytest.approx(0.75)


def test_equal_split_is_a_labelled_last_resort():
    w, basis = ae.allocation_weights({"x": 0.0, "y": float("nan")})
    assert basis == "equal_split_last_resort" and w == {"x": 0.5, "y": 0.5}
    assert ae.allocation_weights({}) == ({}, "no_contenders")


def test_slice_qty_never_exceeds_riskmanager_size_and_splits_the_budget():
    a = slice_qty(sized_qty=2.0, entry=100, sl=95, weight=0.75, budget_trades=1.0, held_risk=0)
    b = slice_qty(sized_qty=2.0, entry=100, sl=95, weight=0.25, budget_trades=1.0, held_risk=0)
    assert a["qty"] == pytest.approx(1.5) and b["qty"] == pytest.approx(0.5)
    assert a["qty"] + b["qty"] == pytest.approx(2.0)  # == the old single trade
    big = slice_qty(sized_qty=2.0, entry=100, sl=95, weight=1.0, budget_trades=3.0, held_risk=0)
    assert big["qty"] == pytest.approx(2.0)


def test_slice_qty_counts_other_strategies_held_risk():
    half = slice_qty(sized_qty=2.0, entry=100, sl=95, weight=1.0, budget_trades=1.0, held_risk=5.0)
    assert half["qty"] == pytest.approx(1.0)
    none = slice_qty(sized_qty=2.0, entry=100, sl=95, weight=1.0, budget_trades=1.0, held_risk=10.0)
    assert none["qty"] == 0.0 and none["reason"] == "budget_exhausted"


def test_backtest_input_reads_only_measured_records(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps(
        {"coverage_state": "measured", "expectancy_r_oos": 0.2}))
    (tmp_path / "bad.json").write_text(json.dumps(
        {"coverage_state": "measured", "expectancy_r_oos": -0.9}))
    (tmp_path / "nh.json").write_text(json.dumps(
        {"coverage_state": "no_harness", "expectancy_r_oos": 5}))
    assert ae.backtest_input("good", evidence_dir=tmp_path) == (pytest.approx(0.7), "measured")
    assert ae.backtest_input("bad", evidence_dir=tmp_path)[0] == 0.0
    assert ae.backtest_input("nh", evidence_dir=tmp_path) == (None, "coverage_no_harness")
    assert ae.backtest_input("absent", evidence_dir=tmp_path) == (None, "no_record")


def test_live_input_is_laplace_smoothed_and_absent_without_trades():
    assert ae.live_input("x", {}) == (None, "no_trades")
    assert ae.live_input("x", {"x": (3, 4)})[0] == pytest.approx(4 / 6)


def _intent(strategy="s", confidence=0.8, conviction=None):
    meta = {"conviction": conviction} if conviction is not None else {}
    return SimpleNamespace(strategy=strategy, confidence=confidence, entry=100.0,
                           sl=95.0, tp=110.0, meta=meta)


def test_score_uses_stamped_conviction_inputs_plus_backtest_and_live(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps(
        {"coverage_state": "measured", "expectancy_r_oos": 0.3}))
    stamped = {"inputs_used": {"c_strat": 0.6, "c_reg": 0.9}, "news_multiplier": 1.0}
    out = ae.intent_allocation_score(
        _intent(conviction=stamped), config=ae.DEFAULT_ALLOCATION_CONFIG,
        counts={"s": (1, 2)}, evidence_dir=tmp_path,
    )
    w = ae.DEFAULT_ALLOCATION_CONFIG["weights"]
    present = {"c_strat": 0.6, "c_reg": 0.9, "c_bt": 0.8, "c_live": 0.5}
    expect = sum(w[k] * v for k, v in present.items()) / sum(w[k] for k in present)
    assert out["score"] == pytest.approx(expect, abs=1e-6)
    assert out["states"]["c_strat"] == "stamped" and out["states"]["c_bt"] == "measured"
    assert set(out["weights_used"]) == set(present)        # c_setup/c_wr dropped, renormalised
    assert out["ev_r"] == pytest.approx(expect * 2 - (1 - expect) - 0.0, abs=0.05)


def test_score_falls_back_to_intent_confidence_when_nothing_is_stamped(tmp_path):
    out = ae.intent_allocation_score(_intent(confidence=0.4), config=ae.DEFAULT_ALLOCATION_CONFIG,
                                     counts={}, evidence_dir=tmp_path)
    assert out["score"] == pytest.approx(0.4)
    assert out["states"]["c_strat"] == "intent_confidence"
    assert out["states"]["c_bt"] == "no_record" and out["states"]["c_live"] == "no_trades"
