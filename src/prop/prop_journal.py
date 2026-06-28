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
        message          TEXT,
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
        # Forward-compat migration: add `message` to a prop_tickets table that
        # predates the column (CREATE TABLE IF NOT EXISTS won't add it). Cheap +
        # idempotent — skip silently when the column is already present.
        cols = {r[1] for r in c.execute("PRAGMA table_info(prop_tickets)")}
        if "message" not in cols:
            c.execute("ALTER TABLE prop_tickets ADD COLUMN message TEXT")
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
    valid_until, status, order_package_id, message (the rendered ticket text),
    meta(dict). Idempotent on ``ticket_id``: a re-emit updates ``status``,
    ``qty``, ``valid_until`` and ``message`` (COALESCEd so a re-emit never
    nulls a previously-captured message); the remaining columns — incl.
    ``order_package_id`` — are NOT updated on conflict, so the first write of
    each id is authoritative for them.
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
                 status, order_package_id, message, meta, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticket_id) DO UPDATE SET
                status = excluded.status,
                qty = excluded.qty,
                valid_until = excluded.valid_until,
                message = COALESCE(excluded.message, prop_tickets.message)
            """,
            (
                ticket_id, account_id, ticket.get("strategy"),
                ticket.get("symbol"), ticket.get("direction"), ticket.get("side"),
                _f(ticket.get("entry")), _f(ticket.get("sl")), _f(ticket.get("tp")),
                _f(ticket.get("qty")), _f(ticket.get("risk_usd")),
                ticket.get("signal_time"), ticket.get("valid_until"),
                str(ticket.get("status") or "emitted"),
                ticket.get("order_package_id"),
                ticket.get("message"),
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
        where.append("account_id = ?")
        params.append(account_id)
    if status:
        where.append("status = ?")
        params.append(status)
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


def _prop_scope(account_id: Optional[str] = None) -> set:
    """Resolve the strategy names that belong to prop accounts.

    Prop tickets are journaled as ordinary ``order_packages`` (that table has no
    account column), so we identify a prop ticket by the prop accounts'
    configured strategy names — read from ``accounts.yaml`` via the canonical
    loader, never hardcoded. ``account_id`` narrows to one account; otherwise the
    union across every prop account.
    """
    try:
        from src.config.accounts_loader import load_accounts_dict

        accts = load_accounts_dict()
    except Exception as exc:  # noqa: BLE001 — fail soft to "no scope"
        logger.warning("prop_journal: accounts.yaml load failed: %s", exc)
        return set()
    strategies: set = set()
    for aid, a in (accts or {}).items():
        if not isinstance(a, dict):
            continue
        is_prop = (
            str(a.get("exchange", "")).lower() == "breakout"
            or str(a.get("account_class", "")).lower() == "prop"
            or str(a.get("type", "")).lower() == "prop"
            or bool(a.get("backtest_ruleset")
                    and str(a.get("backtest_ruleset")) != "standard")
        )
        if not is_prop:
            continue
        if account_id and aid != account_id:
            continue
        for s in (a.get("strategies") or []):
            strategies.add(str(s))
    return strategies


def list_outbound_tickets(
    *, account_id: Optional[str] = None, status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """The canonical 'prop tickets sent' view.

    Single source of truth = ``order_packages`` (every prop decision is
    journaled there), filtered to the prop accounts' strategies, **enriched** by
    the ``prop_tickets`` sidecar (the rendered message + validity/qty captured at
    emit time). Historical tickets emitted before the sidecar existed still show
    up because ``order_packages`` has them — that was the wiring bug this
    replaces. Any sidecar row without a matching order package is still
    surfaced so nothing is hidden.
    """
    strategies = _prop_scope(account_id)
    conn = _connect(read_only=True)
    try:
        present = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        cap = max(int(limit) * 3, int(limit))
        op_rows = []
        if "order_packages" in present and strategies:
            qmarks = ",".join("?" * len(strategies))
            op_rows = conn.execute(
                f"SELECT order_package_id, created_at, updated_at, strategy_name, "
                f"symbol, direction, entry, sl, tp, status, close_reason, "
                f"linked_trade_id FROM order_packages "
                f"WHERE strategy_name IN ({qmarks}) "
                f"ORDER BY created_at DESC LIMIT ?",
                (*sorted(strategies), cap),
            ).fetchall()
        tk_rows = []
        if "prop_tickets" in present:
            tk_rows = conn.execute(
                "SELECT * FROM prop_tickets ORDER BY created_at DESC LIMIT ?",
                (cap,),
            ).fetchall()
    finally:
        conn.close()

    tk_by_op: Dict[str, Dict[str, Any]] = {}
    tk_orphans: List[Dict[str, Any]] = []
    for r in tk_rows:
        d = _ticket_row(r)
        opid = d.get("order_package_id")
        if opid:
            tk_by_op[opid] = d
        else:
            tk_orphans.append(d)

    out: List[Dict[str, Any]] = []
    seen: set = set()

    def _emit(op: Optional[Dict[str, Any]], tk: Dict[str, Any]) -> Dict[str, Any]:
        op = op or {}
        return {
            "order_package_id": op.get("order_package_id") or tk.get("order_package_id"),
            "ticket_id": tk.get("ticket_id"),
            "created_at": op.get("created_at") or tk.get("created_at"),
            "signal_time": tk.get("signal_time") or op.get("created_at"),
            "strategy": op.get("strategy_name") or tk.get("strategy"),
            "symbol": op.get("symbol") or tk.get("symbol"),
            "direction": op.get("direction") or tk.get("direction"),
            "entry": op.get("entry") if op.get("entry") is not None else tk.get("entry"),
            "sl": op.get("sl") if op.get("sl") is not None else tk.get("sl"),
            "tp": op.get("tp") if op.get("tp") is not None else tk.get("tp"),
            "qty": tk.get("qty"),
            "risk_usd": tk.get("risk_usd"),
            "valid_until": tk.get("valid_until"),
            # Status: the sidecar's emission/fill lifecycle when known, else the
            # order package's own status (open/closed/orphaned/rejected).
            "status": tk.get("status") or op.get("status"),
            "op_status": op.get("status"),
            "close_reason": op.get("close_reason"),
            "message": tk.get("message"),
            "source": "order_package" if op else "prop_ticket",
        }

    for r in op_rows:
        d = dict(r)
        opid = d.get("order_package_id")
        seen.add(opid)
        out.append(_emit(d, tk_by_op.get(opid, {})))
    # Sidecar tickets whose order package wasn't in the prop-strategy slice
    # (e.g. a strategy renamed, or scope couldn't resolve) — surface anyway.
    for opid, tk in tk_by_op.items():
        if opid not in seen:
            out.append(_emit(None, tk))
    for tk in tk_orphans:
        out.append(_emit(None, tk))

    if status:
        out = [r for r in out if str(r.get("status")) == status]
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return out[:limit]


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
        where.append("account_id = ?")
        params.append(account_id)
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
