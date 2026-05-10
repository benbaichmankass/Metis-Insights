"""Predictor abstraction (S-AI-WS4-FU).

A `Predictor` wraps a trainer's `model_state` into a predict-callable.
Decouples evaluators from trainer state shape: each evaluator just
calls `predictor.predict(row)` and operates on the scalar result.

Trainers expose a `PREDICTOR_CLASS` class variable. The evaluator
base class resolves it dynamically from `model_state['trainer']`
(populated by the trainer's `fit(...)` output).
"""
from .base import Predictor
from .constant import ConstantPredictor
from .per_group import PerGroupPredictor

__all__ = ["ConstantPredictor", "PerGroupPredictor", "Predictor"]
