"""Tests for the PURE decision core of src/units/strategies/pairs_executor.py.
The live I/O layer (run_pairs_tick) is exercised on the VM paper soak, not here."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.units.strategies import pairs_engine as pe  # noqa: E402
from src.units.strategies import pairs_executor as px  # noqa: E402


def _params():
    # hedge_beta="one" so the engine spread == the synthetic construction (beta=1),
    # making the jammed last bar a deterministic high-z entry.
    return pe.PairParams("SOLUSDT", "BTCUSDT", lookback=15, entry_z=2.0,
                         exit_z=0.5, stop_z=2.0, max_hold_bars=20, hedge_beta="one")


def _open_from_entry(ca, cb, bars_held):
    sig = pe.entry_signal(ca, cb, _params())
    assert sig is not None
    return pe.OpenPair(direction=sig["direction"], entry_spread=sig["entry_spread"],
                       stop_spread=sig["stop_spread"], bars_held=bars_held)


def _extended_spread(n=120, seed=1):
    """Series whose LATEST bar has a large |z| (extended spread → entry fires)."""
    rng = np.random.default_rng(seed)
    lb = np.cumsum(rng.normal(0, 0.005, n)) + np.log(50000.0)
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = 0.9 * s[i - 1] + rng.normal(0, 0.01)
    s[-1] = s[-2] + 0.15  # jam the last bar far from the mean → high z
    la = lb + s
    return np.exp(la), np.exp(lb)


def test_skip_flat_when_low_z():
    ca = np.full(120, 100.0)
    cb = np.full(120, 50000.0)
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "skip_flat" and not d.legs


def test_open_produces_two_legs_opposite_directions():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "open", d.soak
    assert len(d.legs) == 2
    dirs = {leg.symbol: leg.direction for leg in d.legs}
    assert set(dirs.values()) == {"long", "short"}          # market-neutral
    assert all(leg.qty > 0 and leg.sl > 0 and leg.tp > 0 for leg in d.legs)
    assert d.soak["pairs_group_id"].startswith("pair-")


def test_skip_concurrency_when_leg_held():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols={"BTCUSDT"},
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "skip_concurrency" and not d.legs


def test_skip_size_when_budget_zero():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=0.0, correlation_open=0)
    assert d.event == "skip_size" and not d.legs


def test_shadow_mode_downgrades_open():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0, execution_mode="shadow")
    assert d.event == "shadow_open"
    assert len(d.legs) == 2 and d.close is False           # computed but NOT placed


def test_hold_when_open_and_no_exit():
    # open on the jammed high-z bar: spread still extended (no revert), stop not
    # breached (sj == entry), within max_hold → hold.
    ca, cb = _extended_spread()
    pos = _open_from_entry(ca, cb, bars_held=1)
    d = px.decide_pair(_params(), ca, cb, open_state=pos, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "hold" and not d.close


def test_close_on_timeout():
    ca, cb = _extended_spread()
    pos = _open_from_entry(ca, cb, bars_held=99)  # past max_hold → timeout
    d = px.decide_pair(_params(), ca, cb, open_state=pos, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "close" and d.close is True
    assert d.soak["outcome"] == "timeout"


def test_correlation_haircut_reduces_budget():
    ca, cb = _extended_spread()
    d0 = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                        risk_budget_usd=100.0, correlation_open=0)
    d2 = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                        risk_budget_usd=100.0, correlation_open=2, corr_factor=0.5)
    # 2 correlated open → 0.25× budget → smaller qty
    assert d2.legs[0].qty < d0.legs[0].qty


def test_monitor_always_none():
    assert px.monitor({}, None, {}) is None
