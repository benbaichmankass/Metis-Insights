"""M28 P1 — tests for the asset-class valuation core.

Covers the metric constructors + the ``value_read`` cheap/fair/rich engine +
the direction mapping. Pure functions, so these are exhaustive and fast."""

from __future__ import annotations

import pytest

from src.units.strategies.macro_thesis.valuation import (
    ValueRead,
    credit_spread,
    equity_risk_premium,
    gold_silver_ratio,
    real_yield,
    term_slope,
    value_read,
    value_to_direction,
)


# --------------------------------------------------------------------------
# Metric constructors
# --------------------------------------------------------------------------

def test_equity_risk_premium_basic():
    assert equity_risk_premium(0.055, 0.020) == pytest.approx(0.035)


def test_equity_risk_premium_bad_input_is_none():
    assert equity_risk_premium(float("nan"), 0.02) is None
    assert equity_risk_premium(0.05, float("inf")) is None
    assert equity_risk_premium(None, 0.02) is None  # type: ignore[arg-type]


def test_real_yield_fisher():
    assert real_yield(0.043, 0.024) == pytest.approx(0.019)


def test_gold_silver_ratio_and_div_guard():
    assert gold_silver_ratio(2000.0, 25.0) == pytest.approx(80.0)
    assert gold_silver_ratio(2000.0, 0.0) is None      # silver non-positive
    assert gold_silver_ratio(2000.0, -1.0) is None
    assert gold_silver_ratio(float("nan"), 25.0) is None


def test_credit_spread_passthrough():
    assert credit_spread(3.5) == 3.5
    assert credit_spread(float("nan")) is None


def test_term_slope():
    assert term_slope(4.2, 5.1) == pytest.approx(-0.9)   # inverted curve
    assert term_slope(4.5, float("nan")) is None


# --------------------------------------------------------------------------
# value_read — the cheap/fair/rich engine
# --------------------------------------------------------------------------

def test_value_read_higher_is_cheaper_high_reads_cheap():
    hist = [float(i) for i in range(101)]  # 0..100
    r = value_read("erp", 95.0, hist, higher_is_cheaper=True)
    assert r.label == "cheap"
    assert r.cheap_score is not None and r.cheap_score >= 0.7
    assert r.percentile == pytest.approx(r.cheap_score)  # same axis when higher_is_cheaper
    assert r.n == 101


def test_value_read_higher_is_cheaper_low_reads_rich():
    hist = [float(i) for i in range(101)]
    r = value_read("erp", 5.0, hist, higher_is_cheaper=True)
    assert r.label == "rich"
    assert r.cheap_score is not None and r.cheap_score <= 0.3


def test_value_read_orientation_inverts():
    hist = [float(i) for i in range(101)]
    # Same high raw value, but higher_is_richer (e.g. real yield): reads rich.
    r = value_read("real_yield", 95.0, hist, higher_is_cheaper=False)
    assert r.label == "rich"
    # cheap_score is 1 - percentile
    assert r.cheap_score is not None and r.percentile is not None
    assert r.cheap_score == pytest.approx(1.0 - r.percentile)


def test_value_read_middle_is_fair():
    hist = [float(i) for i in range(101)]
    r = value_read("erp", 50.0, hist, higher_is_cheaper=True)
    assert r.label == "fair"


def test_value_read_percentile_and_zscore():
    hist = [0.0, 10.0, 20.0, 30.0, 40.0]
    r = value_read("m", 20.0, hist, higher_is_cheaper=True)
    # 20 is the median: below=2, equal=1 → (2 + 0.5)/5 = 0.5
    assert r.percentile == pytest.approx(0.5)
    # z = (20 - 20) / pstdev = 0
    assert r.z_score == pytest.approx(0.0)


def test_value_read_empty_history_unknown():
    r = value_read("m", 5.0, [], higher_is_cheaper=True)
    assert r.label == "unknown"
    assert r.percentile is None and r.z_score is None and r.cheap_score is None
    assert r.note == "empty_history"


def test_value_read_nonfinite_value_unknown():
    r = value_read("m", float("nan"), [1.0, 2.0, 3.0], higher_is_cheaper=True)
    assert r.label == "unknown"
    assert r.value is None
    assert r.note == "value_not_finite"


def test_value_read_drops_nonfinite_history_samples():
    hist = [1.0, float("nan"), 2.0, None, 3.0, float("inf")]  # type: ignore[list-item]
    r = value_read("m", 2.0, hist, higher_is_cheaper=True)
    assert r.n == 3  # only the 3 finite samples counted


def test_value_read_single_sample_no_zscore():
    r = value_read("m", 5.0, [4.0], higher_is_cheaper=True)
    assert r.n == 1
    assert r.z_score is None          # need >= 2 for stdev
    assert r.percentile is not None    # percentile still defined
    assert r.note == "thin_history"


def test_value_read_zero_variance_history_no_zscore():
    r = value_read("m", 5.0, [5.0, 5.0, 5.0], higher_is_cheaper=True)
    assert r.z_score is None          # stdev 0 → no z


def test_value_read_thin_history_note():
    r = value_read("m", 5.0, [1.0, 2.0, 3.0], higher_is_cheaper=True)
    assert r.note == "thin_history"
    r2 = value_read("m", 5.0, [float(i) for i in range(30)], higher_is_cheaper=True)
    assert r2.note == ""


def test_value_read_bad_thresholds_fall_back_to_defaults():
    hist = [float(i) for i in range(101)]
    # rich_pct > cheap_pct is invalid → defaults (0.30/0.70) used
    r = value_read("m", 95.0, hist, higher_is_cheaper=True, cheap_pct=0.2, rich_pct=0.9)
    assert r.label == "cheap"   # 95th pct still cheap under the default 0.70 cut


def test_value_read_never_raises_on_junk():
    # Exercises the fail-permissive contract across odd inputs.
    for v, h in [(None, [1, 2]), (float("inf"), []), (5.0, [float("nan")])]:
        r = value_read("m", v, h, higher_is_cheaper=True)  # type: ignore[arg-type]
        assert isinstance(r, ValueRead)


# --------------------------------------------------------------------------
# value_to_direction
# --------------------------------------------------------------------------

def test_value_to_direction_cheap_is_bullish():
    hist = [float(i) for i in range(101)]
    r = value_read("erp", 95.0, hist, higher_is_cheaper=True)
    assert value_to_direction(r) == "bullish"


def test_value_to_direction_rich_is_bearish():
    hist = [float(i) for i in range(101)]
    r = value_read("erp", 5.0, hist, higher_is_cheaper=True)
    assert value_to_direction(r) == "bearish"


def test_value_to_direction_fair_is_neutral():
    hist = [float(i) for i in range(101)]
    r = value_read("erp", 50.0, hist, higher_is_cheaper=True)
    assert value_to_direction(r) == "neutral"


def test_value_to_direction_unknown_is_neutral():
    r = value_read("erp", float("nan"), [1.0, 2.0], higher_is_cheaper=True)
    assert value_to_direction(r) == "neutral"
