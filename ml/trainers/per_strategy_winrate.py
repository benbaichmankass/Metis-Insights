"""Per-bucket mean-target trainer (WS5-A + WS4-FU + WS5-C).

Originally a binary win-rate baseline (S-AI-WS5-A). Generalised in
S-AI-WS5-C to also support continuous targets via the
`target_kind` config knob (default `binary` keeps the WS5-A
behavior; new `numeric_mean` computes per-group mean of any
numeric column — used by the setup-quality scorer for
`r_multiple`).

The state shape is unchanged: `per_group_rate` is the per-group
mean of the (coerced) target, `global_rate` is the overall mean,
and `per_group_total` records counts. For binary targets the
"rate" is a win rate; for numeric targets it's the mean R or
similar. PerGroupPredictor returns whatever the trainer stored.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.per_group import PerGroupPredictor
from .base import Trainer


def _coerce_label(target_value: Any, kind: str) -> float | None:
    """Return the float label or None to skip the row."""
    if kind == "binary":
        return 1.0 if bool(target_value) else 0.0
    if kind == "numeric_mean":
        try:
            return float(target_value)
        except (TypeError, ValueError):
            return None
    raise ValueError(
        f"unknown target_kind {kind!r}; expected 'binary' or 'numeric_mean'"
    )


class PerStrategyWinRateTrainer(Trainer):
    PREDICTOR_CLASS = PerGroupPredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        target = config.get("target_column", "won")
        feature = config.get("feature_column", "strategy_name")
        unknown_bucket = config.get("unknown_bucket", "")
        target_kind = str(config.get("target_kind", "binary"))

        per_group_total: dict[str, int] = {}
        per_group_sum: dict[str, float] = {}
        global_total = 0
        global_sum = 0.0

        for row in rows:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = _coerce_label(target_value, target_kind)
            if label is None:
                continue
            key_value = row.get(feature)
            key = (
                str(key_value).strip()
                if key_value is not None and str(key_value).strip()
                else unknown_bucket
            )
            per_group_total[key] = per_group_total.get(key, 0) + 1
            per_group_sum[key] = per_group_sum.get(key, 0.0) + label
            global_total += 1
            global_sum += label

        if global_total == 0:
            raise ValueError(
                f"no rows with non-null target {target!r} found in training set"
            )

        per_group_rate = {
            key: per_group_sum[key] / per_group_total[key]
            for key in per_group_total
        }
        global_rate = global_sum / global_total

        return {
            "trainer": "ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer",
            "target_column": target,
            "feature_column": feature,
            "target_kind": target_kind,
            "unknown_bucket": unknown_bucket,
            "per_group_rate": per_group_rate,
            "per_group_total": per_group_total,
            # `per_group_wins` retained for backward compat with WS5-A
            # state files: for binary targets it equals the per-group
            # win count (sum of 1s); for numeric_mean it's the per-group
            # sum of the (cast) target value.
            "per_group_wins": dict(per_group_sum),
            "global_rate": float(global_rate),
            "n_train": global_total,
        }
