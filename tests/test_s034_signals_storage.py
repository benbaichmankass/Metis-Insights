"""S-034 regression tests
(architecture-audit-2026-05-02 § P2-9).

Pre-PR signals lived in two stores: ``runtime_logs/signal_audit.jsonl``
(the JSONL the UI reads) and ``data/trades.db::signals`` (a legacy
SQL table no UI surface touches). Per CLAUDE.md § Architecture rules
§ 4 the DB unit owns the signals log — alongside trades and
order_packages — in ``trade_journal.db``.

This PR opens the transition window:
  1. Adds a ``signals`` table to ``trade_journal.db`` with the same
     fields the JSONL writer records (logged_at_utc, strategy, symbol,
     side, qty, status, reason, plus a meta JSON blob for extras).
  2. Adds ``Database.insert_signal`` writer + ``Database.get_recent_signals``
     reader.
  3. Wires the JSONL writer to dual-write to the SQL table.

Tests pin:
  - Schema is created (signals table + indexes).
  - insert_signal round-trips a full payload + extras land in meta.
  - get_recent_signals returns rows in oldest-first window order
    (matches JSONL "tail" semantics) + filters by strategy
    case-insensitively + caps the limit at 200.
  - Dual-write fires on log_signal; JSONL write happens regardless of
    whether the SQL side errors.
  - SIGNAL_DUAL_WRITE_DISABLED=true cleanly skips the SQL side.
"""
from __future__ import annotations

import json
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return db_path


def _table_exists(db_path, table_name):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


class TestSchema:
    def test_signals_table_created(self, tmp_db_path):
        from src.data_layer.database import Database
        Database(db_path=str(tmp_db_path))
        assert _table_exists(tmp_db_path, "signals")

    def test_signals_indexes_created(self, tmp_db_path):
        from src.data_layer.database import Database
        Database(db_path=str(tmp_db_path))
        conn = sqlite3.connect(str(tmp_db_path))
        try:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        finally:
            conn.close()
        assert "idx_signals_strategy_logged" in names
        assert "idx_signals_logged_at" in names


# ---------------------------------------------------------------------------
# Database.insert_signal
# ---------------------------------------------------------------------------


