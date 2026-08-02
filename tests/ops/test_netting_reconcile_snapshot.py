"""Tests for scripts/ops/netting_reconcile_snapshot.py — the live-read half of
the netting partial-close reconcile (BL-20260801, option c+b). The pure
transform build_snapshot() is exercised for direction canonicalization
(long/short ↔ Buy/Sell), the flat-→size-0 rule, the could-not-read →
OMIT fail-safe, and resting-leg pass-through. No broker / DB is touched.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOD = os.path.join(_ROOT, "scripts", "ops", "netting_reconcile_snapshot.py")
_spec = importlib.util.spec_from_file_location("netting_reconcile_snapshot", _MOD)
nrs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nrs)


def test_canon_dir_maps_both_vocabularies():
    assert nrs._canon_dir("long") == nrs._canon_dir("Buy") == "long"
    assert nrs._canon_dir("short") == nrs._canon_dir("Sell") == "short"
    assert nrs._canon_dir("LONG") == "long"


def test_long_matches_buy_side_size():
    # journal direction 'long' must match the Bybit 'Buy' side position's size.
    groups = [("bybit_1", "BTCUSDT", "long")]
    exch = {"bybit_1": [{"symbol": "BTCUSDT", "side": "Buy", "size": 0.01}]}
    snap = nrs.build_snapshot(groups, exch)
    assert snap == {"bybit_1/BTCUSDT/long": {"size": 0.01}}


def test_readable_but_no_matching_position_is_size_zero():
    # Account read OK (flat for this symbol/direction) -> size 0 -> engine closes
    # the whole group as surplus. A Sell net position does NOT satisfy a 'long'
    # journal group.
    groups = [("bybit_1", "BTCUSDT", "long")]
    exch = {
        "bybit_1": [{"symbol": "BTCUSDT", "side": "Sell", "size": 5.0},
                    {"symbol": "ETHUSDT", "side": "Buy", "size": 2.0}],
    }
    snap = nrs.build_snapshot(groups, exch)
    assert snap["bybit_1/BTCUSDT/long"]["size"] == 0.0


def test_empty_list_is_flat_size_zero():
    groups = [("bybit_1", "SOLUSDT", "long")]
    snap = nrs.build_snapshot(groups, {"bybit_1": []})
    assert snap["bybit_1/SOLUSDT/long"]["size"] == 0.0


def test_unreadable_account_is_omitted():
    # None = could-not-read -> group OMITTED so the engine's exchange.get(gk) is
    # None fail-safe SKIPS it (never close on an unconfirmed read).
    groups = [("bybit_2", "BTCUSDT", "long")]
    snap = nrs.build_snapshot(groups, {"bybit_2": None})
    assert snap == {}


def test_account_absent_from_read_set_is_omitted():
    groups = [("bybit_2", "BTCUSDT", "long")]
    snap = nrs.build_snapshot(groups, {})  # account never read
    assert snap == {}


def test_resting_legs_passed_through():
    groups = [("bybit_1", "BTCUSDT", "long")]
    exch = {"bybit_1": [{"symbol": "BTCUSDT", "side": "Buy", "size": 0.5}]}
    legs = {("bybit_1", "BTCUSDT", "long"): ["legA", "legB"]}
    snap = nrs.build_snapshot(groups, exch, legs)
    assert snap["bybit_1/BTCUSDT/long"] == {"size": 0.5, "resting_legs": ["legA", "legB"]}


def test_key_carries_verbatim_journal_direction():
    # The emitted key must use the journal's verbatim direction so it lines up
    # with the engine's _group_key (which reads trades.direction). Even a legacy
    # 'buy'-vocabulary journal row emits its own verbatim value.
    groups = [("bybit_1", "BTCUSDT", "buy")]
    exch = {"bybit_1": [{"symbol": "BTCUSDT", "side": "Buy", "size": 0.01}]}
    snap = nrs.build_snapshot(groups, exch)
    assert "bybit_1/BTCUSDT/buy" in snap  # verbatim, not normalized to 'long'
    assert snap["bybit_1/BTCUSDT/buy"]["size"] == 0.01
