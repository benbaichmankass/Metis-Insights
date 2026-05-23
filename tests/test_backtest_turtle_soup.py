"""Net-of-fee invariants + summary-path smoke for the turtle_soup
backtest harness (S-STRAT-IMPROVE-S5). Dep-light: exercises _summarize
with synthetic Trade objects (the strategy's setup is rare, so we don't
rely on it firing on sample data)."""
from __future__ import annotations

import importlib

import pandas as pd

bt = importlib.import_module("scripts.backtest_turtle_soup")


def _df():
    return pd.DataFrame({"timestamp": pd.to_datetime(
        ["2026-01-01T00:00:00Z", "2026-01-01T00:15:00Z"], utc=True)})


def _trade(direction, entry, exit_price, risk, r):
    return bt.Trade(
        entry_index=0, entry_time=0, direction=direction, entry=entry,
        sl=entry - risk if direction == "long" else entry + risk,
        tp=entry + risk if direction == "long" else entry - risk,
        risk=risk, exit_index=1, exit_time=1, exit_price=exit_price,
        outcome="tp_hit" if r > 0 else "sl_hit", r_multiple=round(r, 4))


def test_net_equals_gross_minus_fee_with_legs():
    trades = [_trade("long", 100.0, 101.0, 1.0, 1.0),
              _trade("short", 100.0, 100.5, 1.0, -0.5)]
    s = bt._summarize(trades, _df(), timeframe="15m", symbol="BTCUSDT")
    assert s["strategy"] == "turtle_soup"
    assert s["total_r"] == round(1.0 + -0.5, 4)
    assert abs(s["net_total_r"] - (s["total_r"] - s["total_fee_r"])) < 1e-6
    # long/short net legs sum to the total net (within rounding)
    assert abs(s["net_total_r_long"] + s["net_total_r_short"] - s["net_total_r"]) < 1e-3
    assert s["total_fee_r"] > 0 and s["net_total_r"] < s["total_r"]


def test_fee_zero_reproduces_gross(monkeypatch):
    monkeypatch.setattr(bt, "FEE_BPS_ROUNDTRIP", 0.0)
    s = bt._summarize([_trade("long", 100.0, 101.0, 1.0, 1.0)],
                      _df(), timeframe="15m", symbol="BTCUSDT")
    assert s["total_fee_r"] == 0.0 and s["net_total_r"] == s["total_r"]


def test_empty_summary_has_net_keys():
    s = bt._summarize([], _df(), timeframe="15m", symbol="BTCUSDT")
    assert s["total_trades"] == 0
    for k in ("net_total_r", "net_expectancy_r", "net_win_rate_pct", "total_fee_r"):
        assert k in s
