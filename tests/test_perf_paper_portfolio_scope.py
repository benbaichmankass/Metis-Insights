"""S-PAPER-PORTFOLIO — /performance `paperPortfolio` sub-block scoping.

The `paper` sub-block aggregates ALL paper accounts (soak + portfolio). The new
`paperPortfolio` sub-block scopes to just the live-portfolio-mirror paper
accounts (`paper_role: portfolio`), so a consumer's "Paper" view can show the
real portfolio without the full soak roster. When no portfolio accounts are
declared, `paperPortfolio` falls back to the all-paper `paper` block.

Exercises the pure aggregation path (performance._query/_aggregate) — no FastAPI
auth import — so it runs even where the optional auth deps aren't installed.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from src.web.api.routers import performance as P


def _seed(db: Path, now: datetime.datetime) -> None:
    def iso(h: int) -> str:
        return (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")

    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE trades(id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT,
            pnl REAL, created_at TEXT, timestamp TEXT, closed_at TEXT, status TEXT,
            is_backtest INT, account_class TEXT, is_demo INT, account_id TEXT,
            exit_reason TEXT, reconcile_status TEXT);
        CREATE TABLE order_packages(id INTEGER PRIMARY KEY, linked_trade_id INT, updated_at TEXT);
        """
    )
    rows = [
        # PORTFOLIO paper book (paper_role: portfolio) — +50
        (1, "trend", "BTCUSDT", 50, iso(3), iso(3), iso(1), "closed", 0, "paper", 1, "bybit_portfolio", "tp"),
        # SOAK paper book — +100 (in `paper`, NOT in `paperPortfolio`)
        (2, "trend", "BTCUSDT", 100, iso(3), iso(3), iso(1), "closed", 0, "paper", 1, "bybit_1", "tp"),
        # REAL money — excluded from both paper blocks
        (3, "trend", "BTCUSDT", 999, iso(3), iso(3), iso(1), "closed", 0, "real_money", 0, "bybit_2", "tp"),
    ]
    conn.executemany(
        "INSERT INTO trades(id,strategy_name,symbol,pnl,created_at,timestamp,closed_at,"
        "status,is_backtest,account_class,is_demo,account_id,exit_reason) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_paper_portfolio_scopes_to_portfolio_accounts(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    _seed(db, datetime.datetime.utcnow())
    monkeypatch.setattr(P, "_DB_PATH", db)
    monkeypatch.setattr(P, "_portfolio_paper_account_ids", lambda: ["bybit_portfolio"])

    out = P.get_performance(window="all")

    # `paper` = ALL paper accounts (soak + portfolio) = 50 + 100 = 150, 2 trades.
    assert out["paper"]["totalTrades"] == 2
    assert out["paper"]["totalPnl"] == 150
    # `paperPortfolio` = ONLY the portfolio book = 50, 1 trade.
    assert out["paperPortfolio"]["totalTrades"] == 1
    assert out["paperPortfolio"]["totalPnl"] == 50
    # real-money top-level untouched (paper never blended).
    assert out["totalPnl"] == 999


def test_paper_portfolio_falls_back_to_all_paper_when_none_declared(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    _seed(db, datetime.datetime.utcnow())
    monkeypatch.setattr(P, "_DB_PATH", db)
    # No portfolio accounts declared (older config) → paperPortfolio == paper.
    monkeypatch.setattr(P, "_portfolio_paper_account_ids", lambda: [])

    out = P.get_performance(window="all")
    assert out["paperPortfolio"] == out["paper"]
    assert out["paperPortfolio"]["totalPnl"] == 150


def test_portfolio_paper_account_ids_reads_config():
    """The real config resolver returns the two portfolio-mirror accounts."""
    ids = P._portfolio_paper_account_ids()
    assert "bybit_portfolio" in ids
    assert "alpaca_portfolio" in ids
    # soak books must NOT be flagged portfolio
    assert "bybit_1" not in ids
    assert "alpaca_paper" not in ids
