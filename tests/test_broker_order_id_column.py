"""Slice B / B0 — trades.broker_order_id column + migration + open-writer.

The broker's entry orderId (already captured inside notes.trade_id) is promoted
to a first-class, indexed column so the broker-truth cost sweep joins to the
exchange-fills store exactly. These tests cover the schema migration
(idempotent, back-fillable on a legacy DB) and that the open-path insert
round-trips the column.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.units.db.database import Database, _migrate_add_broker_order_id


@pytest.fixture()
def tmp_db(tmp_path):
    return Database(db_path=str(tmp_path / "trade_journal.db"))


def _columns(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    finally:
        conn.close()


def test_fresh_db_has_broker_order_id_column_and_index(tmp_db):
    assert "broker_order_id" in _columns(str(tmp_db.db_path))
    conn = sqlite3.connect(str(tmp_db.db_path))
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_trades_broker_order_id" in idx
    finally:
        conn.close()


def test_migration_adds_column_to_legacy_table_and_is_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    try:
        # A legacy trades table without broker_order_id.
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, notes TEXT)")
        cur = conn.cursor()
        assert _migrate_add_broker_order_id(cur) is True   # added
        assert _migrate_add_broker_order_id(cur) is False  # idempotent no-op
        conn.commit()
    finally:
        conn.close()
    assert "broker_order_id" in _columns(str(db))


def test_insert_trade_round_trips_broker_order_id(tmp_db):
    trade_id = tmp_db.insert_trade({
        "timestamp": "2026-07-17T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "direction": "buy",
        "entry_price": 100.0,
        "position_size": 1.0,
        "status": "open",
        "is_backtest": 0,
        "account_id": "bybit_2",
        "broker_order_id": "bybit-entry-orderid-abc123",
    })
    conn = sqlite3.connect(str(tmp_db.db_path))
    try:
        got = conn.execute(
            "SELECT broker_order_id FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert got == "bybit-entry-orderid-abc123"
