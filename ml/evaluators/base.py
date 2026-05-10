"""Evaluator abstract base class (WS4 + WS4-FU)."""
from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping

from ..predictors.base import Predictor


class Evaluator(ABC):
    @abstractmethod
    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        ...

    @staticmethod
    def _resolve_predictor(model_state: Mapping[str, Any]) -> Predictor:
        """Resolve `state['trainer']` qualname → `cls.PREDICTOR_CLASS(state)`.

        Trainers populate `state['trainer']` in `fit(...)`. The
        evaluator imports the trainer class, reads its
        `PREDICTOR_CLASS`, and instantiates it from the same state.
        """
        qualname = model_state.get("trainer")
        if not qualname or not isinstance(qualname, str) or "." not in qualname:
            raise ValueError(
                "model_state['trainer'] must be a fully-qualified Python class "
                f"qualname; got {qualname!r}"
            )
        module_name, _, attr = qualname.rpartition(".")
        module = importlib.import_module(module_name)
        trainer_cls = getattr(module, attr, None)
        if trainer_cls is None:
            raise ValueError(f"trainer class {qualname!r} not found")
        predictor_cls = getattr(trainer_cls, "PREDICTOR_CLASS", None)
        if predictor_cls is None:
            raise ValueError(
                f"trainer {qualname!r} has no PREDICTOR_CLASS; "
                "cannot resolve predictor for evaluation"
            )
        return predictor_cls(model_state)
