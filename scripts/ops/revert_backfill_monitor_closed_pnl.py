"""One-shot REVERT of the backfill_monitor_closed_pnl.py writes.

The 2026-05-18 dispatch of ``backfill-monitor-closed-pnl`` (issue
#1411) wrote 40 rows but the result pattern was suspicious: many
distinct trade rows collapsed to identical ``pnl`` values (e.g. 15
different trades all flipped to ``-0.1710``) and one row swung
from ``+10.48`` to ``-0.17``. Strong signature of
``account_closed_pnl_for_trade`` returning the same Bybit record
for multiple distinct trade rows when the qty filter is too loose
(every ``long BTCUSDT 0.004`` matches the same recent close).

This script reverts those 40 writes using the audit trail the
backfill itself preserved in ``notes``:
  * ``notes.original_pnl`` — restores ``pnl`` to its pre-backfill
    value
  * ``pnl_percent`` recomputed from notional + restored pnl (the
    gross formula the live writer used)
  * ``exit_price`` derived algebraically from the gross PnL
    formula (`exit = entry + pnl/size` for longs, mirror for
    shorts) — this is exactly what the deleted ``_compute_close_pnl``
    + the verdict's exit_price would have produced
  * ``notes`` cleaned: removes ``bybit_closed_pnl``,
    ``original_pnl``, ``backfilled_*``, ``closed_at``, and resets
    ``exit_price_source`` to its pre-backfill state (we drop the
    field entirely since the original close path didn't write it)

Idempotent. The WHERE filter is
``notes LIKE '%backfilled_by": "backfill_monitor_closed_pnl_script%'``,
which the revert removes, so subsequent runs find nothing.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/revert_backfill_monitor_closed_pnl.py            # dry-run
    python3 scripts/ops/revert_backfill_monitor_closed_pnl.py --apply    # write

Follow-up: once these 40 rows are reverted, the dashboard returns
to its pre-backfill state (known-wrong-but-consistent gross PnL).
The matching bug in ``account_closed_pnl_for_trade`` needs a
separate investigation before any future backfill attempt — see
the dispatch issue #1411 for the suspicious-output transcript.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Rows that carry the backfill audit stamp."""
    # Match the backfill's "backfilled_by" key specifically (with
    # the trailing `_by"`) — not the bare script name substring,
    # which would also match our own "reverted_by" stamp and break
    # idempotency.
    cur = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, exit_reason, pnl, pnl_percent,
               is_backtest, strategy_name, account_id, created_at,
               timestamp, notes
        FROM trades
        WHERE notes LIKE '%"backfilled_by": "backfill_monitor_closed_pnl_script"%'
          AND COALESCE(is_backtest, 0) = 0
        ORDER BY id ASC
        """
    )
    return cur.fetchall()


def _decode_notes(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _derive_exit_price_from_gross(
    row: sqlite3.Row, gross_pnl: float,
) -> Optional[float]:
    """Inverse of the deleted ``_compute_close_pnl`` formula.

    long:  pnl = (exit - entry) * size  →  exit = entry + pnl/size
    short: pnl = (entry - exit) * size  →  exit = entry - pnl/size

    Returns None when entry/size are missing or size is zero."""
    try:
        entry = float(row["entry_price"]) if row["entry_price"] else None
        size = float(row["position_size"]) if row["position_size"] else None
    except (TypeError, ValueError):
        return None
    if not entry or not size:
        return None
    direction = str(row["direction"] or "").lower()
    if direction == "long":
        return round(entry + gross_pnl / size, 4)
    if direction == "short":
        return round(entry - gross_pnl / size, 4)
    return None


def _gross_pnl_percent(
    row: sqlite3.Row, gross_pnl: float,
) -> Optional[float]:
    """Match the gross-PnL-percent convention the deleted live
    writer used."""
    try:
        entry = float(row["entry_price"]) if row["entry_price"] else None
        size = float(row["position_size"]) if row["position_size"] else None
    except (TypeError, ValueError):
        return None
    if not entry or not size:
        return None
    notional = entry * size
    if notional == 0:
        return None
    return round((gross_pnl / notional) * 100.0, 4)


def _plan_row(
    row: sqlite3.Row,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Per-row revert plan. Returns ``(updates, skip_reason)``.

    Most common skip causes:
      * notes JSON malformed (truncated to 500 chars by the backfill
        write)
      * ``original_pnl`` key missing from notes (shouldn't happen
        for backfilled rows but defends against partial writes)
    """
    notes = _decode_notes(row["notes"])
    if not notes:
        return None, "notes JSON empty / malformed (possibly truncated)"
    if "backfilled_by" not in notes:
        return None, "no backfilled_by stamp (not a backfill candidate)"
    if "original_pnl" not in notes:
        return None, "no original_pnl in notes — can't revert pnl"

    raw_orig = notes["original_pnl"]
    # `original_pnl` was stored as the raw row['pnl'] which is either
    # a number or NULL (None). NULL pnl pre-backfill means the row
    # was already in the new SSOT state; reverting to NULL is the
    # correct action — the live sweep will refill it on the next tick.
    if raw_orig is None:
        new_pnl: Optional[float] = None
    else:
        try:
            new_pnl = float(raw_orig)
        except (TypeError, ValueError):
            return None, f"original_pnl={raw_orig!r} not a number"

    # Strip the audit stamp + the bybit-truth bookkeeping that the
    # revert undoes. Preserve any pre-existing notes fields the
    # backfill didn't introduce.
    cleaned = {
        k: v for k, v in notes.items()
        if k not in {
            "backfilled_at", "backfilled_by", "backfilled_source",
            "bybit_closed_pnl", "original_pnl",
            "exit_price_source",
            "closed_at",  # was added by the backfill from rec['closed_at']
        }
    }
    # Stamp the revert itself for audit.
    cleaned["reverted_at"] = datetime.now(timezone.utc).isoformat()
    cleaned["reverted_by"] = "revert_backfill_monitor_closed_pnl_script"

    # 2026-05-18: 4000-char cap matches the backfill writers
    # (see incident #1420). 500 was too tight for audit fields.
    updates: Dict[str, Any] = {
        "pnl": new_pnl,
        "notes": json.dumps(cleaned, ensure_ascii=False)[:4000],
    }

    if new_pnl is not None:
        new_exit = _derive_exit_price_from_gross(row, new_pnl)
        if new_exit is not None:
            updates["exit_price"] = new_exit
        pct = _gross_pnl_percent(row, new_pnl)
        if pct is not None:
            updates["pnl_percent"] = pct
    else:
        # Restoring pnl to NULL also restores pnl_percent to NULL
        # and exit_price to its NULL-pending state (the live sweep
        # will write the real fill on the next tick).
        updates["pnl_percent"] = None
        updates["exit_price"] = None

    return updates, None


