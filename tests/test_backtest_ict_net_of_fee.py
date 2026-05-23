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


# --- exit-grid break-even logic (S6 variation sweep) ---

def _bars(rows):
    return pd.DataFrame(rows, columns=["timestamp", "high", "low", "close"]).assign(
        timestamp=lambda d: pd.to_datetime(
            ["2026-01-01T00:00:00Z"] * len(d), utc=True))


def test_exit_be_tp_hit():
    df = _bars([[0, 102.5, 100.1, 102.0]])  # high reaches tp=102
    r = bt._simulate_exit_be(df, start_idx=0, direction="long", entry=100.0,
                             sl=99.0, tp=102.0, be_trigger_r=None, timeout_bars=5)
    assert r["outcome"] == "tp_hit" and r["exit_price"] == 102.0


def test_exit_be_sl_hit_no_be():
    df = _bars([[0, 100.2, 98.9, 99.0]])  # low breaches sl=99
    r = bt._simulate_exit_be(df, start_idx=0, direction="long", entry=100.0,
                             sl=99.0, tp=102.0, be_trigger_r=None, timeout_bars=5)
    assert r["outcome"] == "sl_hit" and r["exit_price"] == 99.0


def test_exit_be_moves_to_entry_then_stops():
    # risk=1; be_trigger_r=0.5 -> arm at 100.5. Bar1 arms (high 101) but
    # doesn't stop; bar2 low 99.9 <= entry(100) -> be_stop at entry, not -1R.
    df = _bars([[0, 101.0, 100.2, 100.8], [0, 100.4, 99.9, 100.0]])
    r = bt._simulate_exit_be(df, start_idx=0, direction="long", entry=100.0,
                             sl=99.0, tp=103.0, be_trigger_r=0.5, timeout_bars=5)
    assert r["outcome"] == "be_stop" and r["exit_price"] == 100.0


def test_exit_be_timeout():
    df = _bars([[0, 100.4, 99.6, 100.1], [0, 100.3, 99.7, 100.2]])
    r = bt._simulate_exit_be(df, start_idx=0, direction="long", entry=100.0,
                             sl=99.0, tp=103.0, be_trigger_r=None, timeout_bars=1)
    assert r["outcome"] == "timeout"
