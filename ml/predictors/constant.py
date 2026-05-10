"""Constant-prediction predictor (S-AI-WS4-FU).

Paired with `ml.trainers.constant_baseline.ConstantPredictionTrainer`.
Returns the trained constant for every row.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import Predictor


class ConstantPredictor(Predictor):
    def __init__(self, state: Mapping[str, Any]) -> None:
        if "constant" not in state:
            raise ValueError("ConstantPredictor requires state['constant']")
        self._constant = float(state["constant"])

    def predict(self, row: Mapping[str, Any]) -> float:
        return self._constant
