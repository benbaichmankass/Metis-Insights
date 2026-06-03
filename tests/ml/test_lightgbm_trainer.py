"""Tests for the LightGBM trainer + predictor pair (v2 model layer).

Covers:
- `LightGBMMulticlassTrainer.fit()` — leakage gate, deterministic seed,
  categorical-mapping freezing, JSON-serializable state, live-spec
  freezing when `freeze_regime_spec=true`.
- `LightGBMMulticlassPredictor` — class-probability output sums to 1,
  unknown categorical handling, regime_spec exposed when state has it.
- `LightGBMRegressionTrainer.fit()` + `LightGBMRegressionPredictor` —
  leakage gate, predict shape, numeric stability on tiny datasets.
- End-to-end with `MulticlassClassificationEvaluator` and
  `RegressionEvaluator` so the predictor-resolution path is covered.
"""
from __future__ import annotations

import json
import math

import pytest

# Skip the whole module if lightgbm isn't installed — the trainer
# lazy-imports it, but every test here exercises an actual fit() call.
lgb = pytest.importorskip("lightgbm")

from ml.evaluators.multiclass_classification import (  # noqa: E402
    MulticlassClassificationEvaluator,
)
from ml.evaluators.regression import RegressionEvaluator  # noqa: E402
from ml.predictors.lightgbm import (  # noqa: E402
    LightGBMMulticlassPredictor,
    LightGBMRegressionPredictor,
)
from ml.trainers.lightgbm_multiclass import LightGBMMulticlassTrainer  # noqa: E402
from ml.trainers.lightgbm_regression import LightGBMRegressionTrainer  # noqa: E402


def _regime_rows() -> list[dict]:
    """Synthetic regime dataset — vol_bucket carries strong signal."""
    rows: list[dict] = []
    # Low-vol bucket → "range"
    for i in range(80):
        rows.append(
            {
                "ts": f"2026-01-01T{i:02d}:00:00Z",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "vol_bucket": "vol_b0",
                "rolling_log_return_vol": 0.0005 + (i % 5) * 1e-5,
                "log_return": 0.0001,
                "regime_label": "range",
            }
        )
    # High-vol bucket → "volatile"
    for i in range(80):
        rows.append(
            {
                "ts": f"2026-01-02T{i:02d}:00:00Z",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "vol_bucket": "vol_b2",
                "rolling_log_return_vol": 0.005 + (i % 5) * 1e-4,
                "log_return": 0.003,
                "regime_label": "volatile",
            }
        )
    return rows


_REGIME_CONFIG = {
    "target_column": "regime_label",
    "feature_columns": ["vol_bucket", "rolling_log_return_vol", "log_return"],
    "categorical_columns": ["vol_bucket"],
    "freeze_regime_spec": True,
    "vol_feature_column": "rolling_log_return_vol",
    "vol_window_n": 20,
    "seed": 42,
    "n_iter": 50,
    "lgbm_params": {"min_data_in_leaf": 10, "num_leaves": 7},
}


