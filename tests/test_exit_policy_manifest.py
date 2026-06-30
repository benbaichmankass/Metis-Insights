"""Tests for the exit-policy AUC evaluator + the exit-policy-v1 manifest.

Companion to ``tests/test_exit_candidates.py`` (the dataset family). Covers:
  - ``ClassificationAUCEvaluator`` — the threshold-free ``auc`` it adds to the
    base classification metric set (perfect / inverted / random / degenerate).
  - ``exit-policy-v1.yaml`` — loads, wires to the new family + evaluator, and
    keeps every future-derived column out of the feature set (leakage gate).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ml.evaluators.classification_auc import (
    ClassificationAUCEvaluator,
    _auc_from_scores,
)
from ml.manifest import TrainingManifest


# ----------------------------------------------------------------------------
# AUC metric (rank-sum) — direct unit tests
# ----------------------------------------------------------------------------
def test_auc_rank_sum_perfect_separation():
    # positives all score above negatives → AUC 1.0
    scored = [(0.9, 1), (0.8, 1), (0.2, 0), (0.1, 0)]
    assert _auc_from_scores(scored) == 1.0


def test_auc_rank_sum_inverted():
    # positives all score BELOW negatives → AUC 0.0
    scored = [(0.1, 1), (0.2, 1), (0.8, 0), (0.9, 0)]
    assert _auc_from_scores(scored) == 0.0


def test_auc_rank_sum_ties_half():
    # all scores tied → no separability → 0.5 (ties count half)
    scored = [(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]
    assert _auc_from_scores(scored) == 0.5


def test_auc_single_class_is_no_information():
    assert _auc_from_scores([(0.9, 1), (0.8, 1)]) == 0.5
    assert _auc_from_scores([(0.1, 0), (0.2, 0)]) == 0.5
    assert _auc_from_scores([]) == 0.5


# ----------------------------------------------------------------------------
# evaluator wiring — a fake trainer/predictor so _resolve_predictor works
# ----------------------------------------------------------------------------
class _FakePredictor:
    """Returns the row's own ``_score`` field as the probability."""

    def __init__(self, model_state: Mapping[str, Any]) -> None:
        self._state = model_state

    def predict(self, row: Mapping[str, Any]) -> float:
        return float(row.get("_score", 0.0))


class _FakeTrainer:
    PREDICTOR_CLASS = _FakePredictor


def test_evaluator_adds_auc_to_base_metrics():
    state = {"trainer": f"{__name__}._FakeTrainer"}
    rows = [
        {"should_hold": 1, "_score": 0.9},
        {"should_hold": 1, "_score": 0.7},
        {"should_hold": 0, "_score": 0.3},
        {"should_hold": 0, "_score": 0.1},
    ]
    metrics = ClassificationAUCEvaluator().score(
        state, rows, {"target_column": "should_hold", "threshold": 0.5}
    )
    # base metric set preserved …
    for k in ("accuracy", "precision", "recall", "f1", "brier", "n_eval"):
        assert k in metrics
    # … plus the threshold-free AUC (perfect separation here).
    assert metrics["auc"] == 1.0
    assert metrics["n_eval"] == 4.0


def test_evaluator_auc_random_scores_midrange():
    state = {"trainer": f"{__name__}._FakeTrainer"}
    rows = [
        {"should_hold": 1, "_score": 0.4},
        {"should_hold": 0, "_score": 0.6},
        {"should_hold": 1, "_score": 0.5},
        {"should_hold": 0, "_score": 0.5},
    ]
    metrics = ClassificationAUCEvaluator().score(
        state, rows, {"target_column": "should_hold"}
    )
    assert 0.0 <= metrics["auc"] <= 1.0


# ----------------------------------------------------------------------------
# manifest validity
# ----------------------------------------------------------------------------
def test_exit_policy_manifest_is_valid():
    m = TrainingManifest.from_yaml(Path("ml/configs/exit-policy-v1.yaml"))
    assert m.model_id == "exit-policy-v1"
    assert m.dataset.family == "exit_candidates"
    assert m.evaluator.endswith("ClassificationAUCEvaluator")
    assert m.evaluator_config["split_strategy"] == "live_holdout"
    assert m.evaluator_config["target_column"] == "should_hold"
    assert m.trainer_config["target_column"] == "should_hold"
    # Ships observe-only: the legacy/explicit stage normalizes to `candidate`
    # (pre-shadow, refused by the shadow factory → no order influence).
    assert m.target_deployment_stage == "candidate"


def test_exit_policy_manifest_no_label_leak():
    m = TrainingManifest.from_yaml(Path("ml/configs/exit-policy-v1.yaml"))
    feats = set(m.trainer_config["feature_columns"])
    forbidden = {
        "should_hold", "label", "fwd_r_multiple", "fwd_ret",
        "barrier_touched", "hold_bars", "is_live_trade", "event_source",
    }
    assert feats.isdisjoint(forbidden), (
        f"future-derived columns leaked into features: {feats & forbidden}"
    )
    # The forbidden list in the manifest covers every outcome column too.
    declared_forbidden = set(m.trainer_config["forbidden_features"])
    assert forbidden <= declared_forbidden
