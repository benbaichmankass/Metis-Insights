"""Predictor invariants (S-AI-WS4-FU)."""
from __future__ import annotations

import pytest

from ml.predictors.constant import ConstantPredictor
from ml.predictors.per_group import PerGroupPredictor


class TestConstantPredictor:
    def test_predicts_constant(self):
        p = ConstantPredictor({"constant": 0.7})
        assert p.predict({"x": 1}) == 0.7
        assert p.predict({"y": "foo"}) == 0.7

    def test_missing_constant_raises(self):
        with pytest.raises(ValueError):
            ConstantPredictor({})

    def test_constant_is_coerced_to_float(self):
        p = ConstantPredictor({"constant": 1})  # int
        assert isinstance(p.predict({}), float)
        assert p.predict({}) == 1.0


class TestPerGroupPredictor:
    def _state(self, **overrides):
        base = {
            "feature_column": "strategy_name",
            "per_group_rate": {"vwap": 0.7, "turtle": 0.3},
            "global_rate": 0.5,
            "unknown_bucket": "",
        }
        base.update(overrides)
        return base

    def test_predicts_per_group(self):
        p = PerGroupPredictor(self._state())
        assert p.predict({"strategy_name": "vwap"}) == 0.7
        assert p.predict({"strategy_name": "turtle"}) == 0.3

    def test_unknown_falls_back_to_global(self):
        p = PerGroupPredictor(self._state())
        assert p.predict({"strategy_name": "unseen"}) == 0.5

    def test_null_collapses_to_unknown_bucket(self):
        p = PerGroupPredictor(
            self._state(per_group_rate={"": 0.4, "vwap": 0.7})
        )
        assert p.predict({"strategy_name": None}) == 0.4
        assert p.predict({"strategy_name": "   "}) == 0.4
        assert p.predict({}) == 0.4  # missing key

    def test_missing_feature_column_raises(self):
        with pytest.raises(ValueError):
            PerGroupPredictor({"per_group_rate": {}, "global_rate": 0.5})

    def test_missing_per_group_rate_raises(self):
        with pytest.raises(ValueError):
            PerGroupPredictor({"feature_column": "x", "global_rate": 0.5})