def _apply_updates(
    conn: sqlite3.Connection, plans: List[Tuple[int, Dict[str, Any]]],
) -> int:
    cur = conn.cursor()
    n = 0
    for trade_id, u in plans:
        sets = ", ".join(f"{k} = ?" for k in u.keys())
        params = list(u.values()) + [trade_id]
        cur.execute(
            f"UPDATE trades SET {sets} "
            "WHERE id = ? "
            "  AND notes LIKE "
            "      '%\"backfilled_by\": \"backfill_monitor_closed_pnl_script\"%'",
            params,
        )
        n += cur.rowcount
    conn.commit()
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the revert (default: dry-run).")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or ./trade_journal.db).")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    conn = _connect(db_path)
    rows = _candidate_rows(conn)
    if not rows:
        print(f"no candidate rows in {db_path} — nothing to revert")
        return 0

    plans: List[Tuple[int, Dict[str, Any]]] = []
    skipped: List[Tuple[int, str]] = []
    for row in rows:
        updates, reason = _plan_row(row)
        if updates is None:
            skipped.append((row["id"], reason or "unknown"))
            continue
        plans.append((row["id"], updates))

    print(f"db: {db_path}")
    print(f"candidates: {len(rows)} | revertable: {len(plans)} | "
          f"skipped: {len(skipped)}")
    print()
    if plans:
        print("would revert:")
        for trade_id, u in plans[:50]:
            row = next(r for r in rows if r["id"] == trade_id)
            current_pnl = row["pnl"]
            target_pnl = u.get("pnl")
            print(f"  id={trade_id} {str(row['direction'] or '?'):>5} "
                  f"{str(row['symbol'] or '?'):<10} "
                  f"acct={row['account_id']!s:<10} "
                  f"backfilled_pnl={current_pnl!s:<10} "
                  f"→ restored_pnl={target_pnl!s}")
        if len(plans) > 50:
            print(f"  ... and {len(plans) - 50} more")
        print()
    if skipped:
        print("skipped:")
        for trade_id, why in skipped:
            print(f"  id={trade_id}: {why}")
        print()

    if not args.apply:
        print("dry-run — pass --apply to write.")
        return 0

    n = _apply_updates(conn, plans)
    print(f"wrote {n} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
