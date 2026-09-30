"""`--flat-at-utc` / `--no-entry-after-utc` for scripts/backtest_trend.py (RQ-20260930-503 instrument).

Harness-only. Locks: (1) both levers empty = byte-identical to origin/main's harness on the committed
fixture; (2) a held position is force-closed at the close of the last bar ending at or before the
flatten time (reason `day_flat`) and never late; (3) a late entry is skipped; (4) a stop the same bar
hit still wins over the flatten; (5) an entry with no bar left to flatten on is not taken.
"""
from __future__ import annotations

import pandas as pd
import pytest

import scripts.backtest_trend as h

BASE = dict(donchian=20, atr_period=14, atr_stop_mult=2.0, trail_mult=5.0, timeout_bars=200,
            cooldown_bars=0, timeframe="1h", symbol="ETHUSDT")


def _reset():
    h.FEE_BPS_ROUNDTRIP = h.execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
    h.SLIPPAGE_BPS_ROUNDTRIP = 0.0
    h.FUNDING_BPS_PER_WINDOW = 0.0


def _frame(breakout_at: int, n: int = 140, crash_at: int | None = None):
    """Flat 100 range, one upside breakout at `breakout_at`, then a slow steady climb."""
    ts = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    rows = []
    px = 100.0
    for i in range(n):
        if i < breakout_at:
            o, hi, lo, c = 100.0, 100.5, 99.5, 100.0
        elif i == breakout_at:
            o, hi, lo, c = 100.0, 103.2, 100.0, 103.0
            px = 103.0
        else:
            px += 0.1
            o, hi, lo, c = px - 0.1, px + 0.15, px - 0.15, px
        if crash_at is not None and i == crash_at:
            o, hi, lo, c = px, px, px - 20.0, px - 20.0
        rows.append((ts[i], o, hi, lo, c, 1.0))
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _run(df, **kw):
    _reset()
    tr = []
    h.run_backtest(df.copy(), trades_out=tr, **BASE, **kw)
    return tr


def test_flat_closes_the_position_the_same_utc_day_at_the_last_bar_boundary():
    df = _frame(breakout_at=40)                 # day 2, entry at the 17:00 close
    held = _run(df)
    assert held and held[0].exit_index > 40 + 24, "control: without the lever the trade is held past midnight"
    flat = _run(df, flat_at_utc="23:45")
    t = flat[0]
    assert t.outcome == "day_flat"
    # 23:45 on 1h bars -> the bar opening 22:00 (closing 23:00) is the last that ends at or before it
    assert pd.Timestamp(t.exit_time).hour == 22
    assert pd.Timestamp(t.exit_time).normalize() == (pd.Timestamp(t.entry_time) + pd.Timedelta(hours=1)).normalize()
    assert t.exit_index == 24 + 22
    # closing at the 23:00 boundary is never AFTER the flatten time
    assert (pd.Timestamp(t.exit_time) + pd.Timedelta(hours=1)) <= (pd.Timestamp(t.exit_time).normalize()
                                                                    + pd.Timedelta(hours=23, minutes=45))


def test_late_entries_are_skipped():
    df = _frame(breakout_at=44)                  # day 2, entry moment 21:00 close
    assert _run(df)                              # control: it trades
    assert _run(df, no_entry_after_utc="20:00") == []
    assert _run(df, no_entry_after_utc="21:00")   # exactly at the limit is allowed


def test_a_stop_hit_on_the_flatten_bar_still_takes_the_stop():
    df = _frame(breakout_at=40, crash_at=46)     # the 22:00 bar collapses through the stop
    t = _run(df, flat_at_utc="23:45")[0]
    assert t.exit_index == 46 and t.outcome in ("stop", "trail_stop")


def test_an_entry_with_no_bar_left_to_flatten_on_is_not_taken():
    df = _frame(breakout_at=46)                  # entry moment 23:00 close: nothing ends <= 23:45 after it
    assert _run(df)
    assert _run(df, flat_at_utc="23:45") == []


def test_daily_bars_are_refused():
    ts = pd.date_range("2026-01-01", periods=80, freq="1D", tz="UTC")
    df = pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0})
    with pytest.raises(ValueError, match="intraday"):
        h.run_backtest(df, **BASE, flat_at_utc="23:45")


