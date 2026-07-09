"""S-557 — GET /api/bot/trades/closed.

Tier-1 read endpoint for the dashboard's Journals tab. Reads
``trade_journal.db::trades`` rows with ``status='closed'`` and the
non-backtest filter. ``closedAt`` prefers the canonical ``trades.closed_at``
column (P1-B, written by every close path); for rows predating that column /
its backfill it falls back to the legacy derivation
(``order_packages.updated_at`` via the join, then ``notes.closed_at`` JSON).

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
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.utils.paths import trade_journal_db_path
from src.web.api._asset_class import asset_class_for_symbol
from src.web.api._clean_trades import (
    account_class_wire,
    exclude_superseded_predicate,
    not_paper_predicate,
)
from src.web.api._closed_at import (
    close_time_sql,
    closed_at_norm_sql,
    normalize_closed_at_value,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


# closed_at normalisation is the single source of truth in
# src/web/api/_closed_at.py (shared with /api/bot/performance + /api/bot/stats
# pnl24h, which previously lacked this guard — the "/performance shows 0 closed
# trades while lifetime is non-zero" bug). The thin aliases below preserve the
# private names this module already uses throughout.
_closed_at_norm_sql = closed_at_norm_sql
_normalize_closed_at_value = normalize_closed_at_value


# The ordering / since key: normalise closed_at first (epoch-ms aware),
# then fall back to the order_packages.updated_at join, then the open
# timestamp — mirroring the wire ``closedAt`` derivation in _row_to_wire.
_CLOSED_AT_SORT_SQL = close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")

# Paper/not-paper split + the account_class wire helper come from the canonical
# src.web.api._clean_trades module (single source of truth). Joined ``trades``
# alias is ``t``. No reconciler exclusion here — /trades/closed is a transparent
# closed-trade LIST; an ``orphan_adopt`` row stays visible carrying its own
# strategy name (it is not silently dropped from a list the way it is from KPIs).
_NOT_PAPER_PREDICATE = not_paper_predicate("t.")
# Superseded rows are confirmed-void phantom orphan-flap DUPLICATES
# (orphan-flap hardening #5) — drop them from the list (distinct from the
# un-consolidated orphan_adopt rows, which the note above keeps visible).
_EXCLUDE_SUPERSEDED = exclude_superseded_predicate("t.")
_account_class_wire = account_class_wire

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
    # Prefer the canonical closed_at COLUMN (P1-B); fall back to the legacy
    # derivation (order_packages.updated_at -> notes.closed_at) for rows that
    # predate the column / its backfill. The column / notes value may be a
    # raw epoch-ms string (reconciler-filled writer) — normalise it to ISO
    # so the dashboard shows a real timestamp, not "1781839121796".
    closed_at = (
        _normalize_closed_at_value(row["closed_at"])
        or row["op_updated_at"]
        or _normalize_closed_at_value(notes_closed_at)
    )
    raw_pnl = row["pnl"]
    return {
        "id": str(row["id"]),
        "account": row["account_id"],
        # ``accountClass`` ("paper" | "real_money") — the canonical
        # paper/real funding category (trades.account_class, mirrored from
        # config/accounts.yaml). Never null: falls back to is_demo for rows
        # predating the column/backfill.
        "accountClass": _account_class_wire(row["account_class"], row["is_demo"]),
        # ``isDemo`` retained for back-compat (dual-emit). Source: the
        # legacy trades.is_demo boolean, kept in sync with account_class.
        "isDemo": bool(row["is_demo"]),
        "symbol": row["symbol"],
        # ``assetClass`` — coarse reporting bucket for the symbol (crypto /
        # index / commodity / bond / equity / fx / unknown) so a consumer can
        # group/filter closed trades by asset group. Reporting-only,
        # config-driven (config/instruments.yaml) with a heuristic fallback;
        # never null.
        "assetClass": asset_class_for_symbol(row["symbol"]),
        "side": _normalise_side(row["direction"]),
        "pattern": row["strategy_name"] if row["strategy_name"] else None,
        "qty": float(row["position_size"]) if row["position_size"] is not None else 0.0,
        "entryPrice": float(row["entry_price"]) if row["entry_price"] is not None else 0.0,
        "exitPrice": float(row["exit_price"]) if row["exit_price"] is not None else None,
        # 2026-06-04 reporting-cleanup: stop coercing NULL pnl to 0.0 —
        # the reconciler fallback path closes trades with pnl=NULL when
        # the broker close-pnl lookup fails (exit_reason ='reconciler_incomplete'
        # marks them). Coercing to 0 used to render them as "$0.00 closed
        # trade", indistinguishable from a real flat. Now they render as
        # ``realizedPnl: null`` and the consumer shows an em-dash.
        "realizedPnl": round(float(raw_pnl), 4) if raw_pnl is not None else None,
        "realizedPnlPct": (
            round(float(row["pnl_percent"]), 6)
            if row["pnl_percent"] is not None else None
        ),
        "openedAt": row["timestamp"],
        "closedAt": closed_at,
        "closeReason": _normalise_close_reason(row["exit_reason"]),
    }


def _query_closed_trades(
    db_path: Path, limit: int, since: Optional[str],
    account_id: Optional[str] = None,
    include_demo: bool = False,
) -> List[Dict[str, Any]]:
    """Return up to *limit* closed trades, newest-first by closedAt
    (``op.updated_at``), filtered by *since* (ISO-8601 UTC) when provided.

    Trades with no linked ``order_packages`` row fall through with
    ``op_updated_at = NULL``; those still appear in the result and use
    ``timestamp`` for ordering + ``notes.closed_at`` (when present) for
    the wire-shape ``closedAt``.

    ``include_demo``: when True, demo-account rows are included alongside
    live rows (each tagged via the ``isDemo`` field). When False (default),
    demo rows are excluded — preserves the pre-2026-06-04 behavior. The
    ``account_id`` filter, when set, always wins (returns only that
    account's rows regardless of this flag).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # _CLOSED_AT_SORT_SQL — COALESCE(normalised(t.closed_at),
        # datetime(op.updated_at), datetime(t.timestamp)) — is the ordering
        # key, and the same expression filters *since*. The normalisation
        # converts a raw epoch-ms closed_at (reconciler-filled writer) so it
        # doesn't datetime()-to-NULL and sink below the LIMIT. Backtest rows
        # are excluded so the dashboard never shows synthetic trades.
        sql = """
            SELECT t.id, t.account_id, t.is_demo, t.account_class, t.symbol,
                   t.direction, t.strategy_name,
                   t.position_size, t.entry_price, t.exit_price,
                   t.pnl, t.pnl_percent,
                   t.timestamp, t.closed_at, t.exit_reason, t.notes,
                   op.updated_at AS op_updated_at
            FROM trades t
            LEFT JOIN (
                SELECT linked_trade_id, MIN(updated_at) AS updated_at
                FROM order_packages
                WHERE linked_trade_id IS NOT NULL
                GROUP BY linked_trade_id
            ) op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
        """
        sql += _EXCLUDE_SUPERSEDED
        params: List[Any] = []
        if account_id:
            sql += " AND t.account_id = ?"
            params.append(account_id)
        elif not include_demo:
            # Exclude paper-money trades from the live journal view.
            sql += _NOT_PAPER_PREDICATE
        if since:
            sql += f" AND {_CLOSED_AT_SORT_SQL} >= datetime(?)"
            params.append(since)
        sql += f" ORDER BY {_CLOSED_AT_SORT_SQL} DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_wire(r) for r in rows]


@router.get("/trades/closed")
def get_closed_trades(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    since: Optional[str] = Query(None, max_length=64),
    account_id: Optional[str] = Query(None, max_length=64),
    include_paper: bool = Query(False),
    include_demo: bool = Query(False),
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` closed (non-backtest) trades.

    Each row carries ``accountClass`` ("paper" | "real_money") plus the
    legacy ``isDemo`` flag so consumers can split paper vs real in their UI.

    Filters:
      - ``account_id`` — return only that account's rows. Takes precedence.
      - ``include_paper=true`` — include paper-money rows alongside
        real-money (default false: real-money only).
      - ``include_demo`` — DEPRECATED alias for ``include_paper`` (kept for
        back-compat). Effective include = include_paper OR include_demo.

    Best-effort: returns ``[]`` on missing DB, locked DB, or an
    unexpected sqlite error. The dashboard treats an empty list the
    same as "no closed trades yet" and keeps the tab usable.
    """
    effective_include = include_paper or include_demo
    if not _DB_PATH.exists():
        return []
    try:
        return _query_closed_trades(
            _DB_PATH, limit, since, account_id=account_id,
            include_demo=effective_include,
        )
    except sqlite3.Error:
        logger.exception("trades_closed: sqlite read failed")
        return []
    except Exception:  # noqa: BLE001
        logger.exception("trades_closed: unexpected error")
        return []
