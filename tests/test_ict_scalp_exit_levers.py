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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
