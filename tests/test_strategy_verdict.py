"""Unit tests for the canonical strategy ``monitor()`` verdict schema (P1).

The VALID cases are the union of every shape the 8 monitor-owning modules
actually emit on ``main`` (enumerated by reading their ``monitor()`` return
statements), plus the documented ``{"tp": ...}`` adjust. The INVALID cases are
representative malformed verdicts. ``validate_verdict`` is pure and must never
raise.
"""
from __future__ import annotations

import pytest

from src.runtime.strategy_verdict import (
    CLOSE_ACTION,
    is_close_verdict,
    validate_verdict,
)


# --------------------------------------------------------------------------- #
# VALID — every real shape the live monitors emit + None.
# --------------------------------------------------------------------------- #
VALID_VERDICTS = [
    # None no-op (every monitor's fall-through).
    None,
    # SL adjust — trend_donchian / htf_pullback / fade / squeeze trail
    #   `return {"sl": round(candidate, 8)}`
    {"sl": 80123.45678901},
    # SL adjust — _base.monitor_breakeven_sl `{"sl": entry + offset}` (turtle/ict_scalp).
    {"sl": 80700.0},
    # SL adjust — integer price level (e.g. an index future).
    {"sl": 5300},
    # TP adjust — documented in the base contract.
    {"tp": 81450.0},
    # Full close, with exit_price — sl_cross / tp_cross / vwap_cross
    #   `{"action": "close", "reason": "sl_cross", "exit_price": current_price}`
    {"action": "close", "reason": "sl_cross", "exit_price": 80300.0},
    {"action": "close", "reason": "tp_cross", "exit_price": 81450.0},
    {"action": "close", "reason": "vwap_cross", "exit_price": 80710.0},
    {"action": "close", "reason": "tp2_cross", "exit_price": 82000.0},
    # Full close, NO exit_price — fvg_range_15m time_decay
    #   `{"action": "close", "reason": "time_decay"}`
    {"action": "close", "reason": "time_decay"},
    # Full close with exit_price — fade_breakout_4h time_decay.
    {"action": "close", "reason": "time_decay", "exit_price": 1985.5},
    # turtle_soup TP1 partial roll — the richest shape.
    {
        "action": "close",
        "close_qty_pct": 0.25,
        "reason": "tp1_partial",
        "exit_price": 81450.0,
        "next_tp": 82000.0,
    },
    # Partial close at the boundary (full close expressed as pct=1.0).
    {"action": "close", "close_qty_pct": 1.0, "reason": "manual_full"},
]


@pytest.mark.parametrize("verdict", VALID_VERDICTS)
def test_valid_verdicts_accepted(verdict):
    ok, reason = validate_verdict(verdict)
    assert ok, f"expected valid, got reason={reason!r} for {verdict!r}"
    assert reason == "ok"


# --------------------------------------------------------------------------- #
# INVALID — representative malformed verdicts.
# --------------------------------------------------------------------------- #
INVALID_VERDICTS = [
    # Wrong top-level type.
    42,
    "close",
    [{"action": "close"}],
    # Empty / unrecognised dict.
    {},
    {"foo": "bar"},
    # Bad action.
    {"action": "halt"},
    {"action": "open", "reason": "x"},
    # Close missing / empty reason.
    {"action": "close"},
    {"action": "close", "reason": ""},
    {"action": "close", "reason": "   "},
    {"action": "close", "reason": 123},
    # close_qty_pct out of (0, 1].
    {"action": "close", "reason": "tp1", "close_qty_pct": 0.0},
    {"action": "close", "reason": "tp1", "close_qty_pct": 1.5},
    {"action": "close", "reason": "tp1", "close_qty_pct": -0.25},
    {"action": "close", "reason": "tp1", "close_qty_pct": "half"},
    {"action": "close", "reason": "tp1", "close_qty_pct": True},
    # Bad exit_price / next_tp on a close.
    {"action": "close", "reason": "sl_cross", "exit_price": 0},
    {"action": "close", "reason": "sl_cross", "exit_price": -10},
    {"action": "close", "reason": "sl_cross", "exit_price": "abc"},
    {"action": "close", "reason": "tp1_partial", "close_qty_pct": 0.25, "next_tp": -1},
    # Non-numeric / non-positive sl / tp.
    {"sl": "low"},
    {"sl": 0},
    {"sl": -80300.0},
    {"sl": None},
    {"sl": True},
    {"tp": 0.0},
    {"tp": float("nan")},
    {"tp": float("inf")},
    # Mutually-exclusive keys.
    {"sl": 80300.0, "tp": 81450.0},
    {"action": "close", "reason": "x", "sl": 80300.0},
]


@pytest.mark.parametrize("verdict", INVALID_VERDICTS)
def test_invalid_verdicts_rejected(verdict):
    ok, reason = validate_verdict(verdict)
    assert not ok, f"expected invalid, but accepted {verdict!r}"
    assert isinstance(reason, str) and reason and reason != "ok"


def test_validator_never_raises_on_weird_input():
    # A dict whose values throw on float() must still be handled gracefully.
    class Exploding:
        def __float__(self):  # pragma: no cover - exercised via validate
            raise RuntimeError("boom")

    for weird in (object(), {"sl": Exploding()}, {"close_qty_pct": Exploding()}):
        ok, reason = validate_verdict(weird)
        assert ok is False
        assert isinstance(reason, str)


def test_is_close_verdict_helper():
    assert is_close_verdict({"action": CLOSE_ACTION, "reason": "x"}) is True
    assert is_close_verdict({"sl": 1.0}) is False
    assert is_close_verdict(None) is False
    assert is_close_verdict("close") is False
