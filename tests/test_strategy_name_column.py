"""
Tests for the ``strategy_name`` column migration on the ``trades`` table.

Covers both code paths that bootstrap the schema:
- ``scripts/init_db.py`` (the bot-side initialiser)
- ``src/data_layer/database.py`` (the trader-side initialiser)

Each path must:
1. Create the column on a fresh DB.
2. Add the column idempotently to a pre-existing DB that lacks it.
3. Be safe to run repeatedly (no error on re-run).
4. Accept inserts that include the column.
"""
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_init_db_module():
    """Import scripts/init_db.py without running its __main__ block."""
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


def _create_legacy_trades_table(db_path: str) -> None:
    """Create a trades table that pre-dates the strategy_name column."""
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

class TestInitDbFresh:
    def test_strategy_name_present_on_fresh_db(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "fresh.db")
        init_db.init_db(db_path)
        assert "strategy_name" in _table_columns(db_path, "trades")

    def test_init_is_idempotent(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "idempotent.db")
        init_db.init_db(db_path)
        # Second call must not raise (CREATE TABLE IF NOT EXISTS + idempotent migration)
        init_db.init_db(db_path)
        assert "strategy_name" in _table_columns(db_path, "trades")


# ---------------------------------------------------------------------------
# scripts/init_db.py — pre-existing DB without the column
# ---------------------------------------------------------------------------

class TestInitDbMigrateLegacy:
    def test_legacy_db_gets_column(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "legacy.db")
        _create_legacy_trades_table(db_path)
        assert "strategy_name" not in _table_columns(db_path, "trades")

        init_db.init_db(db_path)

        assert "strategy_name" in _table_columns(db_path, "trades")

    def test_legacy_db_preserves_existing_rows(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "legacy_data.db")
        _create_legacy_trades_table(db_path)

        init_db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT symbol, pnl, strategy_name FROM trades ORDER BY id")
            rows = cur.fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "BTCUSDT"
        assert rows[0][1] == 25.5
        # Pre-existing rows have NULL strategy_name; bot will render "n/a".
        assert rows[0][2] is None


# ---------------------------------------------------------------------------
# scripts/init_db.py — migration helper directly
# ---------------------------------------------------------------------------

class TestMigrationHelper:
    def test_helper_returns_true_when_added(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "helper_added.db")
        _create_legacy_trades_table(db_path)
        conn = sqlite3.connect(db_path)
        try:
            added = init_db.migrate_add_strategy_name(conn.cursor())
            conn.commit()
        finally:
            conn.close()
        assert added is True
        assert "strategy_name" in _table_columns(db_path, "trades")

    def test_helper_returns_false_when_already_present(self, tmp_path):
        init_db = _load_init_db_module()
        db_path = str(tmp_path / "helper_present.db")
        init_db.init_db(db_path)  # creates with column
        conn = sqlite3.connect(db_path)
        try:
            added = init_db.migrate_add_strategy_name(conn.cursor())
        finally:
            conn.close()
        assert added is False


# ---------------------------------------------------------------------------
# src/data_layer/database.py — Database class
# ---------------------------------------------------------------------------

# Skip the trader-side test if pandas-touching modules can't import; we only
# need sqlite3 + the Database class itself.
db_module = pytest.importorskip("src.data_layer.database")


class TestTraderDatabaseFresh:
    def test_strategy_name_present_on_fresh_db(self, tmp_path):
        db_path = str(tmp_path / "trader_fresh.db")
        db_module.Database(db_path=db_path)
        assert "strategy_name" in _table_columns(db_path, "trades")

    def test_construction_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "trader_idempotent.db")
        db_module.Database(db_path=db_path)
        # Second construction must not raise.
        db_module.Database(db_path=db_path)
        assert "strategy_name" in _table_columns(db_path, "trades")


class TestTraderDatabaseMigrateLegacy:
    def test_legacy_db_gets_column(self, tmp_path):
        db_path = str(tmp_path / "trader_legacy.db")
        _create_legacy_trades_table(db_path)
        assert "strategy_name" not in _table_columns(db_path, "trades")

        db_module.Database(db_path=db_path)

        assert "strategy_name" in _table_columns(db_path, "trades")

    def test_insert_trade_accepts_strategy_name(self, tmp_path):
        db_path = str(tmp_path / "trader_insert.db")
        db = db_module.Database(db_path=db_path)
        trade_id = db.insert_trade(
            {
                "timestamp": "2026-04-29T10:00:00Z",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "entry_price": 50000.0,
                "position_size": 0.01,
                "strategy_name": "killzone",
            }
        )
        assert isinstance(trade_id, int) and trade_id > 0

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT symbol, strategy_name FROM trades WHERE id = ?", (trade_id,)
            )
            row = cur.fetchone()
        finally:
            conn.close()
        assert row == ("BTCUSDT", "killzone")
