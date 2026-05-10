"""Constant-prediction baseline trainer (WS4).

Trivial baseline: predicts the mean of `target_column` across the
training rows. Used to round-trip the WS4 training center end to end.
Real baselines (regime classifier, setup quality scorer, etc.)
follow in WS5.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .base import Trainer


class ConstantPredictionTrainer(Trainer):
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
        mean = sum(values) / len(values)
        return {
            "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
            "target_column": target,
            "constant": float(mean),
            "n_train": len(values),
        }
