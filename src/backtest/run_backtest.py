import os
import sys
import sqlite3
from datetime import datetime, date

import pandas as pd

from src.backtest.backtester import ICTBacktester

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from src.utils.paths import repo_root as _repo_root  # noqa: E402
from src.utils.paths import trade_journal_db_path as _trade_journal_db_path  # noqa: E402
REPO_ROOT = _repo_root()
# Canonical resolver first (TRADE_JOURNAL_DB env →
# $DATA_DIR/trade_journal.db → repo-root), and nothing else. The SCRIPT_DIR (CWD-ish) candidate was
# dropped — see src/utils/paths.py::trade_journal_db_path.
# The repo-root existence fallback was REMOVED 2026-10-01 (ORDER-AUDIT-2 item 9,
# AUD-20260927-CA-A15-db-loaders-stray-trade-journal-fallback): with DATA_DIR
# set but $DATA_DIR/trade_journal.db not yet present, `next(... exists ...)`
# silently picked a stray <repo>/trade_journal.db. The resolver already falls
# back to repo root when no env is set, so the second candidate only ever
# changed the answer in that wrong case.
DB_PATH = _trade_journal_db_path()

DATA_CANDIDATES = [
    os.environ.get("BACKTEST_DATA_PATH", ""),
    os.path.join(REPO_ROOT, "data", "backtest_candles.csv"),
    os.path.join(REPO_ROOT, "data", "candles.csv"),
]

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

def load_data():
    for path in DATA_CANDIDATES:
        if path and os.path.exists(path):
            df = pd.read_csv(path)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            elif df.index.name == "timestamp":
                df = df.reset_index()
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            if "timestamp" not in df.columns:
                raise ValueError(f"Missing timestamp column in {path}")
            df = df.dropna(subset=["timestamp"]).copy()
            needed = ["open", "high", "low", "close", "volume"]
            missing = [c for c in needed if c not in df.columns]
            if missing:
                raise ValueError(f"Missing required columns in {path}: {missing}")
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df, path
    raise FileNotFoundError(
        "No backtest data found. Set BACKTEST_DATA_PATH or place a CSV in data/backtest_candles.csv"
    )

def _risk_stats(pnl, initial_capital):
    """max_drawdown / max_drawdown_pct / sharpe_ratio / total_pnl_pct, MEASURED.

    These four were hardcoded ``0.0`` on every run, trades or not
    (AUD-20260927-CA-A15-run-backtest-summarize-fabricated-zeros, ORDER-AUDIT-2
    item 7) — indistinguishable from a genuinely flat run. Now:

    * ``max_drawdown`` — largest peak-to-trough fall (USD) of the equity curve
      ``initial_capital + cumsum(net_pnl)``, peak including the start.
    * ``max_drawdown_pct`` / ``total_pnl_pct`` — against that curve /
      ``initial_capital``; ``None`` when no capital basis is known.
    * ``sharpe_ratio`` — PER-TRADE, NOT annualized: mean / sample-std of
      ``net_pnl``. The harness has no return-period basis to annualize on.
      ``None`` (not measurable) with fewer than 2 trades or zero dispersion.

    ``None`` is written as SQL NULL — "not measured", never ``0``.
    """
    n = len(pnl)
    if n == 0:
        return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0 if initial_capital else None,
                "sharpe_ratio": None, "total_pnl_pct": 0.0 if initial_capital else None}
    base = float(initial_capital) if initial_capital else 0.0
    equity = base + pnl.cumsum()
    peak = equity.cummax().clip(lower=base)
    dd = peak - equity
    max_dd = float(dd.max())
    if initial_capital and float(initial_capital) > 0:
        dd_pct = float((dd / peak.where(peak > 0)).max() * 100)
        total_pnl_pct = float(pnl.sum() / float(initial_capital) * 100)
    else:
        dd_pct = None
        total_pnl_pct = None
    std = float(pnl.std(ddof=1)) if n >= 2 else 0.0
    sharpe = float(pnl.mean() / std) if n >= 2 and std > 0 else None
    r = lambda v: None if v is None or v != v else round(v, 4)  # noqa: E731
    return {"max_drawdown": round(max_dd, 2), "max_drawdown_pct": r(dd_pct),
            "sharpe_ratio": r(sharpe), "total_pnl_pct": r(total_pnl_pct)}


def summarize(trades, start_date, end_date, strategy_version, initial_capital=None):
    if not trades:
        return {
            "run_date": str(date.today()),
            "strategy_version": strategy_version,
            "start_date": start_date,
            "end_date": end_date,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            **_risk_stats(pd.Series(dtype=float), initial_capital),
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

    df_t = pd.DataFrame(trades)
    pnl = df_t["net_pnl"] if "net_pnl" in df_t.columns else pd.Series(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    total = len(df_t)
    total_pnl = float(pnl.sum()) if len(pnl) else 0.0
    win_rate = float(len(wins) / total * 100) if total else 0.0
    profit_factor = float(abs(wins.sum() / losses.sum())) if len(losses) and losses.sum() != 0 else 0.0
    expectancy = float(pnl.mean()) if len(pnl) else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    largest_win = float(wins.max()) if len(wins) else 0.0
    largest_loss = float(losses.min()) if len(losses) else 0.0

    return {
        "run_date": str(date.today()),
        "strategy_version": strategy_version,
        "start_date": start_date,
        "end_date": end_date,
        "total_trades": int(total),
        "winning_trades": int((pnl > 0).sum()),
        "losing_trades": int((pnl < 0).sum()),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        **_risk_stats(pnl.astype(float).reset_index(drop=True), initial_capital),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
        "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }

def run_backtest():
    df, source_path = load_data()
    print(f"Backtest data loaded from {source_path} with {len(df)} rows")

    bt = ICTBacktester(df, {})
    trades = bt.run()

    strategy_version = os.environ.get("STRATEGY_VERSION", "ict-v1")
    result = summarize(
        trades,
        str(df["timestamp"].iloc[0].date()),
        str(df["timestamp"].iloc[-1].date()),
        strategy_version,
        initial_capital=bt.cfg.get("initial_capital"),
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
    conn.commit()
    conn.close()

    print(f"Backtest done. Result saved to {DB_PATH}")
    print(f"Trades: {result['total_trades']} | PnL: {result['total_pnl']} | Win rate: {result['win_rate']}")

if __name__ == "__main__":
    try:
        run_backtest()
        sys.exit(0)
    except Exception as exc:
        print(f"Backtest failed: {exc}", file=sys.stderr)
        sys.exit(1)
