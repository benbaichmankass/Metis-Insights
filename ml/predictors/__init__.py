"""Predictor abstraction (S-AI-WS4-FU + WS7-PART-1).

A `Predictor` wraps a trainer's `model_state` into a predict-callable.
Decouples evaluators from trainer state shape: each evaluator just
calls `predictor.predict(row)` and operates on the scalar result.

Trainers expose a `PREDICTOR_CLASS` class variable. The evaluator
base class resolves it dynamically from `model_state['trainer']`
(populated by the trainer's `fit(...)` output).
"""
from .base import Predictor
from .constant import ConstantPredictor
from .multiclass import MulticlassPredictor
from .per_bucket_multiclass import PerBucketMulticlassPredictor
from .per_group import PerGroupPredictor
from .shadow import ShadowPredictor

__all__ = [
    "ConstantPredictor",
    "MulticlassPredictor",
    "PerBucketMulticlassPredictor",
    "PerGroupPredictor",
    "Predictor",
    "ShadowPredictor",
]
