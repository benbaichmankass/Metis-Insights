"""Regression: windowed KPIs must count closed trades whose ``closed_at`` is a
raw **epoch-milliseconds string** — the format the reconciler-filled close path
writes (Bybit ``updatedTime``) while every other writer uses ISO-8601.

The bug (found 2026-06-22 from a real-money ``bybit_2`` Bybit transaction log):
``/api/bot/performance?window=24h`` and ``/api/bot/stats`` pnl24h did
``datetime(COALESCE(closed_at, ...))`` directly. ``datetime("1782128223798")``
is read as a Julian day → NULL → the row fails the window filter and is
silently dropped, so a real closed trade (+1.30 at 11:37) reported as "0 closed
trades in 24h" while lifetime totals (no datetime parse) still showed it.
``/api/bot/trades/closed`` already guarded this; ``/performance`` + pnl24h did
not. The fix centralises the epoch-ms-aware normalisation in
``src/web/api/_closed_at.py`` and wires all three readers to it.

These exercise the pure aggregation path (performance._query/_aggregate) plus
the shared SQL helper — no FastAPI auth import — so they run anywhere.
"""

from __future__ import annotations

import datetime
import sqlite3
import tempfile
from pathlib import Path

from src.web.api import _asset_class as ac
from src.web.api._closed_at import (
    close_time_sql,
    closed_at_norm_sql,
    normalize_closed_at_value,
)
from src.web.api.routers import performance as P


def _epoch_ms(dt: datetime.datetime) -> str:
    """The reconciler-filled writer's format: a raw epoch-ms string."""
    return str(int(dt.timestamp() * 1000))


def _seed(db: Path, now: datetime.datetime) -> None:
    def iso(h):
        return (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")

    def ms(h):
        return _epoch_ms(now - datetime.timedelta(hours=h))

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
        # real, OPENED 50h ago, CLOSED 1h ago with an EPOCH-MS closed_at (the
        # reconciler-filled format). Must count in 24h — this is the bug row.
        (1, "trend", "BTCUSDT", 1.30, iso(50), iso(50), ms(1), "closed", 0, "real_money", 0, "bybit_2", "reconciler_filled"),
        # real, ISO closed_at 2h ago, +2.0 — the already-working format.
        (2, "trend", "BTCUSDT", 2.0, iso(5), iso(5), iso(2), "closed", 0, "real_money", 0, "bybit_2", "tp"),
        # real, EPOCH-MS closed_at 30h ago — outside the 24h window, must NOT count.
        (3, "trend", "BTCUSDT", 99.0, iso(40), iso(40), ms(30), "closed", 0, "real_money", 0, "bybit_2", "reconciler_filled"),
    ]
    conn.executemany("INSERT INTO trades(id,strategy_name,symbol,pnl,created_at,timestamp,closed_at,status,is_backtest,account_class,is_demo,account_id,exit_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_performance_counts_epoch_ms_closed_at_in_window():
    now = datetime.datetime.now(datetime.timezone.utc)
    db = Path(tempfile.mktemp(suffix=".db"))
    _seed(db, now)
    ac.reset_cache()

    since = (now - datetime.timedelta(hours=24)).isoformat()
    agg = P._aggregate(P._query(db, since, demo=False), "24h", since)

    # Trade 1 (epoch-ms closed_at, in window) + trade 2 (ISO, in window) = 2.
    # Trade 3 (epoch-ms, 30h ago) excluded. Pre-fix the epoch-ms rows parsed to
    # NULL → trade 1 dropped → totalTrades==1 / totalPnl==2.0 (the bug).
    assert agg["totalTrades"] == 2
    assert round(agg["totalPnl"], 2) == 3.30
    assert agg["error"] is False

    # all-time still counts every closed row regardless of closed_at format
    agg_all = P._aggregate(P._query(db, None, demo=False), "all", None)
    assert agg_all["totalTrades"] == 3
    assert round(agg_all["totalPnl"], 2) == 102.30

    db.unlink()


def test_closed_at_norm_sql_handles_both_formats():
    """The shared SQL guard converts epoch-ms to a real datetime and leaves
    ISO untouched, in a live SQLite query."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(closed_at TEXT)")
    # 2026-06-22T11:37:03Z as epoch-ms, and the same instant as ISO
    conn.execute("INSERT INTO t VALUES('1782128223000')")
    conn.execute("INSERT INTO t VALUES('2026-06-22T11:37:03')")
    conn.execute("INSERT INTO t VALUES(NULL)")
    expr = closed_at_norm_sql("closed_at")
    got = [r[0] for r in conn.execute(f"SELECT {expr} FROM t").fetchall()]
    # epoch-ms row resolves to a parseable datetime (NOT NULL)
    assert got[0] == "2026-06-22 11:37:03"
    assert got[1] == "2026-06-22 11:37:03"
    assert got[2] is None
    conn.close()


def test_close_time_sql_falls_back_through_coalesce():
    """closed_at NULL → updated_at → timestamp, all datetime()-parseable."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(closed_at TEXT, timestamp TEXT)")
    conn.execute("CREATE TABLE op(updated_at TEXT)")
    conn.execute("INSERT INTO t VALUES(NULL, '2026-06-22T07:15:35')")
    conn.execute("INSERT INTO op VALUES(NULL)")
    expr = close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")
    got = conn.execute(f"SELECT {expr} FROM t, op").fetchone()[0]
    assert got == "2026-06-22 07:15:35"
    conn.close()


def test_normalize_closed_at_value_epoch_ms_to_iso():
    iso = normalize_closed_at_value("1782128223000")
    assert iso is not None and iso.startswith("2026-06-22T11:37:03")
    assert normalize_closed_at_value("2026-06-22T11:37:03+00:00") == "2026-06-22T11:37:03+00:00"
    assert normalize_closed_at_value(None) is None
    assert normalize_closed_at_value("") is None
