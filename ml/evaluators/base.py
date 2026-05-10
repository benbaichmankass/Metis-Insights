"""Evaluator abstract base class (WS4)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping


class Evaluator(ABC):
    @abstractmethod
    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        ...
