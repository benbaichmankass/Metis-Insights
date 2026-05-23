"""Smoke + net-of-fee invariants for the trend-follower backtest
(S-STRAT-IMPROVE-S7). Dep-light."""
from __future__ import annotations

import importlib

import pandas as pd

bt = importlib.import_module("scripts.backtest_trend")


def _synthetic_uptrend(n=120, step=50.0):
    # Monotone uptrend so a Donchian breakout long fires and trails up.
    ts = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    close = [10000.0 + i * step for i in range(n)]
    rows = {"timestamp": ts,
            "open": [c - step / 2 for c in close],
            "high": [c + step / 2 for c in close],
            "low": [c - step for c in close],
            "close": close}
    return pd.DataFrame(rows)


def test_atr_positive():
    df = _synthetic_uptrend()
    atr = bt._atr(df, 14)
    assert (atr.dropna() > 0).all()


def test_uptrend_produces_long_trade():
    df = _synthetic_uptrend()
    s = bt.run_backtest(df, donchian=20, atr_period=14, atr_stop_mult=2.5,
                        trail_mult=3.0, timeout_bars=200, cooldown_bars=1,
                        timeframe="1h", symbol="BTCUSDT")
    assert s["total_trades"] >= 1
    assert s["trades_long"] >= 1
    # net = gross - fees (within rounding), and fees are positive
    assert abs(s["net_total_r"] - (s["total_r"] - s["total_fee_r"])) < 1e-2
    assert s["total_fee_r"] >= 0


def test_empty_summary_has_net_keys():
    s = bt._summarize([], _synthetic_uptrend(), timeframe="1h",
                      symbol="BTCUSDT", params={})
    assert s["total_trades"] == 0
    for k in ("net_total_r", "net_expectancy_r", "total_fee_r"):
        assert k in s
