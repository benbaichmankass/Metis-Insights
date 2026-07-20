"""Netted-position cascade close + proration guard
(BL-20260720-ICTSCALP-PASTSTOP-EXITS).

Covers:
  * ``order_monitor._cascade_close_netted_siblings`` — when the reconciler
    observes the (account, symbol) position FLAT and closes one row, every
    other DB-open same-direction row on that netted position closes too,
    with the same exit fill and a qty-prorated pnl share.
  * The proration guard inside ``_close_trade_from_order_status`` — a
    closed-pnl record whose qty exceeds the row's share is prorated, never
    booked whole onto one row.

The incident fixture mirrors the Jun 21-23 2026 bybit_2 BTCUSDT rows:
several strategies' journal trades sharing one netted long position; a
position-level bracket fire flattened everything but only the newest row
was closed, leaving phantom-open siblings.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from src.runtime import order_monitor as om


class _SqliteDB:
    """Database stand-in backed by a real in-memory-ish sqlite file so the
    cascade's ``db.connect()`` sibling query runs the production SQL."""

    def __init__(self, tmp_path):
        self.path = str(tmp_path / "journal.db")
        conn = sqlite3.connect(self.path)
        conn.execute(
            "CREATE TABLE trades ("
            " id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
            " direction TEXT, position_size REAL, entry_price REAL,"
            " exit_price REAL, pnl REAL, pnl_percent REAL, status TEXT,"
            " exit_reason TEXT, notes TEXT, created_at TEXT, closed_at TEXT,"
            " setup_type TEXT, is_backtest INTEGER DEFAULT 0)"
        )
        conn.commit()
        conn.close()
        self.trade_updates = {}

    def connect(self):
        return sqlite3.connect(self.path)

    def insert(self, **row):
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = self.connect()
        conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({marks})", list(row.values())
        )
        conn.commit()
        conn.close()

    def update_trade(self, trade_id, updates):
        self.trade_updates[int(trade_id)] = dict(updates)
        conn = self.connect()
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE trades SET {sets} WHERE id=?",
            list(updates.values()) + [int(trade_id)],
        )
        conn.commit()
        conn.close()
        return 1


def _mk_db(tmp_path):
    db = _SqliteDB(tmp_path)
    # Primary (newest) row — the one the reconciler is checking.
    db.insert(
        id=2797, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        position_size=0.002, entry_price=64660.0, status="open",
        notes=json.dumps({"trade_id": "p"}),
        created_at="2026-06-22T20:06:50+00:00", setup_type="ict_scalp_5m",
    )
    # Phantom-open netted siblings (older, same direction).
    db.insert(
        id=2765, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        position_size=0.001, entry_price=64296.1, status="open",
        notes=json.dumps({"trade_id": "s1"}),
        created_at="2026-06-22T03:20:21+00:00", setup_type="ict_scalp_5m",
    )
    db.insert(
        id=2783, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        position_size=0.003, entry_price=64726.1, status="open",
        notes=json.dumps({"trade_id": "s2"}),
        created_at="2026-06-22T16:55:46+00:00", setup_type="ict_scalp_5m",
    )
    # A row created AFTER the flatten — belongs to a newer position; must
    # NOT be cascaded.
    db.insert(
        id=2900, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        position_size=0.001, entry_price=64000.0, status="open",
        notes=json.dumps({"trade_id": "newer"}),
        created_at="2026-06-23T09:00:00+00:00", setup_type="trend_donchian",
    )
    # Different direction — out of scope for the cascade.
    db.insert(
        id=2901, account_id="bybit_2", symbol="BTCUSDT", direction="short",
        position_size=0.001, entry_price=64100.0, status="open",
        notes=json.dumps({"trade_id": "shortleg"}),
        created_at="2026-06-22T10:00:00+00:00", setup_type="fade_breakout_4h",
    )
    return db


_REC = {
    # The bracket-fire flatten record for the whole netted position:
    # 0.002 + 0.001 + 0.003 = 0.006 qty, closed 21:23 Jun 22.
    "avg_exit_price": 64250.0,
    "avg_entry_price": 64550.0,
    "closed_pnl": -1.8,
    "qty": 0.006,
    "side": "Sell",
    "closed_at": "2026-06-22T21:23:31+00:00",
}


