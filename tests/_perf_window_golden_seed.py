"""Shared seed for tests/test_performance_recent_blocks.py: a deterministic
journal and a frozen clock, so /api/bot/performance can be compared
byte-for-byte against the golden captured BEFORE the unknown-window change."""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

FROZEN_NOW = _dt.datetime(2026, 9, 29, 12, 0, 0, tzinfo=_dt.timezone.utc)
TRUST_MAP = {"read_state": "ok", "accounts": {}}


class FrozenDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return FROZEN_NOW if tz else FROZEN_NOW.replace(tzinfo=None)


def seed(db: Path, legs=(("leg_a", "bybit_2", "real_money", 0), ("leg_a", "bybit_portfolio", "paper", 1),
                         ("leg_b", "alpaca_live", "real_money", 0), ("leg_c", "bybit_1", "paper", 1)),
         n_per=70, pnl_fn=None) -> None:
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE trades(id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT,
            pnl REAL, created_at TEXT, timestamp TEXT, closed_at TEXT, status TEXT,
            is_backtest INT, account_class TEXT, is_demo INT, account_id TEXT,
            exit_reason TEXT, reconcile_status TEXT, entry_price REAL, stop_loss REAL,
            position_size REAL, direction TEXT, notes TEXT);
        CREATE TABLE order_packages(order_package_id TEXT PRIMARY KEY, linked_trade_id INT, updated_at TEXT);
        """
    )
    tid = 0
    for li, (leg, acct, cls, demo) in enumerate(legs):
        for i in range(n_per):
            tid += 1
            # one trade every 12h, oldest first; spans ~35 days so 24h/7d/30d differ
            t = (FROZEN_NOW - _dt.timedelta(hours=12 * (n_per - i), minutes=li)).strftime("%Y-%m-%dT%H:%M:%S")
            pnl = pnl_fn(leg, acct, i) if pnl_fn else (10.0 if (i + li) % 3 else -12.0)
            conn.execute(
                "INSERT INTO trades(id,strategy_name,symbol,pnl,created_at,timestamp,closed_at,status,"
                "is_backtest,account_class,is_demo,account_id,exit_reason,entry_price,stop_loss,"
                "position_size,direction,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, leg, "BTCUSDT", pnl, t, t, t, "closed", 0, cls, demo, acct,
                 "tp" if pnl > 0 else "sl", 100.0, 90.0, 1.0, "long", None))
    conn.commit()
    conn.close()
