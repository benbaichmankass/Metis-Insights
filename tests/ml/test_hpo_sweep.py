"""Tests for the Optuna HPO harness core (S-MLOPT-S3 / M14 Session 0.3).

The Optuna study itself needs the package; the CV objective is a pure function
(`cv_evaluate`) tested here WITHOUT Optuna installed.
"""
from __future__ import annotations

import importlib

from ml.experiments.splitters import iter_folds
from scripts.ml.hpo_sweep import cv_evaluate


def _rows(n: int = 60) -> list[dict]:
    return [
        {
            "id": i,
            "total_pnl_pct": 0.1 + 0.01 * i,
            "created_at": f"2026-05-{(i % 28) + 1:02d}T{i % 24:02d}:00:00Z",
        }
        for i in range(n)
    ]


_CV_CFG = {
    "split_strategy": "purged_walk_forward",
    "n_folds": 4,
    "min_train_fraction": 0.5,
    "label_horizon": 1,
    "time_column": "created_at",
}
_SCORE_CFG = {"target_column": "total_pnl_pct", "metrics": ["mse", "mae"]}


def _make_trainer_evaluator():
    # Reuse the real constant-baseline trainer + regression evaluator so the
    # predictor-resolution path is exercised end to end (no lightgbm needed).
    trainer = importlib.import_module(
        "ml.trainers.constant_baseline"
    ).ConstantPredictionTrainer()
    evaluator = importlib.import_module(
        "ml.evaluators.regression"
    ).RegressionEvaluator()
    return trainer, evaluator


def test_cv_evaluate_pools_over_purged_folds():
    rows = _rows()
    folds = iter_folds(rows, _CV_CFG)
    assert len(folds) >= 2
    trainer, evaluator = _make_trainer_evaluator()
    pooled = cv_evaluate(
        trainer=trainer, evaluator=evaluator, folds=folds,
        trainer_config={"target_column": "total_pnl_pct"},
        evaluator_config=_SCORE_CFG,
    )
    assert "mae" in pooled and "mse" in pooled
    assert pooled["n_folds"] == float(len(folds))
    # n_eval is summed across folds.
    assert pooled["n_eval"] == sum(len(ev) for _, ev in folds)


def test_cv_evaluate_report_called_once_per_fold():
    rows = _rows()
    folds = iter_folds(rows, _CV_CFG)
    trainer, evaluator = _make_trainer_evaluator()
    steps: list[int] = []
    cv_evaluate(
        trainer=trainer, evaluator=evaluator, folds=folds,
        trainer_config={"target_column": "total_pnl_pct"},
        evaluator_config=_SCORE_CFG,
        report=lambda step, pooled: steps.append(step),
    )
    assert steps == list(range(len(folds)))


def test_module_imports_without_optuna():
    # Optuna is imported lazily inside main(); the module + cv_evaluate must be
    # usable without it (CI has no optuna).
    mod = importlib.import_module("scripts.ml.hpo_sweep")
    assert hasattr(mod, "cv_evaluate")
    assert hasattr(mod, "main")


def test_baseline_trial_params_fills_missing_keys():
    from scripts.ml.hpo_sweep import _baseline_trial_params

    p = _baseline_trial_params({"learning_rate": 0.05, "num_leaves": 31}, 200)
    assert p["learning_rate"] == 0.05
    assert p["num_leaves"] == 31
    assert p["n_iter"] == 200
    # Missing keys are filled from the search-space midpoints.
    assert "min_data_in_leaf" in p and "lambda_l2" in p
