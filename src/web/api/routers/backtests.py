"""M5 P4 — GET /api/bot/backtests.

Tier-1 read endpoint for the dashboard's Backtests tab. Returns the
N most recent rows from ``trade_journal.db::backtest_results``, the
table populated by the M5 backtest consumer (one row per
``/test <strategy>`` invocation).

The dashboard tab consumes this list to surface a strategy-test
history with the headline metrics from each run; the operator can
pull the full row by ``id`` from the DB if they need raw config /
percentile fields not surfaced here.

Wire-shape (camelCase per the dashboard convention):

    { id, strategy, runDate, startDate, endDate,
      totalTrades, winningTrades, losingTrades,
      winRate, profitFactor, expectancy,
      sharpeRatio, maxDrawdownPct, totalPnl,
      createdAt }

See ``docs/api-tier-policy.md`` Tier 1.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_to_wire(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        # ``strategy_version`` is the column the M5 consumer stamps
        # with the strategy name (see run_backtest_m5.py); surface
        # under the friendlier ``strategy`` key for the dashboard.
        "strategy": row["strategy_version"],
        "runDate": row["run_date"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "totalTrades": _coerce_int(row["total_trades"]) or 0,
        "winningTrades": _coerce_int(row["winning_trades"]) or 0,
        "losingTrades": _coerce_int(row["losing_trades"]) or 0,
        "winRate": _coerce_float(row["win_rate"]),
        "profitFactor": _coerce_float(row["profit_factor"]),
        "expectancy": _coerce_float(row["expectancy"]),
        "sharpeRatio": _coerce_float(row["sharpe_ratio"]),
        "maxDrawdownPct": _coerce_float(row["max_drawdown_pct"]),
        "totalPnl": _coerce_float(row["total_pnl"]),
        "createdAt": row["created_at"],
    }


def _query_backtests(
    db_path: Path,
    limit: int,
    strategy: Optional[str],
) -> List[Dict[str, Any]]:
    """Return up to *limit* backtest rows, newest-first by id.

    ``id`` is a monotonic AUTOINCREMENT and the M5 consumer never
    backdates inserts, so ordering by id is equivalent to ordering
    by ``created_at`` and avoids a string-compare on the timestamp.

    *strategy* (optional, exact match against ``strategy_version``)
    lets the dashboard filter the history to one strategy at a time.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT id, run_date, strategy_version, start_date, end_date, "
            "total_trades, winning_trades, losing_trades, win_rate, "
            "profit_factor, expectancy, sharpe_ratio, max_drawdown_pct, "
            "total_pnl, created_at "
            "FROM backtest_results"
        )
        params: List[Any] = []
        if strategy:
            sql += " WHERE strategy_version = ?"
            params.append(strategy)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_wire(r) for r in rows]


@router.get("/backtests")
async def get_backtests(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    strategy: Optional[str] = Query(None, max_length=64),
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` recent backtest result rows.

    Best-effort: returns ``[]`` on missing DB, missing
    ``backtest_results`` table (fresh checkout, M5 consumer never
    ran), or any sqlite read error. The dashboard treats an empty
    list as "no backtests yet" and keeps the tab usable.
    """
    if not _DB_PATH.exists():
        return []
    try:
        return _query_backtests(_DB_PATH, limit, strategy)
    except sqlite3.OperationalError as exc:
        # "no such table: backtest_results" lands here on a fresh
        # checkout where the M5 consumer has never written; collapse
        # to an empty list instead of a 500.
        if "no such table" in str(exc).lower():
            return []
        logger.exception("backtests: sqlite operational error")
        return []
    except sqlite3.Error:  # allow-silent: tier-1 dashboard read; logged via logger.exception, dashboard treats [] as "no data" — same contract as trades_closed.py
        logger.exception("backtests: sqlite read failed")
        return []
    except Exception:  # noqa: BLE001  # allow-silent: tier-1 dashboard read; logged via logger.exception — never 500 the dashboard tab on an unexpected error
        logger.exception("backtests: unexpected error")
        return []
