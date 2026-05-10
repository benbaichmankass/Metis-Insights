"""Local sqlite store for Bybit fill records (S-067 follow-up #6).

Phase-1 of the exchange-fills P&L attribution feature: store raw fill
records pulled from Bybit ``GET /v5/execution/list`` (or ccxt's
``fetch_my_trades`` wrapper around the same endpoint). Read-path
endpoints can compute per-strategy / per-symbol fee totals + flow
volumes from this store rather than from
``trade_journal.db::trades``, insulating performance reads from any
local schema or state bug.

The store is intentionally separate from ``trade_journal.db``:

- Different lifecycle: trades are written by the runtime; fills are
  pulled out-of-band from the exchange.
- Different trust contract: when local + exchange disagree, exchange
  wins. The two stores must not share a connection or transaction.
- Different gitignore class: ``trade_journal.db`` lives at repo
  root (gitignored individually); fills live under ``runtime_state/``
  (gitignored as a directory) alongside ``prop_state.json``.

The :func:`upsert_fills` helper is **idempotent** — the same fill
inserted twice produces a single row, keyed by Bybit's ``exec_id``.
This makes the puller safe to re-run on overlapping windows.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FILLS_DB_PATH = _REPO_ROOT / "runtime_state" / "exchange_fills.sqlite"


def get_fills_db_path() -> Path:
    """Resolve the fills DB path. Override via ``EXCHANGE_FILLS_DB``."""
    env = os.environ.get("EXCHANGE_FILLS_DB")
    if env:
        return Path(env)
    return _DEFAULT_FILLS_DB_PATH


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange_fills (
    exec_id        TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,
    price          REAL NOT NULL,
    qty            REAL NOT NULL,
    fee            REAL NOT NULL DEFAULT 0,
    fee_currency   TEXT,
    exec_time      TEXT NOT NULL,
    order_id       TEXT,
    is_maker       INTEGER NOT NULL DEFAULT 0,
    raw            TEXT,
    inserted_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exchange_fills_account_time
    ON exchange_fills (account_id, datetime(exec_time) DESC);
CREATE INDEX IF NOT EXISTS idx_exchange_fills_symbol_time
    ON exchange_fills (symbol, datetime(exec_time) DESC);
"""


def init_db(path: Optional[Path] = None) -> Path:
    """Create the store at *path* if it does not exist. Idempotent."""
    p = path or get_fills_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return p


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _normalise_fill(row: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce a fill record (Bybit V5 / ccxt-shaped) into the store
    schema. The puller is responsible for picking the right field
    aliases for its source; this helper just casts and fills defaults.
    """
    return {
        "exec_id": str(row["exec_id"]),
        "account_id": str(row["account_id"]),
        "symbol": str(row["symbol"]),
        "side": str(row["side"]).lower(),
        "price": float(row["price"]),
        "qty": float(row["qty"]),
        "fee": float(row.get("fee") or 0.0),
        "fee_currency": row.get("fee_currency"),
        "exec_time": str(row["exec_time"]),
        "order_id": row.get("order_id"),
        "is_maker": 1 if row.get("is_maker") else 0,
        "raw": json.dumps(row.get("raw")) if row.get("raw") is not None else None,
    }


def upsert_fills(
    rows: Iterable[Mapping[str, Any]],
    path: Optional[Path] = None,
) -> int:
    """Idempotently insert fill records keyed by ``exec_id``.

    Returns the number of NEW rows inserted (existing exec_ids are
    silently ignored — re-running the puller on overlapping windows
    is safe by design).
    """
    p = init_db(path)
    inserted = 0
    conn = sqlite3.connect(str(p))
    try:
        for raw in rows:
            row = _normalise_fill(raw)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO exchange_fills (
                    exec_id, account_id, symbol, side, price, qty, fee,
                    fee_currency, exec_time, order_id, is_maker, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["exec_id"], row["account_id"], row["symbol"],
                    row["side"], row["price"], row["qty"], row["fee"],
                    row["fee_currency"], row["exec_time"], row["order_id"],
                    row["is_maker"], row["raw"],
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


# ---------------------------------------------------------------------------
# Aggregates (read-side, used by /api/bot/pnl/exchange)
# ---------------------------------------------------------------------------


def aggregate_by_symbol(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Per-symbol fee + gross-volume aggregate over the last *days*.

    True P&L attribution requires lot-matching (FIFO buy/sell pairing)
    which is filed as a Phase-2 follow-up. Phase-1 surfaces the
    fee totals + gross flow, which is enough to (a) reconcile fee
    expectations against ``trade_journal.db::trades.pnl`` and (b)
    flag missing fills (zero-volume symbols where ``trade_journal.db``
    has executed orders).
    """
    if days <= 0:
        return []
    p = path or get_fills_db_path()
    if not p.exists():
        return []
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT symbol,
                   COUNT(*)                                    AS fill_count,
                   COALESCE(SUM(qty), 0)                       AS gross_qty,
                   COALESCE(SUM(qty * price), 0)               AS gross_notional,
                   COALESCE(SUM(fee), 0)                       AS total_fees,
                   MIN(exec_time)                              AS first_exec_time,
                   MAX(exec_time)                              AS last_exec_time
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            GROUP BY symbol
            ORDER BY symbol
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def aggregate_summary(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Cross-symbol summary over the last *days*."""
    if days <= 0:
        return {"fill_count": 0, "total_fees": 0.0, "symbol_count": 0,
                "window_days": days}
    p = path or get_fills_db_path()
    if not p.exists():
        return {"fill_count": 0, "total_fees": 0.0, "symbol_count": 0,
                "window_days": days}
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(p))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(fee), 0),
                   COUNT(DISTINCT symbol)
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            """,
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "fill_count": int(row[0] or 0),
        "total_fees": float(row[1] or 0.0),
        "symbol_count": int(row[2] or 0),
        "window_days": days,
    }
