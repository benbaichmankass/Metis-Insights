"""Smoke tests for run_smoke_test.py — daily-resolution harness validation.

Exercises the toy VWAP adapter and the bucket-slicer on synthetic
fixtures. The full network-dependent ``main()`` happy-path is
exercised manually (it pulls from coinmetrics/data); these unit
tests stay deterministic.
"""
from __future__ import annotations


import numpy as np
import pandas as pd

from scripts.sprint015 import run_smoke_test as st
from scripts.sprint015 import sample_data as sd


def _frame(prices, freq="1D"):
    idx = pd.date_range("2024-01-01", periods=len(prices), freq=freq, tz="UTC")
    closes = np.asarray(prices, dtype=float)
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": np.full_like(closes, 1.0),
    }, index=idx)


def test_toy_vwap_strategy_emits_signals_on_extremes():
    """A square-wave price series with a long lookback should fire at
    least one buy and one sell — confirms the deviation arithmetic
    isn't dead and the adapter is actually computing the rolling VWAP."""
    prices = (
        [100.0] * 30
        + [120.0] * 5  # spike up — should fire sell
        + [100.0] * 5
        + [80.0] * 5   # spike down — should fire buy
        + [100.0] * 5
    )
    frame = _frame(prices)
    sigs = st._toy_vwap_strategy(frame, {"threshold_std": 1.0, "lookback": 20})
    sides = {s["side"] for s in sigs}
    assert "buy" in sides and "sell" in sides
    assert all(s["qty"] == 1.0 for s in sigs)


def test_toy_vwap_strategy_no_signals_for_flat_series():
    frame = _frame([100.0] * 50)
    sigs = st._toy_vwap_strategy(frame, {"threshold_std": 1.0, "lookback": 20})
    assert sigs == []


def test_toy_vwap_strategy_short_frame_returns_empty():
    """If the frame is shorter than the lookback the adapter must yield
    nothing rather than error out — needed because individual fold
    slices may be very small for a particular month."""
    frame = _frame([100.0] * 5)
    sigs = st._toy_vwap_strategy(frame, {"threshold_std": 1.0, "lookback": 20})
    assert sigs == []


def test_slice_for_buckets_filters_to_requested_months():
    """Sampler hands us buckets; this slicer must keep ONLY rows whose
    (year, month) match a bucket — no leakage into other months."""
    idx = pd.date_range("2024-01-15", periods=180, freq="1D", tz="UTC")
    frame = pd.DataFrame({
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
    }, index=idx)
    buckets = [sd.MonthBucket(2024, 2), sd.MonthBucket(2024, 4)]
    out = st._slice_for_buckets(frame, buckets)
    months = sorted({(ts.year, ts.month) for ts in out.index})
    assert months == [(2024, 2), (2024, 4)]
    assert len(out) > 0


def test_format_per_fold_shapes_match_report_template():
    """Report rendering relies on every dict carrying the same keys —
    pin the contract so a refactor of the harness can't break the
    markdown table silently."""
    from scripts.sprint015.run_backtest import BacktestResult, FoldMetrics

    result = BacktestResult(
        strategy="toy", params={},
        folds=[
            FoldMetrics(realised_pnl=10.0, n_trades=2, win_rate=0.5,
                        sharpe=0.1, max_drawdown=2.0, trades=[]),
            FoldMetrics(realised_pnl=-5.0, n_trades=1, win_rate=0.0,
                        sharpe=-0.2, max_drawdown=5.0, trades=[]),
        ],
    )
    rows = st._format_per_fold(result)
    assert [r["fold"] for r in rows] == [0, 1]
    for r in rows:
        assert set(r.keys()) == {
            "fold", "n_trades", "realised_pnl",
            "win_rate", "sharpe", "max_drawdown",
        }
