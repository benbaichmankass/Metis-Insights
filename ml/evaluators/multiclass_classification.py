"""Multiclass classification evaluator (S-AI-WS5-B-PART-2 PR 2B).

Predictor-resolved (WS4-FU + WS5-B-PART-2): uses the base
`Evaluator._resolve_predictor(state)`; then narrows to a
`MulticlassPredictor` so the predictor can emit class labels
explicitly. A trainer paired with a non-multiclass predictor will
fail loudly here.

Metrics:
- `accuracy`
- per-class `precision_<label>`, `recall_<label>`, `f1_<label>`
- `macro_f1`, `weighted_f1`
- `n_eval`

Per-class metrics are surfaced for every class in the trainer's
`class_labels` list so the metric-set is stable across runs even
when the eval split happens to lack a particular class.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.multiclass import MulticlassPredictor
from .base import Evaluator


class MulticlassClassificationEvaluator(Evaluator):
    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        target = config.get("target_column", "regime_label")
        predictor = self._resolve_predictor(model_state)
        if not isinstance(predictor, MulticlassPredictor):
            raise TypeError(
                "MulticlassClassificationEvaluator requires a MulticlassPredictor; "
                f"trainer paired predictor is {type(predictor).__name__}"
            )

        class_labels = list(model_state.get("class_labels", []))
        if not class_labels:
            raise ValueError(
                "model_state['class_labels'] is empty; "
                "trainer must record the canonical class label list"
            )

        # Per-class confusion counts.
        tp: dict[str, int] = {c: 0 for c in class_labels}
        fp: dict[str, int] = {c: 0 for c in class_labels}
        fn: dict[str, int] = {c: 0 for c in class_labels}
        support: dict[str, int] = {c: 0 for c in class_labels}
        n_correct = 0
        n_eval = 0

        for row in rows:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = str(target_value).strip()
            if not label:
                continue
            # Out-of-vocab eval label: count toward n_eval but it can't
            # be predicted (no class slot), so it's always wrong against
            # any in-vocab prediction. Surface as marginal class with
            # zero support — recorded as a miss.
            if label not in support:
                support[label] = 0
                tp[label] = 0
                fp[label] = 0
                fn[label] = 0
                class_labels.append(label)

            predicted = predictor.predict_label(row)
            if predicted not in tp:
                # Predictor returned a class the trainer didn't declare;
                # add it to the slot table so we count the FP.
                tp[predicted] = 0
                fp[predicted] = 0
                fn[predicted] = 0
                support[predicted] = 0
                if predicted not in class_labels:
                    class_labels.append(predicted)

            support[label] += 1
            n_eval += 1
            if predicted == label:
                tp[label] += 1
                n_correct += 1
            else:
                fp[predicted] += 1
                fn[label] += 1

        if n_eval == 0:
            base = {
                "accuracy": 0.0,
                "macro_f1": 0.0,
                "weighted_f1": 0.0,
                "n_eval": 0.0,
            }
            for c in class_labels:
                base[f"precision_{c}"] = 0.0
                base[f"recall_{c}"] = 0.0
                base[f"f1_{c}"] = 0.0
            return base

        per_class_precision: dict[str, float] = {}
        per_class_recall: dict[str, float] = {}
        per_class_f1: dict[str, float] = {}
        for c in class_labels:
            denom_p = tp[c] + fp[c]
            denom_r = tp[c] + fn[c]
            precision = tp[c] / denom_p if denom_p > 0 else 0.0
            recall = tp[c] / denom_r if denom_r > 0 else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall) > 0
                else 0.0
            )
            per_class_precision[c] = precision
            per_class_recall[c] = recall
            per_class_f1[c] = f1

        accuracy = n_correct / n_eval
        macro_f1 = (
            sum(per_class_f1.values()) / len(class_labels)
            if class_labels
            else 0.0
        )
        # Weighted by support; classes absent from eval contribute 0.
        weighted_f1 = (
            sum(per_class_f1[c] * support[c] for c in class_labels) / n_eval
            if n_eval > 0
            else 0.0
        )

        out: dict[str, float] = {
            "accuracy": float(accuracy),
            "macro_f1": float(macro_f1),
            "weighted_f1": float(weighted_f1),
            "n_eval": float(n_eval),
        }
        for c in class_labels:
            out[f"precision_{c}"] = float(per_class_precision[c])
            out[f"recall_{c}"] = float(per_class_recall[c])
            out[f"f1_{c}"] = float(per_class_f1[c])
        return out
