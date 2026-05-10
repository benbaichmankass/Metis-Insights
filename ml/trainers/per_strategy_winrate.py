"""Per-strategy historical-winrate baseline trainer (WS5-A + WS4-FU)."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.per_group import PerGroupPredictor
from .base import Trainer


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

        per_group_total: dict[str, int] = {}
        per_group_wins: dict[str, int] = {}
        global_total = 0
        global_wins = 0

        for row in rows:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = 1 if bool(target_value) else 0
            key_value = row.get(feature)
            key = (
                str(key_value).strip()
                if key_value is not None and str(key_value).strip()
                else unknown_bucket
            )
            per_group_total[key] = per_group_total.get(key, 0) + 1
            per_group_wins[key] = per_group_wins.get(key, 0) + label
            global_total += 1
            global_wins += label

        if global_total == 0:
            raise ValueError(
                f"no rows with non-null target {target!r} found in training set"
            )

        per_group_rate = {
            key: per_group_wins[key] / per_group_total[key]
            for key in per_group_total
        }
        global_rate = global_wins / global_total

        return {
            "trainer": "ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer",
            "target_column": target,
            "feature_column": feature,
            "unknown_bucket": unknown_bucket,
            "per_group_rate": per_group_rate,
            "per_group_total": per_group_total,
            "global_rate": float(global_rate),
            "n_train": global_total,
        }
