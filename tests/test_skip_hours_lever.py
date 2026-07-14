"""M21 E-2 — harness time-of-day entry lever (research harnesses only).

Contract (both harnesses):
  * ``skip_hours=""`` (default) ⇒ byte-identical trade list.
  * A NEW entry whose TRIGGER bar's UTC hour is in the CSV set is
    suppressed; the tape is then re-evaluated normally, so a fresh
    trigger on a later (non-skipped) bar may still enter — the lever
    moves/skips entries, it never rewrites history around them.
  * Hours not in the set are untouched; exits are never touched (an
    open trade rides through skipped hours unchanged).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

from backtest_trend import backtest  # noqa: E402
from backtest_pullback import run_backtest  # noqa: E402


# ---------------------------------------------------------------- donchian --

def _df(closes):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # bar i ⇒ hour i % 24
    rows = []
    for i, c in enumerate(closes):
        rows.append({"timestamp": start + timedelta(hours=i),
                     "open": c, "high": c + 0.5, "low": c - 0.5,
                     "close": c, "volume": 1.0})
    return pd.DataFrame(rows)


def _trend(df, **kw):
    return backtest(df, donchian=10, atr_p=5, atr_stop=2.0, trail_mult=3.0,
                    timeout=0, long_only=False, **kw)


def _breakout_tape():
    # 20 flat bars build the channel; sustained breakout at bar 20 (hour
    # 20); long coda so the position resolves.
    return _df([100.0] * 20 + [104.0, 104.5, 105.0, 104.0] + [100.0] * 20)


def test_trend_default_off_is_byte_identical():
    df = _breakout_tape()
    base = _trend(df)
    off = _trend(df, skip_hours="")
    assert [t.__dict__ for t in base] == [t.__dict__ for t in off]


def test_trend_signal_hour_skipped():
    df = _breakout_tape()
    base = _trend(df)
    assert base, "tape must produce a baseline trade"
    hr = pd.Timestamp(base[0].entry_time).hour
    skipped = _trend(df, skip_hours=str(hr))
    assert all(pd.Timestamp(t.entry_time).hour != hr for t in skipped)


def test_trend_non_matching_hour_unchanged():
    df = _breakout_tape()
    base = _trend(df)
    hr = pd.Timestamp(base[0].entry_time).hour
    other = _trend(df, skip_hours=str((hr + 3) % 24))
    assert [t.__dict__ for t in base] == [t.__dict__ for t in other]


# ---------------------------------------------------------------- pullback --

_PB_KW = dict(trend_lookback=10, pullback_lookback=5, atr_period=5,
              pullback_frac=0.5, min_confidence=0.0, atr_stop_mult=2.0,
              trail_mult=2.0, timeout_bars=50, cooldown_bars=0,
              timeframe="1h", symbol="TEST")


def _pb_tape():
    # Ramp builds an uptrend (ends 126.6 at idx 19), a 2-bar pullback into
    # the lower half of the 5-bar range, then the up-close TRIGGER at
    # idx 23 (hour 23). The follow-through bar at idx 24 (hour 0) forms a
    # fresh trigger of its own; the dump coda closes whatever entered.
    closes = ([100 + k * 1.4 for k in range(20)]
              + [127.0, 124.5, 123.2, 123.8]
              + [124.5, 125.5, 126.5]
              + [118.0] * 10)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)  # idx k ⇒ hour k % 24
    rows = [{"timestamp": (start + timedelta(hours=k)).isoformat(),
             "open": c, "high": c + 0.4, "low": c - 0.4, "close": c,
             "volume": 1.0} for k, c in enumerate(closes)]
    return pd.DataFrame(rows)


def _pb_entry_hours(df, emit, **extra):
    summary = run_backtest(df, **_PB_KW, emit_path=str(emit), **extra)
    hours = []
    if emit.exists():
        import json
        hours = [pd.Timestamp(json.loads(line)["entry_time"]).hour
                 for line in emit.read_text().splitlines() if line.strip()]
        emit.unlink()
    return summary, hours


def test_pullback_default_off_is_byte_identical(tmp_path):
    df = _pb_tape()
    base, base_h = _pb_entry_hours(df, tmp_path / "a.jsonl")
    off, off_h = _pb_entry_hours(df, tmp_path / "b.jsonl", skip_hours="")
    base.pop("run_date", None), off.pop("run_date", None)
    assert base_h == [23] and off_h == [23]
    assert "skip_hours" not in off["params"]
    assert base == off


def test_pullback_skip_moves_entry_to_next_fresh_trigger(tmp_path):
    # Skipping the hour-23 trigger does NOT kill the setup: the hour-0
    # follow-through bar is itself a valid fresh trigger and enters.
    df = _pb_tape()
    _, hours = _pb_entry_hours(df, tmp_path / "e.jsonl", skip_hours="23")
    assert hours == [0]


def test_pullback_skipping_both_trigger_hours_kills_the_entry(tmp_path):
    df = _pb_tape()
    summary, hours = _pb_entry_hours(df, tmp_path / "e.jsonl",
                                     skip_hours="23,0")
    assert summary["total_trades"] == 0 and hours == []
    assert summary["params"]["skip_hours"] == "0,23"


def test_pullback_non_matching_hour_unchanged(tmp_path):
    df = _pb_tape()
    _, hours = _pb_entry_hours(df, tmp_path / "e.jsonl", skip_hours="5")
    assert hours == [23]
