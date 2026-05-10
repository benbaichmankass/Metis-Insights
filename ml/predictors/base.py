"""Predictor abstract base class (S-AI-WS4-FU)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class Predictor(ABC):
    """Wraps a trainer's `model_state` into a predict-callable.

    Subclasses know how to interpret a specific trainer's output
    shape. Concrete predictors are paired to trainers via the
    trainer's `PREDICTOR_CLASS` class variable.

    Returns a real-valued prediction. Regression predictors return
    values in the target's native range; classification predictors
    return probabilities in `[0, 1]`.
    """

    @abstractmethod
    def predict(self, row: Mapping[str, Any]) -> float:
        ...
