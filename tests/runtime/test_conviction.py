"""Tests for src.runtime.conviction — the v1 conviction blend (design § 3, § 4a).

Covers: weighted blend, weight renormalization over present inputs, the
missing-input rule (no heads -> conviction == c_strat), reductive news
multiplier, the no-trade floor, and the never-strand empty-input behaviour.
"""

from __future__ import annotations

import pytest

from src.runtime.conviction import (
    DEFAULT_CONVICTION_WEIGHTS,
    compute_conviction,
)


def test_full_inputs_weighted_blend():
    inputs = {"c_strat": 0.8, "c_setup": 0.6, "c_wr": 0.5, "c_reg": 0.4}
    r = compute_conviction(inputs)
    w = DEFAULT_CONVICTION_WEIGHTS
    expected = (
        w["c_strat"] * 0.8 + w["c_setup"] * 0.6 + w["c_wr"] * 0.5 + w["c_reg"] * 0.4
    ) / sum(w.values())
    assert r.conviction == pytest.approx(expected)
    assert r.blended == pytest.approx(expected)
    assert r.note == "ok"
    assert r.below_floor is False


def test_missing_inputs_renormalize_weights():
    # only c_strat + c_wr present -> weights renormalize over those two
    inputs = {"c_strat": 0.9, "c_wr": 0.3}
    r = compute_conviction(inputs)
    w = DEFAULT_CONVICTION_WEIGHTS
    denom = w["c_strat"] + w["c_wr"]
    expected = (w["c_strat"] * 0.9 + w["c_wr"] * 0.3) / denom
    assert r.conviction == pytest.approx(expected)
    assert set(r.weights_used) == {"c_strat", "c_wr"}
    assert sum(r.weights_used.values()) == pytest.approx(1.0)
    assert r.note == "partial_inputs"


def test_only_strategy_confidence_equals_cstrat():
    r = compute_conviction({"c_strat": 0.73})
    assert r.conviction == pytest.approx(0.73)


def test_none_values_dropped():
    r = compute_conviction({"c_strat": 0.6, "c_setup": None, "c_wr": None})
    assert r.conviction == pytest.approx(0.6)
    assert set(r.inputs_used) == {"c_strat"}


def test_news_multiplier_is_reductive():
    base = compute_conviction({"c_strat": 0.8}).conviction
    red = compute_conviction({"c_strat": 0.8}, news_multiplier=0.5).conviction
    assert red == pytest.approx(base * 0.5)
    # never amplifies: a >1 multiplier is clamped to 1.0
    amp = compute_conviction({"c_strat": 0.8}, news_multiplier=2.0).conviction
    assert amp == pytest.approx(base)


def test_no_trade_floor_flags_below():
    r = compute_conviction({"c_strat": 0.2}, floor=0.3)
    assert r.below_floor is True
    r2 = compute_conviction({"c_strat": 0.5}, floor=0.3)
    assert r2.below_floor is False


def test_default_floor_is_inert():
    r = compute_conviction({"c_strat": 0.001})
    assert r.floor == 0.0
    assert r.below_floor is False  # 0.001 !< 0.0


def test_empty_inputs_never_strands():
    for bad in (None, {}, {"unknown": 0.5}):
        r = compute_conviction(bad)
        assert r.conviction is None
        assert r.below_floor is False
        assert r.note == "no_inputs_present"


def test_inputs_clamped_to_unit_interval():
    r = compute_conviction({"c_strat": 1.5, "c_wr": -0.4})
    assert r.inputs_used["c_strat"] == 1.0
    assert r.inputs_used["c_wr"] == 0.0


def test_custom_weights_respected():
    r = compute_conviction(
        {"c_strat": 1.0, "c_wr": 0.0}, weights={"c_strat": 1.0, "c_wr": 3.0}
    )
    # 1*1 + 3*0 over 4 -> 0.25
    assert r.conviction == pytest.approx(0.25)


def test_to_dict_serializable():
    d = compute_conviction({"c_strat": 0.6}).to_dict()
    assert set(d) >= {"conviction", "blended", "below_floor", "inputs_used"}
