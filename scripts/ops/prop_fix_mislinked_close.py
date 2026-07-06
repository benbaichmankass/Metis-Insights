#!/usr/bin/env python3
"""Repair a mis-linked prop CLOSE (BL-20260706-PROP-CLOSE-MISLINK) — one-shot
prop-journal hygiene.

Background: before the ``match_fill_to_ticket`` fix (PR #5744), a prop CLOSE with
no explicit ``ticket_id`` linked to the *newest* open-status ticket — which could
be a never-placed ``emitted`` SIGNAL rather than the actually-``filled`` POSITION.
On breakout_1 the 2026-07-06 ETH close (``prop_fills`` id 17) linked to the
emitted ticket ``prop-manual-849ece101a3c`` (07-05 14:01) instead of the filled
position ticket ``prop-manual-5bc393741ec4`` (07-05 08:00). Net effect: the
phantom signal ticket got marked ``closed`` and the real position stayed
``filled`` (dashboard "still open").

The code fix (#5744) stops recurrence; this repairs the rows already written,
with NO artifacts (the Option-B clean end state):

  1. **relink** the close fill to the real position's ticket
     (``prop_fills.ticket_id``: ``<from>`` → ``<to>``);
  2. **close** the real position's ticket (``prop_tickets.status``: ``filled`` →
     ``closed``);
  3. **restore** the phantom ticket wrongly marked ``closed`` back to its true
     stale state (``prop_tickets.status``: ``closed`` → ``expired``).

Every step is **guarded by its expected current value**, so the tool is
idempotent: once applied, a re-run matches nothing and is a clean no-op. It only
ever touches ``prop_fills`` / ``prop_tickets`` (the prop journal is isolated from
the real-money/paper KPIs) — never a ``trades`` row, never an exchange position.

**DRY-RUN by default** — prints the planned changes (current → new) without
writing. ``--apply`` commits (the wrapper takes a DB backup first).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional


def _backup_db(db_path: str) -> str:
    """Timestamped ``cp`` backup taken before any ``--apply`` write."""
    dest = f"{db_path}.bak-prop-mislinked-close-{int(time.time())}"
    shutil.copy2(db_path, dest)
    return dest


def _connect(db_path: str, read_only: bool) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro" if read_only else f"file:{db_path}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def plan_repair(
    conn: sqlite3.Connection,
    *,
    relink_fill_id: int,
    relink_from_ticket: str,
    relink_to_ticket: str,
    close_ticket: str,
    restore_ticket: str,
    restore_status: str,
) -> List[Dict[str, Any]]:
    """Build the guarded operation plan (only ops whose precondition holds)."""
    ops: List[Dict[str, Any]] = []

    # 1. relink the close fill, only if it is currently mis-linked as expected.
    row = conn.execute(
        "SELECT id, ticket_id, symbol, status, pnl FROM prop_fills WHERE id = ?",
        (relink_fill_id,),
    ).fetchone()
    if row is None:
        ops.append({"op": "relink_fill", "skip": f"fill id {relink_fill_id} not found"})
    elif str(row["ticket_id"] or "") != relink_from_ticket:
        ops.append({
            "op": "relink_fill", "skip":
            f"fill {relink_fill_id} ticket_id={row['ticket_id']!r} != expected "
            f"{relink_from_ticket!r} (already repaired or different state)",
        })
    else:
        ops.append({
            "op": "relink_fill", "table": "prop_fills", "id": relink_fill_id,
            "column": "ticket_id", "from": relink_from_ticket, "to": relink_to_ticket,
        })

    # 2. close the real position's ticket, only if currently `filled`.
    ops.append(_ticket_status_op(conn, close_ticket, expect="filled", new="closed",
                                 label="close_position_ticket"))
    # 3. restore the phantom ticket, only if currently `closed` (the wrong state).
    ops.append(_ticket_status_op(conn, restore_ticket, expect="closed", new=restore_status,
                                 label="restore_phantom_ticket"))
    return ops


def _ticket_status_op(
    conn: sqlite3.Connection, ticket_id: str, *, expect: str, new: str, label: str,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT ticket_id, status, symbol, direction FROM prop_tickets WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return {"op": label, "skip": f"ticket {ticket_id!r} not found"}
    cur = str(row["status"] or "")
    if cur != expect:
        return {"op": label, "skip":
                f"ticket {ticket_id!r} status={cur!r} != expected {expect!r} "
                f"(already repaired or different state)"}
    return {"op": label, "table": "prop_tickets", "ticket_id": ticket_id,
            "column": "status", "from": expect, "to": new}


def apply_plan(conn: sqlite3.Connection, ops: List[Dict[str, Any]]) -> int:
    changed = 0
    for op in ops:
        if op.get("skip"):
            continue
        if op["op"] == "relink_fill":
            cur = conn.execute(
                "UPDATE prop_fills SET ticket_id = ? WHERE id = ? AND ticket_id = ?",
                (op["to"], op["id"], op["from"]),
            )
        else:
            cur = conn.execute(
                "UPDATE prop_tickets SET status = ? WHERE ticket_id = ? AND status = ?",
                (op["to"], op["ticket_id"], op["from"]),
            )
        changed += cur.rowcount
    conn.commit()
    return changed


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="trade_journal.db path")
    ap.add_argument("--relink-fill", type=int, required=True, help="prop_fills.id to repoint")
    ap.add_argument("--relink-from-ticket", required=True, help="guard: its current (wrong) ticket_id")
    ap.add_argument("--relink-to-ticket", required=True, help="the correct position ticket_id")
    ap.add_argument("--close-ticket", required=True, help="ticket to set filled→closed")
    ap.add_argument("--restore-ticket", required=True, help="phantom ticket to set closed→restore-status")
    ap.add_argument("--restore-status", default="expired", help="phantom ticket's restored status (default expired)")
    ap.add_argument("--apply", action="store_true", help="commit the writes (default: dry-run report)")
    args = ap.parse_args(argv)

    if args.apply:
        backup = _backup_db(args.db)
        print(f"backup: {backup}")
    conn = _connect(args.db, read_only=not args.apply)
    try:
        ops = plan_repair(
            conn,
            relink_fill_id=args.relink_fill,
            relink_from_ticket=args.relink_from_ticket,
            relink_to_ticket=args.relink_to_ticket,
            close_ticket=args.close_ticket,
            restore_ticket=args.restore_ticket,
            restore_status=args.restore_status,
        )
        actionable = [o for o in ops if not o.get("skip")]
        print(f"prop_fix_mislinked_close — {'APPLY' if args.apply else 'DRY-RUN'} — db={args.db}")
        print(json.dumps({"ops": ops, "actionable": len(actionable)}, indent=2))
        if args.apply and actionable:
            changed = apply_plan(conn, ops)
            print(f"APPLIED: {changed} row(s) updated.")
        elif args.apply:
            print("APPLIED: nothing to do (already repaired / preconditions not met).")
        else:
            print("Dry-run — no rows changed. Re-run with --apply to commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