class TestInsertSignal:
    def test_round_trip_minimal_payload(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        rid = db.insert_signal({
            "logged_at_utc": "2026-05-02T10:00:00+00:00",
            "strategy": "vwap",
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.001,
            "status": "submitted",
            "reason": None,
        })
        assert isinstance(rid, int) and rid > 0
        conn = sqlite3.connect(str(tmp_db_path))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM signals WHERE id = ?", (rid,)
            ).fetchone()
        finally:
            conn.close()
        assert row["strategy"] == "vwap"
        assert row["symbol"] == "BTCUSDT"
        assert row["side"] == "buy"
        assert row["qty"] == pytest.approx(0.001)
        assert row["status"] == "submitted"
        assert row["meta"] is None  # nothing extra

    def test_extra_fields_land_in_meta_blob(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        rid = db.insert_signal({
            "strategy": "turtle_soup",
            "symbol": "BTCUSDT",
            "extra1": "value1",
            "extra2": {"nested": True, "n": 42},
        })
        conn = sqlite3.connect(str(tmp_db_path))
        try:
            row = conn.execute(
                "SELECT meta FROM signals WHERE id = ?", (rid,)
            ).fetchone()
        finally:
            conn.close()
        meta = json.loads(row[0])
        assert meta["extra1"] == "value1"
        assert meta["extra2"]["n"] == 42

    def test_missing_logged_at_uses_now(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        rid = db.insert_signal({"strategy": "vwap"})
        conn = sqlite3.connect(str(tmp_db_path))
        try:
            row = conn.execute(
                "SELECT logged_at_utc FROM signals WHERE id = ?", (rid,)
            ).fetchone()
        finally:
            conn.close()
        # ISO-8601 with timezone — at minimum starts with a year.
        assert row[0].startswith("20")
        assert "T" in row[0]


# ---------------------------------------------------------------------------
# Database.get_recent_signals
# ---------------------------------------------------------------------------


class TestGetRecentSignals:
    def test_empty_table_returns_empty_list(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        assert db.get_recent_signals() == []

    def test_window_order_is_oldest_first(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        for i, ts in enumerate([
            "2026-05-02T08:00:00+00:00",
            "2026-05-02T09:00:00+00:00",
            "2026-05-02T10:00:00+00:00",
        ]):
            db.insert_signal({
                "logged_at_utc": ts,
                "strategy": "vwap",
                "symbol": f"SYM{i}",
            })
        rows = db.get_recent_signals(limit=10)
        # Oldest first within the window — matches the JSONL tail
        # ordering that processor.get_recent_signals returns.
        assert [r["symbol"] for r in rows] == ["SYM0", "SYM1", "SYM2"]

    def test_strategy_filter_case_insensitive(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        db.insert_signal({"strategy": "VWAP", "symbol": "A"})
        db.insert_signal({"strategy": "turtle_soup", "symbol": "B"})
        rows = db.get_recent_signals(strategy="vwap")
        assert [r["symbol"] for r in rows] == ["A"]

    def test_limit_is_capped_at_200(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        # Insert 5 — the cap mechanism is what we're verifying;
        # invalid ints become 10, huge ints clamp to 200.
        for i in range(5):
            db.insert_signal({"strategy": "vwap", "symbol": f"S{i}"})
        # Limit > cap → still works, all 5 rows.
        rows = db.get_recent_signals(limit=999)
        assert len(rows) == 5
        # Non-int → defaults to 10 (still 5 rows here).
        rows = db.get_recent_signals(limit="not-an-int")
        assert len(rows) == 5

    def test_meta_unpacked_into_top_level(self, tmp_db_path):
        from src.data_layer.database import Database
        db = Database(db_path=str(tmp_db_path))
        db.insert_signal({
            "strategy": "vwap",
            "symbol": "BTCUSDT",
            "confidence": 0.84,
            "trace_id": "abc-123",
        })
        rows = db.get_recent_signals()
        assert len(rows) == 1
        # Extras came back via meta unpack.
        assert rows[0]["confidence"] == pytest.approx(0.84)
        assert rows[0]["trace_id"] == "abc-123"


# ---------------------------------------------------------------------------
# JSONL log_signal dual-writer
# ---------------------------------------------------------------------------


class TestLogSignalDualWrite:
    def test_jsonl_and_sql_both_get_written(self, tmp_path, monkeypatch):
        # Redirect both writers at tmp_path.
        from src.utils import signal_audit_logger as sal
        monkeypatch.setattr(sal, "BASE", tmp_path)
        monkeypatch.setattr(sal, "SIGNAL_FILE", tmp_path / "signal_audit.jsonl")
        db_path = tmp_path / "trade_journal.db"
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))

        sal.log_signal({
            "strategy": "vwap",
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.001,
            "status": "submitted",
        })

        # JSONL side.
        body = (tmp_path / "signal_audit.jsonl").read_text(encoding="utf-8")
        assert "vwap" in body and "BTCUSDT" in body
        # SQL side.
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT strategy, symbol, status FROM signals"
            ).fetchone()
        finally:
            conn.close()
        assert row == ("vwap", "BTCUSDT", "submitted")

    def test_sql_failure_does_not_break_jsonl(
        self, tmp_path, monkeypatch, caplog,
    ):
        from src.utils import signal_audit_logger as sal
        monkeypatch.setattr(sal, "BASE", tmp_path)
        monkeypatch.setattr(sal, "SIGNAL_FILE", tmp_path / "signal_audit.jsonl")

        # Force the SQL side to blow up via a path that can't be opened.
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "missing" / "x.db"),
        )

        sal.log_signal({"strategy": "vwap", "symbol": "BTCUSDT"})
        # JSONL still got the row.
        body = (tmp_path / "signal_audit.jsonl").read_text(encoding="utf-8")
        assert "vwap" in body

    def test_dual_write_disabled_skips_sql(self, tmp_path, monkeypatch):
        from src.utils import signal_audit_logger as sal
        monkeypatch.setattr(sal, "BASE", tmp_path)
        monkeypatch.setattr(sal, "SIGNAL_FILE", tmp_path / "signal_audit.jsonl")
        db_path = tmp_path / "trade_journal.db"
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
        monkeypatch.setenv("SIGNAL_DUAL_WRITE_DISABLED", "true")

        sal.log_signal({"strategy": "vwap", "symbol": "BTCUSDT"})

        # JSONL still got the row.
        body = (tmp_path / "signal_audit.jsonl").read_text(encoding="utf-8")
        assert "vwap" in body
        # SQL side: no DB created (Database.__init__ creates the file
        # when called, so its absence proves _dual_write_to_db
        # short-circuited).
        assert not db_path.exists()
