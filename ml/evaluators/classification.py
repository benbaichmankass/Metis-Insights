"""Classification evaluator (WS5-A + WS4-FU).

Predictor-resolved (S-AI-WS4-FU): no longer reads trainer-specific
state keys; uses `Evaluator._resolve_predictor(model_state)` to get
a `Predictor` returning a probability in `[0, 1]`.

Metrics: accuracy, precision, recall, f1, brier, n_eval. Scalar-only
(per-strategy detail lives in a future evaluation_detail.json
artifact, filed as a follow-up).
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
        predictor = self._resolve_predictor(model_state)

        tp = fp = fn = tn = 0
        sq_err = 0.0
        n = 0
        for row in rows:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = 1 if bool(target_value) else 0
            prob = predictor.predict(row)
            # Clamp into [0,1] so a regression-style predictor used
            # for classification doesn't break Brier.
            if prob < 0.0:
                prob = 0.0
            elif prob > 1.0:
                prob = 1.0
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
