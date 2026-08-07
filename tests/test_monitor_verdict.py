"""The shared monitor-verdict interpreter (P2 · unified engine).

Two duties:

1. Pin the SEMANTICS extracted from ``order_monitor._apply_update`` — this is a
   live order-path refactor, so "behaviour preserving" has to be an assertion,
   not a claim.
2. Pin the three exit-path signals the backtest harness used to DROP
   (``exit_price`` / ``close_qty_pct`` / ``next_tp``), so a future edit that
   re-narrows the interpretation fails here rather than silently re-opening the
   backtest↔live gap.

The roster-shape test is the one that keeps this honest over time: it reads what
the REAL strategy monitors can emit and asserts the interpreter handles each
key, so adding a monitor that emits a new key surfaces as a failure instead of a
silent drop.
"""
from __future__ import annotations

import ast
import os

import pytest

from src.runtime.monitor_verdict import (
    KIND_CLOSE, KIND_MODIFY, KIND_NONE, KIND_PARTIAL_CLOSE,
    MEANINGFUL_MODIFY_REL_TOL, interpret_verdict,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# 1. semantics extracted from _apply_update
# --------------------------------------------------------------------------

def test_close_defaults_reason():
    d = interpret_verdict({"action": "close"})
    assert d.kind == KIND_CLOSE and d.reason == "monitor_close"


def test_close_qty_pct_one_is_a_full_close():
    """Live: 'close_qty_pct == 1.0 falls through to full-close below.'"""
    d = interpret_verdict({"action": "close", "close_qty_pct": 1.0})
    assert d.kind == KIND_CLOSE
    assert d.is_close


@pytest.mark.parametrize("pct", [0.0, -0.5, 1.5])
def test_close_qty_pct_out_of_range_is_rejected(pct):
    d = interpret_verdict({"action": "close", "close_qty_pct": pct})
    assert d.kind == KIND_NONE and d.rejection == "close_qty_pct_out_of_range"


def test_close_qty_pct_unparseable_is_rejected():
    d = interpret_verdict({"action": "close", "close_qty_pct": "half"})
    assert d.kind == KIND_NONE and d.rejection == "invalid_close_qty_pct"


def test_sl_and_tp_apply_independently():
    """Live uses two independent `if`s. The harness used an elif chain, which
    silently dropped the tp. Latent today (no roster monitor emits both) —
    pinned so it stays impossible rather than merely unlikely."""
    d = interpret_verdict({"sl": 100.0, "tp": 200.0}, current_sl=90.0,
                          current_tp=190.0)
    assert d.kind == KIND_MODIFY
    assert d.sl == 100.0 and d.tp == 200.0


def test_unknown_shape_is_rejected_with_a_cause():
    d = interpret_verdict({"something": 1})
    assert d.kind == KIND_NONE and d.rejection == "unknown_verdict_shape"


def test_noise_sized_modify_is_dropped():
    """BL-20260722-XRP-SLSPAM: a trail recomputed off a forming candle."""
    cur = 50_000.0
    noise = cur * MEANINGFUL_MODIFY_REL_TOL * 0.5
    d = interpret_verdict({"sl": cur + noise}, current_sl=cur)
    assert d.kind == KIND_NONE and d.rejection == "no_meaningful_change"


def test_real_trail_step_survives_the_filter():
    cur = 50_000.0
    d = interpret_verdict({"sl": cur * 1.01}, current_sl=cur)
    assert d.kind == KIND_MODIFY and d.sl == pytest.approx(cur * 1.01)


def test_unparseable_current_level_keeps_the_modify():
    """Live `except (TypeError, ValueError): continue` — a package whose stored
    level won't parse must not swallow a genuine trail."""
    d = interpret_verdict({"sl": 100.0}, current_sl="n/a")
    assert d.kind == KIND_MODIFY and d.sl == 100.0


def test_non_dict_verdict_is_rejected_not_raised():
    for bad in (None, "close", 3, []):
        d = interpret_verdict(bad)
        assert d.kind == KIND_NONE and d.rejection == "not_a_dict"


def test_every_none_carries_a_rejection():
    """A no-op must always name its cause — an unexplained 'nothing happened'
    is the silent-empty class this repo has a guard family for."""
    for v in (None, {}, {"x": 1}, {"action": "close", "close_qty_pct": 0}):
        d = interpret_verdict(v)
        if d.kind == KIND_NONE:
            assert d.rejection, f"KIND_NONE with no rejection for {v!r}"


# --------------------------------------------------------------------------
# 2. the three signals the harness dropped
# --------------------------------------------------------------------------

def test_exit_price_is_carried():
    """4 of 9 roster monitors emit it; the harness exited at the bar close."""
    d = interpret_verdict({"action": "close", "exit_price": 101.5,
                           "reason": "trail_stop"})
    assert d.exit_price == 101.5 and d.reason == "trail_stop"


def test_partial_close_is_not_a_close():
    """turtle_soup's TP1 scale-out. Conflating this with a full close is what
    made the runner — the part that earns the trend — vanish in backtest."""
    d = interpret_verdict({"action": "close", "close_qty_pct": 0.5,
                           "next_tp": 210.0, "exit_price": 205.0})
    assert d.kind == KIND_PARTIAL_CLOSE
    assert not d.is_close, "a partial must never read as a full close"
    assert d.close_qty_pct == 0.5
    assert d.next_tp == 210.0
    assert d.exit_price == 205.0


# --------------------------------------------------------------------------
# 3. the roster keeps the interpreter honest
# --------------------------------------------------------------------------

_HANDLED_KEYS = {"action", "reason", "exit_price", "close_qty_pct", "next_tp",
                 "sl", "tp"}


def _monitor_dict_keys(module_file: str) -> set[str]:
    with open(module_file, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "monitor"), None)
    if fn is None:
        return set()
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


@pytest.mark.parametrize("module", [
    "trend_donchian", "fade_breakout_4h", "squeeze_breakout_4h",
    "fvg_range_15m", "turtle_soup", "ict_scalp", "htf_pullback_trend_2h",
    "hf_displacement_cont", "hf_vwap_revert",
])
def test_roster_monitor_emits_no_key_the_interpreter_ignores(module):
    """If a strategy grows a new verdict key, this fails — instead of the key
    being silently dropped by whichever caller forgot about it. That silent
    drop IS the defect class this module was built to close.

    Scoped to the keys the interpreter is responsible for; a monitor's internal
    dict literals (meta payloads, config lookups) are not verdict keys, so the
    assertion is one-directional: every key that LOOKS like a verdict key must
    be handled, not every string key in the function.
    """
    path = os.path.join(_REPO, "src", "units", "strategies", f"{module}.py")
    if not os.path.exists(path):
        pytest.skip(f"{module} not present")
    verdictish = _monitor_dict_keys(path) & (
        _HANDLED_KEYS | {"close_qty", "qty_pct", "partial", "new_sl", "new_tp",
                         "exit", "price"})
    unhandled = verdictish - _HANDLED_KEYS
    assert not unhandled, (
        f"{module}.monitor() emits verdict-shaped key(s) {sorted(unhandled)} "
        f"that interpret_verdict does not handle — it would be silently "
        f"dropped by every caller")


def test_order_monitor_reexports_the_shared_tolerance():
    """Two copies of the tolerance is how the two paths drift apart."""
    from src.runtime import order_monitor
    assert order_monitor._MEANINGFUL_MODIFY_REL_TOL is MEANINGFUL_MODIFY_REL_TOL
