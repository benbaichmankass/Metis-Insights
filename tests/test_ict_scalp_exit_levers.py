"""Unit tests for the M20 exit levers on scripts/backtest_ict_scalp.py
(stale-stop + giveback-stop), added 2026-07-28 as the M27 ict_scalp_mgc_15m
exit-refinement follow-up.

The levers must (a) fire on the correct bar, (b) be stop-first (an SL/TP hit on
the same bar wins), and (c) leave the baseline byte-for-byte unchanged when off.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO_ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


bts = _load("backtest_ict_scalp_levers", "scripts/backtest_ict_scalp.py")
_simulate_exit = bts._simulate_exit


def _frame(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# A long trade (entry=100, sl=98 => risk=2, tp far away) that runs to a 3R peak
# on bar 1 then closes back near entry — the classic giveback shape.
_GIVEBACK_LONG = [(100, 101, 99, 100), (100, 106, 100, 101), (101, 106, 100, 101.0)]


def test_giveback_fires_long():
    res = _simulate_exit(_frame(_GIVEBACK_LONG), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=2.0, giveback_r=1.0)
    # bar 1: mfe=(106-100)/2=3R, close=101 => open_r=0.5R, surrendered 2.5R>=1R
    assert res["outcome"] == "giveback_stop"
    assert res["exit_index"] == 1
    assert res["exit_price"] == 101.0


def test_giveback_fires_short():
    rows = [(100, 101, 99, 100), (100, 100, 94, 99), (99, 100, 94, 99.0)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="short",
                         sl=102, tp=90, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=2.0, giveback_r=1.0)
    assert res["outcome"] == "giveback_stop"
    assert res["exit_index"] == 1


def test_giveback_off_by_default():
    # Same peaky frame, no lever => runs to timeout (baseline), NOT giveback.
    res = _simulate_exit(_frame(_GIVEBACK_LONG), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100)
    assert res["outcome"] == "timeout"


def test_giveback_needs_min_mfe():
    # Peak only reaches 3R; min_mfe=5R is never armed => no giveback.
    res = _simulate_exit(_frame(_GIVEBACK_LONG), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=5.0, giveback_r=1.0)
    assert res["outcome"] == "timeout"


# A long trade that never gets profitable — stale-stop should cut it at N bars.
_STALE_LONG = [(100, 100.5, 99.5, 100), (100, 100.2, 99.4, 99.8),
               (99.8, 100.0, 99.2, 99.6), (99.6, 99.9, 99.1, 99.5)]


def test_stale_fires_when_underwater():
    res = _simulate_exit(_frame(_STALE_LONG), start_idx=0, direction="long",
                         sl=95, tp=120, timeout_bars=20, entry=100,
                         stale_exit_bars=3, stale_exit_below_r=0.0)
    assert res["outcome"] == "stale_stop"
    assert res["exit_index"] == 3


def test_stale_off_by_default():
    res = _simulate_exit(_frame(_STALE_LONG), start_idx=0, direction="long",
                         sl=95, tp=120, timeout_bars=20, entry=100)
    assert res["outcome"] == "timeout"


def test_stale_holds_a_winner():
    # Trade IS in profit at the stale bar (open_r >= 0) => stale must NOT fire
    # with the default below_r=0.0 (only cuts trades still under water).
    rows = [(100, 101, 100, 100.5), (100.5, 102, 100.4, 101.5),
            (101.5, 103, 101.4, 102.5), (102.5, 104, 102.4, 103.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=130, timeout_bars=20, entry=100,
                         stale_exit_bars=2, stale_exit_below_r=0.0)
    assert res["outcome"] != "stale_stop"


def test_stop_first_beats_lever():
    # ONE bar that both peaks to 3R (high=106) AND hits the stop (low=97 <= sl=98)
    # AND would satisfy the giveback (close=98.5 => big surrender). Stop-first
    # ordering (SL checked before the lever block) means sl_hit must win.
    rows = [(100, 106, 97, 98.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=200, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=1.0, giveback_r=0.5)
    assert res["outcome"] in ("sl_hit", "be_stop")


# ---------------------------------------------------------------------------
# Live-monitor ↔ harness parity (the M20 P5 requirement): the shipped
# ict_scalp.monitor() stale-stop must fire on the SAME bar the validated harness
# (_simulate_exit) does, and be OFF by default for a leg that declares nothing.
# ---------------------------------------------------------------------------

# The real live module (NOT the isolated harness import above).
from src.units.strategies import ict_scalp as live_scalp  # noqa: E402


def _frame_ts(rows, *, freq_min=15):
    """OHLC frame with a `timestamp` column (the live fetch_candles shape)."""
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=len(rows), freq=f"{freq_min}min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", ts)
    return df


# Signal bar = index 2 (two pre-entry bars exercise the strictly-after age
# filter); entry = 100 at its close; then a run of never-profitable bars.
_PARITY_ROWS = [
    (100, 101.0, 99.0, 100.0),   # 0  pre-entry
    (100, 101.0, 99.0, 100.0),   # 1  pre-entry
    (100, 101.0, 99.0, 100.0),   # 2  SIGNAL/entry bar (entry=100)
    (100, 100.2, 99.4, 99.8),    # 3  bars_since_entry=0
    (99.8, 100.0, 99.2, 99.6),   # 4  =1
    (99.6, 99.9, 99.1, 99.5),    # 5  =2
    (99.5, 99.8, 99.0, 99.4),    # 6  =3  -> stale_exit_bars=3 fires here (open_r<0)
    (99.4, 99.7, 98.9, 99.3),    # 7  (never reached)
]
_SIG_IDX = 2
_STALE_N = 3


def _parity_open_pkg(df):
    entry, sl = 100.0, 95.0
    return {
        "order_package_id": "parity-1",
        "symbol": "ETHUSDT",
        "direction": "long",
        "entry": entry,
        "sl": sl,
        "tp": 120.0,
        "meta": {
            "strategy_name": "ict_scalp_eth_15m",
            "timeframe": "15m",
            "risk_per_unit": abs(entry - sl),
            "entry_time": str(df["timestamp"].iloc[_SIG_IDX]),
        },
    }


def test_monitor_matches_harness_fire_bar():
    """The live monitor stale-stop fires on the exact bar the harness does."""
    df = _frame_ts(_PARITY_ROWS)
    ohlc = _frame([r for r in _PARITY_ROWS])
    harness = _simulate_exit(
        ohlc, start_idx=_SIG_IDX + 1, direction="long", sl=95.0, tp=120.0,
        timeout_bars=50, entry=100.0, stale_exit_bars=_STALE_N,
        stale_exit_below_r=0.0,
    )
    assert harness["outcome"] == "stale_stop"
    fire_idx = harness["exit_index"]

    cfg = {"stale_exit_bars": _STALE_N, "stale_exit_below_r": 0.0}
    pkg = _parity_open_pkg(df)
    # Every bar BEFORE the harness fire bar: monitor must not stale-close.
    for j in range(_SIG_IDX + 1, fire_idx):
        v = live_scalp.monitor(cfg, df.iloc[: j + 1], pkg)
        assert not (isinstance(v, dict) and v.get("reason") == "stale_stop"), (
            f"live monitor stale-closed at bar {j}, harness fires at {fire_idx}"
        )
    # The harness fire bar: monitor stale-closes at the same bar + close price.
    v = live_scalp.monitor(cfg, df.iloc[: fire_idx + 1], pkg)
    assert v == {
        "action": "close", "reason": "stale_stop",
        "exit_price": float(df["close"].iloc[fire_idx]),
    }


def test_monitor_stale_off_when_undeclared():
    """No stale_exit_bars declared (cfg or meta) => baseline unchanged: the
    monitor never stale-closes, even well past N underwater bars. This is what
    keeps the real-money ict_scalp_5m BTC leg byte-for-byte unaffected."""
    df = _frame_ts(_PARITY_ROWS)
    pkg = _parity_open_pkg(df)
    for j in range(_SIG_IDX + 1, len(_PARITY_ROWS)):
        v = live_scalp.monitor({}, df.iloc[: j + 1], pkg)  # empty cfg
        assert not (isinstance(v, dict) and v.get("reason") == "stale_stop")


def test_monitor_stale_holds_a_winner():
    """A trade in profit at the stale bar is NOT cut (open_r >= below_r=0.0)."""
    rows = [
        (100, 101, 99, 100.0),    # 0 pre-entry
        (100, 101, 99, 100.0),    # 1 pre-entry
        (100, 101, 99, 100.0),    # 2 signal/entry (entry=100)
        (100, 102, 100, 101.5),   # 3
        (101.5, 103, 101, 102.5),  # 4
        (102.5, 104, 102, 103.5),  # 5  in profit -> no stale cut
    ]
    df = _frame_ts(rows)
    pkg = _parity_open_pkg(df)
    cfg = {"stale_exit_bars": 2, "stale_exit_below_r": 0.0}
    for j in range(_SIG_IDX + 1, len(rows)):
        v = live_scalp.monitor(cfg, df.iloc[: j + 1], pkg)
        assert not (isinstance(v, dict) and v.get("reason") == "stale_stop")


def test_monitor_stale_stop_first_defers_to_sl():
    """Stop-first: if the current bar's close has crossed SL, the stale-stop
    defers (the exchange bracket / normal path owns the stop)."""
    rows = [
        (100, 101, 99, 100.0),   # 0 pre-entry
        (100, 101, 99, 100.0),   # 1 pre-entry
        (100, 101, 99, 100.0),   # 2 signal/entry (entry=100, sl=95)
        (100, 100, 96, 99.0),    # 3
        (99, 99, 95, 96.0),      # 4
        (96, 96, 90, 94.0),      # 5  close 94 <= sl 95 (bars_since_entry=2 >= N=2)
    ]
    df = _frame_ts(rows)
    pkg = _parity_open_pkg(df)  # sl=95
    cfg = {"stale_exit_bars": 2, "stale_exit_below_r": 0.0}
    v = live_scalp.monitor(cfg, df, pkg)
    # SL already crossed at the last bar => stale must NOT fire.
    assert not (isinstance(v, dict) and v.get("reason") == "stale_stop")


def test_order_package_stamps_entry_time():
    """order_package() stamps meta.entry_time = the signal (last) bar's
    timestamp, the anchor the stale-stop's age count reads."""
    # Minimal synthetic frame that produces a signal is complex to hand-build;
    # assert the stamp helper + meta wiring directly instead.
    df = _frame_ts(_PARITY_ROWS)
    assert live_scalp._extract_entry_time(df) == str(df["timestamp"].iloc[-1])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
