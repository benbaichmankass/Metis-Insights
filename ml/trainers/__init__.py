"""Trainer interface + concrete baselines."""
from .base import Trainer
from .constant_baseline import ConstantPredictionTrainer
from .per_strategy_winrate import PerStrategyWinRateTrainer

__all__ = [
    "ConstantPredictionTrainer",
    "PerStrategyWinRateTrainer",
    "Trainer",
]
