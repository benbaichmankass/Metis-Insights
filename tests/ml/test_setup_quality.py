"""Tests for the setup-quality baseline (S-AI-WS5-C).

Covers:
- `PerStrategyWinRateTrainer` with `target_kind: numeric_mean`
  (per-bucket mean of any numeric target, not just win rate).
- End-to-end CSV-as-fixture round-trip from `setup_labels`-shaped
  rows → trainer → `RegressionEvaluator`.
- Backward-compat check that the default `target_kind: binary`
  still returns identical state to the WS5-A behavior.
"""
from __future__ import annotations

import pytest

from ml.evaluators.regression import RegressionEvaluator
from ml.predictors.per_group import PerGroupPredictor
from ml.trainers.per_strategy_winrate import PerStrategyWinRateTrainer

_TRAINER = "ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer"


def _row(setup_type: str, r_multiple: float) -> dict:
    return {"setup_type": setup_type, "r_multiple": r_multiple}


class TestNumericMeanTrainer:
    def test_per_group_mean_of_continuous_target(self):
        rows = [
            _row("FVG", 1.5),
            _row("FVG", -0.5),
            _row("FVG", 2.0),
            _row("OB", -1.0),
            _row("OB", 0.5),
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(
            rows,
            {
                "target_column": "r_multiple",
                "feature_column": "setup_type",
                "target_kind": "numeric_mean",
            },
        )
        # FVG mean = (1.5 - 0.5 + 2.0) / 3 = 1.0
        # OB  mean = (-1.0 + 0.5) / 2 = -0.25
        assert state["per_group_rate"]["FVG"] == pytest.approx(1.0)
        assert state["per_group_rate"]["OB"] == pytest.approx(-0.25)
        # Global mean = (1.5 - 0.5 + 2.0 - 1.0 + 0.5) / 5 = 0.5
        assert state["global_rate"] == pytest.approx(0.5)
        assert state["n_train"] == 5
        assert state["target_kind"] == "numeric_mean"

    def test_skips_non_numeric_target(self):
        rows = [
            _row("FVG", 1.0),
            {"setup_type": "FVG", "r_multiple": "garbage"},
            _row("FVG", 3.0),
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(
            rows,
            {
                "target_column": "r_multiple",
                "feature_column": "setup_type",
                "target_kind": "numeric_mean",
            },
        )
        assert state["n_train"] == 2
        assert state["per_group_rate"]["FVG"] == pytest.approx(2.0)

    def test_unknown_target_kind_raises(self):
        trainer = PerStrategyWinRateTrainer()
        with pytest.raises(ValueError, match="unknown target_kind"):
            trainer.fit(
                [_row("FVG", 1.0)],
                {
                    "target_column": "r_multiple",
                    "feature_column": "setup_type",
                    "target_kind": "made-up",
                },
            )

    def test_binary_default_unchanged(self):
        # Backward-compat: omit target_kind → identical state to WS5-A.
        rows = [
            {"strategy_name": "vwap", "won": True},
            {"strategy_name": "vwap", "won": False},
            {"strategy_name": "vwap", "won": True},
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(
            rows, {"target_column": "won", "feature_column": "strategy_name"}
        )
        assert state["per_group_rate"]["vwap"] == pytest.approx(2 / 3)
        assert state["target_kind"] == "binary"

    def test_predictor_resolution_for_numeric_mean(self):
        # PerGroupPredictor handles both rate and mean uniformly.
        from ml.evaluators.base import Evaluator

        state = PerStrategyWinRateTrainer().fit(
            [
                _row("FVG", 1.0),
                _row("FVG", 2.0),
                _row("OB", -1.0),
            ],
            {
                "target_column": "r_multiple",
                "feature_column": "setup_type",
                "target_kind": "numeric_mean",
            },
        )
        predictor = Evaluator._resolve_predictor(state)
        assert isinstance(predictor, PerGroupPredictor)
        assert predictor.predict({"setup_type": "FVG"}) == pytest.approx(1.5)
        assert predictor.predict({"setup_type": "OB"}) == pytest.approx(-1.0)

    def test_per_group_wins_records_sum_for_numeric(self):
        # `per_group_wins` is overloaded post-WS5-C: for numeric_mean
        # it stores the sum of the (cast) target.
        state = PerStrategyWinRateTrainer().fit(
            [
                _row("FVG", 1.5),
                _row("FVG", -0.5),
            ],
            {
                "target_column": "r_multiple",
                "feature_column": "setup_type",
                "target_kind": "numeric_mean",
            },
        )
        assert state["per_group_wins"]["FVG"] == pytest.approx(1.0)


class TestSetupQualityEndToEnd:
    """Trainer (numeric_mean) → RegressionEvaluator round-trip."""

    def test_pipeline_round_trip(self):
        train = [
            _row("FVG", 1.5),
            _row("FVG", 0.5),
            _row("FVG", 2.0),
            _row("OB", -1.0),
            _row("OB", -0.5),
        ]
        evalset = [
            _row("FVG", 1.0),     # predict 4/3 ≈ 1.33; diff ≈ -0.33
            _row("OB", -0.75),    # predict -0.75; diff = 0.0
            _row("UNKNOWN", 2.0), # predict global mean 0.5; diff = 1.5
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(
            train,
            {
                "target_column": "r_multiple",
                "feature_column": "setup_type",
                "target_kind": "numeric_mean",
            },
        )
        evaluator = RegressionEvaluator()
        metrics = evaluator.score(
            state,
            evalset,
            {"target_column": "r_multiple", "metrics": ["mse", "mae"]},
        )
        assert metrics["n_eval"] == 3.0
        # Diffs: FVG: 1.0 - 4/3 = -1/3; OB: -0.75 - (-0.75) = 0; UNK: 2.0 - 0.5 = 1.5
        diffs = [-1 / 3, 0.0, 1.5]
        expected_mse = sum(d * d for d in diffs) / 3
        expected_mae = sum(abs(d) for d in diffs) / 3
        assert metrics["mse"] == pytest.approx(expected_mse)
        assert metrics["mae"] == pytest.approx(expected_mae)

    def test_empty_eval_returns_zeros(self):
        state = PerStrategyWinRateTrainer().fit(
            [_row("FVG", 1.0)],
            {
                "target_column": "r_multiple",
                "feature_column": "setup_type",
                "target_kind": "numeric_mean",
            },
        )
        evaluator = RegressionEvaluator()
        metrics = evaluator.score(
            state, [], {"target_column": "r_multiple"}
        )
        assert metrics["n_eval"] == 0.0
        assert metrics["mse"] == 0.0
        assert metrics["mae"] == 0.0


def test_manifest_parses():
    """Sanity-check the YAML manifest parses through TrainingManifest."""
    from pathlib import Path

    from ml.manifest import TrainingManifest

    m = TrainingManifest.from_yaml(
        Path("ml/configs/baseline-setup-quality.yaml")
    )
    assert m.model_id == "setup-quality-baseline-v0"
    assert m.trainer == _TRAINER
    assert m.evaluator == "ml.evaluators.regression.RegressionEvaluator"
    assert m.dataset.family == "setup_labels"
    assert m.trainer_config["target_kind"] == "numeric_mean"
    assert m.evaluator_config["split_strategy"] == "time_aware_holdout"
    assert m.evaluator_config["time_column"] == "created_at"
