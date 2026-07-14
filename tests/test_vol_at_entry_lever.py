"""M21 E-2 round 4 — vol-at-entry harness lever contract.

Contract (mirrors tests/test_skip_hours_lever.py):
  * Default (both pctl params 0.0) ⇒ byte-identical run — the percentile
    series is never even computed.
  * ``vol_skip_above_pctl`` skips a trigger whose trailing ATR percentile
    is in the hot tail; ``vol_skip_below_pctl`` the dead tail.
  * The percentile is TRAILING (causal): NaN until ``vol_pctl_window``
    bars exist → never skip (fail-permissive).
  * Exits are never touched — the levers gate NEW entries only.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from backtest_trend import backtest as trend_backtest  # noqa: E402
from backtest_pullback import run_backtest as pullback_backtest  # noqa: E402


def _df(rows):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame([{"timestamp": start + timedelta(hours=i),
                          "open": o, "high": h, "low": lo, "close": c,
                          "volume": 1.0}
                         for i, (o, h, lo, c) in enumerate(rows)])


def _trend_tape(n_flat=30, spike=False):
    """Flat tape then a breakout, then a flat tail so the timeout can close
    the trade (a position still open at tape end records no Trade).
    ``spike`` adds huge-DOWNSIDE-range bars (close unchanged) right before
    the breakout: ATR explodes but the upper Donchian edge stays ~100.5, so
    the long breakout still fires — with a hot trailing ATR percentile."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * n_flat
    if spike:
        rows += [(100.0, 100.5, 85.0, 100.0)] * 3
    rows += [(100.0, 103.0, 100.0, 102.5)]          # breakout trigger
    rows += [(102.0, 102.5, 101.5, 102.0)] * 8      # tail: timeout exit
    return _df(rows)


TREND_KW = dict(donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                timeout=5, long_only=False)


def test_trend_default_off_byte_identical():
    df = _trend_tape(spike=True)
    base = trend_backtest(df, **TREND_KW)
    off = trend_backtest(df, **TREND_KW,
                         vol_skip_above_pctl=0.0, vol_skip_below_pctl=0.0)
    assert [t.entry_time for t in base] == [t.entry_time for t in off]


def test_trend_hot_tail_skips():
    df = _trend_tape(spike=True)
    base = trend_backtest(df, **TREND_KW, vol_pctl_window=20)
    gated = trend_backtest(df, **TREND_KW, vol_skip_above_pctl=0.9,
                           vol_pctl_window=20)
    assert len(base) >= 1                # the breakout enters un-gated
    assert len(gated) < len(base)        # hot-ATR trigger skipped


def test_trend_dead_tail_skips():
    # Flat tape: every bar's ATR is identical → percentile rank 1.0 (max of
    # ties) — the hot gate at 0.9 fires, the dead gate at 0.1 does not.
    df = _trend_tape(n_flat=40, spike=False)
    base = trend_backtest(df, **TREND_KW, vol_pctl_window=20)
    lo = trend_backtest(df, **TREND_KW, vol_skip_below_pctl=0.1,
                        vol_pctl_window=20)
    assert [t.entry_time for t in base] == [t.entry_time for t in lo]


def test_trend_window_unfilled_never_skips():
    df = _trend_tape(spike=True)
    base = trend_backtest(df, **TREND_KW)
    gated = trend_backtest(df, **TREND_KW, vol_skip_above_pctl=0.9,
                           vol_pctl_window=500)   # window never fills
    assert [t.entry_time for t in base] == [t.entry_time for t in gated]


# ---------------------------------------------------------------- pullback --

PB_KW = dict(trend_lookback=10, pullback_lookback=5, pullback_frac=0.5,
             atr_period=5, atr_stop_mult=2.0, trail_mult=2.0,
             timeout_bars=50, cooldown_bars=0, timeframe="1h", symbol="T")

# Ramp → 2-bar pullback → up-close trigger at idx 23 → falling tail so the
# trail closes the trade. ``spike`` widens ONE pre-trigger bar (idx 22) so
# the trigger bar's trailing ATR percentile is hot (0.9 within a 10-bar
# window — a bigger spike would break the Donchian-midline uptrend filter).
_PB_CLOSES = ([100 + k * 1.4 for k in range(20)]
              + [127.0, 124.5, 123.2, 123.8] + [118.0] * 10)


def _pb_tape(spike=False):
    rows = []
    for k, c in enumerate(_PB_CLOSES):
        hi, lo = c + 0.5, c - 0.5
        if spike and k == 22:
            hi, lo = c + 3.0, c - 3.0
        rows.append((c, hi, lo, c))
    return _df(rows)


def test_pullback_default_off_byte_identical():
    df = _pb_tape(spike=True)
    base = pullback_backtest(df.copy(), **PB_KW)
    off = pullback_backtest(df.copy(), **PB_KW,
                            vol_skip_above_pctl=0.0, vol_skip_below_pctl=0.0)
    assert base["total_trades"] == off["total_trades"]
    assert base["net_total_r"] == off["net_total_r"]


def test_pullback_hot_tail_skips():
    df = _pb_tape(spike=True)
    base = pullback_backtest(df.copy(), **PB_KW, vol_pctl_window=10)
    # The trigger bar ranks 9th of 10 in its trailing window (pctl exactly
    # 0.9; the spike bar itself ranks higher) — gate strictly-above at 0.85.
    gated = pullback_backtest(df.copy(), **PB_KW, vol_skip_above_pctl=0.85,
                              vol_pctl_window=10)
    assert base["total_trades"] >= 1
    assert gated["total_trades"] < base["total_trades"]


def test_pullback_window_unfilled_never_skips():
    df = _pb_tape(spike=True)
    base = pullback_backtest(df.copy(), **PB_KW)
    gated = pullback_backtest(df.copy(), **PB_KW, vol_skip_above_pctl=0.9,
                              vol_pctl_window=500)
    assert base["total_trades"] == gated["total_trades"]
