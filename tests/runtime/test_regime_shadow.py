"""Tests for the regime-model shadow enrichment (2026-05-22 wiring fix).

Covers `src.runtime.regime_shadow`: live rolling-vol computation, bucket
mapping against frozen edges, and the per-predictor gating that decides
whether a regime model is scored on a live `vol_bucket`, skipped, or (for
non-regime models) scored on the base trade-signal row unchanged.
"""
from __future__ import annotations

import math
import statistics

import pytest

from src.runtime.regime_shadow import (
    bucket_for_vol,
    closes_from_candles,
    feature_row_for_predictor,
    regime_spec_of,
    rolling_log_return_vol,
)


class _FakeBase:
    def __init__(self, regime_spec):
        self.regime_spec = regime_spec

    def predict(self, row):  # pragma: no cover - not exercised here
        return 0.0


class _FakeShadow:
    """Mimics ShadowPredictor's `wrapped` accessor + predict surface."""

    def __init__(self, regime_spec):
        self._wrapped = _FakeBase(regime_spec)

    @property
    def wrapped(self):
        return self._wrapped


def _spec(symbol="BTCUSDT", timeframe="5m", edges=(0.001, 0.002), window=20):
    return {
        "feature_column": "vol_bucket",
        "vol_feature_column": "rolling_log_return_vol",
        "vol_window_n": window,
        "vol_bucket_edges": list(edges),
        "vol_bucket_labels": ["vol_b0", "vol_b1", "vol_b2"],
        "symbol": symbol,
        "timeframe": timeframe,
    }


class TestRollingVol:
    def test_matches_pstdev_of_last_window(self):
        # 25 closes → 24 log returns; window of 20 = last 20.
        closes = [100.0 * (1.001 ** i) for i in range(25)]
        log_returns = [
            math.log(closes[i + 1] / closes[i]) for i in range(len(closes) - 1)
        ]
        expected = statistics.pstdev(log_returns[-20:])
        assert rolling_log_return_vol(closes, 20) == pytest.approx(expected)

    def test_insufficient_data_returns_none(self):
        assert rolling_log_return_vol([100.0], 20) is None
        assert rolling_log_return_vol([], 20) is None

    def test_skips_nonpositive_closes(self):
        # A zero close breaks the two adjacent log returns but the rest
        # still yield a value.
        closes = [100.0, 101.0, 0.0, 102.0, 103.0, 104.0]
        vol = rolling_log_return_vol(closes, 20)
        assert vol is not None and vol >= 0.0


class TestBucketForVol:
    def test_lands_in_correct_bucket(self):
        edges = [0.001, 0.002]
        labels = ["vol_b0", "vol_b1", "vol_b2"]
        assert bucket_for_vol(0.0005, edges, labels) == "vol_b0"
        assert bucket_for_vol(0.001, edges, labels) == "vol_b0"  # <= edge
        assert bucket_for_vol(0.0015, edges, labels) == "vol_b1"
        assert bucket_for_vol(0.002, edges, labels) == "vol_b1"
        assert bucket_for_vol(0.005, edges, labels) == "vol_b2"  # saturates

    def test_empty_labels_returns_none(self):
        assert bucket_for_vol(0.5, [], []) is None


class TestClosesFromCandles:
    def test_reads_pandas_close_column(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0], "open": [0, 0, 0]})
        assert closes_from_candles(df) == [1.0, 2.0, 3.0]

    def test_none_and_missing_column_yield_empty(self):
        assert closes_from_candles(None) == []
        assert closes_from_candles({"open": [1, 2]}) == []