class TestLightGBMMulticlassTrainer:
    def test_fit_basic_shape(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        assert state["trainer"] == (
            "ml.trainers.lightgbm_multiclass.LightGBMMulticlassTrainer"
        )
        assert state["target_column"] == "regime_label"
        assert state["feature_columns"] == [
            "vol_bucket",
            "rolling_log_return_vol",
            "log_return",
        ]
        assert state["categorical_columns"] == ["vol_bucket"]
        # Both labels present + sorted
        assert state["class_labels"] == ["range", "volatile"]
        # Categorical mapping was frozen
        assert state["categorical_mappings"]["vol_bucket"]["vol_b0"] == 0
        assert state["categorical_mappings"]["vol_bucket"]["vol_b2"] == 1
        # Booster serialized as a string
        assert isinstance(state["booster_str"], str)
        assert "tree" in state["booster_str"]
        # n_train = filtered usable rows
        assert state["n_train"] == 160

    def test_state_is_json_serializable(self):
        """The runner does `json.dumps(model_state, …)` on the trainer's
        output. Anything non-serializable fails the cycle silently."""
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        s = json.dumps(state)
        # round-trip succeeds
        json.loads(s)

    def test_determinism_seed_42(self):
        a = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        b = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        # Same seed, same data → bit-identical booster string.
        assert a["booster_str"] == b["booster_str"]
        assert a["feature_importance_gain"] == b["feature_importance_gain"]

    def test_class_weight_captured_and_roundtrips(self):
        cfg = {**_REGIME_CONFIG, "class_weight": {"range": 1.0, "volatile": 10.0}}
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)
        assert state["class_weight"] == {"range": 1.0, "volatile": 10.0}
        # JSON-safe alongside the rest of model_state.
        json.loads(json.dumps(state))

    def test_class_weight_none_by_default(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        assert state["class_weight"] is None

    def test_class_weight_changes_booster(self):
        """Reweighting the minority class must actually reach lgb.train —
        a no-op wiring bug would yield the same booster as the unweighted run."""
        base = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        cfg = {**_REGIME_CONFIG, "class_weight": {"range": 1.0, "volatile": 50.0}}
        weighted = LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)
        assert weighted["booster_str"] != base["booster_str"]

    def test_sample_weight_none_by_default(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        assert state["sample_weight"] is None

    def test_sample_weight_recency_changes_booster(self):
        """Recency decay must reach lgb.train (the `ts` column spans two days,
        so older rows are down-weighted) — a no-op wiring bug would match the
        unweighted booster."""
        base = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        cfg = {**_REGIME_CONFIG, "sample_weight": {"half_life_days": 0.25}}
        weighted = LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)
        assert weighted["booster_str"] != base["booster_str"]
        assert weighted["sample_weight"] == {"half_life_days": 0.25}

    def test_sample_weight_composes_with_class_weight(self):
        cfg = {
            **_REGIME_CONFIG,
            "class_weight": {"range": 1.0, "volatile": 5.0},
            "sample_weight": {"half_life_days": 0.5, "uniqueness": True},
        }
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)
        assert state["class_weight"] == {"range": 1.0, "volatile": 5.0}
        assert state["sample_weight"]["uniqueness"] is True

    def test_sample_weight_missing_timestamp_raises(self):
        # time_column that no row carries → every weight-time is None → fail loud.
        cfg = {**_REGIME_CONFIG, "sample_weight": {"half_life_days": 1, "time_column": "nope"}}
        with pytest.raises(ValueError, match="parseable timestamp"):
            LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)

    def test_class_weight_missing_class_raises(self):
        cfg = {**_REGIME_CONFIG, "class_weight": {"range": 1.0}}  # volatile missing
        with pytest.raises(ValueError, match="missing entries"):
            LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)

    def test_class_weight_wrong_type_raises(self):
        cfg = {**_REGIME_CONFIG, "class_weight": [1.0, 10.0]}  # list, not dict
        with pytest.raises(ValueError, match="must be a dict"):
            LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)

    def test_leakage_gate_blocks_forward_columns(self):
        cfg = {
            **_REGIME_CONFIG,
            "feature_columns": ["forward_log_return"],
            "categorical_columns": [],
        }
        with pytest.raises(ValueError, match="forbidden"):
            LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)

    def test_leakage_gate_blocks_target_as_feature(self):
        cfg = {
            **_REGIME_CONFIG,
            "feature_columns": ["regime_label", "vol_bucket"],
            "categorical_columns": ["vol_bucket"],
        }
        with pytest.raises(ValueError, match="trivial leakage|forbidden"):
            LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)

    def test_extra_forbidden_features(self):
        cfg = {
            **_REGIME_CONFIG,
            "feature_columns": ["vol_bucket", "ts"],
            "categorical_columns": ["vol_bucket"],
            "forbidden_features": ["ts"],
        }
        with pytest.raises(ValueError, match="forbidden"):
            LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)

    def test_freeze_regime_spec_populates_edges(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        # 2 buckets in the training data → 1 edge between them.
        assert len(state["vol_bucket_labels"]) == 2
        assert state["vol_bucket_labels"] == ["vol_b0", "vol_b2"]
        assert len(state["vol_bucket_edges"]) == 1
        # Edge is the largest raw vol in the lower bucket — matches
        # RegimeClassifierTrainer's reconstruction.
        edge = state["vol_bucket_edges"][0]
        assert 0.0005 <= edge < 0.005
        assert state["symbol"] == "BTCUSDT"
        assert state["timeframe"] == "5m"

    def test_freeze_off_leaves_spec_empty(self):
        cfg = {**_REGIME_CONFIG, "freeze_regime_spec": False}
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)
        assert state["vol_bucket_labels"] == []
        assert state["vol_bucket_edges"] == []


