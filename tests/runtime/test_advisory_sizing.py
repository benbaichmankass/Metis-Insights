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


# ---- compute_advisory_factor: stage/mode gating ---------------------------

def test_compute_off_mode_opts_out(monkeypatch):
    # An explicit advisory_policy mode=off is the per-strategy opt-out: no
    # model resolution, factor 1.0. (ADVISORY_MODE was removed 2026-06-13.)
    import src.strategy_registry as sr
    monkeypatch.setattr(
        sr, "_strategy_cfg", lambda name: {"advisory_policy": {"mode": "off"}},
    )
    factor, record = compute_advisory_factor(_pkg())
    assert factor == 1.0
    assert record["action"] == "off"


def test_compute_annotate_default_no_models_is_one(monkeypatch):
    # Default (no advisory_policy) is annotate, but with no influence-stage
    # models resolved the factor is still 1.0 (nothing to score).
    import src.strategy_registry as sr
    monkeypatch.setattr(sr, "_strategy_cfg", lambda name: {})
    factor, record = compute_advisory_factor(_pkg())
    assert factor == 1.0
    assert record["action"] in ("no_advisory_models", "no_scores", "error", "annotate")


# ---- compute_advisory_factor: regime-head exclusion (PR #4602, option B) ---

def _shadow_pred(model_id, stage, score, *, regime=False):
    """A ShadowPredictor stub; when regime=True the wrapped base exposes a
    `regime_spec` so `regime_spec_of` flags it as a regime head."""
    from ml.predictors.shadow import ShadowPredictor
    from ml.predictors.base import Predictor

    s = score

    class _P(Predictor):
        def predict(self, row):
            return s

    w = _P()
    if regime:
        w.regime_spec = {
            "symbol": "BTCUSDT", "timeframe": "1h",
            "vol_bucket_labels": ["vol_b0", "vol_b1", "vol_b2"],
        }
    return ShadowPredictor(w, model_id=model_id, stage=stage, log_path=None)


def test_compute_excludes_regime_heads_from_quorum(monkeypatch):
    # A regime head at advisory must NOT enter the downsize quorum: it would be
    # scored on the bare _feature_row_from_pkg row (no market_features) and emit
    # a degenerate ~constant. Only the directional model should be scored.
    import src.strategy_registry as sr
    import ml.registry.model_registry as mr
    import ml.shadow.factory as fac

    monkeypatch.setattr(
        sr, "_strategy_cfg",
        lambda name: {"advisory_policy": {"mode": "downsize"}},
    )
    monkeypatch.setattr(
        mr, "ModelRegistry",
        lambda root: SimpleNamespace(list=lambda: [
            SimpleNamespace(model_id="trade-outcome-x", target_deployment_stage="advisory"),
            SimpleNamespace(model_id="btc-regime-1h-lgbm-yz-v1", target_deployment_stage="advisory"),
        ]),
    )
    dir_p = _shadow_pred("trade-outcome-x", "advisory", 0.1)
    reg_p = _shadow_pred("btc-regime-1h-lgbm-yz-v1", "advisory", 0.98, regime=True)
    monkeypatch.setattr(
        fac, "resolve_predictors",
        lambda ids, registry, log_path=None: [dir_p, reg_p],
    )

    _factor, record = compute_advisory_factor(_pkg())
    assert record.get("excluded_regime") == ["btc-regime-1h-lgbm-yz-v1"]
    assert "btc-regime-1h-lgbm-yz-v1" not in record.get("scores", {})
    assert "trade-outcome-x" in record.get("scores", {})


def test_compute_all_regime_advisory_yields_no_scores(monkeypatch):
    # If every advisory model is a regime head, the quorum is empty → factor 1.0
    # (no downsize from degenerate regime constants).
    import src.strategy_registry as sr
    import ml.registry.model_registry as mr
    import ml.shadow.factory as fac

    monkeypatch.setattr(
        sr, "_strategy_cfg",
        lambda name: {"advisory_policy": {"mode": "downsize"}},
    )
    monkeypatch.setattr(
        mr, "ModelRegistry",
        lambda root: SimpleNamespace(list=lambda: [
            SimpleNamespace(model_id="btc-regime-1h", target_deployment_stage="advisory"),
        ]),
    )
    reg_p = _shadow_pred("btc-regime-1h", "advisory", 0.98, regime=True)
    monkeypatch.setattr(
        fac, "resolve_predictors",
        lambda ids, registry, log_path=None: [reg_p],
    )

    factor, record = compute_advisory_factor(_pkg())
    assert factor == 1.0
    assert record["action"] == "no_scores"
    assert record["excluded_regime"] == ["btc-regime-1h"]


# ---- apply_advisory_downsize ----------------------------------------------

def test_apply_off_mode_returns_unchanged(monkeypatch):
    import src.strategy_registry as sr
    monkeypatch.setattr(
        sr, "_strategy_cfg", lambda name: {"advisory_policy": {"mode": "off"}},
    )
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
