"""Tests for `PerStrategyWinRateTrainer` + `ClassificationEvaluator`."""
from __future__ import annotations

import pytest

from ml.evaluators.classification import ClassificationEvaluator
from ml.trainers.per_strategy_winrate import PerStrategyWinRateTrainer


def _row(strategy_name: str, won: bool) -> dict:
    return {"strategy_name": strategy_name, "won": won}


class TestPerStrategyWinRateTrainer:
    def test_fit_basic(self):
        rows = [
            _row("vwap", True),
            _row("vwap", True),
            _row("vwap", False),
            _row("turtle", True),
            _row("turtle", False),
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(rows, {"target_column": "won", "feature_column": "strategy_name"})
        assert state["per_group_rate"]["vwap"] == pytest.approx(2 / 3)
        assert state["per_group_rate"]["turtle"] == pytest.approx(0.5)
        assert state["global_rate"] == pytest.approx(3 / 5)
        assert state["n_train"] == 5

    def test_fit_uses_unknown_bucket(self):
        rows = [
            _row("", True),
            _row("", False),
            _row("vwap", True),
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(
            rows,
            {"target_column": "won", "feature_column": "strategy_name"},
        )
        # empty strategy collapses to default unknown bucket (also "")
        assert state["per_group_rate"][""] == pytest.approx(0.5)
        assert state["per_group_rate"]["vwap"] == pytest.approx(1.0)

    def test_fit_skips_null_target(self):
        rows = [
            _row("vwap", True),
            {"strategy_name": "vwap", "won": None},
            _row("vwap", False),
        ]
        trainer = PerStrategyWinRateTrainer()
        state = trainer.fit(rows, {"target_column": "won"})
        assert state["n_train"] == 2
        assert state["per_group_rate"]["vwap"] == pytest.approx(0.5)

    def test_fit_empty_rows_raises(self):
        trainer = PerStrategyWinRateTrainer()
        with pytest.raises(ValueError):
            trainer.fit([], {"target_column": "won"})

    def test_fit_all_null_target_raises(self):
        trainer = PerStrategyWinRateTrainer()
        with pytest.raises(ValueError):
            trainer.fit(
                [{"strategy_name": "v", "won": None}],
                {"target_column": "won"},
            )


class TestClassificationEvaluator:
    def test_score_perfect(self):
        # State predicts 1.0 for vwap and 0.0 for turtle.
        state = {
            "feature_column": "strategy_name",
            "per_group_rate": {"vwap": 1.0, "turtle": 0.0},
            "global_rate": 0.5,
            "unknown_bucket": "",
        }
        rows = [
            _row("vwap", True),
            _row("vwap", True),
            _row("turtle", False),
            _row("turtle", False),
        ]
        evaluator = ClassificationEvaluator()
        metrics = evaluator.score(
            state, rows, {"target_column": "won", "threshold": 0.5}
        )
        assert metrics["accuracy"] == pytest.approx(1.0)
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["f1"] == pytest.approx(1.0)
        assert metrics["brier"] == pytest.approx(0.0)
        assert metrics["n_eval"] == 4

    def test_score_constant_predictor(self):
        # All groups predict 0.5 — the >= threshold rule produces all-1.
        state = {
            "feature_column": "strategy_name",
            "per_group_rate": {"vwap": 0.5},
            "global_rate": 0.5,
            "unknown_bucket": "",
        }
        rows = [
            _row("vwap", True),
            _row("vwap", False),
        ]
        evaluator = ClassificationEvaluator()
        metrics = evaluator.score(
            state, rows, {"target_column": "won", "threshold": 0.5}
        )
        assert metrics["accuracy"] == pytest.approx(0.5)
        assert metrics["precision"] == pytest.approx(0.5)
        assert metrics["recall"] == pytest.approx(1.0)  # all predicted 1
        assert metrics["brier"] == pytest.approx(0.25)

    def test_score_unknown_strategy_uses_global(self):
        state = {
            "feature_column": "strategy_name",
            "per_group_rate": {"vwap": 1.0},
            "global_rate": 0.6,
            "unknown_bucket": "",
        }
        rows = [
            _row("unseen", True),  # unseen strategy → global 0.6 → predicted 1 → correct
            _row("unseen", False),  # → predicted 1 → incorrect
        ]
        evaluator = ClassificationEvaluator()
        metrics = evaluator.score(
            state, rows, {"target_column": "won", "threshold": 0.5}
        )
        assert metrics["accuracy"] == pytest.approx(0.5)
        assert metrics["n_eval"] == 2

    def test_score_empty_rows(self):
        state = {
            "feature_column": "strategy_name",
            "per_group_rate": {},
            "global_rate": 0.5,
            "unknown_bucket": "",
        }
        evaluator = ClassificationEvaluator()
        metrics = evaluator.score(state, [], {"target_column": "won"})
        assert metrics["n_eval"] == 0
        assert metrics["accuracy"] == 0.0

    def test_score_missing_state_keys(self):
        evaluator = ClassificationEvaluator()
        with pytest.raises(ValueError):
            evaluator.score({}, [{"won": True}], {"target_column": "won"})


def test_round_trip_trainer_to_evaluator():
    train_rows = [
        _row("vwap", True),
        _row("vwap", True),
        _row("vwap", False),
        _row("turtle", False),
        _row("turtle", False),
    ]
    eval_rows = [
        _row("vwap", True),
        _row("turtle", False),
    ]
    trainer = PerStrategyWinRateTrainer()
    state = trainer.fit(
        train_rows, {"target_column": "won", "feature_column": "strategy_name"}
    )
    evaluator = ClassificationEvaluator()
    metrics = evaluator.score(
        state, eval_rows, {"target_column": "won", "threshold": 0.5}
    )
    # vwap predicts 2/3 ≥ 0.5 → 1; turtle predicts 0 < 0.5 → 0.
    # Both eval rows match → 100% accuracy.
    assert metrics["accuracy"] == pytest.approx(1.0)
