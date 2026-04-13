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

# The DB lives next to telegram_query_bot.py in src/bot/
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "bot")
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, "trade_journal.db"))


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
            created_at      TEXT DEFAULT (datetime('now'))
        )
        """
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
