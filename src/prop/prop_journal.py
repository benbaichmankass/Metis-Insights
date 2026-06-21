"""Prop-account journal — the inbound half of the Breakout manual bridge (P2).

The Breakout prop account has **no broker API**, so the bot never sees a fill or
a close on its own. The manual-bridge design
(``docs/integrations/breakout-poc-manual-bridge-DESIGN.md``) closes that loop
with a report-back path: the executor (browser-Claude / Comet / the operator)
posts the fill/close + periodic account-status back, and this module journals it.

Three tables, all in the canonical ``trade_journal.db`` (so the Data Explorer
federates them automatically) but **kept entirely separate from the live
``trades`` table** — prop is a third funding class (``account_class: prop``),
explicitly excluded from the real-money AND paper KPIs, so its rows must never
leak into the ``/stats`` / ``/performance`` / ``/pnl`` aggregates:

- ``prop_tickets``        — one row per OUTBOUND ticket the bot emitted
  (``breakout_executor.emit_prop_ticket``). The anchor reconciliation matches
  inbound fills against, and the source of "un-acted ticket" detection.
- ``prop_fills``          — one row per INBOUND fill/close report. Linked to a
  ticket (``ticket_id``) when reconciliation finds a match.
- ``prop_account_status`` — one row per INBOUND account-status snapshot
  (balance / equity / day P&L / drawdown) → drives the dashboard rule-distance
  panel (distance to the $150 daily-loss and $300 static-DD limits).

Read-mostly + lazy: tables are created on first touch (mirrors the
``device_tokens`` lazy pattern). Every writer is best-effort at the call site
(the executor never loses an emission over a journal write); this module itself
raises on a genuinely bad write so the ingest endpoint can surface a 500.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    from src.utils.paths import trade_journal_db_path

    return str(trade_journal_db_path())


def _connect(read_only: bool = False) -> sqlite3.Connection:
    path = _db_path()
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS prop_tickets (
        ticket_id        TEXT PRIMARY KEY,
        account_id       TEXT NOT NULL,
        strategy         TEXT,
        symbol           TEXT,
        direction        TEXT,
        side             TEXT,
        entry            REAL,
        sl               REAL,
        tp               REAL,
        qty              REAL,
        risk_usd         REAL,
        signal_time      TEXT,
        valid_until      TEXT,
        status           TEXT NOT NULL DEFAULT 'emitted',
        order_package_id TEXT,
        meta             TEXT,
        created_at       TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prop_fills (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id        TEXT NOT NULL,
        ticket_id         TEXT,
        external_order_id TEXT,
        symbol            TEXT,
        direction         TEXT,
        qty               REAL,
        entry_price       REAL,
        exit_price        REAL,
        pnl               REAL,
        pnl_percent       REAL,
        status            TEXT NOT NULL DEFAULT 'closed',
        reason            TEXT,
        opened_at         TEXT,
        closed_at         TEXT,
        reported_at       TEXT NOT NULL,
        raw               TEXT,
        created_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prop_account_status (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL,
        balance         REAL,
        equity          REAL,
        realized_today  REAL,
        unrealized      REAL,
        day_start_balance REAL,
        drawdown        REAL,
        reported_at     TEXT NOT NULL,
        raw             TEXT,
        created_at      TEXT NOT NULL
    )
    """,
)

_TABLES = ("prop_tickets", "prop_fills", "prop_account_status")


