"""Tests for `ml.manifest.TrainingManifest`."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ml.manifest import (
    DatasetRef,
    MANIFEST_VERSION,
    TrainingManifest,
    VALID_DEPLOYMENT_STAGES,
)


def _payload(**overrides):
    base = {
        "manifest_version": MANIFEST_VERSION,
        "model_id": "m-1",
        "model_family": "regression_baseline",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "y"},
        "dataset": {
            "family": "backtest_results",
            "symbol_scope": "all",
            "timeframe": "all",
            "version": "v001",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {"target_column": "y", "metrics": ["mse"]},
        "target_deployment_stage": "research_only",
    }
    base.update(overrides)
    return base


class TestTrainingManifest:
    def test_from_dict_minimal(self):
        m = TrainingManifest.from_dict(_payload())
        assert m.model_id == "m-1"
        assert isinstance(m.dataset, DatasetRef)
        assert m.dataset.version == "v001"

    def test_invalid_manifest_version(self):
        with pytest.raises(ValueError):
            TrainingManifest.from_dict(_payload(manifest_version="v999"))

    def test_invalid_target_deployment_stage(self):
        with pytest.raises(ValueError):
            TrainingManifest.from_dict(_payload(target_deployment_stage="prod"))

    def test_trainer_must_be_qualified(self):
        with pytest.raises(ValueError):
            TrainingManifest.from_dict(_payload(trainer="NotQualified"))

    def test_evaluator_must_be_qualified(self):
        with pytest.raises(ValueError):
            TrainingManifest.from_dict(_payload(evaluator="NotQualified"))

    def test_dataset_required(self):
        payload = _payload()
        del payload["dataset"]
        with pytest.raises(ValueError):
            TrainingManifest.from_dict(payload)

    def test_to_dict_roundtrip(self):
        m = TrainingManifest.from_dict(_payload())
        d = m.to_dict()
        m2 = TrainingManifest.from_dict(d)
        assert m == m2

    def test_dataset_path_under(self, tmp_path: Path):
        m = TrainingManifest.from_dict(_payload())
        p = m.dataset.path_under(tmp_path / "datasets")
        assert p == tmp_path / "datasets" / "backtest_results" / "all" / "all" / "v001"

    def test_from_yaml(self, tmp_path: Path):
        manifest_path = tmp_path / "m.yaml"
        manifest_path.write_text(yaml.safe_dump(_payload()), encoding="utf-8")
        m = TrainingManifest.from_yaml(manifest_path)
        assert m.model_id == "m-1"

    def test_blank_model_id_rejected(self):
        with pytest.raises(ValueError):
            TrainingManifest.from_dict(_payload(model_id="   "))

    def test_description_defaults_blank(self):
        m = TrainingManifest.from_dict(_payload())
        assert m.description == ""
        assert m.to_dict()["description"] == ""

    def test_description_roundtrips(self):
        m = TrainingManifest.from_dict(
            _payload(description="Baseline win-rate model for the trade-outcome family.")
        )
        assert m.description == "Baseline win-rate model for the trade-outcome family."
        m2 = TrainingManifest.from_dict(m.to_dict())
        assert m2 == m
        assert m2.description == m.description


def test_valid_deployment_stages_distinct():
    assert len(set(VALID_DEPLOYMENT_STAGES)) == len(VALID_DEPLOYMENT_STAGES)


class TestDatasetBuildParams:
    def test_dataset_build_params_roundtrips(self):
        ds = {
            "family": "market_sequences",
            "symbol_scope": "BTCUSDT",
            "timeframe": "15m",
            "version": "v001",
            "build_params": {"vol_threshold": 0.003},
        }
        m = TrainingManifest.from_dict(_payload(dataset=ds))
        assert m.dataset.build_params == {"vol_threshold": 0.003}
        assert m.to_dict()["dataset"]["build_params"] == {"vol_threshold": 0.003}
        assert TrainingManifest.from_dict(m.to_dict()) == m

    def test_dataset_build_params_defaults_none_and_absent(self):
        m = TrainingManifest.from_dict(_payload())
        assert m.dataset.build_params is None
        assert "build_params" not in m.to_dict()["dataset"]
