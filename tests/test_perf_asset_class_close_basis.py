"""Regression: /performance asset-class breakdown + close-time basis + the
new error/profitFactor/maxDrawdown fields, and the _asset_class resolver.

Covers the dashboard-audit fixes (2026-06-20):
  * pnl24h / windows key on CLOSE-time, not trade OPEN calendar-day — a trade
    closed in the last 24h but opened earlier is counted (the "24h P&L wrongly
    $0.00" bug).
  * the order_packages join is pre-aggregated 1:1 so a trade with multiple
    order packages is counted ONCE (no fan-out).
  * real vs paper never blended.
  * NULL-pnl closed trades excluded from aggregates.
  * perAssetClass groups by instrument asset class (crypto/index/commodity/…).

These exercise the pure aggregation path (performance._query/_aggregate +
_asset_class) — no FastAPI auth import — so they run even where the optional
auth deps aren't installed.
"""

from __future__ import annotations

import datetime
import sqlite3
import tempfile
from pathlib import Path

from src.web.api import _asset_class as ac
from src.web.api.routers import performance as P


def test_asset_class_resolver_tags_real_instruments():
    ac.reset_cache()
    assert ac.asset_class_for_symbol("BTCUSDT") == "crypto"
    assert ac.asset_class_for_symbol("ETHUSDT") == "crypto"
    assert ac.asset_class_for_symbol("MES") == "index"
    assert ac.asset_class_for_symbol("MGC") == "commodity"
    assert ac.asset_class_for_symbol("MHG") == "commodity"
    assert ac.asset_class_for_symbol("XAUUSD") == "commodity"
    assert ac.asset_class_for_symbol("SPY") == "equity"
    assert ac.asset_class_for_symbol("QQQ") == "equity"
    assert ac.asset_class_for_symbol("GLD") == "commodity"
    # unregistered crypto perp still buckets via suffix heuristic
    assert ac.asset_class_for_symbol("DOGEUSDT") == "crypto"
    assert ac.asset_class_for_symbol(None) == "unknown"


def _seed(db: Path, now: datetime.datetime) -> None:
    def iso(h):
        return (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")

    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE trades(id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT,
            pnl REAL, created_at TEXT, timestamp TEXT, closed_at TEXT, status TEXT,
            is_backtest INT, account_class TEXT, is_demo INT, account_id TEXT,
            exit_reason TEXT);
        CREATE TABLE order_packages(id INTEGER PRIMARY KEY, linked_trade_id INT, updated_at TEXT);
        """
    )
    rows = [
        # real, OPENED 50h ago but CLOSED 1h ago, +10 BTC -> must count in 24h
        (1, "trend", "BTCUSDT", 10, iso(50), iso(50), iso(1), "closed", 0, "real_money", 0, "bybit_2", "tp"),
        # real, closed 30h ago -5 BTC (outside 24h)
        (2, "trend", "BTCUSDT", -5, iso(40), iso(40), iso(30), "closed", 0, "real_money", 0, "bybit_2", "sl"),
        # PAPER closed 1h ago +100 (excluded from real)
        (3, "trend", "BTCUSDT", 100, iso(2), iso(2), iso(1), "closed", 0, "paper", 1, "bybit_1", "tp"),
        # real closed 2h ago NULL pnl (reconciler-incomplete -> excluded)
        (4, "trend", "BTCUSDT", None, iso(3), iso(3), iso(2), "closed", 0, "real_money", 0, "bybit_2", "x"),
        # real OPEN
        (5, "trend", "BTCUSDT", None, iso(1), iso(1), None, "open", 0, "real_money", 0, "bybit_2", None),
        # real MES closed 5h ago +20 (asset class index)
        (6, "mes_x", "MES", 20, iso(6), iso(6), iso(5), "closed", 0, "real_money", 0, "ib_live", "tp"),
    ]
    conn.executemany("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    # trade 1 has TWO order packages (entry + rearm) — must NOT double-count
    conn.executemany(
        "INSERT INTO order_packages(linked_trade_id,updated_at) VALUES(?,?)",
        [(1, iso(1)), (1, iso(1)), (6, iso(5))],
    )
    conn.commit()
    conn.close()


def test_performance_close_time_basis_and_asset_class():
    now = datetime.datetime.now(datetime.timezone.utc)
    db = Path(tempfile.mktemp(suffix=".db"))
    _seed(db, now)
    ac.reset_cache()

    since = (now - datetime.timedelta(hours=24)).isoformat()
    agg = P._aggregate(P._query(db, since, demo=False), "24h", since)
    # trade 1 (+10) and trade 6 (+20) closed within 24h; trade 2 outside; paper
    # + NULL excluded. Total 30 — the bug returned ~0 (both opened earlier).
    assert agg["totalTrades"] == 2
    assert agg["totalPnl"] == 30.0
    assert agg["winRate"] == 100.0
    assert agg["error"] is False
    assert agg["profitFactor"] is None  # no losing trades in window
    classes = {c["assetClass"]: c["totalPnl"] for c in agg["perAssetClass"]}
    assert classes == {"crypto": 10.0, "index": 20.0}

    # all-time real: trade 1 counted ONCE despite 2 order packages (no fan-out)
    agg_all = P._aggregate(P._query(db, None, demo=False), "all", None)
    assert agg_all["totalTrades"] == 3  # 1, 2, 6
    assert agg_all["totalPnl"] == 25.0
    assert agg_all["profitFactor"] == 6.0  # gross 30 / gross loss 5

    # paper kept strictly separate
    agg_paper = P._aggregate(P._query(db, None, demo=True), "all", None)
    assert agg_paper["totalTrades"] == 1
    assert agg_paper["totalPnl"] == 100.0

    db.unlink()
