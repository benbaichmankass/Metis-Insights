"""S-557 — GET /api/bot/trades/closed.

Tier-1 read endpoint for the dashboard's Journals tab. Reads
``trade_journal.db::trades`` rows with ``status='closed'`` and the
non-backtest filter, joining ``order_packages.updated_at`` as the
authoritative ``closed_at`` (the trades table has no closed_at column
of its own — see comment in ``src/runtime/order_monitor.py:2277``).

The dashboard already has a fallback path that derives best-effort
closed-trade rows from ``/api/bot/logs`` when this endpoint isn't
deployed yet (``ict-trader-dashboard/src/services/api.ts``
``getClosedTrades`` handles the 404). Once this endpoint deploys, the
fallback path goes silent automatically — no dashboard change needed.

Wire-shape (camelCase per the dashboard ``ClosedTrade`` interface):

    { id, account, symbol, side, pattern, qty, entryPrice, exitPrice,
      realizedPnl, realizedPnlPct, openedAt, closedAt, closeReason }

See ``docs/api-tier-policy.md`` Tier 1.
"""
from __future__ import annotations

import json
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

# direction values seen in the wild + their wire-shape side equivalents.
_SIDE_MAP = {
    "buy": "buy",
    "sell": "sell",
    "long": "buy",
    "short": "sell",
}

# exit_reason → closeReason normaliser. Anything matching `reconciler*`
# (e.g. `reconciler_filled`, `reconciler_orphaned`) collapses to
# `reconciler`; the four-known-reasons union matches the dashboard's
# `ClosedTrade.closeReason` type so renderers can switch on it.
_KNOWN_REASONS = {"tp", "sl", "manual"}


def _normalise_side(direction: Any) -> str:
    if not isinstance(direction, str):
        return str(direction or "")
    return _SIDE_MAP.get(direction.strip().lower(), direction.strip().lower())


def _normalise_close_reason(exit_reason: Any) -> Optional[str]:
    if not isinstance(exit_reason, str) or not exit_reason.strip():
        return None
    raw = exit_reason.strip().lower()
    if raw in _KNOWN_REASONS:
        return raw
    if raw.startswith("reconciler"):
        return "reconciler"
    return "other"


def _decode_notes_closed_at(notes: Any) -> Optional[str]:
    """The reconciler-close path stuffs ``closed_at`` into the trade's
    ``notes`` JSON (see ``src/runtime/order_monitor.py:2237``). We use
    this as a fallback when the trade has no linked order_packages row."""
    if not isinstance(notes, str) or not notes:
        return None
    try:
        decoded = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    val = decoded.get("closed_at")
    return str(val) if val is not None else None


def _row_to_wire(row: sqlite3.Row) -> Dict[str, Any]:
    notes_closed_at = _decode_notes_closed_at(row["notes"])
    closed_at = row["op_updated_at"] or notes_closed_at
    return {
        "id": str(row["id"]),
        "account": row["account_id"],
        "symbol": row["symbol"],
        "side": _normalise_side(row["direction"]),
        "pattern": row["strategy_name"] if row["strategy_name"] else None,
        "qty": float(row["position_size"]) if row["position_size"] is not None else 0.0,
        "entryPrice": float(row["entry_price"]) if row["entry_price"] is not None else 0.0,
        "exitPrice": float(row["exit_price"]) if row["exit_price"] is not None else None,
        "realizedPnl": round(float(row["pnl"] or 0.0), 4),
        "realizedPnlPct": (
            round(float(row["pnl_percent"]), 6)
            if row["pnl_percent"] is not None else None
        ),
        "openedAt": row["timestamp"],
        "closedAt": closed_at,
        "closeReason": _normalise_close_reason(row["exit_reason"]),
    }


def _query_closed_trades(
    db_path: Path, limit: int, since: Optional[str]
) -> List[Dict[str, Any]]:
    """Return up to *limit* closed trades, newest-first by closedAt
    (``op.updated_at``), filtered by *since* (ISO-8601 UTC) when provided.

    Trades with no linked ``order_packages`` row fall through with
    ``op_updated_at = NULL``; those still appear in the result and use
    ``timestamp`` for ordering + ``notes.closed_at`` (when present) for
    the wire-shape ``closedAt``.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # COALESCE(op.updated_at, t.timestamp) is the ordering key — and
        # the same expression filters *since*. Backtest rows are excluded
        # so the dashboard never shows synthetic trades.
        sql = """
            SELECT t.id, t.account_id, t.symbol, t.direction, t.strategy_name,
                   t.position_size, t.entry_price, t.exit_price,
                   t.pnl, t.pnl_percent,
                   t.timestamp, t.exit_reason, t.notes,
                   op.updated_at AS op_updated_at
            FROM trades t
            LEFT JOIN order_packages op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
        """
        params: List[Any] = []
        if since:
            sql += (
                " AND datetime(COALESCE(op.updated_at, t.timestamp)) >= datetime(?)"
            )
            params.append(since)
        sql += (
            " ORDER BY datetime(COALESCE(op.updated_at, t.timestamp)) DESC LIMIT ?"
        )
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_wire(r) for r in rows]


@router.get("/trades/closed")
async def get_closed_trades(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    since: Optional[str] = Query(None, max_length=64),
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` closed (live, non-backtest) trades.

    Best-effort: returns ``[]`` on missing DB, locked DB, or an
    unexpected sqlite error. The dashboard treats an empty list the
    same as "no closed trades yet" and keeps the tab usable.
    """
    if not _DB_PATH.exists():
        return []
    try:
        return _query_closed_trades(_DB_PATH, limit, since)
    except sqlite3.Error:
        logger.exception("trades_closed: sqlite read failed")
        return []
    except Exception:  # noqa: BLE001
        logger.exception("trades_closed: unexpected error")
        return []
