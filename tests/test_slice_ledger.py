"""NETTED-SLICES: the slice ledger reconciles to the venue (src/runtime/positions.py)."""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime import positions as sl


def _journal(tmp_path, rows):
    db = tmp_path / "trade_journal.db"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
            " strategy_name TEXT, direction TEXT, position_size REAL, entry_price REAL,"
            " stop_loss REAL, take_profit_1 REAL, sl_order_id TEXT, tp_order_id TEXT,"
            " status TEXT, is_backtest INTEGER)"
        )
        for r in rows:
            c.execute(
                "INSERT INTO trades (account_id, symbol, strategy_name, direction,"
                " position_size, entry_price, stop_loss, take_profit_1, sl_order_id,"
                " tp_order_id, status, is_backtest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("account_id", "bybit_1"), r.get("symbol", "ETHUSDT"), r["strategy"],
                 r["direction"], r["qty"], r.get("entry", 100.0), r.get("sl", 95.0),
                 r.get("tp", 110.0), r.get("sl_id"), r.get("tp_id"),
                 r.get("status", "open"), r.get("bt", 0)),
            )
    return str(db)


def test_open_slices_reads_only_open_live_rows_for_the_book(tmp_path):
    db = _journal(tmp_path, [
        {"strategy": "a", "direction": "long", "qty": 1.0},
        {"strategy": "b", "direction": "long", "qty": 2.0},
        {"strategy": "c", "direction": "long", "qty": 9.0, "status": "closed"},
        {"strategy": "d", "direction": "long", "qty": 9.0, "bt": 1},
        {"strategy": "e", "direction": "long", "qty": 9.0, "symbol": "BTCUSDT"},
        {"strategy": "f", "direction": "long", "qty": 9.0, "account_id": "bybit_2"},
    ])
    rows = sl.open_slices("bybit_1", "ETHUSDT", db_path=db)
    assert [r["strategy_name"] for r in rows] == ["a", "b"]
    assert sl.ledger_net(rows) == pytest.approx(3.0)


def test_missing_journal_is_could_not_look_not_flat(tmp_path):
    assert sl.open_slices("bybit_1", "ETHUSDT", db_path=str(tmp_path / "nope.db")) is None
    assert sl.reconcile(None, [], "ETHUSDT")["state"] == sl.UNREADABLE


def test_reconcile_three_states():
    slices = [{"strategy_name": "a", "direction": "long", "position_size": 1.0},
              {"strategy_name": "b", "direction": "long", "position_size": 0.5}]
    ok = sl.reconcile(slices, [{"symbol": "ETHUSDT", "side": "Buy", "size": 1.5}], "ETHUSDT")
    assert ok["state"] == sl.RECONCILED and ok["gap"] == pytest.approx(0.0)
    bad = sl.reconcile(slices, [{"symbol": "ETHUSDT", "side": "Buy", "size": 1.0}], "ETHUSDT")
    assert bad["state"] == sl.DIVERGENT and bad["gap"] == pytest.approx(0.5)
    # venue flat but ledger holds slices -> divergent (we looked, it is not there)
    flat = sl.reconcile(slices, [], "ETHUSDT")
    assert flat["state"] == sl.DIVERGENT
    # venue unreadable -> unreadable, never "equal"
    assert sl.reconcile(slices, None, "ETHUSDT")["state"] == sl.UNREADABLE


def test_venue_net_sums_both_hedge_books_and_ignores_other_symbols():
    pos = [{"symbol": "ETHUSDT", "side": "Buy", "size": 2.0},
           {"symbol": "ETHUSDT", "side": "Sell", "size": 0.5},
           {"symbol": "BTCUSDT", "side": "Buy", "size": 7.0}]
    assert sl.venue_net(pos, "ETHUSDT") == pytest.approx(1.5)
    assert sl.venue_net([{"symbol": "ETHUSDT", "side": "", "size": 1.0}], "ETHUSDT") is None


def test_opposite_slices_in_a_hedge_book_reconcile_to_the_economic_net():
    slices = [{"strategy_name": "a", "direction": "long", "position_size": 2.0},
              {"strategy_name": "b", "direction": "short", "position_size": 0.5}]
    pos = [{"symbol": "ETHUSDT", "side": "Buy", "size": 2.0},
           {"symbol": "ETHUSDT", "side": "Sell", "size": 0.5}]
    assert sl.reconcile(slices, pos, "ETHUSDT")["state"] == sl.RECONCILED
    assert sl.strategy_signed_qty(slices, "b") == pytest.approx(-0.5)
    assert sl.has_opposite_slices(slices, "long") is True


def test_held_stop_risk_uses_current_stop_and_frees_budget_past_breakeven():
    slices = [
        {"strategy_name": "a", "direction": "long", "position_size": 2.0,
         "entry_price": 100.0, "stop_loss": 95.0},          # 10 at risk
        {"strategy_name": "b", "direction": "short", "position_size": 1.0,
         "entry_price": 100.0, "stop_loss": 104.0},         # 4 at risk
        {"strategy_name": "c", "direction": "long", "position_size": 3.0,
         "entry_price": 100.0, "stop_loss": 101.0},         # trailed past BE: 0
    ]
    assert sl.held_stop_risk(slices) == pytest.approx(14.0)
    assert sl.held_stop_risk(slices, exclude_strategies=["a"]) == pytest.approx(4.0)


def test_held_stop_risk_unmeasurable_slice_is_none_never_zero():
    slices = [{"strategy_name": "a", "direction": "long", "position_size": 2.0,
               "entry_price": 100.0, "stop_loss": None}]
    assert sl.held_stop_risk(slices) is None
    assert sl.held_stop_risk(slices, exclude_strategies=["a"]) == 0.0
