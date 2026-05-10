"""Trainer abstract base class (WS4)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping


class Trainer(ABC):
    """Abstract trainer interface.

    Subclasses implement `fit(rows, config)` and return a JSON-
    serialisable model state dict. The state is what the matching
    `Evaluator` consumes to make predictions; pairing trainer +
    evaluator is the manifest's responsibility.
    """

    @abstractmethod
    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...
