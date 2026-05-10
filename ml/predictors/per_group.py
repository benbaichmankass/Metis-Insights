"""Per-group historical-mean predictor (S-AI-WS4-FU).

Paired with
`ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer`.
Reads the trainer's `per_group_rate` table; falls back to
`global_rate` for unseen feature values. Empty / null feature
values collapse to `unknown_bucket`.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import Predictor


class PerGroupPredictor(Predictor):
    def __init__(self, state: Mapping[str, Any]) -> None:
        feature = state.get("feature_column")
        if feature is None:
            raise ValueError("PerGroupPredictor requires state['feature_column']")
        per_group = state.get("per_group_rate")
        if per_group is None:
            raise ValueError("PerGroupPredictor requires state['per_group_rate']")
        self._feature = feature
        self._per_group = dict(per_group)
        self._global = float(state.get("global_rate", 0.5))
        self._unknown_bucket = state.get("unknown_bucket", "")

    def predict(self, row: Mapping[str, Any]) -> float:
        key_value = row.get(self._feature)
        key = (
            str(key_value).strip()
            if key_value is not None and str(key_value).strip()
            else self._unknown_bucket
        )
        return float(self._per_group.get(key, self._global))
