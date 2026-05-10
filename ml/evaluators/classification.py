"""Classification evaluator (WS5-A).

Paired with `PerStrategyWinRateTrainer`. Reads the trainer's
`per_group_rate` table to score each evaluation row, then computes
headline classification metrics. Operates on binary labels: the
`target_column` is interpreted truthy / falsy.

Metrics:
  - accuracy
  - precision    (positive class only)
  - recall       (positive class only)
  - f1           (positive class only)
  - brier        (mean squared error of probability vs label)
  - n_eval       (count of scored rows)

Undefined precision / recall / f1 (zero positives) report as 0.0
rather than NaN; the registry stores `Mapping[str, float]` and NaN
breaks JSON round-trips.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .base import Evaluator


class ClassificationEvaluator(Evaluator):
    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        target = config.get("target_column", "won")
        threshold = float(config.get("threshold", 0.5))
        feature = model_state.get("feature_column")
        if feature is None:
            raise ValueError(
                "model_state.feature_column missing — incompatible trainer"
            )
        per_group_rate = model_state.get("per_group_rate")
        if per_group_rate is None:
            raise ValueError(
                "model_state.per_group_rate missing — incompatible trainer"
            )
        global_rate = float(model_state.get("global_rate", 0.5))
        unknown_bucket = model_state.get("unknown_bucket", "")

        tp = fp = fn = tn = 0
        sq_err = 0.0
        n = 0
        for row in rows:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = 1 if bool(target_value) else 0
            key_value = row.get(feature)
            key = (
                str(key_value).strip()
                if key_value is not None and str(key_value).strip()
                else unknown_bucket
            )
            prob = float(per_group_rate.get(key, global_rate))
            predicted = 1 if prob >= threshold else 0
            if predicted == 1 and label == 1:
                tp += 1
            elif predicted == 1 and label == 0:
                fp += 1
            elif predicted == 0 and label == 1:
                fn += 1
            else:
                tn += 1
            sq_err += (prob - label) ** 2
            n += 1

        if n == 0:
            return {
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "brier": 0.0,
                "n_eval": 0.0,
            }

        accuracy = (tp + tn) / n
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        brier = sq_err / n

        return {
            "accuracy": float(accuracy),
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "brier": float(brier),
            "n_eval": float(n),
        }
