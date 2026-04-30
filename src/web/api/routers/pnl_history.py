"""S-014 M0 PR #1 — GET /api/pnl/history.

Per-day realised P&L history backing the home-dashboard equity sparkline.

Reads ``trade_journal.db`` directly (single source of truth — no caching,
no parallel store). One row per UTC date in the requested window, even
on days with zero closed trades (so the sparkline x-axis is contiguous).

Empty journal or missing DB file → ``points: []`` (200, not 503).
SQLite error on an existing file → 503.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.web.api.auth import require_session
from src.web.api.routers import pnl as pnl_module

router = APIRouter(prefix="/api", tags=["pnl"])

SCHEMA_VERSION = 1
DEFAULT_DAYS = 7
MAX_DAYS = 90


def _query_history(
    db_path: Path, days: int, today_utc: date
) -> List[Dict[str, Any]]:
    """Return a contiguous list of N daily points, or ``[]`` if there are no
    realised trades in the window (or no DB).

    Zero-fill applies *within* the window once at least one day has data, so
    Chart.js gets a contiguous x-axis. With nothing to show, return ``[]``
    so the sparkline can render an explicit empty state.
    """
    if not db_path.exists():
        return []

    start = today_utc - timedelta(days=days - 1)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT substr(COALESCE(created_at, timestamp), 1, 10) AS day,
                   COALESCE(SUM(pnl), 0)                         AS realized,
                   COUNT(*)                                       AS trades
              FROM trades
             WHERE COALESCE(is_backtest, 0) = 0
               AND status != 'open'
               AND substr(COALESCE(created_at, timestamp), 1, 10) >= ?
               AND substr(COALESCE(created_at, timestamp), 1, 10) <= ?
             GROUP BY day
            """,
            (start.isoformat(), today_utc.isoformat()),
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
            "realized_usd": round(realized, 2),
            "trades": trades,
        })
    return points


def build_pnl_history(
    days: int,
    db_path: Optional[Path] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    db_path = db_path or pnl_module._resolve_db_path()
    now = now_utc or datetime.now(timezone.utc)
    try:
        points = _query_history(db_path, days, now.date())
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "pnl_history_unavailable",
                "reason": f"db error: {exc.__class__.__name__}",
            },
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "days": days,
        "points": points,
        "as_of_utc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


@router.get("/pnl/history")
async def get_pnl_history(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    _session: dict = Depends(require_session),
) -> Dict[str, Any]:
    return build_pnl_history(days)
