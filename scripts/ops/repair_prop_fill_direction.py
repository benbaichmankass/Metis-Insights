#!/usr/bin/env python3
"""Repair prop_fills rows that are UNCLOSABLE because they carry no direction.

BL-20260820-PROP-FILL-DIRECTION-ADMISSION-GAP — the data half.

WHY
---
``prop_monitor_pulse._position_key`` identifies a prop position by
``(account_id, symbol, canonical_direction)``. ``prop_report.ingest_report``
— the single chokepoint every report-back passes through — validates
``account_id`` and ``symbol`` and lets ``direction`` through unvalidated. A
fill admitted with no direction therefore keys as ``akd:<acct>|<SYM>|`` while
its own close, reported with a direction, keys as ``akd:<acct>|<SYM>|long``.
Different keys, so the close is invisible to the open-position filter and the
row reads OPEN for ever:

  - ``prop_monitor_pulse`` pings hourly about a trade that is closed;
  - ``prop_sl_tp_alert._sl_crossed('')`` falls through to ``return False``, so
    the position can never fire a stop-loss or take-profit alert — the half
    that matters on an account with a static-DD floor.

The motivating row, measured 2026-08-20 over the full 32-row population:
``prop_fills`` id 30, ``breakout_1`` / ``SOLUSDT`` / ``direction NULL`` /
``open`` / qty 83.0, reported 2026-08-19T12:52:40, ticket
``prop-manual-5e30b930…`` — whose own closes (ids 31, 32) both carry
``direction='long'``.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------
It fills in the MISSING FIELD from an authority that already recorded it: the
linked outbound ticket. It does **not** invent a direction, does **not** write
a synthetic close fill (that would trade a phantom-open artifact for a
phantom-close one, and would fire a spurious ``prop_closed`` notification),
and does **not** touch ``trades``, ``order_packages`` or any exchange position.

**Parameterised over the class, not hardcoded to the incident.** This is the
fourth occurrence of one shape — ticket-id mismatch, direction alias
(BL-20260708-PROP-PULSE-DIRECTION-ALIAS), direction absent — and the first
three each got their own single-purpose repair script with row ids baked in.
A fifth bespoke script is the failure the operator named: *"we keep on fixing
the one sequence but not fixing the root problem."* So the selector is a
predicate, not an id.

This is the DATA repair only. The structural fix — making admission cover
identity in ``ingest_report``, and giving the prop book an explicit
reconciliation state so "not reported closed" is distinguishable from
"confirmed open" — is the row's resolution criteria and is NOT done here.

SAFETY
------
- **DRY-RUN by default**; ``--apply`` writes, and the wrapper takes a DB
  backup first.
- Every update is **guarded by its expected current value** (direction still
  empty, id unchanged), so a re-run after applying matches nothing: idempotent.
- A row whose ticket carries no usable direction either is **reported and
  skipped**, never guessed. "We could not resolve it" and "there was nothing
  to do" are separate outcomes in the output.
- Read-only connection unless ``--apply``.

Usage:
  python3 scripts/ops/repair_prop_fill_direction.py                # dry-run, all accounts
  python3 scripts/ops/repair_prop_fill_direction.py --account breakout_1
  python3 scripts/ops/repair_prop_fill_direction.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

# Same canonical vocabulary the runtime keys on. Imported rather than
# re-declared so this tool and the pulse can never disagree about what "long"
# means — re-deriving it here is exactly how the alias fix and the admission
# gap ended up in two different modules.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Statuses that make a fill represent a POSITION (the pulse's _OPEN_STATUSES
#: plus 'closed'): a directionless row in any of these is unclosable/unkeyable.
_POSITION_STATUSES = ("open", "filled", "closed")


def _canonical_direction(value: Any) -> str:
    from src.prop.prop_monitor_pulse import _canonical_direction as canon
    return canon(value)


def _backup_db(db_path: str) -> str:
    dest = f"{db_path}.bak-prop-fill-direction-{int(time.time())}"
    shutil.copy2(db_path, dest)
    return dest


def _connect(db_path: str, read_only: bool) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro" if read_only else f"file:{db_path}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def plan_repairs(conn: sqlite3.Connection,
                 account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Rows needing repair, each with the resolved direction or a refusal reason.

    Three outcomes per candidate, never collapsed into two:
      ``resolvable``   — the linked ticket carries a usable direction
      ``no_ticket``    — the fill has no ticket_id to resolve from
      ``ticket_blank`` — the ticket exists and its direction is unusable too
    """
    q = (
        "SELECT id, account_id, symbol, direction, status, qty, ticket_id, "
        "       reported_at "
        "FROM prop_fills "
        "WHERE (direction IS NULL OR TRIM(direction) = '') "
        f"  AND LOWER(COALESCE(status,'')) IN ({','.join('?' * len(_POSITION_STATUSES))})"
    )
    params: List[Any] = list(_POSITION_STATUSES)
    if account_id:
        q += " AND account_id = ?"
        params.append(account_id)
    q += " ORDER BY id"

    out: List[Dict[str, Any]] = []
    for r in conn.execute(q, params).fetchall():
        row = dict(r)
        tid = row.get("ticket_id")
        resolved = ""
        if tid:
            t = conn.execute(
                "SELECT direction FROM prop_tickets WHERE ticket_id = ?", (str(tid),)
            ).fetchone()
            if t is not None:
                resolved = _canonical_direction(t["direction"])
        if resolved:
            row["outcome"] = "resolvable"
            row["resolved_direction"] = resolved
        elif not tid:
            row["outcome"] = "no_ticket"
            row["resolved_direction"] = None
        else:
            row["outcome"] = "ticket_blank"
            row["resolved_direction"] = None
        out.append(row)
    return out


def apply_repairs(conn: sqlite3.Connection,
                  planned: List[Dict[str, Any]]) -> Dict[str, int]:
    """Write only the ``resolvable`` rows, each guarded by its current value."""
    updated = 0
    for row in planned:
        if row.get("outcome") != "resolvable":
            continue
        cur = conn.execute(
            "UPDATE prop_fills SET direction = ? "
            "WHERE id = ? AND (direction IS NULL OR TRIM(direction) = '')",
            (row["resolved_direction"], row["id"]),
        )
        updated += cur.rowcount
    conn.commit()
    return {"updated": updated}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", default=None, help="restrict to one account_id")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--db", default=None, help="override the journal path")
    args = ap.parse_args(argv)

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())

    backup = None
    if args.apply:
        backup = _backup_db(db_path)

    conn = _connect(db_path, read_only=not args.apply)
    try:
        planned = plan_repairs(conn, account_id=args.account)
        result: Dict[str, Any] = {
            "db": db_path,
            "backup": backup,
            "applied": bool(args.apply),
            "candidates": len(planned),
            "resolvable": sum(1 for p in planned if p["outcome"] == "resolvable"),
            "no_ticket": sum(1 for p in planned if p["outcome"] == "no_ticket"),
            "ticket_blank": sum(1 for p in planned if p["outcome"] == "ticket_blank"),
            "rows": planned,
        }
        if args.apply:
            result.update(apply_repairs(conn, planned))
    finally:
        conn.close()

    print(json.dumps(result, indent=2, default=str))
    # A row we could not resolve is NOT success: it still reads open for ever.
    return 1 if (result["no_ticket"] or result["ticket_blank"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
