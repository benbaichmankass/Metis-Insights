"""Tests for `PerStrategyWinRateTrainer` + `ClassificationEvaluator`.

Updated 2026-05-10 (S-AI-WS4-FU): mock state dicts now include a
`trainer` qualname so the predictor-resolved evaluator can dispatch.
The live `fit()` output already includes this; only the unit-test
mock states needed updating.
"""
from __future__ import annotations

import pytest

from ml.evaluators.classification import ClassificationEvaluator
from ml.trainers.per_strategy_winrate import PerStrategyWinRateTrainer

_TRAINER = "ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer"


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
        state = trainer.fit(
            rows, {"target_column": "won", "feature_column": "strategy_name"}
        )
        assert state["per_group_rate"]["vwap"] == pytest.approx(2 / 3)
        assert state["per_group_rate"]["turtle"] == pytest.approx(0.5)
        assert state["global_rate"] == pytest.approx(3 / 5)
        assert state["n_train"] == 5
        assert state["trainer"] == _TRAINER

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
    def _state(self, **overrides):
        base = {
            "trainer": _TRAINER,
            "feature_column": "strategy_name",
            "per_group_rate": {"vwap": 1.0, "turtle": 0.0},
            "global_rate": 0.5,
            "unknown_bucket": "",
        }
        base.update(overrides)
        return base

    def test_score_perfect(self):
        state = self._state()
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
        state = self._state(per_group_rate={"vwap": 0.5})
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
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["brier"] == pytest.approx(0.25)

    def test_score_unknown_strategy_uses_global(self):
        state = self._state(
            per_group_rate={"vwap": 1.0},
            global_rate=0.6,
        )
        rows = [
            _row("unseen", True),
            _row("unseen", False),
        ]
        evaluator = ClassificationEvaluator()
        metrics = evaluator.score(
            state, rows, {"target_column": "won", "threshold": 0.5}
        )
        assert metrics["accuracy"] == pytest.approx(0.5)
        assert metrics["n_eval"] == 2

    def test_score_empty_rows(self):
        state = self._state(per_group_rate={})
        evaluator = ClassificationEvaluator()
        metrics = evaluator.score(state, [], {"target_column": "won"})
        assert metrics["n_eval"] == 0
        assert metrics["accuracy"] == 0.0

    def test_score_missing_trainer_qualname(self):
        evaluator = ClassificationEvaluator()
        with pytest.raises(ValueError):
            evaluator.score(
                {"feature_column": "strategy_name"},  # no `trainer` key
                [{"won": True}],
                {"target_column": "won"},
            )


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
