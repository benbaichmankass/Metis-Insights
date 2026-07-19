#!/usr/bin/env python3
r"""One-shot writeback: void-flag historical INTENT-REDUCE phantom-PnL rows.

CONTEXT (BL-20260711, root-caused + fixed 2026-07-19; PR #6926).
An ``intent_reduce`` leg is a DELIBERATE partial-close the intent layer stamps
when a new intent nets down an existing position rather than opening a fresh
one. It is **bookkeeping, not an independent trading decision** —
``apply_intent_reduce_partial_close`` leaves its ``pnl`` NULL by design, and the
read path (``src/web/api/_clean_trades.py::exclude_reduce_leg_predicate``)
excludes reduce legs from every analytics surface.

Before PR #6926 the reconciler write-back
(``order_monitor._close_trade_from_order_status``) and the universal
mark-to-market sweep (``_sweep_local_pnl_for_unpriced``) would nonetheless book
a **non-NULL pnl** onto a reduce leg. On a netting account the qty-matched
``closed_pnl`` is the **parent position's** realized close, so it was attributed
onto the bookkeeping leg with an ``entry==exit`` signature — a fabricated
win/loss (the observed ``trend_donchian`` demo rows 2604/2607/2610 at
+$561/+620/+898). PR #6926 stops NEW phantoms at the source (reduce-leg pnl now
stays NULL; the sweep skips reduce legs). This script cleans the **historical
phantoms already in the DB**, marking them ``reconcile_status='superseded'``
(the canonical void-flag — already excluded from analytics), mirroring
``supersede_reset_orphan_artifacts.py``'s contract.

WHAT IT TARGETS (precise signature — a closed reduce leg carrying the
contract-violating non-NULL pnl):

    COALESCE(is_backtest,0) = 0
    AND (setup_type = 'intent_reduce'
         OR COALESCE(notes,'') LIKE '%"intent_reduce": true%')   -- is_reduce_leg
    AND status = 'closed'
    AND pnl IS NOT NULL                                           -- the fabrication
    AND COALESCE(reconcile_status,'') != 'superseded'            -- idempotent

The ``entry==exit`` phantoms (zero price movement yet non-zero pnl) are the
ironclad fabrications; the dry-run reports them as a distinct sub-count, and
splits real-money vs paper so a human eyeballs the real-money rows before any
write. ``--equal-only`` restricts the APPLY to just the ``entry==exit`` rows
(the most conservative scope); ``--ids a,b,c`` pins an explicit allowlist.

This is account-agnostic on purpose: the phantom is a data-correctness issue
wherever a netting account sized into a partial close (real-money ``bybit_2``
included). It is nonetheless pure journal hygiene — it NULLs no real trade's
pnl (the parent close row keeps the realized pnl), opens/closes no exchange
position, deletes no row; it only flips ``reconcile_status`` + appends
``superseded_*`` to notes on the bookkeeping leg.

SAFETY (same contract as supersede_reset_orphan_artifacts.py):
  * **Dry-run by default.** ``--apply`` is required to write.
  * On ``--apply`` a timestamped ``cp`` backup of the DB is taken first.
  * Idempotent — a second run skips already-superseded rows (SQL guard).
  * Never touches a non-reduce-leg row; never touches the parent position.

Usage:
    python3 scripts/ops/supersede_intent_reduce_phantom_pnl.py                 # dry-run (all reduce-leg phantoms)
    python3 scripts/ops/supersede_intent_reduce_phantom_pnl.py --equal-only    # dry-run (entry==exit only)
    python3 scripts/ops/supersede_intent_reduce_phantom_pnl.py --apply         # write (backup first)
    python3 scripts/ops/supersede_intent_reduce_phantom_pnl.py --apply --ids 2604,2607,2610

The DB path comes from ``--db`` else ``$TRADE_JOURNAL_DB`` (the action wrapper
passes ``--db "$(runtime_db_path)"`` — the canonical resolver).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from typing import List, Optional

SUPERSEDED_REASON = "intent_reduce_phantom_pnl_bl20260711"
_EQUAL_EPS = 1e-9


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_notes(blob) -> dict:
    if not blob:
        return {}
    try:
        d = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _is_equal_entry_exit(row: sqlite3.Row) -> bool:
    """True when entry_price == exit_price (the ironclad phantom signature —
    zero price movement yet a non-NULL pnl)."""
    e, x = row["entry_price"], row["exit_price"]
    if e is None or x is None:
        return False
    try:
        return abs(float(x) - float(e)) < _EQUAL_EPS
    except (TypeError, ValueError):
        return False


def _candidate_rows(
    conn: sqlite3.Connection, *, ids: Optional[List[int]],
) -> List[sqlite3.Row]:
    sql = (
        """
        SELECT id, symbol, direction, entry_price, exit_price, position_size,
               status, setup_type, strategy_name, order_package_id, pnl,
               account_id, is_demo, closed_at, notes, reconcile_status
        FROM trades
        WHERE COALESCE(is_backtest, 0) = 0
          AND (setup_type = 'intent_reduce'
               OR COALESCE(notes, '') LIKE '%"intent_reduce": true%')
          AND status = 'closed'
          AND pnl IS NOT NULL
          AND COALESCE(reconcile_status, '') != 'superseded'
        """
    )
    params: list = []
    if ids:
        placeholders = ",".join("?" for _ in ids)
        sql += f" AND id IN ({placeholders})"
        params.extend(ids)
    sql += " ORDER BY id ASC"
    return conn.execute(sql, params).fetchall()


def _backup_db(db_path: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = f"{db_path}.bak-supersede-intent-reduce-phantom-pnl-{ts}"
    shutil.copy2(db_path, dest)
    return dest


def _is_real_money(row: sqlite3.Row) -> bool:
    try:
        return int(row["is_demo"] or 0) == 0
    except (TypeError, ValueError):
        return True  # unknown → treat as real for the loud report


def run(db_path: str, *, apply: bool, equal_only: bool,
        ids: Optional[List[int]]) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = _candidate_rows(conn, ids=ids)

    equal_rows = [r for r in rows if _is_equal_entry_exit(r)]
    nonequal_rows = [r for r in rows if not _is_equal_entry_exit(r)]
    target = equal_rows if equal_only else rows

    print("INTENT-REDUCE phantom-pnl superseder (BL-20260711) — "
          f"db={db_path}")
    if ids:
        print(f"  restricted to ids: {sorted(ids)}")
    print(f"  scope: {'entry==exit rows only' if equal_only else 'all reduce-leg rows carrying non-NULL pnl'}")
    print(f"  matched {len(rows)} reduce-leg row(s) with non-NULL pnl "
          f"({len(equal_rows)} entry==exit [ironclad phantom], "
          f"{len(nonequal_rows)} entry!=exit):")

    real_pnl = paper_pnl = 0.0
    for r in target:
        try:
            p = float(r["pnl"]) if r["pnl"] is not None else 0.0
        except (TypeError, ValueError):
            p = 0.0
        if _is_real_money(r):
            real_pnl += p
        else:
            paper_pnl += p
        tag = "REAL" if _is_real_money(r) else "paper"
        eq = "eq" if _is_equal_entry_exit(r) else "≠ "
        print(f"    id={r['id']:<6} {str(r['symbol']):<7} {str(r['direction']):<5} "
              f"{eq} {tag:<5} acct={str(r['account_id'] or ''):<16} "
              f"entry={r['entry_price']} exit={r['exit_price']} pnl={r['pnl']} "
              f"strat={str(r['strategy_name'] or '')}")
    print(f"  fabricated PnL these target rows carry — REAL-money: {real_pnl:.2f}  "
          f"paper: {paper_pnl:.2f}")

    if not target:
        print("nothing to do (already clean / idempotent no-op).")
        conn.close()
        return 0

    if not apply:
        print("\ndry-run — pass --apply to write (a DB backup is taken first). "
              "Use --equal-only to restrict to the ironclad entry==exit rows.")
        conn.close()
        return 0

    backup = _backup_db(db_path)
    print(f"\nbackup: {backup}")
    now = _now_iso()
    for r in target:
        notes = _decode_notes(r["notes"])
        notes["superseded_at"] = now
        notes["superseded_by"] = "supersede_intent_reduce_phantom_pnl"
        notes["superseded_reason"] = SUPERSEDED_REASON
        notes["phantom_pnl_voided"] = r["pnl"]
        conn.execute(
            "UPDATE trades SET reconcile_status = 'superseded', notes = ? "
            "WHERE id = ? AND COALESCE(reconcile_status,'') != 'superseded'",
            (json.dumps(notes, ensure_ascii=False)[:1000], int(r["id"])),
        )
    conn.commit()
    applied = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE reconcile_status='superseded' "
        "AND notes LIKE ?",
        ('%"superseded_reason": "' + SUPERSEDED_REASON + '"%',),
    ).fetchone()[0]
    print(f"applied: {len(target)} row(s) flagged reconcile_status='superseded' "
          f"(total intent-reduce phantom rows now superseded by this tool: {applied}).")
    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write the supersede flags (default: dry-run). "
                             "Takes a timestamped DB backup first.")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or the canonical resolver).")
    parser.add_argument("--equal-only", action="store_true",
                        help="Restrict to the ironclad entry==exit phantom rows "
                             "(the most conservative scope).")
    parser.add_argument("--ids", default=None,
                        help="Optional comma-separated trade-id allowlist to "
                             "further restrict the match.")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        from src.utils.paths import trade_journal_db_path
        db_path = str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    ids: Optional[List[int]] = None
    if args.ids:
        try:
            ids = [int(x) for x in args.ids.split(",") if x.strip()]
        except ValueError:
            print(f"error: --ids must be comma-separated integers, got "
                  f"{args.ids!r}", file=sys.stderr)
            return 2

    return run(db_path, apply=args.apply, equal_only=args.equal_only, ids=ids)


if __name__ == "__main__":
    sys.exit(main())
