"""Tests for the regime classifier baseline (S-AI-WS5-B-PART-2 PR 2B).

Covers:
- `RegimeClassifierTrainer`: leakage guard, fit shape, OOV class
  handling, marginal probability normalisation.
- `PerBucketMulticlassPredictor`: predict_label, predict_proba,
  marginal fallback, default Predictor.predict surface.
- `MulticlassClassificationEvaluator`: accuracy, per-class
  precision/recall/f1, macro/weighted f1, n_eval, type-narrowing
  guard.
- End-to-end via the synthetic `market_raw` fixture from
  `test_market_features` (CSV → market_raw → market_features →
  trainer → predictor → evaluator).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ml.datasets.families.market_features import MarketFeaturesBuilder
from ml.evaluators.multiclass_classification import (
    MulticlassClassificationEvaluator,
)
from ml.predictors.multiclass import MulticlassPredictor
from ml.predictors.per_bucket_multiclass import PerBucketMulticlassPredictor
from ml.trainers.regime_classifier import RegimeClassifierTrainer


def _stage_market_raw(
    tmp_path: Path,
    *,
    closes: list[float],
    base_ts_iso: str = "2025-01-01T00:00:00Z",
    bar_seconds: int = 3600,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    source: str = "csv",
) -> Path:
    """Local copy of the market_raw stager from test_market_features."""
    base = datetime.fromisoformat(base_ts_iso.replace("Z", "+00:00"))
    root = tmp_path / "market_raw" / symbol / timeframe / "v001"
    root.mkdir(parents=True, exist_ok=True)
    data = root / "data.jsonl"
    with data.open("w", encoding="utf-8") as fh:
        for i, close in enumerate(closes):
            ts = (base + timedelta(seconds=bar_seconds * i)).isoformat().replace(
                "+00:00", "Z"
            )
            row = {
                "ts": ts,
                "symbol": symbol,
                "timeframe": timeframe,
                "open": float(close),
                "high": float(close) * 1.001,
                "low": float(close) * 0.999,
                "close": float(close),
                "volume": 100.0,
                "source": source,
            }
            fh.write(json.dumps(row) + "\n")
    metadata = {
        "family": "market_raw",
        "version": "v001",
        "symbol_scope": symbol,
        "timeframe": timeframe,
        "source": source,
        "timezone_name": "UTC",
        "generation_commit_sha": "test",
        "label_version": "n/a",
        "leakage_test_status": "n/a",
        "builder": "MarketRawBuilder",
        "builder_version": "v1",
        "row_count": len(closes),
        "schema": {},
        "notes": "",
        "generated_at": "2026-05-10T00:00:00+00:00",
        "schema_version": "v1",
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _trending_then_choppy(n_per_phase: int = 80) -> list[float]:
    closes: list[float] = []
    price = 100.0
    for _ in range(n_per_phase):
        price *= 1.005
        closes.append(price)
    price = closes[-1]
    for i in range(n_per_phase):
        sign = 1 if i % 2 == 0 else -1
        price *= 1 + sign * 0.03
        closes.append(price)
    price = closes[-1]
    for i in range(n_per_phase):
        sign = 1 if i % 2 == 0 else -1
        price *= 1 + sign * 0.0005
        closes.append(price)
    return closes


def _train_rows() -> list[dict[str, object]]:
    """Hand-crafted training rows with two buckets and three classes."""
    return [
        {"vol_bucket": "vol_b0", "regime_label": "trend"},
        {"vol_bucket": "vol_b0", "regime_label": "trend"},
        {"vol_bucket": "vol_b0", "regime_label": "range"},
        {"vol_bucket": "vol_b1", "regime_label": "volatile"},
        {"vol_bucket": "vol_b1", "regime_label": "volatile"},
        {"vol_bucket": "vol_b1", "regime_label": "range"},
    ]


class TestRegimeClassifierTrainer:
    def test_fit_basic(self):
        trainer = RegimeClassifierTrainer()
        state = trainer.fit(_train_rows(), {})
        assert (
            state["trainer"]
            == "ml.trainers.regime_classifier.RegimeClassifierTrainer"
        )
        assert state["target_column"] == "regime_label"
        assert state["feature_column"] == "vol_bucket"
        assert state["n_train"] == 6
        # Per-bucket probability sums must be 1 in each bucket.
        for bucket, proba in state["per_bucket_proba"].items():
            assert math.isclose(sum(proba.values()), 1.0)
        # Marginal sums to 1 too.
        assert math.isclose(sum(state["marginal_proba"].values()), 1.0)
        # vol_b0 has 2 trend / 1 range out of 3.
        assert math.isclose(
            state["per_bucket_proba"]["vol_b0"]["trend"], 2 / 3
        )

    def test_leakage_guard_refuses_label_as_feature(self):
        trainer = RegimeClassifierTrainer()
        with pytest.raises(ValueError, match="leak the label"):
            trainer.fit(_train_rows(), {"feature_column": "regime_label"})
        with pytest.raises(ValueError, match="leak the label"):
            trainer.fit(
                _train_rows(), {"feature_column": "forward_log_return"}
            )
        with pytest.raises(ValueError, match="leak the label"):
            trainer.fit(
                _train_rows(),
                {"feature_column": "forward_log_return_vol"},
            )

    def test_target_equals_feature_refused(self):
        trainer = RegimeClassifierTrainer()
        with pytest.raises(ValueError, match="trivial leakage"):
            trainer.fit(
                _train_rows(),
                {"target_column": "vol_bucket", "feature_column": "vol_bucket"},
            )

    def test_empty_training_set_raises(self):
        trainer = RegimeClassifierTrainer()
        with pytest.raises(ValueError, match="no rows"):
            trainer.fit([], {})

    def test_oov_class_extends_class_labels(self):
        # Training rows include a class not in the configured class_labels.
        rows = _train_rows() + [
            {"vol_bucket": "vol_b1", "regime_label": "surprise"}
        ]
        trainer = RegimeClassifierTrainer()
        state = trainer.fit(rows, {})
        assert "surprise" in state["class_labels"]
        # Marginal proba carries the new class too.
        assert "surprise" in state["marginal_proba"]
        # Per-bucket proba for vol_b1 covers the new class.
        assert "surprise" in state["per_bucket_proba"]["vol_b1"]


class TestPerBucketMulticlassPredictor:
    def test_predict_label_known_bucket(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        predictor = PerBucketMulticlassPredictor(state)
        # vol_b0 has 2 trend / 1 range → modal is "trend".
        assert predictor.predict_label({"vol_bucket": "vol_b0"}) == "trend"
        # vol_b1 has 2 volatile / 1 range → modal is "volatile".
        assert (
            predictor.predict_label({"vol_bucket": "vol_b1"}) == "volatile"
        )

    def test_predict_proba_sums_to_1(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        predictor = PerBucketMulticlassPredictor(state)
        proba = predictor.predict_proba({"vol_bucket": "vol_b0"})
        assert math.isclose(sum(proba.values()), 1.0)

    def test_unseen_bucket_falls_back_to_marginal(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        predictor = PerBucketMulticlassPredictor(state)
        proba = predictor.predict_proba({"vol_bucket": "vol_b9"})
        # Equals marginal.
        for c in state["marginal_proba"]:
            assert math.isclose(proba[c], state["marginal_proba"][c])

    def test_default_predict_returns_top_class_probability(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        predictor = PerBucketMulticlassPredictor(state)
        # vol_b0 modal is trend at 2/3.
        assert math.isclose(
            predictor.predict({"vol_bucket": "vol_b0"}), 2 / 3
        )

    def test_unknown_bucket_for_empty_feature(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        predictor = PerBucketMulticlassPredictor(state)
        # Empty / null feature → unknown_bucket → marginal.
        proba_empty = predictor.predict_proba({"vol_bucket": ""})
        for c in state["marginal_proba"]:
            assert math.isclose(proba_empty[c], state["marginal_proba"][c])


class TestMulticlassClassificationEvaluator:
    def test_perfect_predictor_yields_full_metrics(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        evaluator = MulticlassClassificationEvaluator()
        # Eval against the same training rows — bucket-modal predictor
        # nails vol_b0 (2/3) and vol_b1 (2/3) so accuracy = 4/6.
        metrics = evaluator.score(state, _train_rows(), {})
        assert math.isclose(metrics["accuracy"], 4 / 6)
        assert math.isclose(metrics["n_eval"], 6.0)
        for class_name in ("trend", "range", "volatile"):
            assert f"precision_{class_name}" in metrics
            assert f"recall_{class_name}" in metrics
            assert f"f1_{class_name}" in metrics
        # macro and weighted f1 are within [0, 1].
        assert 0.0 <= metrics["macro_f1"] <= 1.0
        assert 0.0 <= metrics["weighted_f1"] <= 1.0

    def test_empty_eval_returns_zeros(self):
        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        evaluator = MulticlassClassificationEvaluator()
        metrics = evaluator.score(state, [], {})
        assert metrics["accuracy"] == 0.0
        assert metrics["n_eval"] == 0.0
        for class_name in ("trend", "range", "volatile"):
            assert metrics[f"precision_{class_name}"] == 0.0

    def test_rejects_non_multiclass_predictor(self):
        # Fake a state that resolves to PerGroupPredictor (binary
        # / regression Predictor, not Multiclass). Evaluator must
        # reject.
        state = {
            "trainer": (
                "ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer"
            ),
            "target_column": "won",
            "feature_column": "strategy_name",
            "per_group_rate": {"a": 0.5},
            "global_rate": 0.5,
            "unknown_bucket": "",
            "class_labels": ["true", "false"],
        }
        evaluator = MulticlassClassificationEvaluator()
        with pytest.raises(TypeError, match="MulticlassPredictor"):
            evaluator.score(state, [{"strategy_name": "a", "won": True}], {})

    def test_perfect_eval_metrics_are_one(self):
        # Construct a state where every predicted label matches and
        # every class is represented — accuracy + f1 should hit 1.0.
        rows = [
            {"vol_bucket": "vol_b0", "regime_label": "trend"},
            {"vol_bucket": "vol_b1", "regime_label": "volatile"},
            {"vol_bucket": "vol_b2", "regime_label": "range"},
        ]
        # Synthetic state: each bucket maps deterministically to its
        # matching class.
        state = {
            "trainer": (
                "ml.trainers.regime_classifier.RegimeClassifierTrainer"
            ),
            "target_column": "regime_label",
            "feature_column": "vol_bucket",
            "unknown_bucket": "",
            "class_labels": ["trend", "range", "volatile"],
            "per_bucket_proba": {
                "vol_b0": {"trend": 1.0, "range": 0.0, "volatile": 0.0},
                "vol_b1": {"trend": 0.0, "range": 0.0, "volatile": 1.0},
                "vol_b2": {"trend": 0.0, "range": 1.0, "volatile": 0.0},
            },
            "marginal_proba": {
                "trend": 1 / 3,
                "range": 1 / 3,
                "volatile": 1 / 3,
            },
        }
        evaluator = MulticlassClassificationEvaluator()
        metrics = evaluator.score(state, rows, {})
        assert math.isclose(metrics["accuracy"], 1.0)
        assert math.isclose(metrics["macro_f1"], 1.0)
        assert math.isclose(metrics["weighted_f1"], 1.0)


class TestEndToEndRegimePipeline:
    """CSV → market_raw → market_features → trainer → evaluator."""

    def test_pipeline_round_trip(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        feat_builder = MarketFeaturesBuilder()
        feat_rows = list(
            feat_builder.iter_rows(
                market_raw_path=market_raw,
                vol_window_n=10,
                forward_window_m=5,
                vol_threshold=0.005,
                trend_threshold=0.005,
                n_vol_buckets=3,
            )
        )
        assert feat_rows
        # Time-ordered split (trainer doesn't care, but mirrors the
        # manifest's split_strategy: time_aware_holdout).
        n = len(feat_rows)
        cut = int(round(n * 0.8))
        train, evalset = feat_rows[:cut], feat_rows[cut:]
        assert train and evalset

        trainer = RegimeClassifierTrainer()
        state = trainer.fit(train, {})

        evaluator = MulticlassClassificationEvaluator()
        metrics = evaluator.score(state, evalset, {})

        # Sanity: metrics are present and shaped correctly.
        assert metrics["n_eval"] == float(len(evalset))
        assert 0.0 <= metrics["accuracy"] <= 1.0
        assert 0.0 <= metrics["macro_f1"] <= 1.0
        for class_name in ("trend", "range", "volatile"):
            assert f"precision_{class_name}" in metrics
            assert f"recall_{class_name}" in metrics
            assert f"f1_{class_name}" in metrics

    def test_predictor_resolution_via_evaluator_base(self, tmp_path: Path):
        # Verify that the trainer's PREDICTOR_CLASS pairing makes
        # _resolve_predictor return our MulticlassPredictor.
        from ml.evaluators.base import Evaluator

        state = RegimeClassifierTrainer().fit(_train_rows(), {})
        predictor = Evaluator._resolve_predictor(state)
        assert isinstance(predictor, MulticlassPredictor)
        assert isinstance(predictor, PerBucketMulticlassPredictor)


def test_predictor_class_pairing():
    # The trainer.PREDICTOR_CLASS attribute is the single source of
    # truth for the trainer→predictor pairing.
    assert (
        RegimeClassifierTrainer.PREDICTOR_CLASS
        is PerBucketMulticlassPredictor
    )
