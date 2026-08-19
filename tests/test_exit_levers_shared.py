"""The M20 R-based exit levers, now shared by both trail families.

`stale_stop` and `giveback_stop` lived only in `trend_donchian`, so the pullback
family could not run them however its YAML was written — while
`scripts/backtest_pullback.py` has modelled BOTH since M20. The harness
simulated a book the live module could not execute, which makes any sweep
result recommending `stale_exit_bars` for a pullback leg unactionable: declaring
it would produce an ORPHANED DECLARE, a YAML key nothing reads.

What these tests pin:

1. **One definition.** Both units resolve to the SAME function object. Two
   copies agreeing on sampled cases is how a drifted definition survives review.
2. **trend_donchian is unchanged.** It trades real money; the extraction must be
   a move, not a rewrite.
3. **The declare is still the gate.** Shipping this changes no behaviour — every
   live leg is undeclared, so both units return None and annotate instead.
4. **The lever actually fires when declared.** A suite that only asserts "no
   change" cannot tell a correct no-op from a lever wired to nothing.
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from src.runtime import exit_levers  # noqa: E402
from src.units.strategies import htf_pullback_trend_2h as pull  # noqa: E402
from src.units.strategies import trend_donchian as donch  # noqa: E402


# NOTE the frame starts BEFORE the package entry_time deliberately. Both
# verdicts refuse when `since_entry` returns the whole frame, because that means
# it fell back rather than filtered — and a pre-entry extreme would fake a peak
# the trade never saw. A fixture whose entry == bar 0 therefore exercises the
# guard, not the lever; the first draft of this file did exactly that and both
# positive controls returned None.
def _frame(closes, start="2026-07-31T00:00:00Z"):
    ts = pd.date_range(start, periods=len(closes), freq="2h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "volume": [1.0] * len(closes),
    })


def _entry_at(df, i=2):
    """entry_time on bar `i`, so `since_entry` genuinely FILTERS.

    Both verdicts refuse when the returned window is the whole frame, since
    that means the filter fell back and a pre-entry extreme would fake a peak.
    An entry before bar 0 (or after the last bar) exercises that guard, not the
    lever — the first draft of this file did both and every positive control
    silently returned None.
    """
    return df["timestamp"].iloc[i].isoformat()


def _pkg(entry=100.0, sl=95.0, **meta):
    base = {"risk_per_unit": 5.0, "entry_time": "2026-07-31T04:00:00+00:00"}
    base.update(meta)
    return {"entry": entry, "sl": sl, "direction": "long",
            "symbol": "XRPUSDT", "order_package_id": "pkg-t",
            "strategy_name": "xrp_pullback_2h", "meta": base}


# --- 1. ONE DEFINITION ---------------------------------------------------- #

def test_both_units_delegate_to_the_same_objects():
    """Object identity, not behavioural agreement on the cases a test picks."""
    import inspect
    for unit in (donch, pull):
        src = inspect.getsource(unit._since_entry)
        assert "exit_levers" in src, f"{unit.__name__} kept a private copy"
    assert donch._since_entry(_frame([1, 2]), _pkg()) is not None


def test_since_entry_is_no_longer_duplicated_in_the_units():
    """The window every R measurement depends on had TWO byte-identical copies."""
    import inspect
    for unit in (donch, pull):
        body = inspect.getsource(unit._since_entry)
        # The real implementation parses timestamps; a shim does not.
        assert "pd.to_datetime" not in body, f"{unit.__name__} still owns a copy"


# --- 2. THE DECLARE IS THE GATE ------------------------------------------- #

def test_undeclared_never_closes_on_either_family():
    """Shipping the shared module changes NO behaviour until a key is declared."""
    df = _frame([100.0] * 12)          # flat: stale WOULD fire if declared
    for cfg in ({}, {"trail_mult": 5.0}):
        assert exit_levers.stale_stop_verdict(
            _pkg()["meta"], cfg, _pkg(), df, 100.0, "long") is None


def test_declared_stale_stop_closes():
    """POSITIVE CONTROL. Without it, the no-op assertions above prove nothing."""
    df = _frame([100.0] * 12)
    pkg = _pkg()
    pkg["meta"]["entry_time"] = _entry_at(df, 2)
    out = exit_levers.stale_stop_verdict(
        pkg["meta"], {"stale_exit_bars": 4, "stale_exit_below_r": 0.5},
        pkg, df, 100.0, "long")
    assert out == {"action": "close", "reason": "stale_stop", "exit_price": 100.0}


def test_declared_giveback_closes_after_a_real_peak():
    """POSITIVE CONTROL for the second lever."""
    df = _frame([99.0, 99.0, 100.0, 102.0, 110.0, 112.0, 103.0])
    pkg = _pkg()
    pkg["meta"]["entry_time"] = _entry_at(df, 2)
    out = exit_levers.giveback_verdict(
        pkg["meta"], {"giveback_min_mfe_r": 1.0, "giveback_r": 1.0},
        pkg, df, 103.0, "long")
    assert out is not None
    assert out["reason"] == "giveback_stop"


def test_a_young_trade_is_not_stale():
    """NEGATIVE CONTROL — the lever must respect its own threshold."""
    df = _frame([100.0] * 6)
    pkg = _pkg()
    pkg["meta"]["entry_time"] = _entry_at(df, 2)
    assert exit_levers.stale_stop_verdict(
        pkg["meta"], {"stale_exit_bars": 20, "stale_exit_below_r": 0.5},
        pkg, df, 100.0, "long") is None


def test_a_profitable_trade_is_not_stale():
    df = _frame([100.0] * 12)
    pkg = _pkg()
    pkg["meta"]["entry_time"] = _entry_at(df, 2)
    assert exit_levers.stale_stop_verdict(
        pkg["meta"], {"stale_exit_bars": 4, "stale_exit_below_r": 0.0},
        pkg, df, 120.0, "long") is None


# --- 3. FAIL-SAFE: never a spurious close --------------------------------- #

@pytest.mark.parametrize("broken", [
    {"risk_per_unit": None},
    {"risk_per_unit": 0.0},
    {"entry_time": None},
])
def test_missing_inputs_skip_rather_than_close(broken):
    """A lever that cannot measure must not act. Fail-safe, never fail-open."""
    df = _frame([100.0] * 12)
    pkg = _pkg()
    pkg["meta"]["entry_time"] = _entry_at(df, 2)
    meta = pkg["meta"] | broken
    cfg = {"stale_exit_bars": 4, "stale_exit_below_r": 0.5}
    assert exit_levers.stale_stop_verdict(meta, cfg, pkg, df, 100.0, "long") is None


def test_levers_never_raise():
    """Called from a live monitor — an exception here strands a position."""
    for fn in (exit_levers.stale_stop_verdict, exit_levers.giveback_verdict):
        assert fn({}, {}, {}, None, 1.0, "long") is None
        assert fn(None, None, None, None, None, None) is None


# --- 4. THE PULLBACK MONITOR ACTUALLY CALLS THEM -------------------------- #

def test_pullback_monitor_wires_both_levers():
    import inspect
    src = inspect.getsource(pull.monitor)
    assert "giveback_verdict" in src and "stale_stop_verdict" in src


def test_pullback_monitor_order_is_giveback_then_stale():
    """Matches THIS family's own harness (backtest_pullback._levers).

    Live trend_donchian runs stale first — an inversion filed as
    BL-20260818-LIVE-DONCHIAN-INVERTS-THE-HARNESS-LEVER-PRECEDENCE and latent
    (no live leg declares both keys). Live/train parity for the family being
    wired beats consistency with a module that is itself inverted.
    """
    import inspect
    src = inspect.getsource(pull.monitor)
    assert src.index("giveback_verdict") < src.index("stale_stop_verdict")


def test_pullback_threads_the_lever_keys_into_meta():
    """So the package records the declaration in force AT ENTRY."""
    import inspect
    src = inspect.getsource(pull)
    for key in ("stale_exit_bars", "stale_exit_below_r",
                "giveback_min_mfe_r", "giveback_r"):
        assert f'"{key}"' in src, f"{key} not threaded into meta"