class TestFeatureRowForPredictor:
    def _closes(self, n=30):
        # Monotonic 0.0015/bar log step → vol ≈ 0 (constant return) lands
        # in vol_b0; we only need a computable value here.
        return [100.0 * (1.0015 ** i) for i in range(n)]

    def test_non_regime_predictor_gets_base_row(self):
        pred = _FakeShadow(None)  # no regime spec
        base = {"strategy_name": "vwap", "symbol": "BTCUSDT"}
        row = feature_row_for_predictor(
            pred, base, closes=self._closes(), symbol="BTCUSDT", timeframe="5m"
        )
        assert row is base  # unchanged, same object

    def test_matching_regime_predictor_gets_vol_bucket(self):
        pred = _FakeShadow(_spec(symbol="BTCUSDT", timeframe="5m"))
        base = {"strategy_name": "vwap", "symbol": "BTCUSDT"}
        row = feature_row_for_predictor(
            pred, base, closes=self._closes(), symbol="BTCUSDT", timeframe="5m"
        )
        assert row is not None
        assert row["vol_bucket"] in ("vol_b0", "vol_b1", "vol_b2")
        assert "rolling_log_return_vol" in row
        # Base fields preserved.
        assert row["strategy_name"] == "vwap"

    def test_symbol_mismatch_is_skipped(self):
        pred = _FakeShadow(_spec(symbol="MES", timeframe="5m"))
        row = feature_row_for_predictor(
            pred, {"x": 1}, closes=self._closes(), symbol="BTCUSDT", timeframe="5m"
        )
        assert row is None

    def test_timeframe_mismatch_is_skipped(self):
        pred = _FakeShadow(_spec(symbol="BTCUSDT", timeframe="15m"))
        row = feature_row_for_predictor(
            pred, {"x": 1}, closes=self._closes(), symbol="BTCUSDT", timeframe="5m"
        )
        assert row is None

    def test_uncomputable_vol_is_skipped(self):
        pred = _FakeShadow(_spec(symbol="BTCUSDT", timeframe="5m"))
        row = feature_row_for_predictor(
            pred, {"x": 1}, closes=[100.0], symbol="BTCUSDT", timeframe="5m"
        )
        assert row is None

    def test_symbol_matching_is_case_insensitive(self):
        pred = _FakeShadow(_spec(symbol="btcusdt", timeframe="5m"))
        row = feature_row_for_predictor(
            pred, {"x": 1}, closes=self._closes(), symbol="BTCUSDT", timeframe="5m"
        )
        assert row is not None


def test_regime_spec_of_handles_bare_predictor():
    bare = _FakeBase(_spec())
    # A bare base predictor (no `wrapped`) still surfaces its spec.
    assert regime_spec_of(bare) is not None
    assert regime_spec_of(_FakeShadow(None)) is None


# ---------------------------------------------------------------------------
# Equivalence guard for the O(n)->O(window) reverse-scan optimization of
# rolling_log_return_vol (perf fix 2026-05-31). The new implementation MUST be
# byte-identical to the original "compute all log returns, take last N" form —
# for every history length AND with non-positive closes present (the case a
# naive tail slice would get wrong). This locks the optimization to its oracle.
# ---------------------------------------------------------------------------
import random as _random  # noqa: E402


def _rolling_vol_reference(closes, vol_window_n):
    """The ORIGINAL pre-optimization algorithm, kept as the oracle."""
    if vol_window_n < 2:
        return None
    log_returns = []
    prev = None
    for c in closes:
        if prev is not None and prev > 0 and c > 0:
            log_returns.append(math.log(c / prev))
        prev = c
    window = log_returns[-vol_window_n:]
    if len(window) < 2:
        return None
    return statistics.pstdev(window)


def _assert_same_vol(closes, n):
    a = _rolling_vol_reference(closes, n)
    b = rolling_log_return_vol(closes, n)
    if a is None or b is None:
        assert a is None and b is None, (closes, n, a, b)
    else:
        assert abs(a - b) <= 1e-15, (closes, n, a, b)


def test_rolling_vol_equivalence_all_positive_fuzz():
    rng = _random.Random(20260531)
    for _ in range(2000):
        length = rng.randint(0, 80)
        closes = [round(rng.uniform(50.0, 50000.0), 4) for _ in range(length)]
        n = rng.choice([2, 3, 14, 20, 30, 50])
        _assert_same_vol(closes, n)


def test_rolling_vol_equivalence_with_nonpositive_closes_fuzz():
    """Skip-past-non-positive — exactly what a naive tail slice would break.
    The reverse scan must continue backwards past a zero/negative close to
    recover the true last-N valid pairs."""
    rng = _random.Random(99)
    for _ in range(2000):
        length = rng.randint(0, 80)
        closes = []
        for _ in range(length):
            if rng.random() < 0.15:
                closes.append(rng.choice([0.0, -1.0, -100.0]))
            else:
                closes.append(round(rng.uniform(50.0, 50000.0), 4))
        n = rng.choice([2, 3, 14, 20, 30])
        _assert_same_vol(closes, n)


def test_rolling_vol_equivalence_explicit_and_edges():
    _assert_same_vol([100.0, 101.0, 0.0, 102.0, 103.0, 104.0], 3)
    _assert_same_vol([100.0, 101.0, 0.0, 102.0, 103.0, 104.0], 20)
    for closes in ([], [100.0], [0.0, 0.0], [100.0, 0.0, 101.0]):
        _assert_same_vol(closes, 20)
