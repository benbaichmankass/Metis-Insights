"""Regression evaluator (WS4 + WS4-FU).

Predictor-resolved (S-AI-WS4-FU): no longer reads trainer-specific
state keys; uses `Evaluator._resolve_predictor(model_state)` to get
a `Predictor` and then calls `.predict(row)` per evaluation row.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .base import Evaluator


class RegressionEvaluator(Evaluator):
    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        target = config.get("target_column")
        if not target:
            raise ValueError("evaluator_config.target_column is required")
        wanted = list(config.get("metrics") or ["mse", "mae"])
        predictor = self._resolve_predictor(model_state)

        diffs: list[float] = []
        for row in rows:
            value = row.get(target)
            if value is None:
                continue
            prediction = predictor.predict(row)
            diffs.append(float(value) - prediction)

        if not diffs:
            return {m: 0.0 for m in wanted} | {"n_eval": 0.0}
        n = len(diffs)
        out: dict[str, float] = {"n_eval": float(n)}
        if "mse" in wanted:
            out["mse"] = sum(d * d for d in diffs) / n
        if "mae" in wanted:
            out["mae"] = sum(abs(d) for d in diffs) / n
        return out
