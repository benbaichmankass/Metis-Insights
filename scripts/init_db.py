#!/usr/bin/env python3
"""
scripts/init_db.py

Initialise the trade journal SQLite database.
Run this once on the server before starting the Telegram bot:

    python scripts/init_db.py

Safe to re-run: uses CREATE TABLE IF NOT EXISTS so existing data is preserved.
"""

import os
import sqlite3


def migrate_add_strategy_name(cur: sqlite3.Cursor) -> bool:
    """Add the ``strategy_name`` column to the ``trades`` table if missing.

    Idempotent: safe to call on fresh DBs (created with the column) and on
    pre-existing DBs (where the column gets added). Returns ``True`` when
    the column was actually added by this call, ``False`` when it was
    already present.
    """
    cur.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cur.fetchall()}
    if "strategy_name" in columns:
        return False
    cur.execute("ALTER TABLE trades ADD COLUMN strategy_name TEXT")
    return True


def migrate_add_account_id(cur: sqlite3.Cursor) -> bool:
    """Add the ``account_id`` column to the ``trades`` table if missing.

    Default ``'live'`` keeps all pre-existing rows attributed to the legacy
    live account. Idempotent: returns ``True`` only on the run that adds the
    column.
    """
    cur.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cur.fetchall()}
    if "account_id" in columns:
        return False
    cur.execute("ALTER TABLE trades ADD COLUMN account_id TEXT NOT NULL DEFAULT 'live'")
    return True


# Canonical trade-journal path, resolved by the single resolver
# (env -> $DATA_DIR -> repo-root). The old "DB lives next to
# telegram_query_bot.py in src/bot/" location is retired — canon is
# /data/bot-data/trade_journal.db (deploy/dropins/data-dir.conf). Resolving
# here (not a hardcoded src/bot path) prevents this operator-run-once script
# from seeding a stray duplicate journal — the #1308 failure class the
# canonical-db-resolver guard exists to prevent (S-AUDIT-H H-2).
import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.paths import trade_journal_db_path  # noqa: E402

DB_PATH = str(trade_journal_db_path())


def init_db(db_path: str) -> None:
    print(f"Initialising database at: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # trades table  — stores every live and backtest trade signal
    # ------------------------------------------------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT,
            symbol          TEXT,
            direction       TEXT,          -- LONG | SHORT
            entry_price     REAL,
            exit_price      REAL,
            stop_loss       REAL,
            take_profit_1   REAL,
            take_profit_2   REAL,
            take_profit_3   REAL,
            position_size   REAL,
            setup_type      TEXT,          -- e.g. FVG | OB | COMBO
            killzone        TEXT,          -- e.g. London Open | NY Open
            bias            TEXT,          -- BULLISH | BEARISH | NEUTRAL
            entry_reason    TEXT,
            exit_reason     TEXT,
            pnl             REAL,
            pnl_percent     REAL,
            status          TEXT,          -- OPEN | CLOSED | CANCELLED
            notes           TEXT,
            is_backtest     INTEGER DEFAULT 0,  -- 0 = live, 1 = backtest
            strategy_name   TEXT,          -- e.g. breakout_confirmation, vwap, killzone, ict
            account_id      TEXT NOT NULL DEFAULT 'live',  -- multi-account identifier
            created_at      TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # Idempotent migrations for pre-existing DBs missing these columns.
    migrate_add_strategy_name(cur)
    migrate_add_account_id(cur)
    # Index for efficient per-account trade history queries.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_trades_account_created "
        "ON trades (account_id, datetime(created_at) DESC)"
    )
    print("  [OK] trades table ready.")

    # ------------------------------------------------------------------
    # backtest_results table  — stores aggregate backtest run summaries
    # ------------------------------------------------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_results (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date            TEXT,
            strategy_version    TEXT,
            start_date          TEXT,
            end_date            TEXT,
            total_trades        INTEGER,
            winning_trades      INTEGER,
            losing_trades       INTEGER,
            win_rate            REAL,
            profit_factor       REAL,
            expectancy          REAL,
            max_drawdown        REAL,
            max_drawdown_pct    REAL,
            sharpe_ratio        REAL,
            total_pnl           REAL,
            total_pnl_pct       REAL,
            avg_win             REAL,
            avg_loss            REAL,
            largest_win         REAL,
            largest_loss        REAL,
            created_at          TEXT DEFAULT (datetime('now'))
        )
        """
    )
    print("  [OK] backtest_results table ready.")

    conn.commit()
    conn.close()
    print("Database initialisation complete.")


if __name__ == "__main__":
    init_db(DB_PATH)
