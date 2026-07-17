"""Tests for the Slice-B/B2 FIFO broker-fee attribution (src/runtime/broker_cost_attribution.py)."""
from __future__ import annotations

from src.runtime.broker_cost_attribution import (
    attribute_roundtrip_fees,
    normalize_symbol,
)


def _fill(acct, sym, side, qty, fee, oid, t, is_maker=False, exec_id=None):
    return {
        "account_id": acct, "symbol": sym, "side": side, "qty": qty, "fee": fee,
        "order_id": oid, "exec_time": t, "is_maker": is_maker,
        "exec_id": exec_id or f"{oid}-{t}", "fee_currency": "USDT",
    }


def test_normalize_symbol():
    assert normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert normalize_symbol("BTCUSDT") == "BTCUSDT"
    assert normalize_symbol(None) == ""


def test_clean_roundtrip_attributes_both_legs():
    # One long trade: entry buy 1.0 (oid E), broker SL/TP sells 1.0 under an
    # untracked oid X. Entry + exit fee both land on the trade; it's clean.
    trades = [{"id": 1, "account_id": "bybit_2", "symbol": "BTCUSDT",
               "direction": "long", "broker_order_id": "E"}]
    fills = [
        _fill("bybit_2", "BTC/USDT:USDT", "buy", 1.0, 0.10, "E", "2026-07-01T00:00:00Z"),
        _fill("bybit_2", "BTC/USDT:USDT", "sell", 1.0, 0.12, "X", "2026-07-01T02:00:00Z"),
    ]
    out = attribute_roundtrip_fees(trades, fills)
    c = out[1]
    assert c.entry_matched and c.exit_matched and not c.ambiguous
    assert c.clean
    assert abs(c.fee_taker_usd - 0.22) < 1e-9   # 0.10 entry + 0.12 exit
    assert c.fee_maker_usd == 0.0


def test_entry_only_is_not_clean():
    # Still-open trade: only the entry fill exists → exit not matched → not clean.
    trades = [{"id": 2, "account_id": "bybit_2", "symbol": "BTCUSDT",
               "direction": "long", "broker_order_id": "E2"}]
    fills = [_fill("bybit_2", "BTCUSDT", "buy", 1.0, 0.10, "E2", "2026-07-01T00:00:00Z")]
    out = attribute_roundtrip_fees(trades, fills)
    c = out[2]
    assert c.entry_matched and not c.exit_matched and not c.clean


def test_maker_taker_split():
    trades = [{"id": 3, "account_id": "a", "symbol": "BTCUSDT",
               "direction": "long", "broker_order_id": "E3"}]
    fills = [
        _fill("a", "BTCUSDT", "buy", 1.0, 0.02, "E3", "t1", is_maker=True),   # maker entry
        _fill("a", "BTCUSDT", "sell", 1.0, 0.11, "X3", "t2", is_maker=False),  # taker exit
    ]
    c = attribute_roundtrip_fees(trades, fills)[3]
    assert c.clean
    assert abs(c.fee_maker_usd - 0.02) < 1e-9
    assert abs(c.fee_taker_usd - 0.11) < 1e-9


def test_overlapping_netted_trades_flagged_ambiguous():
    # Two long trades on the SAME account+symbol open before either closes →
    # the exchange nets them into one position; per-trade split is ambiguous.
    trades = [
        {"id": 10, "account_id": "a", "symbol": "BTCUSDT", "direction": "long", "broker_order_id": "A"},
        {"id": 11, "account_id": "a", "symbol": "BTCUSDT", "direction": "long", "broker_order_id": "B"},
    ]
    fills = [
        _fill("a", "BTCUSDT", "buy", 1.0, 0.10, "A", "t1"),   # trade 10 opens
        _fill("a", "BTCUSDT", "buy", 1.0, 0.10, "B", "t2"),   # trade 11 opens (now 2 lots)
        _fill("a", "BTCUSDT", "sell", 2.0, 0.24, "X", "t3"),  # both close
    ]
    out = attribute_roundtrip_fees(trades, fills)
    assert out[10].ambiguous and out[11].ambiguous
    assert not out[10].clean and not out[11].clean


def test_sequential_same_symbol_trades_stay_clean():
    # Two long trades on the same symbol but NON-overlapping (first flat before
    # second opens) → each is a clean, independently-attributable round trip.
    trades = [
        {"id": 20, "account_id": "a", "symbol": "BTCUSDT", "direction": "long", "broker_order_id": "A"},
        {"id": 21, "account_id": "a", "symbol": "BTCUSDT", "direction": "long", "broker_order_id": "B"},
    ]
    fills = [
        _fill("a", "BTCUSDT", "buy", 1.0, 0.10, "A", "t1"),   # 20 opens
        _fill("a", "BTCUSDT", "sell", 1.0, 0.12, "X", "t2"),  # 20 closes (flat)
        _fill("a", "BTCUSDT", "buy", 1.0, 0.10, "B", "t3"),   # 21 opens
        _fill("a", "BTCUSDT", "sell", 1.0, 0.12, "Y", "t4"),  # 21 closes
    ]
    out = attribute_roundtrip_fees(trades, fills)
    assert out[20].clean and out[21].clean
    assert abs(out[20].fee_taker_usd - 0.22) < 1e-9
    assert abs(out[21].fee_taker_usd - 0.22) < 1e-9