class TestCascadeCloseNettedSiblings:
    def _run(self, db, rec=_REC):
        primary = {
            "id": 2797, "account_id": "bybit_2", "symbol": "BTCUSDT",
            "direction": "long", "position_size": 0.002,
        }
        with patch.object(om, "_cascade_close_linked_package", return_value=None), \
             patch.object(om, "_classify_broker_exit", return_value="sl"):
            return om._cascade_close_netted_siblings(db, primary, rec)

    def test_siblings_closed_with_prorated_pnl(self, tmp_path):
        db = _mk_db(tmp_path)
        n = self._run(db)
        assert n == 2
        u1 = db.trade_updates[2765]
        assert u1["status"] == "closed"
        assert u1["exit_price"] == 64250.0
        # -1.8 * (0.001 / 0.006)
        assert abs(u1["pnl"] - (-0.3)) < 1e-6
        notes1 = json.loads(u1["notes"])
        assert notes1["pnl_source"] == "netted_prorated_cascade"
        assert notes1["netted_primary_trade_id"] == 2797
        assert notes1["closed_by"] == "monitor_reconciler_netted_cascade"
        u2 = db.trade_updates[2783]
        assert abs(u2["pnl"] - (-0.9)) < 1e-6

    def test_newer_and_opposite_rows_untouched(self, tmp_path):
        db = _mk_db(tmp_path)
        self._run(db)
        assert 2900 not in db.trade_updates
        assert 2901 not in db.trade_updates

    def test_no_record_still_closes_with_honest_stamps(self, tmp_path):
        db = _mk_db(tmp_path)
        n = self._run(db, rec=None)
        # Without a record the flatten time is NOW, so the "newer" long row
        # (2900) is also stale-open at flat observation and cascades too.
        assert n == 3
        assert 2900 in db.trade_updates
        u = db.trade_updates[2765]
        assert u["status"] == "closed"
        assert "exit_price" not in u
        assert "pnl" not in u
        notes = json.loads(u["notes"])
        assert notes["exit_price_source"] == "netted_flat_no_record"

    def test_reduce_leg_pnl_deferred(self, tmp_path):
        db = _mk_db(tmp_path)
        db.insert(
            id=2950, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=0.001, entry_price=64200.0, status="open",
            notes=json.dumps({"trade_id": "r"}),
            created_at="2026-06-22T12:00:00+00:00", setup_type="intent_reduce",
        )
        self._run(db)
        u = db.trade_updates[2950]
        assert u["status"] == "closed"
        assert "pnl" not in u
        assert json.loads(u["notes"])["pnl_source"] == "deferred_intent_reduce"

    def test_best_effort_on_db_without_connect(self):
        class _NoConnect:
            pass
        n = om._cascade_close_netted_siblings(
            _NoConnect(), {"id": 1, "account_id": "a", "symbol": "S",
                           "direction": "long"}, _REC,
        )
        assert n == 0


class TestPrimaryProrationGuard:
    def _close(self, row, rec):
        class _FakeDB:
            def __init__(self):
                self.trade_updates = {}

            def update_trade(self, trade_id, updates):
                self.trade_updates[int(trade_id)] = dict(updates)
                return 1

        db = _FakeDB()
        order_status = {"avg_price": 0.0, "exec_time": "1780793499383"}
        with patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            return_value=rec,
        ), patch.object(om, "_cascade_close_linked_package", return_value=None), \
             patch.object(om, "_cascade_close_netted_siblings", return_value=0), \
             patch.object(om, "_classify_broker_exit", return_value=None):
            om._close_trade_from_order_status(
                db, row, order_status, cfg={"account_id": "bybit_2"},
            )
        return db

    def _row(self, qty):
        return {
            "id": 10, "symbol": "BTCUSDT", "direction": "long",
            "position_size": qty, "entry_price": 64296.1,
            "created_at": "2026-06-22T03:20:21+00:00",
            "setup_type": "ict_scalp_5m",
            "notes": json.dumps({"trade_id": "x"}),
        }

    def test_record_bigger_than_row_is_prorated(self):
        rec = dict(_REC)  # qty 0.006, pnl -1.8
        db = self._close(self._row(0.001), rec)
        u = db.trade_updates[10]
        assert abs(u["pnl"] - (-0.3)) < 1e-6
        notes = json.loads(u["notes"])
        assert notes["pnl_source"] == "netted_prorated"
        assert notes["bybit_closed_pnl_record_total"] == -1.8

    def test_matching_qty_books_full_record(self):
        rec = dict(_REC)
        rec["qty"] = 0.001
        db = self._close(self._row(0.001), rec)
        u = db.trade_updates[10]
        assert abs(u["pnl"] - (-1.8)) < 1e-6
        assert "pnl_source" not in json.loads(u["notes"])
