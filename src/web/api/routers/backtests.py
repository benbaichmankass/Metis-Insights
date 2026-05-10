"""M5 P4 — GET /api/bot/backtests.

Tier-1 read endpoint for the dashboard's backtest-history tab. Reads
``trade_journal.db::backtest_results`` rows written by the M5 pipeline
(``src/backtest/run_backtest_m5.py::main`` → ``Database.save_backtest_results``)
plus the older ad-hoc backtest harness in ``src/backtest/run_backtest.py``,
both of which target the same canonical table.

The dashboard's backtest-history tab is the optional P4 deliverable
carved out of the M5 close (#640). The companion UI tab + Vercel
wiring lives in ``benbaichmankass/ict-trader-dashboard``; this repo
only ships the data feed.

Wire-shape (camelCase per the dashboard convention used by
``trades_closed.py``):

    { id, runDate, strategy, startDate, endDate, totalTrades,
      winningTrades, losingTrades, winRate, profitFactor, expectancy,
      maxDrawdown, maxDrawdownPct, sharpeRatio, totalPnl, totalPnlPct,
      avgWin, avgLoss, largestWin, largestLoss, createdAt }

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


def _row_to_wire(row: sqlite3.Row) -> Dict[str, Any]:
    def _f(key: str) -> Optional[float]:
        val = row[key]
        return float(val) if val is not None else None

    def _i(key: str) -> Optional[int]:
        val = row[key]
        return int(val) if val is not None else None

    return {
        "id": str(row["id"]),
        "runDate": row["run_date"],
        "strategy": row["strategy_version"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "totalTrades": _i("total_trades"),
        "winningTrades": _i("winning_trades"),
        "losingTrades": _i("losing_trades"),
        "winRate": _f("win_rate"),
        "profitFactor": _f("profit_factor"),
        "expectancy": _f("expectancy"),
        "maxDrawdown": _f("max_drawdown"),
        "maxDrawdownPct": _f("max_drawdown_pct"),
        "sharpeRatio": _f("sharpe_ratio"),
        "totalPnl": _f("total_pnl"),
        "totalPnlPct": _f("total_pnl_pct"),
        "avgWin": _f("avg_win"),
        "avgLoss": _f("avg_loss"),
        "largestWin": _f("largest_win"),
        "largestLoss": _f("largest_loss"),
        "createdAt": row["created_at"],
    }


def _query_backtest_results(
    db_path: Path, limit: int, strategy: Optional[str], since: Optional[str]
) -> List[Dict[str, Any]]:
    """Return up to *limit* backtest rows, newest-first by ``created_at``,
    optionally filtered by *strategy* (exact match on ``strategy_version``)
    and *since* (ISO-8601, applied to ``created_at``).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT id, run_date, strategy_version, start_date, end_date, "
            "total_trades, winning_trades, losing_trades, "
            "win_rate, profit_factor, expectancy, "
            "max_drawdown, max_drawdown_pct, sharpe_ratio, "
            "total_pnl, total_pnl_pct, avg_win, avg_loss, "
            "largest_win, largest_loss, created_at "
            "FROM backtest_results"
        )
        params: List[Any] = []
        clauses: List[str] = []
        if strategy:
            clauses.append("strategy_version = ?")
            params.append(strategy)
        if since:
            clauses.append("datetime(created_at) >= datetime(?)")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
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
    since: Optional[str] = Query(None, max_length=64),
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` backtest_results rows, newest-first.

    Best-effort: returns ``[]`` on missing DB, missing
    ``backtest_results`` table (fresh install never ran a backtest),
    locked DB, or unexpected sqlite error. The dashboard treats an
    empty list the same as "no backtests yet" and keeps the tab usable.
    """
    if not _DB_PATH.exists():
        return []
    try:
        return _query_backtest_results(_DB_PATH, limit, strategy, since)
    except sqlite3.OperationalError as exc:
        # `no such table: backtest_results` is the expected shape on a
        # fresh install where neither the M5 consumer nor the standalone
        # backtest harness has ever run. Surface it as empty without an
        # error log so the dashboard isn't noisy on day 1.
        if "no such table" in str(exc).lower():
            return []
        logger.exception("backtests: sqlite OperationalError")
        return []
    except sqlite3.Error:
        logger.exception("backtests: sqlite read failed")
        return []
    except Exception:  # noqa: BLE001  # allow-silent: read-path Tier-1 endpoint matches the trades_closed shape — never crash on a malformed row.
        logger.exception("backtests: unexpected error")
        return []