class TestLightGBMMulticlassPredictor:
    def test_predict_probs_sum_to_one(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        predictor = LightGBMMulticlassPredictor(state)
        probs = predictor.predict_proba(
            {"vol_bucket": "vol_b0", "rolling_log_return_vol": 0.0005, "log_return": 0.0}
        )
        assert set(probs) == {"range", "volatile"}
        assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-6)

    def test_signal_recovers_label(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        predictor = LightGBMMulticlassPredictor(state)
        # Low-vol pattern → "range"
        assert (
            predictor.predict_label(
                {
                    "vol_bucket": "vol_b0",
                    "rolling_log_return_vol": 0.0005,
                    "log_return": 0.0,
                }
            )
            == "range"
        )
        # High-vol pattern → "volatile"
        assert (
            predictor.predict_label(
                {
                    "vol_bucket": "vol_b2",
                    "rolling_log_return_vol": 0.005,
                    "log_return": 0.003,
                }
            )
            == "volatile"
        )

    def test_unknown_categorical_maps_to_minus_one(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        predictor = LightGBMMulticlassPredictor(state)
        # Unseen bucket value — predictor should not crash; returns valid probs.
        probs = predictor.predict_proba(
            {
                "vol_bucket": "vol_b99",  # unknown
                "rolling_log_return_vol": 0.001,
                "log_return": 0.0,
            }
        )
        assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-6)

    def test_regime_spec_populated_when_frozen(self):
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), _REGIME_CONFIG)
        predictor = LightGBMMulticlassPredictor(state)
        assert predictor.regime_spec is not None
        assert predictor.regime_spec["feature_column"] == "vol_bucket"
        assert predictor.regime_spec["vol_feature_column"] == "rolling_log_return_vol"
        assert predictor.regime_spec["vol_window_n"] == 20
        assert predictor.regime_spec["symbol"] == "BTCUSDT"
        assert predictor.regime_spec["timeframe"] == "5m"

    def test_regime_spec_none_without_freeze(self):
        cfg = {**_REGIME_CONFIG, "freeze_regime_spec": False}
        state = LightGBMMulticlassTrainer().fit(_regime_rows(), cfg)
        predictor = LightGBMMulticlassPredictor(state)
        assert predictor.regime_spec is None


class TestLightGBMMulticlassEvaluatorIntegration:
    def test_multiclass_evaluator_runs(self):
        rows = _regime_rows()
        state = LightGBMMulticlassTrainer().fit(rows, _REGIME_CONFIG)
        # Round-trip through json to mirror what the runner persists.
        state = json.loads(json.dumps(state))
        evaluator = MulticlassClassificationEvaluator()
        metrics = evaluator.score(
            state, rows, {"target_column": "regime_label"}
        )
        assert metrics["n_eval"] == 160.0
        # Synthetic data has perfect separation → metrics should be high.
        assert metrics["accuracy"] >= 0.9


def _setup_rows() -> list[dict]:
    """Synthetic setup-quality dataset — setup_type carries signal."""
    rows: list[dict] = []
    # Good setup
    for i in range(40):
        rows.append(
            {
                "created_at": f"2026-01-01T{i:02d}:00:00Z",
                "setup_type": "fvg_long",
                "strategy_name": "vwap",
                "killzone": "london",
                "bias": "long",
                "direction": "buy",
                "symbol": "BTCUSDT",
                "account_id": "bybit_2",
                "r_multiple": 1.5 + (i % 3) * 0.1,
            }
        )
    # Bad setup
    for i in range(40):
        rows.append(
            {
                "created_at": f"2026-01-02T{i:02d}:00:00Z",
                "setup_type": "fvg_short",
                "strategy_name": "vwap",
                "killzone": "ny",
                "bias": "short",
                "direction": "sell",
                "symbol": "BTCUSDT",
                "account_id": "bybit_2",
                "r_multiple": -0.5 + (i % 3) * 0.1,
            }
        )
    return rows


