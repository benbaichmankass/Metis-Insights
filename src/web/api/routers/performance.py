"""GET /api/bot/performance — windowed aggregate performance stats.

Tier-1 read endpoint backing the Android Performance tab (and any other
consumer that wants headline trade analytics over a selectable window).

Why this exists: the consumers previously pulled ``/api/bot/trades/closed``
(capped at 200 rows) and aggregated client-side. With more than 200 closed
trades that made the headline "Trades" count freeze at 200 and skewed every
derived metric (win rate, expectancy, equity curve) to the most recent 200
fills only. This endpoint computes the aggregates in SQL over the **full**
trade history within the requested window — no row cap — so the numbers are
correct regardless of how many trades the bot has taken.

Window (``?window=``):
  - ``24h`` — trades closed in the last 24 hours.
  - ``7d``  — last 7 days.
  - ``30d`` — last 30 days.
  - ``all`` — all closed trades (default).

The close-time basis mirrors ``trades_closed.py``:
``COALESCE(op.updated_at, t.timestamp)`` (the trades table has no closed_at
column of its own). Backtest + demo rows are excluded so the figures reflect
live money, exactly like ``/api/bot/stats``.

Wire shape (camelCase):

    {
      "window": "7d",
      "since": "2026-05-22T09:00:00+00:00" | null,
      "totalTrades": 412,
      "wins": 250,
      "losses": 150,
      "winRate": 60.7,                  # percent, winners / closed × 100
      "totalPnl": 1234.56,
      "expectancy": 3.0,                # totalPnl / totalTrades
      "perStrategy": [
        {"name": "vwap", "trades": 120, "wins": 70, "winRate": 58.3,
         "totalPnl": 540.2, "expectancy": 4.5}
      ],
      "equity": [{"t": "2026-05-22T09:01:00+00:00", "cum": 12.5}]  # oldest→newest
    }

Best-effort: returns a zeroed envelope on a missing/locked DB so the consumer
keeps the tab usable. Tier 1 — no auth, no secrets in the response.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.utils.paths import trade_journal_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_DB_PATH = Path(trade_journal_db_path())

# window token → lookback timedelta. ``all`` maps to None (no since filter).
_WINDOWS: Dict[str, Optional[timedelta]] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}

# Cap on equity-curve points returned. The aggregates are uncapped, but the
# point-by-point equity series is only for sparkline rendering — a few hundred
# points is plenty and keeps the mobile payload small. When the window holds
# more closed trades than this we down-sample evenly (keep newest exact).
_MAX_EQUITY_POINTS = 500


def _window_since(window: str) -> Optional[str]:
    """ISO-8601 UTC cutoff for *window*, or None for the all-time window."""
    delta = _WINDOWS.get(window)
    if delta is None:
        return None
    return (datetime.now(timezone.utc) - delta).isoformat()


def _empty(window: str, since: Optional[str]) -> Dict[str, Any]:
    return {
        "window": window,
        "since": since,
        "totalTrades": 0,
        "wins": 0,
        "losses": 0,
        "winRate": 0.0,
        "totalPnl": 0.0,
        "expectancy": 0.0,
        "perStrategy": [],
        "equity": [],
    }


def _query(db_path: Path, since: Optional[str]) -> List[sqlite3.Row]:
    """Closed (live, non-backtest, non-demo) trades within *since*, oldest→newest.

    Oldest-first ordering lets the caller build the cumulative equity curve in a
    single pass without re-sorting.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT t.strategy_name,
                   COALESCE(t.pnl, 0.0) AS pnl,
                   COALESCE(op.updated_at, t.timestamp) AS closed_at
            FROM trades t
            LEFT JOIN order_packages op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
              AND COALESCE(t.is_demo, 0) = 0
        """
        params: List[Any] = []
        if since:
            sql += " AND datetime(COALESCE(op.updated_at, t.timestamp)) >= datetime(?)"
            params.append(since)
        sql += " ORDER BY datetime(COALESCE(op.updated_at, t.timestamp)) ASC"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _downsample(points: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """Evenly thin *points* to at most *cap*, always keeping the last point."""
    n = len(points)
    if n <= cap:
        return points
    step = n / cap
    out = [points[int(i * step)] for i in range(cap)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def _aggregate(rows: List[sqlite3.Row], window: str, since: Optional[str]) -> Dict[str, Any]:
    total = len(rows)
    if total == 0:
        return _empty(window, since)

    wins = 0
    total_pnl = 0.0
    per: Dict[str, Dict[str, float]] = {}
    equity: List[Dict[str, Any]] = []
    cum = 0.0
    for r in rows:
        pnl = float(r["pnl"] or 0.0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        name = r["strategy_name"] or "(unknown)"
        bucket = per.setdefault(name, {"trades": 0.0, "wins": 0.0, "pnl": 0.0})
        bucket["trades"] += 1
        if pnl > 0:
            bucket["wins"] += 1
        bucket["pnl"] += pnl
        cum += pnl
        equity.append({"t": r["closed_at"], "cum": round(cum, 4)})

    losses = total - wins
    per_strategy = [
        {
            "name": name,
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
        }
        for name, b in per.items()
    ]
    per_strategy.sort(key=lambda s: s["totalPnl"], reverse=True)

    return {
        "window": window,
        "since": since,
        "totalTrades": total,
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / total * 100.0, 1) if total else 0.0,
        "totalPnl": round(total_pnl, 4),
        "expectancy": round(total_pnl / total, 4) if total else 0.0,
        "perStrategy": per_strategy,
        "equity": _downsample(equity, _MAX_EQUITY_POINTS),
    }


@router.get("/performance")
async def get_performance(
    window: str = Query("all", max_length=8),
) -> Dict[str, Any]:
    """Aggregate live-trade performance for the requested *window*.

    See module docstring for the wire shape. Returns a zeroed envelope (HTTP
    200) on an unknown window token or a DB read error so the consumer's tab
    stays usable instead of erroring.
    """
    window = window if window in _WINDOWS else "all"
    since = _window_since(window)
    if not _DB_PATH.exists():
        return _empty(window, since)
    try:
        rows = _query(_DB_PATH, since)
        return _aggregate(rows, window, since)
    except sqlite3.Error:
        logger.exception("performance: sqlite read failed")
        return _empty(window, since)
    except Exception:  # noqa: BLE001
        logger.exception("performance: unexpected error")
        return _empty(window, since)
