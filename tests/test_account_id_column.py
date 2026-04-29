"""
Tests for the ``account_id`` column migration on the ``trades`` table.

Covers both code paths that bootstrap the schema:
- ``scripts/init_db.py`` (the bot-side initialiser)
- ``src/data_layer/database.py`` (the trader-side initialiser)

Each path must:
1. Create the column on a fresh DB with DEFAULT 'live'.
2. Add the column idempotently to a pre-existing DB that lacks it.
3. Be safe to run repeatedly (no error on re-run).
4. Preserve existing rows (which get account_id='live').
5. Accept inserts that populate account_id.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_init_db_module():
    path = REPO_ROOT / "scripts" / "init_db.py"
    spec = importlib.util.spec_from_file_location("ict_init_db", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_columns(db_path: str, table: str) -> set:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def _list_indexes(db_path: str, table: str) -> set:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,))
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _create_legacy_trades_table(db_path: str) -> None:
    """Create a trades table that pre-dates the account_id column."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                direction TEXT,
                entry_price REAL,
                pnl REAL,
                status TEXT,
                is_backtest INTEGER DEFAULT 0,
                strategy_name TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, pnl, status, is_backtest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2026-04-28T12:00:00Z", "BTCUSDT", "LONG", 50000.0, 25.5, "closed", 0),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# scripts/init_db.py — fresh DB
# ---------------------------------------------------------------------------

class TestInitDbFreshAccountId:
    def test_account_id_present_on_fresh_db(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "fresh.db")
        init_db.init_db(db_path)
        assert "account_id" in _table_columns(db_path, "trades")

    def test_init_is_idempotent(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "idempotent.db")
        init_db.init_db(db_path)
        init_db.init_db(db_path)  # must not raise
        assert "account_id" in _table_columns(db_path, "trades")

    def test_index_created(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "indexed.db")
        init_db.init_db(db_path)
        assert "idx_trades_account_created" in _list_indexes(db_path, "trades")


# ---------------------------------------------------------------------------
# scripts/init_db.py — pre-existing DB without the column
# ---------------------------------------------------------------------------

class TestInitDbMigrateLegacyAccountId:
    def test_legacy_db_gets_account_id_column(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "legacy.db")
        _create_legacy_trades_table(db_path)
        assert "account_id" not in _table_columns(db_path, "trades")

        init_db.init_db(db_path)

        assert "account_id" in _table_columns(db_path, "trades")

    def test_legacy_rows_get_default_live(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "legacy_data.db")
        _create_legacy_trades_table(db_path)

        init_db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT symbol, account_id FROM trades ORDER BY id")
            rows = cur.fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "BTCUSDT"
        assert rows[0][1] == "live"


# ---------------------------------------------------------------------------
# scripts/init_db.py — migration helper directly
# ---------------------------------------------------------------------------

class TestMigrationHelperAccountId:
    def test_helper_returns_true_when_added(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "helper_added.db")
        _create_legacy_trades_table(db_path)
        conn = sqlite3.connect(db_path)
        try:
            added = init_db.migrate_add_account_id(conn.cursor())
            conn.commit()
        finally:
            conn.close()
        assert added is True
        assert "account_id" in _table_columns(db_path, "trades")

    def test_helper_returns_false_when_already_present(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "helper_present.db")
        init_db.init_db(db_path)  # creates with column
        conn = sqlite3.connect(db_path)
        try:
            added = init_db.migrate_add_account_id(conn.cursor())
        finally:
            conn.close()
        assert added is False


# ---------------------------------------------------------------------------
# src/data_layer/database.py — Database class
# ---------------------------------------------------------------------------

db_module = pytest.importorskip("src.data_layer.database")


class TestTraderDatabaseAccountId:
    def test_account_id_present_on_fresh_db(self, tmp_path):
        db_path = str(tmp_path / "trader_fresh.db")
        db_module.Database(db_path=db_path)
        assert "account_id" in _table_columns(db_path, "trades")

    def test_construction_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "trader_idempotent.db")
        db_module.Database(db_path=db_path)
        db_module.Database(db_path=db_path)  # must not raise
        assert "account_id" in _table_columns(db_path, "trades")

    def test_index_created(self, tmp_path):
        db_path = str(tmp_path / "trader_indexed.db")
        db_module.Database(db_path=db_path)
        assert "idx_trades_account_created" in _list_indexes(db_path, "trades")

    def test_legacy_db_gets_account_id_column(self, tmp_path):
        db_path = str(tmp_path / "trader_legacy.db")
        _create_legacy_trades_table(db_path)
        assert "account_id" not in _table_columns(db_path, "trades")

        db_module.Database(db_path=db_path)

        assert "account_id" in _table_columns(db_path, "trades")

    def test_legacy_rows_get_default_live(self, tmp_path):
        db_path = str(tmp_path / "trader_legacy_data.db")
        _create_legacy_trades_table(db_path)

        db_module.Database(db_path=db_path)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT symbol, account_id FROM trades ORDER BY id")
            rows = cur.fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "BTCUSDT"
        assert rows[0][1] == "live"

    def test_insert_trade_accepts_account_id(self, tmp_path):
        db_path = str(tmp_path / "trader_insert.db")
        db = db_module.Database(db_path=db_path)
        trade_id = db.insert_trade(
            {
                "timestamp": "2026-04-29T10:00:00Z",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "entry_price": 50000.0,
                "position_size": 0.01,
                "account_id": "bybit_main",
            }
        )
        assert isinstance(trade_id, int) and trade_id > 0

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT symbol, account_id FROM trades WHERE id = ?", (trade_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        assert row == ("BTCUSDT", "bybit_main")


class TestInsertTradeAccountIdDefault:
    """insert_trade always writes account_id — defaults to 'live' when omitted."""

    def _read_account_id(self, db_path: str, trade_id: int) -> str:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT account_id FROM trades WHERE id = ?", (trade_id,))
            return cur.fetchone()[0]
        finally:
            conn.close()

    def test_default_live_when_account_id_omitted(self, tmp_path):
        db = db_module.Database(db_path=str(tmp_path / "default.db"))
        tid = db.insert_trade({
            "timestamp": "2026-04-29T10:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_price": 50000.0,
            "position_size": 0.01,
        })
        assert self._read_account_id(str(tmp_path / "default.db"), tid) == "live"

    def test_explicit_account_id_is_preserved(self, tmp_path):
        db = db_module.Database(db_path=str(tmp_path / "explicit.db"))
        tid = db.insert_trade({
            "timestamp": "2026-04-29T10:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_price": 50000.0,
            "position_size": 0.01,
            "account_id": "bybit_sub1",
        })
        assert self._read_account_id(str(tmp_path / "explicit.db"), tid) == "bybit_sub1"

    def test_caller_dict_not_mutated(self, tmp_path):
        db = db_module.Database(db_path=str(tmp_path / "nomutate.db"))
        original = {
            "timestamp": "2026-04-29T10:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_price": 50000.0,
            "position_size": 0.01,
        }
        before_keys = set(original.keys())
        db.insert_trade(original)
        assert set(original.keys()) == before_keys  # account_id not injected into caller's dict