def ensure_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    """Idempotently create the three prop journal tables."""
    own = conn is None
    c = conn or _connect()
    try:
        for ddl in _DDL:
            c.execute(ddl)
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def tables_present() -> List[str]:
    """Return which prop tables currently exist (for graceful empty reads)."""
    try:
        conn = _connect(read_only=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('prop_tickets','prop_fills','prop_account_status')"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ── Outbound tickets ──────────────────────────────────────────────────

def record_ticket(ticket: Dict[str, Any]) -> str:
    """Insert (or replace) an outbound prop ticket.

    ``ticket`` keys: ticket_id (required), account_id (required), strategy,
    symbol, direction, side, entry, sl, tp, qty, risk_usd, signal_time,
    valid_until, status, order_package_id, meta(dict). Idempotent on
    ``ticket_id`` (re-emit replaces).
    """
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    account_id = str(ticket.get("account_id") or "").strip()
    if not ticket_id or not account_id:
        raise ValueError("record_ticket needs ticket_id + account_id")
    meta = ticket.get("meta")
    conn = _connect()
    try:
        ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO prop_tickets
                (ticket_id, account_id, strategy, symbol, direction, side,
                 entry, sl, tp, qty, risk_usd, signal_time, valid_until,
                 status, order_package_id, meta, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticket_id) DO UPDATE SET
                status = excluded.status,
                qty = excluded.qty,
                valid_until = excluded.valid_until
            """,
            (
                ticket_id, account_id, ticket.get("strategy"),
                ticket.get("symbol"), ticket.get("direction"), ticket.get("side"),
                _f(ticket.get("entry")), _f(ticket.get("sl")), _f(ticket.get("tp")),
                _f(ticket.get("qty")), _f(ticket.get("risk_usd")),
                ticket.get("signal_time"), ticket.get("valid_until"),
                str(ticket.get("status") or "emitted"),
                ticket.get("order_package_id"),
                json.dumps(meta) if isinstance(meta, (dict, list)) else None,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return ticket_id


def set_ticket_status(ticket_id: str, status: str) -> int:
    conn = _connect()
    try:
        ensure_tables(conn)
        cur = conn.execute(
            "UPDATE prop_tickets SET status = ? WHERE ticket_id = ?",
            (status, ticket_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def list_tickets(
    *, account_id: Optional[str] = None, status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    if not tables_present():
        return []
    where, params = [], []
    if account_id:
        where.append("account_id = ?"); params.append(account_id)
    if status:
        where.append("status = ?"); params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = _connect(read_only=True)
    try:
        rows = conn.execute(
            f"SELECT * FROM prop_tickets{clause} ORDER BY created_at DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [_ticket_row(r) for r in rows]
    finally:
        conn.close()


# ── Inbound fills ─────────────────────────────────────────────────────

def insert_fill(fill: Dict[str, Any]) -> int:
    """Insert one inbound fill/close report; returns the new row id."""
    account_id = str(fill.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("insert_fill needs account_id")
    conn = _connect()
    try:
        ensure_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO prop_fills
                (account_id, ticket_id, external_order_id, symbol, direction,
                 qty, entry_price, exit_price, pnl, pnl_percent, status, reason,
                 opened_at, closed_at, reported_at, raw, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account_id, fill.get("ticket_id"), fill.get("external_order_id"),
                fill.get("symbol"), fill.get("direction"),
                _f(fill.get("qty")), _f(fill.get("entry_price")),
                _f(fill.get("exit_price")), _f(fill.get("pnl")),
                _f(fill.get("pnl_percent")),
                str(fill.get("status") or "closed"), fill.get("reason"),
                fill.get("opened_at"), fill.get("closed_at"),
                _now_iso(),
                json.dumps(fill.get("raw")) if fill.get("raw") is not None else None,
                _now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_fills(
    *, account_id: Optional[str] = None, limit: int = 100,
) -> List[Dict[str, Any]]:
    if not tables_present():
        return []
    where, params = [], []
    if account_id:
        where.append("account_id = ?"); params.append(account_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = _connect(read_only=True)
    try:
        rows = conn.execute(
            f"SELECT * FROM prop_fills{clause} ORDER BY id DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Inbound account-status snapshots ──────────────────────────────────

def insert_account_status(status: Dict[str, Any]) -> int:
    account_id = str(status.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("insert_account_status needs account_id")
    conn = _connect()
    try:
        ensure_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO prop_account_status
                (account_id, balance, equity, realized_today, unrealized,
                 day_start_balance, drawdown, reported_at, raw, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account_id, _f(status.get("balance")), _f(status.get("equity")),
                _f(status.get("realized_today")), _f(status.get("unrealized")),
                _f(status.get("day_start_balance")), _f(status.get("drawdown")),
                _now_iso(),
                json.dumps(status.get("raw")) if status.get("raw") is not None else None,
                _now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def latest_account_status(account_id: str) -> Optional[Dict[str, Any]]:
    if not tables_present():
        return None
    conn = _connect(read_only=True)
    try:
        row = conn.execute(
            "SELECT * FROM prop_account_status WHERE account_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── helpers ───────────────────────────────────────────────────────────

def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ticket_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    if d.get("meta"):
        try:
            d["meta"] = json.loads(d["meta"])
        except (ValueError, TypeError):
            pass
    return d


__all__ = [
    "ensure_tables",
    "tables_present",
    "record_ticket",
    "set_ticket_status",
    "list_tickets",
    "insert_fill",
    "list_fills",
    "insert_account_status",
    "latest_account_status",
]
