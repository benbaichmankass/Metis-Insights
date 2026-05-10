"""Constant-prediction baseline trainer (WS4 + WS4-FU)."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.constant import ConstantPredictor
from .base import Trainer


class ConstantPredictionTrainer(Trainer):
    PREDICTOR_CLASS = ConstantPredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        target = config.get("target_column")
        if not target:
            raise ValueError("trainer_config.target_column is required")
        values = [
            row[target]
            for row in rows
            if row.get(target) is not None
        ]
        if not values:
            raise ValueError(f"no non-null values found for target {target!r}")
        # bool / int / float all sum-and-divide cleanly:
        # mean of [True, False, True] = 2/3.
        mean = sum(values) / len(values)
        return {
            "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
            "target_column": target,
            "constant": float(mean),
            "n_train": len(values),
        }
