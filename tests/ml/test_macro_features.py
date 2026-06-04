"""Tests for the pure macro feature transforms (S-MLOPT-S12, Phase 2.4)."""
from __future__ import annotations

import math

from ml.datasets.macro_features import (
    MACRO_FEATURE_COLUMNS,
    compute_macro_feature_rows,
    level_spread,
    rolling_return,
    rolling_zscore,
    term_structure_slope,
)


class TestPureHelpers:
    def test_rolling_zscore_basic(self):
        # last value is one stdev above the mean of [0,1,2,3,4] (mean 2, pstdev ~1.414)
        z = rolling_zscore([0.0, 1.0, 2.0, 3.0, 4.0])
        assert z is not None and z > 0

    def test_rolling_zscore_zero_variance_is_none(self):
        assert rolling_zscore([5.0, 5.0, 5.0, 5.0, 5.0]) is None

    def test_rolling_zscore_too_short_is_none(self):
        assert rolling_zscore([1.0, 2.0], min_n=5) is None

    def test_term_structure_slope_backwardation_positive(self):
        # VIX > VIX3M => backwardation => positive slope.
        s = term_structure_slope(30.0, 25.0)
        assert s is not None and s > 0

    def test_term_structure_slope_contango_negative(self):
        s = term_structure_slope(15.0, 18.0)
        assert s is not None and s < 0

    def test_term_structure_slope_guards(self):
        assert term_structure_slope(None, 20.0) is None
        assert term_structure_slope(20.0, 0.0) is None

    def test_level_spread(self):
        assert level_spread(40.0, 15.0) == 25.0
        assert level_spread(None, 1.0) is None

    def test_rolling_return(self):
        r = rolling_return([100.0, 101.0, 102.0])
        assert r is not None and math.isclose(r, math.log(102.0 / 100.0))
        assert rolling_return([0.0, 0.0]) is None


class TestComputeMacroFeatureRows:
    def _daily(self, n: int) -> list[dict]:
        rows = []
        for i in range(n):
            # day i, ascending dates from 2025-01-01
            d = f"2025-01-{i + 1:02d}"
            rows.append({
                "date": d,
                "vix": 15.0 + (i % 5),
                "vix3m": 16.0 + (i % 3),
                "dxy": 100.0 + i * 0.1,
                "ust10y": 40.0 + i * 0.05,
                "ust3m": 38.0,
            })
        return rows

    def test_emits_all_columns(self):
        rows = compute_macro_feature_rows(self._daily(25), zscore_window_n=10)
        assert rows
        for r in rows:
            for c in MACRO_FEATURE_COLUMNS:
                assert c in r and isinstance(r[c], float)

    def test_one_day_leakage_lag(self):
        # A day-D row must be stamped at the START of day D+1.
        rows = compute_macro_feature_rows(self._daily(5))
        # first input date is 2025-01-01 → stamped 2025-01-02T00:00:00Z
        assert rows[0]["ts"] == "2025-01-02T00:00:00Z"
        assert rows[-1]["ts"] == "2025-01-06T00:00:00Z"

    def test_term_slope_sign(self):
        # vix < vix3m for the first row (15 < 16) => contango (negative slope).
        rows = compute_macro_feature_rows(self._daily(3))
        assert rows[0]["vix_term_slope"] < 0

    def test_empty_input(self):
        assert compute_macro_feature_rows([]) == []

    def test_missing_series_is_zero_not_crash(self):
        # No vix3m / ust3m → term slope + rates slope degrade to 0.0 (neutral).
        daily = [{"date": f"2025-02-{i + 1:02d}", "vix": 20.0, "dxy": 100.0} for i in range(8)]
        rows = compute_macro_feature_rows(daily)
        assert rows
        for r in rows:
            assert r["vix_term_slope"] == 0.0
            assert r["ust_slope_3m10y"] == 0.0
            assert r["vix_level"] == 20.0
