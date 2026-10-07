"""TP doctrine B1, rule 1: the trend_donchian measured-move TP revision.

One pure core (`tp_revision.measured_move_path`) is called by the live unit's
monitor(), the prop trail (via plan_tp_revision) and scripts/backtest_trend.py's
lever; these tests pin that the three see the same bars and get the same level.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.runtime import tp_revision as tr
from src.units.strategies import trend_donchian as td

ROOT = Path(__file__).resolve().parents[1]
H = 3_600_000


def _frame(highs, lows, closes, t0=1_700_000_000_000):
    return pd.DataFrame({"timestamp": [t0 + i * H for i in range(len(highs))],
                         "open": closes, "high": highs, "low": lows, "close": closes,
                         "volume": [1.0] * len(highs)})


def test_measured_move_long_extends_with_structure_and_never_contracts():
    highs = [10, 11, 12, 13, 15, 14, 13]
    lows = [9, 9, 10, 11, 12, 12, 11]
    out1 = tr.measured_move_path(highs, lows, start=2, end=3, n=3, direction="long")
    t1, d1 = out1
    assert t1 == pytest.approx(13 + (13 - 9))                  # k=3: hi 13, lo 9
    t2, _ = tr.measured_move_path(highs, lows, start=2, end=4, n=3, direction="long")
    assert t2 == pytest.approx(15 + (15 - 10))                 # new extreme pushes it out
    t3, _ = tr.measured_move_path(highs, lows, start=2, end=6, n=3, direction="long")
    assert t3 == pytest.approx(t2)                             # pullback does not pull it in
    assert d1["n"] == 3 and d1["width_mult"] == 1.0


def test_measured_move_short_mirror_and_width_mult():
    highs = [20, 19, 18, 17]
    lows = [18, 17, 16, 14]
    t, _ = tr.measured_move_path(highs, lows, start=1, end=3, n=3, direction="short", width_mult=0.5)
    # k=2: lo 16, hi 20 -> 16-2=14 ; k=3: lo 14, hi 19 -> 14-2.5=11.5 (more favourable)
    assert t == pytest.approx(11.5)


def test_no_full_window_is_none():
    assert tr.measured_move_path([1, 2], [0, 1], start=0, end=1, n=5, direction="long") is None


def _uptrend(n=60, drift=1.0):
    closes = [100 + i * drift for i in range(n)]
    return ([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)


def _pkg(df, entry_i, rule=True, sl=None, tp=None):
    entry = float(df["close"].iloc[entry_i])
    meta = {"donchian": 20, "atr": 2.0, "trail_mult": 3.0, "risk_per_unit": 5.0,
            "entry_time": str(df["timestamp"].iloc[entry_i]), "timeframe": "1h"}
    if rule:
        meta["tp_revision"] = {"rule": "donchian_measured_move", "width_mult": 1.0}
    return {"order_package_id": "p", "strategy_name": "trend_donchian_eth_4h", "direction": "long",
            "entry": entry, "sl": sl if sl is not None else entry - 5.0,
            "tp": tp if tp is not None else entry * 1.5, "meta": meta}


def test_live_monitor_produces_a_reasoned_tp_when_declared():
    h, lo, c = _uptrend()
    df = _frame(h, lo, c)
    v = td.monitor({}, df, _pkg(df, entry_i=40))
    assert v is not None and "tp" in v and "donchian_measured_move" in v["tp_reason"]
    closed = df.iloc[:-1]
    want, _ = tr.measured_move_path(closed["high"].to_numpy(), closed["low"].to_numpy(),
                                    start=40, end=len(closed) - 1, n=20, direction="long")
    cur = float(df["close"].iloc[-1])
    want = min(want, cur * (1 + tr.TP_VENUE_CAP_PCT))
    assert v["tp"] == pytest.approx(round(want, 8))
    assert v["tp"] > cur


def test_live_monitor_without_declaration_never_moves_the_tp():
    h, lo, c = _uptrend()
    df = _frame(h, lo, c)
    v = td.monitor({}, df, _pkg(df, entry_i=40, rule=False))
    assert v is None or "tp" not in v


def test_live_monitor_merges_tp_with_the_trail_sl():
    h, lo, c = _uptrend()
    df = _frame(h, lo, c)
    v = td.monitor({}, df, _pkg(df, entry_i=40, sl=100.0))     # far stop: the trail ratchets
    assert "sl" in v and "tp" in v


def test_close_verdict_wins_over_a_revision():
    h, lo, c = _uptrend()
    df = _frame(h, lo, c)
    pkg = _pkg(df, entry_i=40, sl=float(df["close"].iloc[-1]) + 1)   # price through the stop
    v = td.monitor({}, df, pkg)
    assert v["action"] == "close" and "tp" not in v


def test_epoch_ms_entry_time_string_resolves_to_the_entry_bar():
    h, lo, c = _uptrend()
    df = _frame(h, lo, c)
    rev = tr.plan_tp_revision(leg={"donchian": 20, "tp_revision": "donchian_measured_move"},
                              direction="long", entry=float(c[40]), risk=5.0, bars=df.iloc[:-1],
                              entry_time=str(df["timestamp"].iloc[40]), ref_price=float(c[-1]))
    assert rev is not None and rev.detail["bars"] == len(df) - 1 - 40


def test_unreadable_entry_time_is_could_not_look():
    h, lo, c = _uptrend()
    df = _frame(h, lo, c)
    assert tr.plan_tp_revision(leg={"donchian": 20, "tp_revision": "donchian_measured_move"},
                               direction="long", entry=140.0, risk=5.0, bars=df.iloc[:-1],
                               entry_time="not a time", ref_price=float(c[-1])) is None


# ── harness lever ───────────────────────────────────────────────────────────
def _bt():
    spec = importlib.util.spec_from_file_location("_bt_trend_tprev", ROOT / "scripts/backtest_trend.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bt_trend_tprev"] = mod
    spec.loader.exec_module(mod)
    return mod


def _series(n=900, seed=7):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.05, 1.0, n)
    close = 100 + np.cumsum(steps)
    close = np.maximum(close, 5.0)
    high = close + np.abs(rng.normal(0, 0.6, n))
    low = close - np.abs(rng.normal(0, 0.6, n))
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": close, "high": high, "low": low,
                         "close": close, "volume": 1.0})


_KW = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.5, timeout_bars=10_000,
           cooldown_bars=0, timeframe="1h", symbol="TEST", tp_cap_pct=0.099, tp_r=50.0)


def test_harness_lever_off_is_byte_identical():
    bt = _bt()
    a = bt.run_backtest(_series(), **_KW)
    b = bt.run_backtest(_series(), **_KW, tp_revision="")
    assert a == b and "tp_revision" not in a.get("params", {})


def test_harness_lever_on_revises_and_changes_the_book():
    bt = _bt()
    off = bt.run_backtest(_series(), **_KW)
    on = bt.run_backtest(_series(), **_KW, tp_revision="donchian_measured_move")
    assert on["params"]["tp_revisions_applied"] > 0
    assert on != off


def test_harness_refuses_an_unmodelled_rule():
    with pytest.raises(ValueError):
        _bt().run_backtest(_series(), **_KW, tp_revision="something_else")


def test_harness_and_live_use_the_same_level_for_the_same_bars():
    """The harness's level after bar j == the live rule on bars[:j+1] with
    entry_time = the entry bar's timestamp (before the cap/price filters)."""
    df = _series()
    entry_i, j = 300, 340
    harness, _ = tr.measured_move_path(df["high"].to_numpy(), df["low"].to_numpy(),
                                       start=entry_i, end=j, n=20, direction="long")
    live = tr.RULES["donchian_measured_move"](
        direction="long", entry=float(df["close"].iloc[entry_i]), risk=5.0,
        bars=df.iloc[:j + 1], entry_time=df["timestamp"].iloc[entry_i],
        params={}, leg={"donchian": 20})
    assert math.isclose(live[0], harness)
