"""Tests for the read-only Bybit bracket-coverage audit.

The case that matters most here is the one the live monitor currently MISSES:
under ``BYBIT_TPSL_MODE=partial`` a netted one-way position holds N journal
trades and N qty-scoped SL legs. If some legs are gone (rejected at Bybit's
20-leg cap, or cancelled when a sibling trade closed) the surviving leg still
satisfies ``order_monitor._bybit_position_protection``'s ``any()`` check, so the
sweep reports PROTECTED and skips — while the position is only PARTIALLY
covered. These tests pin the audit's *quantity* verdict so that blind spot
cannot silently return.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_PATH = os.path.join(
    os.path.dirname(_HERE), "scripts", "ops", "bybit_bracket_audit.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("bybit_bracket_audit", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


class FakeClient:
    """Minimal stand-in for the bot's Bybit client (read calls only)."""

    def __init__(self, position=None, legs=None, pos_raises=False, oo_raises=False):
        self._position = position
        self._legs = legs or []
        self._pos_raises = pos_raises
        self._oo_raises = oo_raises

    def get_positions(self, category=None, symbol=None):
        if self._pos_raises:
            raise RuntimeError("boom")
        rows = [self._position] if self._position else []
        return {"result": {"list": rows}}

    def get_open_orders(self, category=None, symbol=None, orderFilter=None):
        if self._oo_raises:
            raise RuntimeError("boom")
        return {"result": {"list": self._legs}}


def _pos(size, stop_loss="", tpsl_mode="Partial", side="Buy"):
    return {
        "size": str(size), "side": side, "stopLoss": stop_loss,
        "takeProfit": "", "tpslMode": tpsl_mode,
    }


def _sl_leg(qty, order_id="oid", trigger="100", kind="PartialStopLoss"):
    return {
        "orderId": order_id, "stopOrderType": kind, "qty": str(qty),
        "triggerPrice": trigger, "orderStatus": "Untriggered",
    }


# --------------------------------------------------------------- leg qty
def test_leg_qty_prefers_qty_then_trigger_qty():
    assert mod._leg_qty({"qty": "0.4"}) == 0.4
    assert mod._leg_qty({"triggerQty": "1.5"}) == 1.5
    assert mod._leg_qty({"size": "2"}) == 2.0
    # unparseable / absent must be None, never coerced to a number — an
    # unknown-qty leg must not be counted as coverage.
    assert mod._leg_qty({}) is None
    assert mod._leg_qty({"qty": "0"}) is None
    assert mod._leg_qty({"qty": "abc"}) is None


# ------------------------------------------------------- coverage verdicts
def test_full_mode_position_stop_covers_whole_position():
    c = FakeClient(position=_pos(0.4, stop_loss="95", tpsl_mode="Full"), legs=[])
    r = mod._audit_symbol(c, "linear", "XRPUSDT", [])
    assert r["verdict"] == "PROTECTED"
    assert r["coverage_source"] == "full_mode_position_stopLoss"
    assert r["sl_covered_qty"] == pytest.approx(0.4)
    assert r["uncovered_qty"] == pytest.approx(0.0)


def test_partial_legs_summing_to_size_is_protected():
    c = FakeClient(
        position=_pos(0.4),
        legs=[_sl_leg(0.2, "a"), _sl_leg(0.2, "b")],
    )
    r = mod._audit_symbol(c, "linear", "XRPUSDT", [])
    assert r["verdict"] == "PROTECTED"
    assert r["sl_covered_qty"] == pytest.approx(0.4)
    assert r["coverage_pct"] == pytest.approx(100.0)


def test_one_surviving_leg_on_a_netted_position_is_PARTIALLY_NAKED():
    """THE blind spot: any() says protected, quantity says half-naked."""
    c = FakeClient(position=_pos(0.4), legs=[_sl_leg(0.2, "a")])
    r = mod._audit_symbol(c, "linear", "XRPUSDT", [])
    assert r["verdict"] == "PARTIALLY_NAKED"
    assert r["sl_covered_qty"] == pytest.approx(0.2)
    assert r["uncovered_qty"] == pytest.approx(0.2)
    assert r["coverage_pct"] == pytest.approx(50.0)