_SETUP_CONFIG = {
    "target_column": "r_multiple",
    "feature_columns": [
        "setup_type",
        "strategy_name",
        "killzone",
        "bias",
        "direction",
        "symbol",
        "account_id",
    ],
    "categorical_columns": [
        "setup_type",
        "strategy_name",
        "killzone",
        "bias",
        "direction",
        "symbol",
        "account_id",
    ],
    "seed": 42,
    "n_iter": 50,
    "lgbm_params": {"min_data_in_leaf": 5, "num_leaves": 7},
}


class TestLightGBMRegressionTrainer:
    def test_fit_basic_shape(self):
        state = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        assert state["trainer"] == (
            "ml.trainers.lightgbm_regression.LightGBMRegressionTrainer"
        )
        assert state["target_column"] == "r_multiple"
        assert state["n_train"] == 80
        assert isinstance(state["booster_str"], str)

    def test_state_is_json_serializable(self):
        state = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        json.dumps(state)

    def test_determinism(self):
        a = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        b = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        assert a["booster_str"] == b["booster_str"]

    def test_sample_weight_none_by_default(self):
        state = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        assert state["sample_weight"] is None

    def test_sample_weight_recency_changes_booster(self):
        base = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        cfg = {**_SETUP_CONFIG, "sample_weight": {"half_life_days": 0.25}}
        weighted = LightGBMRegressionTrainer().fit(_setup_rows(), cfg)
        assert weighted["booster_str"] != base["booster_str"]
        assert weighted["sample_weight"] == {"half_life_days": 0.25}

    def test_sample_weight_missing_timestamp_raises(self):
        cfg = {**_SETUP_CONFIG, "sample_weight": {"half_life_days": 1, "time_column": "nope"}}
        with pytest.raises(ValueError, match="parseable timestamp"):
            LightGBMRegressionTrainer().fit(_setup_rows(), cfg)

    def test_leakage_gate_blocks_outcome_columns(self):
        cfg = {
            **_SETUP_CONFIG,
            "feature_columns": ["pnl_percent"],
            "categorical_columns": [],
        }
        with pytest.raises(ValueError, match="forbidden"):
            LightGBMRegressionTrainer().fit(_setup_rows(), cfg)

    def test_predictor_recovers_signal(self):
        state = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        predictor = LightGBMRegressionPredictor(state)
        good_pred = predictor.predict(
            {
                "setup_type": "fvg_long",
                "strategy_name": "vwap",
                "killzone": "london",
                "bias": "long",
                "direction": "buy",
                "symbol": "BTCUSDT",
                "account_id": "bybit_2",
            }
        )
        bad_pred = predictor.predict(
            {
                "setup_type": "fvg_short",
                "strategy_name": "vwap",
                "killzone": "ny",
                "bias": "short",
                "direction": "sell",
                "symbol": "BTCUSDT",
                "account_id": "bybit_2",
            }
        )
        # Good setup ≈ 1.5; bad setup ≈ -0.5
        assert good_pred > bad_pred

    def test_regression_predictor_has_no_regime_spec(self):
        state = LightGBMRegressionTrainer().fit(_setup_rows(), _SETUP_CONFIG)
        predictor = LightGBMRegressionPredictor(state)
        assert predictor.regime_spec is None


class TestLightGBMRegressionEvaluatorIntegration:
    def test_regression_evaluator_runs(self):
        rows = _setup_rows()
        state = LightGBMRegressionTrainer().fit(rows, _SETUP_CONFIG)
        state = json.loads(json.dumps(state))
        evaluator = RegressionEvaluator()
        metrics = evaluator.score(
            state,
            rows,
            {"target_column": "r_multiple", "metrics": ["mse", "mae"]},
        )
        assert metrics["n_eval"] == 80.0
        # Synthetic data → MAE should be small.
        assert metrics["mae"] < 0.3
