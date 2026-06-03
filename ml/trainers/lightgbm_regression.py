"""LightGBM regression trainer (v2 model layer).

Companion to `LightGBMMulticlassTrainer` for continuous targets:
`r_multiple` (setup quality), `entry_slippage_bps` (execution quality),
or any other numeric outcome. Pairs with
`ml.predictors.lightgbm.LightGBMRegressionPredictor`.

Same shape as the multiclass trainer — JSON-safe state via
`Booster.model_to_string`, deterministic seeded training, frozen
categorical mappings, fit-time leakage gate.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.lightgbm import LightGBMRegressionPredictor
from .base import Trainer
from .lightgbm_multiclass import _DEFAULT_PARAMS, _OUTCOME_FORBIDDEN, _check_leakage


def _default_forbidden_regression(target: str) -> frozenset[str]:
    # `r_multiple` is derived from pnl_percent so all outcome columns leak.
    # `entry_slippage_bps` is its own outcome — fill_latency_seconds and
    # actual_entry are derived; pnl is downstream.
    if target == "r_multiple":
        return _OUTCOME_FORBIDDEN
    if target == "entry_slippage_bps":
        return frozenset(
            {"entry_slippage_bps", "fill_latency_seconds", "actual_entry", "pnl", "pnl_percent"}
        )
    return frozenset()


class LightGBMRegressionTrainer(Trainer):
    """Gradient-boosted-trees regressor."""

    PREDICTOR_CLASS = LightGBMRegressionPredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        import lightgbm as lgb  # noqa: PLC0415 — lazy
        import numpy as np  # noqa: PLC0415

        target = str(config.get("target_column", "r_multiple"))
        feature_columns = [str(c) for c in config.get("feature_columns", [])]
        if not feature_columns:
            raise ValueError("trainer_config.feature_columns must be non-empty")
        categorical_columns = [
            str(c) for c in config.get("categorical_columns", [])
        ]
        for cat in categorical_columns:
            if cat not in feature_columns:
                raise ValueError(
                    f"categorical_columns contains {cat!r} not in feature_columns"
                )

        extra_forbidden = frozenset(
            str(c) for c in config.get("forbidden_features", [])
        )
        forbidden = _default_forbidden_regression(target) | extra_forbidden
        _check_leakage(feature_columns, target, forbidden)

        # Opt-in recency / uniqueness sample weighting (S-MLOPT-S2). Absent →
        # no behaviour change. Capture the time column per kept row.
        sample_weight_cfg = config.get("sample_weight")
        sw_time_col = str((sample_weight_cfg or {}).get("time_column", "created_at"))

        raw_categoricals: dict[str, set[str]] = {c: set() for c in categorical_columns}
        kept_rows: list[tuple[list[Any], float]] = []
        kept_times: list[Any] = []
        for row in rows:
            target_raw = row.get(target)
            if target_raw is None:
                continue
            try:
                y_val = float(target_raw)
            except (TypeError, ValueError):
                continue

            x_raw: list[Any] = []
            skip = False
            for col in feature_columns:
                v = row.get(col)
                if col in categorical_columns:
                    if v is None:
                        x_raw.append("")
                    else:
                        s = str(v).strip()
                        x_raw.append(s)
                        if s:
                            raw_categoricals[col].add(s)
                else:
                    if v is None:
                        x_raw.append(None)
                        continue
                    try:
                        x_raw.append(float(v))
                    except (TypeError, ValueError):
                        skip = True
                        break
            if skip:
                continue
            kept_rows.append((x_raw, y_val))
            kept_times.append(row.get(sw_time_col))

        if not kept_rows:
            raise ValueError(
                f"no usable rows: target {target!r} or features missing on every row"
            )

        cat_mappings: dict[str, dict[str, int]] = {
            col: {v: i for i, v in enumerate(sorted(raw_categoricals[col]))}
            for col in categorical_columns
        }

        x_matrix: list[list[float]] = []
        y_vec: list[float] = []
        for x_raw, y_val in kept_rows:
            enc: list[float] = []
            for col, v in zip(feature_columns, x_raw, strict=True):
                if col in categorical_columns:
                    idx = cat_mappings[col].get(str(v), -1) if v != "" else -1
                    enc.append(float(idx))
                else:
                    enc.append(float("nan") if v is None else float(v))
            x_matrix.append(enc)
            y_vec.append(y_val)

        user_params = dict(config.get("lgbm_params") or {})
        seed = int(config.get("seed", 42))
        params: dict[str, Any] = {
            **_DEFAULT_PARAMS,
            **user_params,
            "objective": "regression",
            "metric": user_params.get("metric", "l2"),
            "seed": seed,
            "deterministic": True,
            "feature_fraction_seed": seed,
            "bagging_seed": seed,
            "data_random_seed": seed,
        }
        n_iter = int(config.get("n_iter", 200))
        cat_idx = [feature_columns.index(c) for c in categorical_columns]

        x_arr = np.asarray(x_matrix, dtype=np.float64)
        y_arr = np.asarray(y_vec, dtype=np.float64)

        # Opt-in recency / uniqueness sample weights (mean-1.0), S-MLOPT-S2.
        sample_weights = None
        if sample_weight_cfg is not None:
            from .sample_weights import compute_sample_weights  # noqa: PLC0415

            extra = compute_sample_weights(kept_times, sample_weight_cfg)
            if extra is not None:
                sample_weights = np.asarray(extra, dtype=np.float64)

        train_data = lgb.Dataset(
            data=x_arr,
            label=y_arr,
            weight=sample_weights,
            categorical_feature=cat_idx if cat_idx else "auto",
            free_raw_data=False,
        )
        booster = lgb.train(
            params,
            train_data,
            num_boost_round=n_iter,
        )

        importance_gain = booster.feature_importance(importance_type="gain").tolist()
        importance_split = booster.feature_importance(importance_type="split").tolist()

        return {
            "trainer": "ml.trainers.lightgbm_regression.LightGBMRegressionTrainer",
            "target_column": target,
            "feature_columns": list(feature_columns),
            "categorical_columns": list(categorical_columns),
            "categorical_mappings": cat_mappings,
            "booster_str": booster.model_to_string(),
            "feature_importance_gain": importance_gain,
            "feature_importance_split": importance_split,
            "params": params,
            "n_iter": n_iter,
            "n_train": len(kept_rows),
            "sample_weight": dict(sample_weight_cfg) if sample_weight_cfg else None,
        }
