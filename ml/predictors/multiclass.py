"""Multiclass predictor abstraction (S-AI-WS5-B-PART-2 PR 2B).

Extends the binary-/regression-flavored `Predictor` abstraction
(WS4-FU) with an explicit multiclass surface. The original
`Predictor.predict` returns a single float — fine for
classification probabilities and regression targets, awkward for
multiclass where the natural prediction is a discrete class label.

`MulticlassPredictor` adds two methods:
- `predict_label(row) -> str` — the predicted class.
- `predict_proba(row) -> Mapping[str, float]` — class
  probabilities (each in `[0, 1]`).

Default `predict(row)` returns the probability of the most-likely
class so an existing single-float consumer doesn't break, but
multiclass evaluators should call `predict_label` /
`predict_proba` directly.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Mapping

from .base import Predictor


class MulticlassPredictor(Predictor):
    """Predictor that emits a class label + per-class probabilities."""

    @abstractmethod
    def predict_label(self, row: Mapping[str, Any]) -> str:
        ...

    @abstractmethod
    def predict_proba(self, row: Mapping[str, Any]) -> Mapping[str, float]:
        ...

    def predict(self, row: Mapping[str, Any]) -> float:
        # Default Predictor surface: probability of the predicted class.
        proba = self.predict_proba(row)
        if not proba:
            return 0.0
        return float(max(proba.values()))
