"""LightGBM-backed predictors (v2 model layer).

Paired with the LightGBM trainers:
- `LightGBMMulticlassPredictor` ← `LightGBMMulticlassTrainer`
- `LightGBMRegressionPredictor` ← `LightGBMRegressionTrainer`

Reloads the trained booster from the trainer's `model_state['booster_str']`
(produced by `Booster.model_to_string()`), then scores rows against the
frozen feature schema + categorical mappings the trainer recorded.

Unknown categorical values map to `-1` so the live row sees a distinct
"out-of-vocab" bucket instead of crashing. Missing numeric features become
NaN, which LightGBM handles natively.

`LightGBMMulticlassPredictor.regime_spec` mirrors
`PerBucketMulticlassPredictor.regime_spec`: when the trainer froze a
non-empty `vol_bucket_labels`, the spec is published so
`src/runtime/regime_shadow.py` can bucket live ticks against the same
edges the model trained on. Models trained without that freeze (e.g.
setup-quality, journal models) expose `regime_spec = None` and the live
path scores them on the unenriched trade-signal row.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .base import Predictor
from .multiclass import MulticlassPredictor


def _build_regime_spec(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project live-scoring fields out of `state` into a spec dict.

    Returns `None` unless the LightGBM trainer was run with
    `freeze_regime_spec: true` (which populates `vol_bucket_labels`).
    """
    labels = state.get("vol_bucket_labels")
    if not labels:
        return None
    # The categorical feature column the live path should write the bucket
    # into; for the regime models this is always `vol_bucket`.
    feature_column = "vol_bucket"
    if feature_column not in state.get("feature_columns", []):
        # The model wasn't actually using vol_bucket as a feature — the spec
        # is meaningless. Surface as None so the live path skips it.
        return None
    return {
        "feature_column": feature_column,
        "vol_feature_column": str(
            state.get("vol_feature_column", "rolling_log_return_vol")
        ),
        "vol_window_n": int(state.get("vol_window_n", 20)),
        "vol_bucket_edges": [float(e) for e in (state.get("vol_bucket_edges") or [])],
        "vol_bucket_labels": [str(b) for b in labels],
        "symbol": str(state.get("symbol", "")),
        "timeframe": str(state.get("timeframe", "")),
    }


def _encode_row(
    row: Mapping[str, Any],
    feature_columns: Sequence[str],
    categorical_columns: Sequence[str],
    cat_mappings: Mapping[str, Mapping[str, int]],
) -> list[float]:
    """Encode one row into the numeric feature vector LightGBM expects.

    Categorical → frozen int; unknown value → -1. Numeric → float; missing
    → NaN. Order matches `feature_columns`.
    """
    enc: list[float] = []
    cat_set = set(categorical_columns)
    for col in feature_columns:
        v = row.get(col)
        if col in cat_set:
            if v is None:
                enc.append(-1.0)
                continue
            s = str(v).strip()
            if not s:
                enc.append(-1.0)
                continue
            enc.append(float(cat_mappings.get(col, {}).get(s, -1)))
        else:
            if v is None:
                enc.append(float("nan"))
                continue
            try:
                enc.append(float(v))
            except (TypeError, ValueError):
                enc.append(float("nan"))
    return enc


class LightGBMMulticlassPredictor(MulticlassPredictor):
    """Probabilistic multiclass classifier backed by a LightGBM booster."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        import lightgbm as lgb  # noqa: PLC0415 — lazy

        booster_str = state.get("booster_str")
        if not booster_str:
            raise ValueError(
                "LightGBMMulticlassPredictor requires state['booster_str']"
            )
        feature_columns = state.get("feature_columns")
        if not feature_columns:
            raise ValueError(
                "LightGBMMulticlassPredictor requires state['feature_columns']"
            )
        class_labels = state.get("class_labels")
        if not class_labels:
            raise ValueError(
                "LightGBMMulticlassPredictor requires state['class_labels']"
            )

        self._feature_columns = [str(c) for c in feature_columns]
        self._categorical_columns = [
            str(c) for c in (state.get("categorical_columns") or [])
        ]
        self._cat_mappings: dict[str, dict[str, int]] = {
            str(col): {str(v): int(i) for v, i in mapping.items()}
            for col, mapping in (state.get("categorical_mappings") or {}).items()
        }
        self._class_labels = tuple(str(c) for c in class_labels)
        self._booster = lgb.Booster(model_str=str(booster_str))
        self.regime_spec = _build_regime_spec(state)

    @property
    def class_labels(self) -> tuple[str, ...]:
        return self._class_labels

    def _predict_raw(self, row: Mapping[str, Any]) -> Sequence[float]:
        import numpy as np  # noqa: PLC0415

        x = _encode_row(
            row,
            self._feature_columns,
            self._categorical_columns,
            self._cat_mappings,
        )
        # LightGBM 4.x requires a 2D numpy array; predict returns shape
        # (n_rows, n_classes) for multiclass.
        out = self._booster.predict(np.asarray([x], dtype=np.float64))
        return out[0]

    def predict_proba(self, row: Mapping[str, Any]) -> Mapping[str, float]:
        raw = self._predict_raw(row)
        return {label: float(raw[i]) for i, label in enumerate(self._class_labels)}

    def predict_label(self, row: Mapping[str, Any]) -> str:
        raw = self._predict_raw(row)
        best_idx = 0
        best_val = float("-inf")
        for i, val in enumerate(raw):
            v = float(val)
            if v > best_val:
                best_val = v
                best_idx = i
        return self._class_labels[best_idx]


class LightGBMRegressionPredictor(Predictor):
    """Numeric-target regressor backed by a LightGBM booster."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        import lightgbm as lgb  # noqa: PLC0415 — lazy

        booster_str = state.get("booster_str")
        if not booster_str:
            raise ValueError(
                "LightGBMRegressionPredictor requires state['booster_str']"
            )
        feature_columns = state.get("feature_columns")
        if not feature_columns:
            raise ValueError(
                "LightGBMRegressionPredictor requires state['feature_columns']"
            )
        self._feature_columns = [str(c) for c in feature_columns]
        self._categorical_columns = [
            str(c) for c in (state.get("categorical_columns") or [])
        ]
        self._cat_mappings: dict[str, dict[str, int]] = {
            str(col): {str(v): int(i) for v, i in mapping.items()}
            for col, mapping in (state.get("categorical_mappings") or {}).items()
        }
        self._booster = lgb.Booster(model_str=str(booster_str))
        # Regression predictors are never regime models — non-regime predictor
        # contract is `regime_spec = None`, so the live shadow path scores
        # them on the unenriched trade-signal row.
        self.regime_spec = None

    def predict(self, row: Mapping[str, Any]) -> float:
        import numpy as np  # noqa: PLC0415

        x = _encode_row(
            row,
            self._feature_columns,
            self._categorical_columns,
            self._cat_mappings,
        )
        out = self._booster.predict(np.asarray([x], dtype=np.float64))
        return float(out[0])
