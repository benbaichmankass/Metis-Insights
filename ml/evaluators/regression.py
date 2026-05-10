"""Regression evaluator (WS4).

First-cut metrics for the constant-prediction baseline: MSE, MAE.
Reads `model_state['constant']` so it is paired specifically with
`ConstantPredictionTrainer`. A general predict() interface is filed
for a follow-up.
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
        prediction = model_state.get("constant")
        if prediction is None:
            raise ValueError(
                "model_state.constant missing — incompatible trainer output"
            )
        diffs = [
            row[target] - prediction
            for row in rows
            if row.get(target) is not None
        ]
        if not diffs:
            return {m: 0.0 for m in wanted} | {"n_eval": 0.0}
        n = len(diffs)
        out: dict[str, float] = {"n_eval": float(n)}
        if "mse" in wanted:
            out["mse"] = sum(d * d for d in diffs) / n
        if "mae" in wanted:
            out["mae"] = sum(abs(d) for d in diffs) / n
        return out
