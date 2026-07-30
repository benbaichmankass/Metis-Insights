"""ROADMAP_MACRO M1 — PIT expectation model (`scripts/macro/econ_expectation.py`).

The gate change this model enables (operator-approved 2026-07-30) replaces archived
survey consensus with a model expectation. That is only legitimate if the model is
**provably** free of lookahead — otherwise the whole event study is unsafe, which is
precisely what M1's original stop condition guarded against.

`TestLeakageSafety` is the load-bearing suite: it mutates every future value and
asserts the expectation is bit-identical. Do not weaken it.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "macro"))

econ_expectation = pytest.importorskip("econ_expectation")
# stdlib-only by design — no numpy dependency (runs on a bare runner)

PERIOD = 12  # short period keeps fixtures small; the logic is period-agnostic


def _seasonal_series(n=120, level=100.0, amp=10.0, period=PERIOD, drift=0.0):
    """Deterministic seasonal series — no RNG, so tests are reproducible."""
    return [
        level + drift * t + amp * math.sin(2.0 * math.pi * (t % period) / period)
        for t in range(n)
    ]


class TestLeakageSafety:
    """The model may read values[:i] and NOTHING else."""

    def test_future_values_cannot_change_the_expectation(self):
        vals = _seasonal_series()
        i = 80
        base = econ_expectation.expectation_at(vals, i, period=PERIOD)
        assert base is not None

        # Corrupt everything from i onward, including the target itself.
        corrupted = list(vals)
        for j in range(i, len(corrupted)):
            corrupted[j] = -999999.0
        after = econ_expectation.expectation_at(corrupted, i, period=PERIOD)
        assert after == base, "expectation moved when only FUTURE values changed"

    def test_target_value_itself_is_never_read(self):
        vals = _seasonal_series()
        i = 60
        base = econ_expectation.expectation_at(vals, i, period=PERIOD)
        bumped = list(vals)
        bumped[i] = vals[i] * 1000.0
        assert econ_expectation.expectation_at(bumped, i, period=PERIOD) == base

    def test_truncating_at_i_is_equivalent_to_the_full_series(self):
        """Strongest form: the full series and a series truncated at i must agree,
        since only values[:i] is admissible input."""
        vals = _seasonal_series()
        i = 70
        full = econ_expectation.expectation_at(vals, i, period=PERIOD)
        trunc = econ_expectation.expectation_at(vals[:i] + [0.0], i, period=PERIOD)
        assert full == trunc

    def test_past_values_DO_change_it(self):
        """Control: the test above would also pass for a constant function."""
        vals = _seasonal_series()
        i = 70
        base = econ_expectation.expectation_at(vals, i, period=PERIOD)
        shifted = [v + 25.0 for v in vals[:i]] + vals[i:]
        assert econ_expectation.expectation_at(shifted, i, period=PERIOD) != base


class TestFitQuality:
    def test_recovers_a_clean_seasonal_level(self):
        """On a noiseless seasonal series the expectation should be very close."""
        vals = _seasonal_series(n=140)
        i = 120
        exp = econ_expectation.expectation_at(vals, i, period=PERIOD)
        assert exp is not None
        assert abs(exp - vals[i]) < 1.0, (exp, vals[i])

    def test_surprise_is_near_zero_on_a_perfectly_anticipated_series(self):
        vals = _seasonal_series(n=140)
        s = econ_expectation.surprise_series(vals, period=PERIOD)
        tail = [x for x in s[100:] if x is not None]
        assert tail
        assert max(abs(x) for x in tail) < 1.0

    def test_surprise_flags_a_genuine_shock(self):
        vals = _seasonal_series(n=140)
        shock_i = 130
        vals[shock_i] += 50.0
        s = econ_expectation.surprise_series(vals, period=PERIOD)
        assert s[shock_i] is not None
        assert s[shock_i] > 40.0, s[shock_i]


class TestHonestNulls:
    def test_thin_history_returns_none_not_a_guess(self):
        vals = _seasonal_series(n=200)
        assert econ_expectation.expectation_at(vals, 3, period=PERIOD) is None

    def test_index_zero_is_none(self):
        assert econ_expectation.expectation_at(_seasonal_series(), 0, period=PERIOD) is None

    def test_out_of_range_index_is_none(self):
        vals = _seasonal_series(n=30)
        assert econ_expectation.expectation_at(vals, 999, period=PERIOD) is None

    def test_nan_in_the_required_lag_returns_none(self):
        vals = _seasonal_series(n=140)
        i = 120
        vals[i - 1] = float("nan")          # lag_1 unusable
        assert econ_expectation.expectation_at(vals, i, period=PERIOD) is None

    def test_surprise_series_is_aligned_and_none_padded(self):
        vals = _seasonal_series(n=100)
        s = econ_expectation.surprise_series(vals, period=PERIOD)
        assert len(s) == len(vals)
        assert s[0] is None
        assert any(x is not None for x in s)

    def test_degenerate_period_returns_none(self):
        vals = _seasonal_series(n=140)
        assert econ_expectation.expectation_at(vals, 100, period=1) is None


class TestCadenceMapping:
    def test_known_cadences(self):
        assert econ_expectation.period_for_cadence("weekly") == 52
        assert econ_expectation.period_for_cadence("monthly") == 12
        assert econ_expectation.period_for_cadence("quarterly") == 4

    def test_unknown_cadence_falls_back(self):
        assert econ_expectation.period_for_cadence("fortnightly") == 52
        assert econ_expectation.period_for_cadence(None) == 52

    def test_spec_version_is_declared(self):
        """The spec is pre-registered; a change must be a visible version bump."""
        assert econ_expectation.SPEC_VERSION == "seasonal_ar_ols_v1"
