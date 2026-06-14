"""Unit tests for shadow_adapter.capture_shadow_preds — the score-returning
sibling of with_shadow_preds used to persist a trade's ML decisions onto the
order package (cheap SELECT later, instead of recompiling from the prediction
log). Observe-only: it must run one predict per model (audit log unchanged),
isolate per-model failures, and RETURN {model_id: {stage, score}}.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ml.predictors import ConstantPredictor, ShadowPredictor
from src.runtime.shadow_adapter import capture_shadow_preds


def _predictor(model_id: str, score: float, stage: str, tmp_path: Path) -> ShadowPredictor:
    return ShadowPredictor(
        ConstantPredictor(state={"constant": score}),
        model_id=model_id, stage=stage,
        log_path=tmp_path / f"{model_id}.jsonl",
    )


def test_returns_score_and_stage_per_model(tmp_path: Path):
    preds = [
        _predictor("regime-5m", 0.62, "shadow", tmp_path),
        _predictor("outcome-v2", 0.48, "advisory", tmp_path),
    ]
    out = capture_shadow_preds(preds, {"strategy_name": "vwap", "symbol": "BTCUSDT"})
    assert out == {
        "regime-5m": {"stage": "shadow", "score": 0.62},
        "outcome-v2": {"stage": "advisory", "score": 0.48},
    }


def test_none_and_empty_are_passthrough(tmp_path: Path):
    assert capture_shadow_preds(None, {}) == {}
    assert capture_shadow_preds([], {}) == {}


def test_one_model_failure_does_not_mask_others(tmp_path: Path):
    class _Boom(ConstantPredictor):
        def predict(self, row):  # noqa: ANN001
            raise RuntimeError("boom")

    good = _predictor("good", 0.5, "shadow", tmp_path)
    bad = ShadowPredictor(_Boom(state={"constant": 0.0}), model_id="bad",
                          stage="shadow", log_path=tmp_path / "bad.jsonl")
    out = capture_shadow_preds([bad, good], {"symbol": "BTCUSDT"})
    # The broken model is omitted; the healthy one still scores.
    assert "bad" not in out
    assert out["good"] == {"stage": "shadow", "score": 0.5}


def test_rejects_non_shadow_predictor(tmp_path: Path):
    with pytest.raises(TypeError):
        capture_shadow_preds([ConstantPredictor(state={"constant": 0.1})],
                             {"symbol": "BTCUSDT"})
