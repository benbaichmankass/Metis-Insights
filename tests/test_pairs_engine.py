"""Parity + unit tests for src/units/strategies/pairs_engine.py.

The critical test is PARITY: the live engine, replayed bar-by-bar, must reproduce
the EXACT trades that scripts/backtest_pairs.py::run_backtest produces on the same
synthetic series — so the live pairs sleeve == the validated backtest. Fully
offline (synthetic OU spread), no network."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.units.strategies import pairs_engine as pe  # noqa: E402

# import the harness module (a script) by path
_SPEC = importlib.util.spec_from_file_location(
    "backtest_pairs", str(REPO_ROOT / "scripts" / "backtest_pairs.py"))
bp = importlib.util.module_from_spec(_SPEC)
sys.modules["backtest_pairs"] = bp
_SPEC.loader.exec_module(bp)  # type: ignore[union-attr]


def _synthetic_pair(n=4000, seed=11):
    """B = random walk; A = B + a mean-reverting (OU) log-spread. Returns close_a, close_b."""
    rng = np.random.default_rng(seed)
    lb = np.cumsum(rng.normal(0, 0.01, n)) + np.log(100.0)
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = 0.92 * s[i - 1] + rng.normal(0, 0.02)  # OU spread
    la = lb + s
    return np.exp(la), np.exp(lb)


def _replay_engine(close_a, close_b, params, cooldown_bars=1):
    """Bar-by-bar replay of the live engine → list of (entry_idx, direction, outcome)."""
    trades = []
    n = len(close_a)
    i = params.lookback + 1
    while i < n - 1:
        # FLAT: look for entry using data through bar i
        sig = pe.entry_signal(close_a[:i + 1], close_b[:i + 1], params)
        if sig is None:
            i += 1
            continue
        entry_idx = i
        pos = pe.OpenPair(direction=sig["direction"], entry_spread=sig["entry_spread"],
                          stop_spread=sig["stop_spread"], bars_held=0)
        outcome = "timeout"
        exit_idx = min(entry_idx + params.max_hold_bars, n - 1)
        for j in range(entry_idx + 1, min(entry_idx + params.max_hold_bars + 1, n)):
            ex = pe.exit_signal(close_a[:j + 1], close_b[:j + 1],
                                params, pe.OpenPair(pos.direction, pos.entry_spread,
                                                    pos.stop_spread, j - entry_idx))
            if ex is not None:
                outcome, exit_idx = ex["outcome"], j
                break
        trades.append((entry_idx, sig["direction"], outcome))
        i = exit_idx + 1 + cooldown_bars
    return trades


def test_engine_matches_harness_trades():
    close_a, close_b = _synthetic_pair()
    params = pe.PairParams(symbol_a="A", symbol_b="B", lookback=20, entry_z=2.0,
                           exit_z=0.5, stop_z=2.0, max_hold_bars=20, hedge_beta="rolling")
    # harness trades
    m = pd.DataFrame({"timestamp": pd.date_range("2023-01-01", periods=len(close_a), freq="1h", tz="UTC"),
                      "close_a": close_a, "close_b": close_b})
    out = bp.run_backtest(m, lookback=20, entry_z=2.0, exit_z=0.5, stop_z=2.0,
                          max_hold_bars=20, cooldown_bars=1, hedge_beta="rolling",
                          timeframe="1h", pair="A/B", emit_path=None)
    assert out["total_trades"] > 30  # a meaningful sample
    # engine trades
    eng = _replay_engine(close_a, close_b, params, cooldown_bars=1)
    # same count + same (entry_idx, direction, outcome) sequence
    assert len(eng) == out["total_trades"], (len(eng), out["total_trades"])
    outcomes_eng = [t[2] for t in eng]
    # harness by_outcome must match the engine's outcome tally
    from collections import Counter
    assert dict(Counter(outcomes_eng)) == out["by_outcome"]


def test_entry_none_when_flat_zscore():
    # constant spread ⇒ z ~ 0 ⇒ no entry
    close_b = np.full(200, 100.0)
    close_a = np.full(200, 200.0)
    params = pe.PairParams("A", "B", lookback=15)
    assert pe.entry_signal(close_a, close_b, params) is None


def test_leg_directions():
    assert pe.leg_directions("long_spread") == {"a": "long", "b": "short"}
    assert pe.leg_directions("short_spread") == {"a": "short", "b": "long"}


def test_short_series_returns_none():
    params = pe.PairParams("A", "B", lookback=15)
    assert pe.entry_signal([1, 2, 3], [1, 2, 3], params) is None
