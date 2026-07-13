"""M21 E-2 — harness confirmation-bar entry lever (research harness only).

Contract:
  * ``confirm_bars=0`` (default) ⇒ byte-identical trade list.
  * A one-bar false breakout (close pokes above the channel then falls
    back inside next bar) is ENTERED by the base engine but SKIPPED by
    ``confirm_bars=1``.
  * A sustained breakout is entered by both — the confirmed entry fires
    N bars later (worse price, same direction).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

from backtest_trend import backtest  # noqa: E402


def _df(closes):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i, c in enumerate(closes):
        rows.append({"timestamp": start + timedelta(hours=i),
                     "open": c, "high": c + 0.5, "low": c - 0.5,
                     "close": c, "volume": 1.0})
    return pd.DataFrame(rows)


def _tape(break_bars):
    # 20 flat bars build the channel, then the breakout shape under test,
    # then a long flat coda so any open position resolves.
    return _df([100.0] * 20 + break_bars + [100.0] * 20)


def test_default_off_is_byte_identical():
    df = _tape([104.0, 104.5, 105.0, 104.0])
    base = backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                    timeout=0, long_only=False)
    off = backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                   timeout=0, long_only=False, confirm_bars=0)
    assert [t.__dict__ for t in base] == [t.__dict__ for t in off]


def test_false_breakout_skipped_by_confirm_1():
    # One close above the channel (101.5 > flat-100 channel high 100.5),
    # then straight back inside and down through the stop — base enters
    # (and is stopped so the trade is recorded), confirm_1 must not.
    df = _df([100.0] * 20 + [101.5, 100.0] + [97.0] * 10 + [100.0] * 10)
    base = backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                    timeout=0, long_only=False)
    conf = backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                    timeout=0, long_only=False, confirm_bars=1)
    assert any(t.direction == "long" for t in base)
    assert not any(t.direction == "long" for t in conf)


def test_sustained_breakout_entered_later():
    df = _tape([101.5, 102.5, 103.5, 104.5, 105.0])
    base = backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                    timeout=0, long_only=False)
    conf = backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                    timeout=0, long_only=False, confirm_bars=1)
    b = next(t for t in base if t.direction == "long")
    c = next(t for t in conf if t.direction == "long")
    # confirmed entry is one bar later -> higher entry on a rising tape
    assert c.entry > b.entry
    assert pd.Timestamp(c.entry_time) > pd.Timestamp(b.entry_time)


# ── pullback-harness confirm lever (M21 E-2 round 2) ─────────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backtest_pullback import run_backtest as pb_run  # noqa: E402


def _pb(df, **kw):
    return pb_run(df, trend_lookback=10, pullback_lookback=5,
                  pullback_frac=0.5, atr_period=5, atr_stop_mult=2.0,
                  trail_mult=3.0, timeout_bars=50, cooldown_bars=1,
                  timeframe="1h", symbol="BTCUSDT", **kw)


def _pb_tape():
    # Steep uptrend (mid lags well below), shallow pullback into the lower
    # half of the recent 5-bar range that stays ABOVE the trend midline,
    # then a resume close (the trigger bar), then the shape under test.
    up = [100.0 + 1.5 * i for i in range(20)]     # to 128.5
    pull = [127.0, 125.5, 124.5]
    trigger = [125.2]
    return up + pull + trigger


def test_pb_default_off_byte_identical():
    df = _df(_pb_tape() + [126.0, 127.0, 128.0] + [118.0] * 10)
    a = _pb(df)
    b = _pb(df, confirm_bars=0)
    assert a["total_trades"] == b["total_trades"]
    assert a["net_total_r"] == b["net_total_r"]


def test_pb_failed_resume_skipped_by_confirm_1():
    # Trigger (125.2 > 124.5 resume), then straight back down — base enters
    # one trade, confirm_1 must not (123.0 not > trigger close 125.2).
    df = _df(_pb_tape() + [123.0] + [118.0] * 10)
    base = _pb(df)
    conf = _pb(df, confirm_bars=1)
    assert base["total_trades"] == 1
    assert conf["total_trades"] == 0


def test_pb_held_resume_enters_later():
    df = _df(_pb_tape() + [126.0, 127.0, 128.0] + [118.0] * 10)
    base = _pb(df)
    conf = _pb(df, confirm_bars=1)
    assert base["total_trades"] == 1 and conf["total_trades"] == 1
