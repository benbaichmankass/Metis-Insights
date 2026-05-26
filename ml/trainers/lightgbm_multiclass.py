"""LightGBM multiclass trainer (v2 model layer).

The first non-baseline trainer. Replaces per-group-mode / per-group-mean
predictors with a gradient-boosted-trees classifier that can capture
interactions between numeric (`rolling_log_return_vol`, `log_return`) and
categorical (`vol_bucket`, `killzone`, …) features.

Pairs with `ml.predictors.lightgbm.LightGBMMulticlassPredictor`. The
trained booster is serialized to a JSON-safe string (`Booster.model_to_string`)
and embedded in the `model_state` dict so the runner's
`json.dumps(model_state, …)` succeeds and the predictor reloads it via
`Booster(model_str=…)` at score time.

Leakage discipline: any feature listed in `forbidden_features` (or the
default set for the target column) raises `ValueError` at `fit()` time.
Same pattern as `RegimeClassifierTrainer`.

Live-scoring spec freezing: when `freeze_regime_spec: true` is set in
`trainer_config`, the trainer reconstructs `vol_bucket_edges` from training
rows (largest raw `rolling_log_return_vol` per bucket = that bucket's upper
cut point, mirroring `RegimeClassifierTrainer`) and freezes
`(symbol, timeframe, vol_window_n, vol_feature_column, vol_bucket_edges,
vol_bucket_labels)` into state. `src/runtime/regime_shadow.py` reads this
to bucket live ticks against the SAME edges the model trained on.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.lightgbm import LightGBMMulticlassPredictor
from .base import Trainer

# Forbidden as features when the target is `regime_label` (mirrors
# RegimeClassifierTrainer). Forward-window columns are derived from the
# label and would leak it.
_REGIME_FORBIDDEN: frozenset[str] = frozenset(
    {"regime_label", "forward_log_return", "forward_log_return_vol"}
)
# Forbidden as features when the target is a trade outcome (mirrors
# PerStrategyWinRateTrainer's gates for `won` / `r_multiple` users).
_OUTCOME_FORBIDDEN: frozenset[str] = frozenset(
    {"won", "pnl", "pnl_percent", "r_multiple"}
)

# Default LightGBM params — conservative regularization so small datasets
# (e.g. setup-quality with <100 rows) don't overfit. Each is overridable
# via `trainer_config.lgbm_params`.
_DEFAULT_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 5,
    "lambda_l2": 0.0,
    "verbose": -1,
}


def _bucket_sort_key(label: str) -> tuple[int, str]:
    """Order `vol_b{i}` labels by their numeric suffix; lex otherwise.

    Same helper as in `regime_classifier.py` — duplicated here to keep
    this module independent of the baseline trainer's internals.
    """
    if label.startswith("vol_b") and label[5:].isdigit():
        return (int(label[5:]), label)
    return (1_000_000, label)


def _default_forbidden(target: str) -> frozenset[str]:
    if target == "regime_label":
        return _REGIME_FORBIDDEN
    if target in {"won", "r_multiple"}:
        return _OUTCOME_FORBIDDEN
    return frozenset()


def _check_leakage(
    feature_columns: list[str],
    target: str,
    forbidden: frozenset[str],
) -> None:
    if target in feature_columns:
        raise ValueError(
            f"feature_columns contains the target column {target!r}; trivial leakage"
        )
    for col in feature_columns:
        if col in forbidden:
            raise ValueError(
                f"feature_columns contains {col!r}; forbidden against target "
                f"{target!r}: {sorted(forbidden)}"
            )


class LightGBMMulticlassTrainer(Trainer):
    """Gradient-boosted-trees multiclass classifier.

    Always trains with `objective=multiclass` (even for 2 classes) so the
    predictor's output shape is uniform: a probability per class label.
    """

    PREDICTOR_CLASS = LightGBMMulticlassPredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        import lightgbm as lgb  # noqa: PLC0415 — lazy so the module imports without lightgbm
        import numpy as np  # noqa: PLC0415

        target = str(config.get("target_column", "regime_label"))
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
        forbidden = _default_forbidden(target) | extra_forbidden
        _check_leakage(feature_columns, target, forbidden)

        # Live-scoring spec — only frozen when explicitly opted in by the
        # manifest. For setup-quality / journal models the spec stays empty.
        freeze_regime_spec = bool(config.get("freeze_regime_spec", False))
        vol_feature_column = str(
            config.get("vol_feature_column", "rolling_log_return_vol")
        )
        vol_window_n = int(config.get("vol_window_n", 20))
        vol_by_bucket: dict[str, list[float]] = {}
        spec_symbol = ""
        spec_timeframe = ""

        # Pass 1: collect rows + freeze categorical mappings + class labels.
        raw_categoricals: dict[str, set[str]] = {c: set() for c in categorical_columns}
        kept_rows: list[tuple[list[Any], str]] = []
        class_set: set[str] = set()
        for row in rows:
            label_raw = row.get(target)
            if label_raw is None:
                continue
            label = str(label_raw).strip()
            if not label:
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
            class_set.add(label)
            kept_rows.append((x_raw, label))

            if freeze_regime_spec:
                raw_vol = row.get(vol_feature_column)
                bucket_key = str(row.get("vol_bucket", "")).strip()
                if raw_vol is not None and bucket_key:
                    try:
                        vol_by_bucket.setdefault(bucket_key, []).append(float(raw_vol))
                    except (TypeError, ValueError):
                        pass
                if not spec_symbol:
                    spec_symbol = str(row.get("symbol") or "")
                if not spec_timeframe:
                    spec_timeframe = str(row.get("timeframe") or "")

        if not kept_rows:
            raise ValueError(
                f"no usable rows: target {target!r} or features missing on every row"
            )
        if len(class_set) < 2:
            raise ValueError(
                f"need >= 2 distinct classes in training data; got {sorted(class_set)}"
            )

        # Freeze categorical → int mapping (deterministic via sorted()).
        cat_mappings: dict[str, dict[str, int]] = {
            col: {v: i for i, v in enumerate(sorted(raw_categoricals[col]))}
            for col in categorical_columns
        }
        class_labels = sorted(class_set)
        label_mapping = {c: i for i, c in enumerate(class_labels)}

        # Encode features → numeric matrix. NaN for missing numeric, -1 for
        # unknown categorical (LightGBM treats negatives as a distinct
        # category bucket).
        x_matrix: list[list[float]] = []
        y_vec: list[int] = []
        for x_raw, label in kept_rows:
            enc: list[float] = []
            for col, v in zip(feature_columns, x_raw, strict=True):
                if col in categorical_columns:
                    idx = cat_mappings[col].get(str(v), -1) if v != "" else -1
                    enc.append(float(idx))
                else:
                    enc.append(float("nan") if v is None else float(v))
            x_matrix.append(enc)
            y_vec.append(label_mapping[label])

        # Merge default + manifest-supplied params. Seed is mandatory for
        # determinism — runs with the same data + seed produce bit-identical
        # boosters.
        user_params = dict(config.get("lgbm_params") or {})
        seed = int(config.get("seed", 42))
        params: dict[str, Any] = {
            **_DEFAULT_PARAMS,
            **user_params,
            "objective": "multiclass",
            "num_class": len(class_labels),
            "seed": seed,
            "deterministic": True,
            "feature_fraction_seed": seed,
            "bagging_seed": seed,
            "data_random_seed": seed,
        }
        n_iter = int(config.get("n_iter", 200))
        cat_idx = [feature_columns.index(c) for c in categorical_columns]

        x_arr = np.asarray(x_matrix, dtype=np.float64)
        y_arr = np.asarray(y_vec, dtype=np.int32)
        train_data = lgb.Dataset(
            data=x_arr,
            label=y_arr,
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

        # Reconstruct live regime spec from training rows (same logic as
        # RegimeClassifierTrainer). Empty edges = degenerate spec → live
        # shadow path skips this model.
        vol_bucket_labels: list[str] = []
        vol_bucket_edges: list[float] = []
        if freeze_regime_spec:
            labels_with_data = [b for b, vs in vol_by_bucket.items() if vs]
            labels_with_data.sort(key=_bucket_sort_key)
            vol_bucket_labels = labels_with_data
            if len(labels_with_data) >= 2:
                vol_bucket_edges = [
                    max(vol_by_bucket[labels_with_data[i]])
                    for i in range(len(labels_with_data) - 1)
                ]

        return {
            "trainer": "ml.trainers.lightgbm_multiclass.LightGBMMulticlassTrainer",
            "target_column": target,
            "feature_columns": list(feature_columns),
            "categorical_columns": list(categorical_columns),
            "categorical_mappings": cat_mappings,
            "class_labels": list(class_labels),
            "label_mapping": label_mapping,
            "booster_str": booster.model_to_string(),
            "feature_importance_gain": importance_gain,
            "feature_importance_split": importance_split,
            "params": params,
            "n_iter": n_iter,
            "n_train": len(kept_rows),
            "n_classes": len(class_labels),
            # Live-scoring spec (consumed by src/runtime/regime_shadow.py;
            # empty unless freeze_regime_spec was set).
            "vol_feature_column": vol_feature_column,
            "vol_window_n": vol_window_n,
            "vol_bucket_edges": vol_bucket_edges,
            "vol_bucket_labels": vol_bucket_labels,
            "symbol": spec_symbol,
            "timeframe": spec_timeframe,
        }
