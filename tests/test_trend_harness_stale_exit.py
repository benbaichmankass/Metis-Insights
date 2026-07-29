"""Backtest-harness stale-exit lever (rec #5 regime-debt faithful re-run).

`scripts/backtest_trend.py` gained `--stale-exit-bars`/`--stale-exit-below-r`
so the regime-debt matrix can re-measure a Donchian variant (e.g.
`trend_donchian_sol`) with its declared exit lever ON instead of omitted — the
"faithful exit path" the matrix flagged (BL-20260717-REGIME-COVERAGE-DEBT).
This is the harness twin of `test_stale_stop_lever.py` (which covers the LIVE
`trend_donchian.monitor()` lever). Covers:
  * off (default) is byte-identical run-to-run (no-op);
  * the lever fires a `stale_stop` close on an old still-underwater trade;
  * the stop still wins over the stale-exit (stop-first);
  * regime_debt_matrix threads the flags + shrinks the omitted-lever list.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(module_name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(REPO, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod  # dataclass decorator reads sys.modules
    spec.loader.exec_module(mod)
    return mod


bt = _load("_bt_trend", "scripts/backtest_trend.py")


def _series() -> pd.DataFrame:
    """Warm-up oscillation (non-zero ATR, no breakout) → one clean breakout →
    an underwater plateau (below entry, above the stop) → a recovery leg. The
    plateau makes the trade old + underwater without a stop hit (the exact
    stale-exit condition); the later recovery makes the early cut MATTER — an
    off run rides to a better exit, so the two runs diverge instead of exiting
    at the same flat price."""
    ts = pd.date_range("2025-01-01", periods=90, freq="1h", tz="UTC")
    rows = []
    for i in range(90):
        if i < 35:                       # warm-up: oscillate ~100 ±0.5
            base = 100.0 + (0.5 if i % 2 else -0.5)
            hi, lo, cl = base + 0.5, base - 0.5, base
        elif i == 35:                    # breakout bar: close clears the channel
            hi, lo, cl = 103.0, 100.0, 103.0
        elif i < 46:                     # underwater plateau ~101.3 (bars 36-45)
            hi, lo, cl = 101.6, 101.0, 101.3
        else:                            # recovery leg back up to ~110
            v = 101.3 + (i - 45) * 0.6
            hi, lo, cl = v + 0.3, v - 0.3, v
        rows.append({"timestamp": ts[i], "open": cl, "high": hi,
                     "low": lo, "close": cl, "volume": 1.0})
    return pd.DataFrame(rows)


_KW = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=50.0,
           timeout_bars=200, cooldown_bars=1, timeframe="1h", symbol="SOLUSDT",
           long_only=True, min_confidence=0.0)


def test_off_is_deterministic_noop():
    df = _series()
    a = bt.run_backtest(df.copy(), **_KW)
    b = bt.run_backtest(df.copy(), **_KW)
    assert a["total_trades"] == b["total_trades"]
    assert a["net_total_r"] == b["net_total_r"]
    assert "stale_stop" not in a["by_outcome"]
    assert "stale_exit_bars" not in a["params"]


def test_lever_fires_stale_stop_on_old_underwater_trade():
    df = _series()
    off = bt.run_backtest(df.copy(), **_KW)
    on = bt.run_backtest(df.copy(), stale_exit_bars=3, stale_exit_below_r=0.0,
                         **_KW)
    # only the lever run cuts the underwater trade stale, and the early cut
    # changes the outcome (a stop-first stale close, not a no-op).
    assert "stale_stop" not in off["by_outcome"]
    assert on["by_outcome"].get("stale_stop", 0) >= 1
    assert on["net_total_r"] != off["net_total_r"]
    assert on["params"]["stale_exit_bars"] == 3


def test_stop_still_wins_over_stale_exit():
    """A bar that pierces the stop closes at the stop, not stale (stop-first)."""
    df = _series()
    # drop hard through the stop on the very next bar after the bar-35 entry
    df.loc[36, ["low", "close"]] = [90.0, 90.0]
    on = bt.run_backtest(df.copy(), stale_exit_bars=3, stale_exit_below_r=0.0,
                         **_KW)
    # the stop fires at bar 36 (before the stale window opens at bar 38), so
    # there is no stale_stop close on that trade.
    assert on["by_outcome"].get("stop", 0) + on["by_outcome"].get("trail_stop", 0) >= 1
    assert "stale_stop" not in on["by_outcome"]


def test_matrix_threads_flags_and_shrinks_omitted():
    sys.path.insert(0, os.path.join(REPO, "scripts/research"))
    import regime_debt_matrix as rdm  # type: ignore

    cfg = {"symbols": ["SOLUSDT"], "timeframe": "1h", "donchian": 20,
           "long_only": True, "stale_exit_bars": 12, "stale_exit_below_r": 0.0,
           "exit_head_model": "exit-head-donchian-1h-v1",
           "exit_head_threshold": 0.1, "exit_head_action": "close"}
    argv, faithful, omitted = rdm.build_harness_cmd(
        "trend_donchian_sol", cfg, "trend", "d.csv", "1h", "e.jsonl", "j.json")
    assert "--stale-exit-bars" in argv and "12" in argv
    assert "--stale-exit-below-r" in argv
    # stale-exit is now modeled → out of omitted; only the unreplayable
    # exit-head keeps the row approximate.
    assert "stale_exit_bars" not in omitted
    assert "stale_exit_below_r" not in omitted
    assert faithful is False  # exit_head_* still present → still approximate
