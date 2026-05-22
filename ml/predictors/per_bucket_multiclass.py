"""Per-bucket multinomial predictor (S-AI-WS5-B-PART-2 PR 2B).

Pairs with `ml.trainers.regime_classifier.RegimeClassifierTrainer`.
Reads a per-bucket class-probability table; falls back to the
training-set marginal class distribution for unseen feature
values.

Input state (set by the trainer):
- `feature_column` — name of the feature to look up in each row.
- `per_bucket_proba` — dict mapping bucket value (str) → dict
  mapping class label (str) → probability (float). Probabilities
  sum to 1 within each bucket.
- `marginal_proba` — dict mapping class label → marginal training
  probability. Used as fallback for unseen buckets.
- `class_labels` — ordered tuple of class labels (canonicalises
  output ordering).
- `unknown_bucket` — string value to use when the row's feature
  is null / empty (default `""`).
"""
from __future__ import annotations

from typing import Any, Mapping

from .multiclass import MulticlassPredictor


def _build_regime_spec(
    state: Mapping[str, Any], *, feature: str
) -> dict[str, Any] | None:
    """Project the live-scoring fields out of ``state`` into a spec dict.

    Returns ``None`` unless the model was trained with the live-scoring
    fields frozen in (``RegimeClassifierTrainer`` since 2026-05-22):
    bucket labels + the rolling-vol window. Models trained before that —
    or non-regime per-bucket models — have no spec and the live shadow
    path scores them on the base trade-signal row, exactly as before.
    The spec is consumed by ``src.runtime.regime_shadow``; ``None`` keeps
    this predictor a pure, generic per-bucket classifier.
    """
    labels = state.get("vol_bucket_labels")
    if not labels:
        return None
    return {
        "feature_column": feature,
        "vol_feature_column": str(
            state.get("vol_feature_column", "rolling_log_return_vol")
        ),
        "vol_window_n": int(state.get("vol_window_n", 20)),
        "vol_bucket_edges": [float(e) for e in (state.get("vol_bucket_edges") or [])],
        "vol_bucket_labels": [str(b) for b in labels],
        "symbol": str(state.get("symbol", "")),
        "timeframe": str(state.get("timeframe", "")),
    }


class PerBucketMulticlassPredictor(MulticlassPredictor):
    def __init__(self, state: Mapping[str, Any]) -> None:
        feature = state.get("feature_column")
        if feature is None:
            raise ValueError(
                "PerBucketMulticlassPredictor requires state['feature_column']"
            )
        per_bucket = state.get("per_bucket_proba")
        if per_bucket is None:
            raise ValueError(
                "PerBucketMulticlassPredictor requires state['per_bucket_proba']"
            )
        marginal = state.get("marginal_proba")
        if marginal is None:
            raise ValueError(
                "PerBucketMulticlassPredictor requires state['marginal_proba']"
            )
        class_labels = state.get("class_labels")
        if not class_labels:
            raise ValueError(
                "PerBucketMulticlassPredictor requires state['class_labels']"
            )
        self._feature = str(feature)
        self._per_bucket = {
            str(k): {str(c): float(p) for c, p in v.items()}
            for k, v in per_bucket.items()
        }
        self._marginal = {str(c): float(p) for c, p in marginal.items()}
        self._class_labels = tuple(str(c) for c in class_labels)
        self._unknown_bucket = str(state.get("unknown_bucket", ""))
        self.regime_spec = _build_regime_spec(state, feature=self._feature)

    @property
    def feature_column(self) -> str:
        return self._feature

    def _key_for(self, row: Mapping[str, Any]) -> str:
        value = row.get(self._feature)
        if value is None or not str(value).strip():
            return self._unknown_bucket
        return str(value).strip()

    def predict_proba(self, row: Mapping[str, Any]) -> Mapping[str, float]:
        key = self._key_for(row)
        return self._per_bucket.get(key, self._marginal)

    def predict_label(self, row: Mapping[str, Any]) -> str:
        proba = self.predict_proba(row)
        # Tie-break in favour of the class that comes first in
        # `class_labels`, then alphabetically — deterministic across
        # runs.
        best_label = self._class_labels[0]
        best_prob = -1.0
        for label in self._class_labels:
            p = proba.get(label, 0.0)
            if p > best_prob:
                best_prob = p
                best_label = label
        return best_label
