"""src/backtester.py
Called by telegram_query_bot.py for the /backtest command.
Replace the placeholder logic with real ICT backtest code.
Must exit 0 on success, non-zero on failure.
"""
import os, sys, sqlite3
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT  = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
_DB_CANDIDATES = [
    os.environ.get("TRADE_JOURNAL_DB", ""),
    os.path.join(REPO_ROOT, "trade_journal.db"),
    os.path.join(SCRIPT_DIR, "trade_journal.db"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p and os.path.exists(p)),
               os.path.join(REPO_ROOT, "trade_journal.db"))

def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, strategy_version TEXT, start_date TEXT, end_date TEXT,
            total_trades INTEGER, winning_trades INTEGER, losing_trades INTEGER,
            win_rate REAL, profit_factor REAL, expectancy REAL,
            max_drawdown REAL, max_drawdown_pct REAL, sharpe_ratio REAL,
            total_pnl REAL, total_pnl_pct REAL, avg_win REAL, avg_loss REAL,
            largest_win REAL, largest_loss REAL, created_at TEXT
        )
    """)
    conn.commit()

def run_backtest():
    # TODO: replace with real backtest logic
    print("Backtest stub running...")
    result = dict(
        run_date=str(date.today()), strategy_version="stub-v0.1",
        start_date="2025-01-01", end_date=str(date.today()),
        total_trades=0, winning_trades=0, losing_trades=0,
        win_rate=0.0, profit_factor=0.0, expectancy=0.0,
        max_drawdown=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
        total_pnl=0.0, total_pnl_pct=0.0, avg_win=0.0, avg_loss=0.0,
        largest_win=0.0, largest_loss=0.0,
        created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)
    conn.execute("""
        INSERT INTO backtest_results
            (run_date,strategy_version,start_date,end_date,total_trades,winning_trades,
             losing_trades,win_rate,profit_factor,expectancy,max_drawdown,max_drawdown_pct,
             sharpe_ratio,total_pnl,total_pnl_pct,avg_win,avg_loss,largest_win,largest_loss,created_at)
        VALUES
            (:run_date,:strategy_version,:start_date,:end_date,:total_trades,:winning_trades,
             :losing_trades,:win_rate,:profit_factor,:expectancy,:max_drawdown,:max_drawdown_pct,
             :sharpe_ratio,:total_pnl,:total_pnl_pct,:avg_win,:avg_loss,:largest_win,:largest_loss,:created_at)
    """, result)
    conn.commit(); conn.close()
    print(f"Backtest done. Result saved to {DB_PATH}")

if __name__ == "__main__":
    try:
        run_backtest()
        sys.exit(0)
    except Exception as exc:
        print(f"Backtest failed: {exc}", file=sys.stderr)
        sys.exit(1)
