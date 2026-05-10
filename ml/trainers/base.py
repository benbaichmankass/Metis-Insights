"""Trainer abstract base class (WS4 + WS4-FU)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Iterable, Mapping

from ..predictors.base import Predictor


class Trainer(ABC):
    """Abstract trainer interface.

    Subclasses implement `fit(rows, config)` and return a JSON-
    serialisable model state dict. WS4-FU adds `PREDICTOR_CLASS`:
    each concrete trainer pairs itself with a `Predictor` subclass
    that knows how to consume the state. Evaluators resolve the
    predictor at score-time via `state['trainer']` qualname →
    `cls.PREDICTOR_CLASS(state)`.
    """

    PREDICTOR_CLASS: ClassVar[type[Predictor] | None] = None

    @abstractmethod
    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...
