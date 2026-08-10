"""Tests for the ONE capital-efficiency definition (scripts/capital_efficiency.py).

The axis the exit-refinement gate has always DECLARED as its tiebreak and no
harness ever computed. These pin the two properties that make the number
trustworthy: unmeasurable is None (never 0.0), and the definition is shared so
a cross-harness comparison means something.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import capital_efficiency as ce  # noqa: E402


def _frame(minutes: int, n: int = 50) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=f"{minutes}min"),
        "close": [1.0] * n,
    })


@pytest.mark.parametrize("minutes", [5, 15, 60, 120, 240])
def test_bar_minutes_is_measured_from_the_frame_not_a_label(minutes):
    """A mislabelled or resampled frame must not silently rescale every
    derived number — the timestamps ARE the data, the label is a claim."""
    assert ce.bar_minutes_from_frame(_frame(minutes)) == float(minutes)


def test_bar_minutes_unmeasurable_is_none_not_a_default():
    assert ce.bar_minutes_from_frame(pd.DataFrame({"close": [1, 2, 3]})) is None
    assert ce.bar_minutes_from_frame(_frame(60, n=1)) is None


def test_days_unknown_bar_length_is_none_never_zero():
    """'We could not measure the hold' and 'the hold was zero' are opposite
    statements. A fabricated 0.0 would make the per-day rate infinite."""
    assert ce.days_from_bars(100, None) is None
    assert ce.days_from_bars(0, 60.0) is None


def test_summarize_every_key_present_and_arithmetic_is_right():
    # 24 bars of 60min = 1.0 position-day; 12 bars = 0.5 capital-days.
    out = ce.summarize(bar_minutes=60.0, position_bars=24.0, capital_bars=12.0,
                       net_total_r=10.0, n_trades=2)
    assert set(ce.KEYS) <= set(out)
    assert out["position_days"] == 1.0
    assert out["capital_days"] == 0.5
    assert out["mean_bars_held"] == 12.0
    assert out["net_r_per_position_day"] == 10.0
    # The whole point: freeing capital early DOUBLES the per-capital-day rate
    # while net_R is unchanged. A net_R-only gate cannot see this at all.
    assert out["net_r_per_capital_day"] == 20.0


def test_summarize_is_none_not_zero_when_bar_length_unknown():
    out = ce.summarize(bar_minutes=None, position_bars=24.0, capital_bars=24.0,
                       net_total_r=10.0, n_trades=2)
    assert out["position_days"] is None and out["capital_days"] is None
    assert out["net_r_per_position_day"] is None
    assert out["mean_bars_held"] == 12.0   # still measurable without bar length


def test_empty_run_rates_are_undefined_not_zero():
    """A run with no trades has an UNDEFINED rate. Emitting 0.0 would rank an
    un-run cell alongside a genuinely flat one."""
    out = ce.empty()
    assert set(out) == set(ce.KEYS)
    assert all(v is None for v in out.values())


def test_the_operators_shape_a_long_hold_that_earns_nothing():
    """The live complaint, as arithmetic: eth_pullback_2h held 149 bars on a 2h
    timeframe (12.4 days) for -0.33R. A 10-bar trade to the same net_R is a
    different object, and only the per-day rate says so."""
    slow = ce.summarize(bar_minutes=120.0, position_bars=149.0,
                        capital_bars=149.0, net_total_r=1.0, n_trades=1)
    fast = ce.summarize(bar_minutes=120.0, position_bars=10.0,
                        capital_bars=10.0, net_total_r=1.0, n_trades=1)
    assert slow["net_r_per_position_day"] < fast["net_r_per_position_day"]
    assert round(slow["position_days"], 1) == 12.4          # matches the live read
    assert fast["net_r_per_position_day"] / slow["net_r_per_position_day"] == pytest.approx(14.9, rel=1e-3)


def test_both_harnesses_import_the_same_definition():
    """The anti-drift assertion. Two candle readers, two trend engines and two
    regime-score derivations have each cost this repo an incident; a
    cross-harness metric with two definitions would be the next one."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for harness in ("backtest_ict_scalp.py", "backtest_pullback.py"):
        text = (scripts / harness).read_text(encoding="utf-8")
        assert "import capital_efficiency" in text, f"{harness} does not use the shared module"
        # ...and does not re-derive it locally.
        assert "def _days(" not in text, f"{harness} still has a local days helper"
