"""Trainer interface + concrete baselines."""
from .base import Trainer
from .constant_baseline import ConstantPredictionTrainer

__all__ = ["Trainer", "ConstantPredictionTrainer"]
