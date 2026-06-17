"""S-014 M0 PR #1 — GET /api/pnl/history.

Per-day realised P&L history backing the Vercel dashboard's Performance
tab (daily bars + cumulative line + drawdown).

Reads ``trade_journal.db`` directly (single source of truth — no caching,
no parallel store). One row per UTC date in the requested window, even
on days with zero closed trades (so the chart x-axis is contiguous).

Empty journal or missing DB file → ``[]`` (200, not 503).
SQLite error on an existing file → 503.

**S-063 (2026-05-09): Tier-1 read surface — no session required.**
Operator decision option (a): drop ``require_session`` on this endpoint
only. Smallest blast radius, read-only data, the dashboard can hit it
without a login flow until S-065 stands one up. Every mutating route
keeps the gate. See ``docs/api-tier-policy.md`` for the full
Tier-1/Tier-2 split.

**S-063: response shape change.** Returns a flat ``PnlHistoryPoint[]``
matching the dashboard's TypeScript contract — ``[{date, pnl, trades},
...]`` ordered oldest → newest. Field rename: ``realized_usd`` → ``pnl``.
The previous wrapper (``{schema_version, days, points, as_of_utc}``) had
no other consumers.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from src.web.api.routers import pnl as pnl_module

router = APIRouter(prefix="/api", tags=["pnl"])

DEFAULT_DAYS = 7
MAX_DAYS = 90


def _query_history(
    db_path: Path, days: int, today_utc: date,
    account_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a contiguous list of N daily points, or ``[]`` if there are no
    realised trades in the window (or no DB).

    Zero-fill applies *within* the window once at least one day has data, so
    the chart gets a contiguous x-axis. With nothing to show, return ``[]``
    so the dashboard can render an explicit empty state.
    """
    if not db_path.exists():
        return []

    start = today_utc - timedelta(days=days - 1)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        base_where = (
            "COALESCE(is_backtest, 0) = 0"
            " AND status != 'open'"
            " AND substr(COALESCE(created_at, timestamp), 1, 10) >= ?"
            " AND substr(COALESCE(created_at, timestamp), 1, 10) <= ?"
        )
        params: list = [start.isoformat(), today_utc.isoformat()]
        if account_id:
            base_where += " AND account_id = ?"
            params.append(account_id)
        else:
            # Exclude paper-money trades from the real-money aggregate view.
            # account_class is authoritative; NULL rows fall back to is_demo.
            base_where += (
                " AND NOT (COALESCE(account_class,'') IN ('paper','prop')"
                " OR (account_class IS NULL AND COALESCE(is_demo,0)=1))"
            )
        cur.execute(
            f"""
            SELECT substr(COALESCE(created_at, timestamp), 1, 10) AS day,
                   COALESCE(SUM(pnl), 0)                         AS realized,
                   COUNT(*)                                       AS trades
              FROM trades
             WHERE {base_where}
             GROUP BY day
            """,
            params,
        )
        rows = {r[0]: (float(r[1]), int(r[2])) for r in cur.fetchall()}
    finally:
        conn.close()

    if not rows:
        return []

    points: List[Dict[str, Any]] = []
    for offset in range(days):
        d = (start + timedelta(days=offset)).isoformat()
        realized, trades = rows.get(d, (0.0, 0))
        points.append({
            "date": d,
            "pnl": round(realized, 2),
            "trades": trades,
        })
    return points


def build_pnl_history(
    days: int,
    db_path: Optional[Path] = None,
    now_utc: Optional[datetime] = None,
    account_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    db_path = db_path or pnl_module._resolve_db_path()
    now = now_utc or datetime.now(timezone.utc)
    try:
        return _query_history(db_path, days, now.date(), account_id=account_id)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "pnl_history_unavailable",
                "reason": f"db error: {exc.__class__.__name__}",
            },
        )


@router.get("/pnl/history")
async def get_pnl_history(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    account_id: Optional[str] = Query(None, max_length=64),
) -> List[Dict[str, Any]]:
    return build_pnl_history(days, account_id=account_id)
