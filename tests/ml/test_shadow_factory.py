"""Tests for `ml.shadow.factory` (S-AI-WS7-PART-4)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from ml.predictors.shadow import ShadowPredictor
from ml.registry.model_registry import ModelRegistry
from ml.shadow.factory import (
    LIVE_INFLUENCE_STAGES,
    ShadowFactoryError,
    resolve_predictor,
    resolve_predictors,
)


def _make_registered_model(
    tmp_path: Path,
    *,
    model_id: str,
    stage: str,
) -> tuple[ModelRegistry, Path]:
    """Register a model in `tmp_path/registry-store` and return
    (registry, model_state_path) for use by tests.

    Uses the constant-baseline trainer + ConstantPredictor pair —
    these are the simplest predictor in the codebase and avoid
    pulling in pandas (which is not installed in the dev sandbox).
    """
    state_path = tmp_path / "model_state.json"
    state_path.write_text(
        json.dumps(
            {
                "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
                "constant": 0.5,
            }
        )
    )
    registry_root = tmp_path / "registry-store"
    registry = ModelRegistry(registry_root)
    registry.register(
        model_id=model_id,
        manifest={"manifest_version": "v1"},
        model_state_path=str(state_path),
        metrics={"mae": 0.1},
        code_revision="x",
    )
    # Walk up the stage ladder to the requested stage.
    ladder = [
        "candidate", "backtest_approved", "shadow",
        "advisory", "limited_live", "live_approved",
    ]
    if stage not in ladder + ["research_only"]:
        raise ValueError(stage)
    if stage != "research_only":
        for step in ladder:
            registry.promote_stage(
                model_id, step, by="op", reason=f"to-{step}",
            )
            if step == stage:
                break
    return registry, state_path


class TestResolvePredictor:
    @pytest.mark.parametrize("stage", sorted(LIVE_INFLUENCE_STAGES))
    def test_resolves_at_allowed_stages(self, tmp_path: Path, stage: str):
        registry, _ = _make_registered_model(
            tmp_path, model_id=f"m-{stage}", stage=stage,
        )
        predictor = resolve_predictor(f"m-{stage}", registry)
        assert isinstance(predictor, ShadowPredictor)
        assert predictor.model_id == f"m-{stage}"
        assert predictor.stage == stage
        # Smoke-call it — ConstantPredictor returns 0.5.
        assert predictor.predict({"k": 1}) == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "stage", ["research_only", "candidate", "backtest_approved"],
    )
    def test_refuses_unpromoted_stages(self, tmp_path: Path, stage: str):
        registry, _ = _make_registered_model(
            tmp_path, model_id=f"m-{stage}", stage=stage,
        )
        with pytest.raises(ShadowFactoryError, match="stage"):
            resolve_predictor(f"m-{stage}", registry)

    def test_unknown_model_id(self, tmp_path: Path):
        registry = ModelRegistry(tmp_path / "registry")
        with pytest.raises(ShadowFactoryError, match="not found"):
            resolve_predictor("does-not-exist", registry)

    def test_missing_model_state_path(self, tmp_path: Path):
        registry, state_path = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        state_path.unlink()
        with pytest.raises(ShadowFactoryError, match="model_state_path"):
            resolve_predictor("m-1", registry)

    def test_unknown_trainer_qualname(self, tmp_path: Path):
        registry, state_path = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        # Rewrite the state with a bogus trainer qualname.
        state_path.write_text(
            json.dumps(
                {"trainer": "no.such.module.Trainer", "constant": 0.5}
            )
        )
        with pytest.raises(ShadowFactoryError):
            resolve_predictor("m-1", registry)

    def test_blank_trainer_qualname(self, tmp_path: Path):
        registry, state_path = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        state_path.write_text(json.dumps({"constant": 0.5}))
        with pytest.raises(ShadowFactoryError, match="qualname"):
            resolve_predictor("m-1", registry)

    def test_log_path_propagates(self, tmp_path: Path):
        registry, _ = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        log = tmp_path / "audit.jsonl"
        predictor = resolve_predictor("m-1", registry, log_path=log)
        predictor.predict({"k": 1})
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["model_id"] == "m-1"


class TestResolvePredictors:
    def test_returns_in_input_order(self, tmp_path: Path):
        for mid in ("a", "b", "c"):
            _make_registered_model(
                tmp_path, model_id=mid, stage="shadow",
            )
        registry = ModelRegistry(tmp_path / "registry-store")
        out = resolve_predictors(["c", "a", "b"], registry, log_path=None)
        assert [p.model_id for p in out] == ["c", "a", "b"]

    def test_skips_unknown_id_when_not_strict(
        self, tmp_path: Path, caplog
    ):
        _make_registered_model(
            tmp_path, model_id="good", stage="shadow",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        with caplog.at_level(logging.WARNING):
            out = resolve_predictors(
                ["good", "missing", "good"], registry, log_path=None,
            )
        assert [p.model_id for p in out] == ["good", "good"]
        assert any(
            "shadow_factory_skipped" in r.message and "missing" in r.message
            for r in caplog.records
        )

    def test_skips_unpromoted_stage_when_not_strict(
        self, tmp_path: Path, caplog
    ):
        _make_registered_model(
            tmp_path, model_id="ok", stage="shadow",
        )
        _make_registered_model(
            tmp_path, model_id="research", stage="research_only",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        with caplog.at_level(logging.WARNING):
            out = resolve_predictors(
                ["ok", "research"], registry, log_path=None,
            )
        assert [p.model_id for p in out] == ["ok"]
        assert any(
            "shadow_factory_skipped" in r.message and "research" in r.message
            for r in caplog.records
        )

    def test_strict_reraises(self, tmp_path: Path):
        _make_registered_model(
            tmp_path, model_id="ok", stage="shadow",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        with pytest.raises(ShadowFactoryError):
            resolve_predictors(
                ["ok", "missing"], registry, log_path=None, strict=True,
            )

    def test_empty_list(self, tmp_path: Path):
        registry = ModelRegistry(tmp_path / "registry-store")
        assert resolve_predictors([], registry) == []
