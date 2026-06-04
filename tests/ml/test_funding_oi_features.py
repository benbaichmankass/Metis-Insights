"""Tests for the funding/OI feature estimators (S-MLOPT-S11)."""
from __future__ import annotations

import math

from ml.datasets.funding_oi_features import (
    _finite_or_zero,
    change_zscore,
    diffs,
    extreme_magnitude,
    log_change,
    rolling_zscore,
)


def test_rolling_zscore_known_value():
    # window mean 1.8, pstdev of [1,1,1,1,5] = 1.6 → z of last (5) = 2.0
    z = rolling_zscore([1.0, 1.0, 1.0, 1.0, 5.0])
    assert math.isclose(z, 2.0, rel_tol=1e-9)


def test_rolling_zscore_zero_variance_is_none():
    assert rolling_zscore([2.0, 2.0, 2.0, 2.0]) is None


def test_rolling_zscore_too_short_is_none():
    assert rolling_zscore([1.0, 2.0]) is None  # < min_n=3


def test_rolling_zscore_ignores_none_but_needs_last():
    assert rolling_zscore([None, 1.0, 1.0, 5.0]) is not None
    assert rolling_zscore([1.0, 2.0, 3.0, None]) is None


def test_extreme_magnitude_is_abs():
    assert extreme_magnitude(-2.5) == 2.5
    assert extreme_magnitude(1.0) == 1.0
    assert extreme_magnitude(None) is None


def test_log_change_positive_for_rising():
    assert math.isclose(log_change([100.0, 110.0, 120.0]), math.log(1.2), rel_tol=1e-9)


def test_log_change_negative_for_falling():
    assert log_change([120.0, 110.0, 100.0]) < 0


def test_log_change_uses_first_positive_base():
    # leading None / non-positive skipped → base is first positive (100).
    assert math.isclose(log_change([None, 0.0, 100.0, 200.0]), math.log(2.0), rel_tol=1e-9)


def test_log_change_none_when_no_base_or_bad_last():
    assert log_change([0.0, 0.0]) is None
    assert log_change([100.0, None]) is None
    assert log_change([]) is None


def test_diffs():
    assert diffs([1.0, 3.0, 6.0]) == [2.0, 3.0]
    assert diffs([None, 1.0, None, 4.0]) == [3.0]


def test_change_zscore_flags_unusual_move():
    # steady +1 diffs then a big +10 → high positive z.
    z = change_zscore([1.0, 2.0, 3.0, 4.0, 14.0])
    assert z is not None and z > 1.5


def test_change_zscore_too_few_diffs_is_none():
    assert change_zscore([1.0, 2.0]) is None  # only 1 diff < min_n


def test_finite_or_zero():
    assert _finite_or_zero(None) == 0.0
    assert _finite_or_zero(float("nan")) == 0.0
    assert _finite_or_zero(float("inf")) == 0.0
    assert _finite_or_zero(3.5) == 3.5
