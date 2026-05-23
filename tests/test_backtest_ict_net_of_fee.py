"""Net-of-fee invariants for the ict_scalp backtest summary
(S-STRAT-IMPROVE-S4). Dep-light: constructs synthetic Trade objects and
checks the fee accounting directly, no candle feed / CLI needed."""
from __future__ import annotations

import importlib

import pandas as pd

bt = importlib.import_module("scripts.backtest_ict_scalp")


def _df():
    return pd.DataFrame({"timestamp": pd.to_datetime(
        ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"], utc=True)})


def _trade(direction, entry, exit_price, risk, r):
    return bt.Trade(
        entry_index=0, entry_time=0, direction=direction, entry=entry,
        sl=entry - risk if direction == "long" else entry + risk,
        tp=entry + 2 * risk if direction == "long" else entry - 2 * risk,
        risk=risk, exit_index=1, exit_time=1, exit_price=exit_price,
        outcome="tp_hit" if r > 0 else "sl_hit", r_multiple=round(r, 4),
    )


def test_net_equals_gross_minus_fee():
    trades = [
        _trade("long", 100.0, 102.0, 1.0, 2.0),
        _trade("short", 100.0, 100.5, 1.0, -0.5),
    ]
    s = bt._summarize(trades, _df(), timeframe="5m", symbol="BTCUSDT")
    assert s["total_r"] == round(2.0 + -0.5, 4)
    # net = gross - total_fee_r (within rounding)
    assert abs(s["net_total_r"] - (s["total_r"] - s["total_fee_r"])) < 1e-6
    assert s["total_fee_r"] > 0  # 7.5 bps default → positive drag
    assert s["net_total_r"] < s["total_r"]


def test_fee_zero_reproduces_gross(monkeypatch):
    monkeypatch.setattr(bt, "FEE_BPS_ROUNDTRIP", 0.0)
    trades = [_trade("long", 100.0, 102.0, 1.0, 2.0)]
    s = bt._summarize(trades, _df(), timeframe="5m", symbol="BTCUSDT")
    assert s["total_fee_r"] == 0.0
    assert s["net_total_r"] == s["total_r"]
    assert s["net_expectancy_r"] == s["expectancy_r"]


def test_tight_stop_makes_fee_large_fraction_of_r():
    # A tight stop (small risk vs price) makes the % fee a large R fraction.
    tight = bt._summarize([_trade("long", 80000.0, 80112.0, 112.0, 1.0)],
                          _df(), timeframe="5m", symbol="BTCUSDT")
    wide = bt._summarize([_trade("long", 80000.0, 80400.0, 400.0, 1.0)],
                         _df(), timeframe="5m", symbol="BTCUSDT")
    # Same gross R, but the tight-stop trade pays a bigger fee in R.
    assert tight["total_fee_r"] > wide["total_fee_r"]