@pytest.mark.parametrize("bad", ["2345", "24:00", "ab:cd", "12:60x"])
def test_garbage_times_are_refused(bad):
    with pytest.raises(ValueError):
        h.run_backtest(_frame(34), **BASE, flat_at_utc=bad)


def test_close_stamped_bars_flatten_at_the_stamp_not_a_bar_early():
    """`_resample` stamps bars at their CLOSE (label="right"). Read as open-stamped they flatten a bar early."""
    df = _frame(breakout_at=40)
    df["timestamp"] = df["timestamp"] + pd.Timedelta(hours=1)          # the same bars, close-stamped
    as_close = _run(df, flat_at_utc="23:45", bar_label="close")[0]
    as_open = _run(df, flat_at_utc="23:45", bar_label="open")[0]
    assert pd.Timestamp(as_close.exit_time).hour == 23        # last bar CLOSING at or before 23:45 is 23:00
    assert pd.Timestamp(as_open.exit_time).hour == 22         # the misreading: a whole bar early
    late = _run(_frame(breakout_at=44).assign(
        timestamp=lambda d: d["timestamp"] + pd.Timedelta(hours=1)), no_entry_after_utc="21:00", bar_label="close")
    assert late, "an entry moment of exactly 21:00 is allowed when the stamps are read as closes"


def test_main_reads_resampled_bars_as_close_stamped_and_native_bars_as_open(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(h, "run_backtest", lambda df, **kw: seen.append(kw) or {"total_trades": 0, "symbol": "X", "timeframe": "1h"})
    csv = tmp_path / "c.csv"
    _frame(breakout_at=40).rename(columns={"timestamp": "timestamp"}).to_csv(csv, index=False)
    for extra, want in (([], "open"), (["--resample", "1h"], "close")):
        h.main(["backtest_trend.py", "--data", str(csv), "--symbol", "ETHUSDT", "--timeframe", "1h",
                "--flat-at-utc", "23:45", *extra])
        assert seen[-1]["bar_label"] == want, extra


def test_a_non_utc_timestamp_column_uses_the_UTC_day():
    df = _frame(breakout_at=40)
    utc = _run(df, flat_at_utc="23:45")[0]
    ny = df.copy()
    ny["timestamp"] = ny["timestamp"].dt.tz_convert("America/New_York")      # same instants, local clock
    local = _run(ny, flat_at_utc="23:45")[0]
    assert (local.entry_index, local.exit_index, local.outcome) == (utc.entry_index, utc.exit_index, utc.outcome)
    assert pd.Timestamp(local.exit_time).tz_convert("UTC").hour == 22


def test_bad_bar_label_is_refused():
    with pytest.raises(ValueError, match="bar_label"):
        h.run_backtest(_frame(40), **BASE, flat_at_utc="23:45", bar_label="middle")


def test_default_off_reproduces_the_stored_pre_lever_baseline(tmp_path):
    """Compares the CURRENT harness (levers unset) with a baseline generated from scripts/backtest_trend.py at
    2058a2f1, i.e. BEFORE the levers existed (tests/fixtures/trend_pre_dayflat_baseline.json). Unlike a diff
    against origin/main, this can fail after the levers merge."""
    import hashlib
    import json
    base = json.loads((h.Path(h.__file__).resolve().parents[1] / "tests/fixtures/trend_pre_dayflat_baseline.json")
                      .read_text())
    df = h._resample(h._load_candles("data/backtest_candles.csv"), "5min")
    h.FEE_BPS_ROUNDTRIP, h.SLIPPAGE_BPS_ROUNDTRIP, h.FUNDING_BPS_PER_WINDOW = base["fees"]
    emit = tmp_path / "rows.jsonl"
    out = h.run_backtest(df.copy(), emit_path=str(emit), **base["kwargs"])
    try:
        assert json.loads(json.dumps(out, default=str, sort_keys=True)) == base["summary"]
        rows = emit.read_text()
        assert len(rows.splitlines()) == base["emitted_rows_n"]
        assert hashlib.sha256(rows.encode()).hexdigest() == base["emitted_rows_sha256"]
        assert base["summary"]["total_trades"] > 0, "baseline produced no trades: equality would prove nothing"
    finally:
        _reset()
