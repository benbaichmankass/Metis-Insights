"""Tests for execution-time advisory downsize (ml.runtime.advisory_sizing).

Covers the live-path qty scaling: reductive factor, default-off inertness,
compute-once caching, and the advisory-stage discovery filter.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.runtime.advisory_influence import AdvisoryPolicy, advisory_downsize_factor
from src.runtime.advisory_sizing import (
    apply_advisory_downsize,
    compute_advisory_factor,
    discover_advisory_stage_model_ids,
)


def _pkg(qty_meta=None, **kw):
    meta = {} if qty_meta is None else qty_meta
    defaults = dict(strategy="vwap", symbol="BTCUSDT", direction="long",
                    confidence=0.5, meta=meta)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ---- advisory_downsize_factor (pure) --------------------------------------

def test_factor_flag_off_is_one():
    assert advisory_downsize_factor({"m": 0.0}, AdvisoryPolicy(mode="downsize"),
                                    flag_enabled=False) == 1.0


def test_factor_non_downsize_mode_is_one():
    assert advisory_downsize_factor({"m": 0.0}, AdvisoryPolicy(mode="annotate"),
                                    flag_enabled=True) == 1.0


def test_factor_no_scores_is_one():
    assert advisory_downsize_factor({}, AdvisoryPolicy(mode="downsize"),
                                    flag_enabled=True) == 1.0


def test_factor_quorum_met_returns_floor():
    f = advisory_downsize_factor(
        {"m": 0.1}, AdvisoryPolicy(mode="downsize", size_floor=0.5, quorum=1),
        flag_enabled=True,
    )
    assert f == 0.5


def test_factor_below_quorum_is_one():
    # majority of 2 = 2; only one bearish
    f = advisory_downsize_factor(
        {"m1": 0.1, "m2": 0.9}, AdvisoryPolicy(mode="downsize"),
        flag_enabled=True,
    )
    assert f == 1.0


def test_factor_never_exceeds_one():
    # size_floor=1.0 (degenerate) still ≤ 1.0
    f = advisory_downsize_factor(
        {"m": 0.0}, AdvisoryPolicy(mode="downsize", size_floor=1.0, quorum=1),
        flag_enabled=True,
    )
    assert f <= 1.0


# ---- discover_advisory_stage_model_ids ------------------------------------

def test_discover_filters_to_influence_stages():
    registry = SimpleNamespace(list=lambda: [
        SimpleNamespace(model_id="a", target_deployment_stage="shadow"),
        SimpleNamespace(model_id="b", target_deployment_stage="advisory"),
        SimpleNamespace(model_id="c", target_deployment_stage="limited_live"),
        SimpleNamespace(model_id="d", target_deployment_stage="research_only"),
        SimpleNamespace(model_id="e", target_deployment_stage="live_approved"),
    ])
    assert discover_advisory_stage_model_ids(registry) == ["b", "c", "e"]


# ---- compute_advisory_factor: default-off inertness -----------------------

def test_compute_disabled_when_flag_off(monkeypatch):
    monkeypatch.delenv("ADVISORY_MODE", raising=False)
    factor, record = compute_advisory_factor(_pkg())
    assert factor == 1.0
    assert record["action"] == "disabled"


# ---- apply_advisory_downsize ----------------------------------------------

def test_apply_flag_off_returns_unchanged(monkeypatch):
    monkeypatch.delenv("ADVISORY_MODE", raising=False)
    p = _pkg()
    assert apply_advisory_downsize(p, 1.5) == 1.5


def test_apply_uses_cached_factor():
    # Pre-seed the cache so no model resolution happens.
    p = _pkg(qty_meta={"_advisory_factor": 0.5})
    assert apply_advisory_downsize(p, 2.0, account_name="bybit_2") == 1.0


def test_apply_cached_factor_one_is_noop():
    p = _pkg(qty_meta={"_advisory_factor": 1.0})
    assert apply_advisory_downsize(p, 2.0) == 2.0


def test_apply_zero_qty_unchanged():
    p = _pkg(qty_meta={"_advisory_factor": 0.5})
    assert apply_advisory_downsize(p, 0.0) == 0.0
    assert apply_advisory_downsize(p, -1.0) == -1.0


def test_apply_never_raises_on_bad_pkg():
    # pkg without meta attribute → still returns qty unchanged
    bad = SimpleNamespace(strategy="x")
    assert apply_advisory_downsize(bad, 1.0) == 1.0