def test_no_sl_leg_and_no_position_stop_is_NAKED():
    # A resting TP leg is NOT protection — only an SL leg is.
    c = FakeClient(
        position=_pos(0.4),
        legs=[{"orderId": "t", "stopOrderType": "PartialTakeProfit",
               "qty": "0.4", "triggerPrice": "150"}],
    )
    r = mod._audit_symbol(c, "linear", "XRPUSDT", [])
    assert r["verdict"] == "NAKED"
    assert r["sl_covered_qty"] == 0
    assert len(r["tp_legs"]) == 1


def test_flat_position_is_FLAT():
    assert mod._audit_symbol(FakeClient(position=_pos(0)), "linear", "X", [])[
        "verdict"] == "FLAT"
    assert mod._audit_symbol(FakeClient(position=None), "linear", "X", [])[
        "verdict"] == "FLAT"


def test_float_noise_within_epsilon_still_reads_protected():
    # Bybit echoes qty as a string at the instrument step; a hair of float
    # noise must not manufacture a coverage hole.
    c = FakeClient(position=_pos(0.3), legs=[_sl_leg(0.29999999, "a")])
    assert mod._audit_symbol(c, "linear", "X", [])["verdict"] == "PROTECTED"


def test_unparseable_leg_qty_marks_the_verdict_unreliable():
    """Never report a coverage number we cannot stand behind."""
    c = FakeClient(
        position=_pos(0.4),
        legs=[{"orderId": "a", "stopOrderType": "PartialStopLoss",
               "triggerPrice": "100"}],  # no qty at all
    )
    r = mod._audit_symbol(c, "linear", "X", [])
    assert "UNRELIABLE_LEG_QTY" in r["verdict"]
    assert r["sl_legs_unknown_qty"] == 1


# ------------------------------------------------- fail-safe on read errors
def test_read_failures_are_reported_not_guessed():
    r = mod._audit_symbol(FakeClient(pos_raises=True), "linear", "X", [])
    assert r["verdict"] == "UNKNOWN"
    assert "get_positions failed" in r["error"]

    r2 = mod._audit_symbol(
        FakeClient(position=_pos(0.4), oo_raises=True), "linear", "X", [])
    assert r2["verdict"] == "UNKNOWN"
    assert "get_open_orders failed" in r2["error"]


# --------------------------------------------- per-trade leg-liveness join
def test_per_trade_join_flags_dead_and_untracked_legs():
    rows = [
        {"id": 1, "symbol": "XRPUSDT", "direction": "long", "position_size": 0.2,
         "stop_loss": 0.9, "take_profit_1": 1.2, "sl_order_id": "alive",
         "tp_order_id": None, "strategy_name": "s1", "account_id": "bybit_2",
         "created_at": "x"},
        {"id": 2, "symbol": "XRPUSDT", "direction": "long", "position_size": 0.2,
         "stop_loss": 0.9, "take_profit_1": 1.2, "sl_order_id": "gone",
         "tp_order_id": None, "strategy_name": "s2", "account_id": "bybit_2",
         "created_at": "x"},
        {"id": 3, "symbol": "XRPUSDT", "direction": "long", "position_size": 0.2,
         "stop_loss": 0.9, "take_profit_1": 1.2, "sl_order_id": None,
         "tp_order_id": None, "strategy_name": "s3", "account_id": "bybit_2",
         "created_at": "x"},
        # different symbol — must be excluded from this symbol's join
        {"id": 4, "symbol": "BTCUSDT", "direction": "long", "position_size": 9.0,
         "stop_loss": 1.0, "take_profit_1": 2.0, "sl_order_id": "x",
         "tp_order_id": None, "strategy_name": "s4", "account_id": "bybit_2",
         "created_at": "x"},
    ]
    c = FakeClient(position=_pos(0.6), legs=[_sl_leg(0.2, "alive")])
    r = mod._audit_symbol(c, "linear", "XRPUSDT", rows)

    assert r["journal_open_trade_count"] == 3          # BTC row excluded
    assert r["journal_qty_sum"] == pytest.approx(0.6)
    assert r["trades_with_tracked_leg_alive"] == 1
    assert r["trades_with_tracked_leg_dead"] == 1      # 'gone' not at broker
    assert r["trades_with_no_tracked_leg"] == 1        # untracked
    # 0.6 net vs one 0.2 leg → two thirds unprotected
    assert r["verdict"] == "PARTIALLY_NAKED"
    assert r["uncovered_qty"] == pytest.approx(0.4)
