#!/usr/bin/env python3
r"""One-shot writeback: void-flag the pre-fix options-account orphan-adoption artifacts.

CONTEXT (incident 2026-06-27, root-caused in the 2026-06-28 system-review).
An options-expressing account (``alpaca_options_paper``) holds ``us_option``
legs for its ETF debit-vertical expressions. **Before** the two fixes below,
the reverse reconciler read those legs via ``account_open_positions``, did NOT
recognise the account as options-expressing, and **adopted them as equity
``adopted_orphan`` trades** (recording the underlying spot as ``entry_price``).
``order_monitor._sweep_local_pnl_for_unpriced`` then priced those rows with the
**equity formula** (``local_markprice`` x qty x ``contract_value_usd=1.0``),
fabricating large phantom realised PnL (the −$845 paper figure on 2026-06-27).

Both root causes are now fixed in code (live in the deployed sha since ~20:08
on 2026-06-27):
  * #4858 — ``clients.account_open_positions`` is options-aware (an
    options-expressing account returns ONLY ``us_option`` positions), so the
    reverse reconciler no longer adopts the legs as equity orphans;
  * #4867 — ``_sweep_local_pnl_for_unpriced`` defers options accounts, so the
    equity formula never prices an option row.

So NO new artifacts are produced post-fix. This script only cleans up the
**historical pre-fix rows** that still carry the fabricated PnL and so pollute
the paper KPIs. It marks them ``reconcile_status='superseded'`` (the canonical
void-flag — excluded from analytics), mirroring
``reconcile_orphan_history.py``'s contract.

WHAT IT TARGETS (precise signature — paper-only, never real money):
    is_demo = 1
    AND setup_type = 'adopted_orphan'
    AND account_id = <options-expressing account, default 'alpaca_options_paper'>
    AND status = 'closed'
    AND notes LIKE '%"pnl_source": "local_compute"%'   (the equity-priced marker)
    AND COALESCE(reconcile_status,'') != 'superseded'   (idempotent)
``--ids a,b,c`` further restricts to an explicit allowlist (the operator can
eyeball the dry-run, then pin exactly those ids on apply).

SAFETY (same contract as reconcile_orphan_history.py):
  * **Dry-run by default.** ``--apply`` is required to write.
  * On ``--apply`` a timestamped ``cp`` backup of the DB is taken first.
  * Real-money rows are categorically excluded (``is_demo=1`` predicate); a
    real-money close is never sourced from this equity-adoption path anyway
    (Bybit closes carry ``exit_price_source='bybit_closed_pnl'``).
  * Idempotent — a second run skips already-superseded rows (SQL guard).
  * Pure journal hygiene: never opens/closes an exchange position, never deletes
    a row; only flips ``reconcile_status`` + appends ``superseded_*`` to notes.

Usage:
    python3 scripts/ops/supersede_options_adoption_artifacts.py            # dry-run
    python3 scripts/ops/supersede_options_adoption_artifacts.py --apply    # write (backup first)
    python3 scripts/ops/supersede_options_adoption_artifacts.py --ids 2999,3000,3002,3004,3005,3006

The DB path comes from ``--db`` else ``$TRADE_JOURNAL_DB`` (the action wrapper
passes ``--db "$(runtime_db_path)"`` — the canonical resolver — so the inline
fallback is never used on the VM).
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

SUPERSEDED_REASON = "options_leg_equity_adoption_artifact_prefix_4858_4867"
DEFAULT_ACCOUNT = "alpaca_options_paper"

# The equity-priced marker the pre-fix local-PnL sweep stamped on every
# fabricated row. Matching on the notes JSON keeps the predicate tight: only
# rows the equity formula actually priced are touched.
_LOCAL_COMPUTE_MARKER = '%"pnl_source": "local_compute"%'


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


def _candidate_rows(
    conn: sqlite3.Connection, *, account_id: str, ids: Optional[List[int]],
) -> List[sqlite3.Row]:
    sql = (
        """
        SELECT id, symbol, direction, entry_price, exit_price, position_size,
               status, setup_type, strategy_name, pnl, account_id, is_demo,
               closed_at, notes, reconcile_status
        FROM trades
        WHERE COALESCE(is_backtest, 0) = 0
          AND COALESCE(is_demo, 0) = 1
          AND setup_type = 'adopted_orphan'
          AND account_id = ?
          AND status = 'closed'
          AND notes LIKE ?
          AND COALESCE(reconcile_status, '') != 'superseded'
        """
    )
    params: list = [account_id, _LOCAL_COMPUTE_MARKER]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        sql += f" AND id IN ({placeholders})"
        params.extend(ids)
    sql += " ORDER BY id ASC"
    return conn.execute(sql, params).fetchall()


def _backup_db(db_path: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = f"{db_path}.bak-supersede-options-artifacts-{ts}"
    shutil.copy2(db_path, dest)
    return dest


def run(db_path: str, *, apply: bool, account_id: str,
        ids: Optional[List[int]]) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _candidate_rows(conn, account_id=account_id, ids=ids)
    finally:
        pass

    print(f"options-adoption artifact superseder — account={account_id} "
          f"db={db_path}")
    if ids:
        print(f"  restricted to ids: {sorted(ids)}")
    print(f"  matched {len(rows)} pre-fix phantom row(s):")
    total_pnl = 0.0
    for r in rows:
        try:
            total_pnl += float(r["pnl"]) if r["pnl"] is not None else 0.0
        except (TypeError, ValueError):
            pass
        print(f"    id={r['id']:<6} {str(r['symbol']):<6} {str(r['direction']):<5} "
              f"{str(r['strategy_name'] or ''):<22} pnl={r['pnl']} "
              f"reconcile_status={r['reconcile_status']}")
    print(f"  fabricated PnL these rows currently contribute to paper KPIs: "
          f"{total_pnl:.2f}")

    if not rows:
        print("nothing to do (already clean / idempotent no-op).")
        return 0

    if not apply:
        print("\ndry-run — pass --apply to write (a DB backup is taken first).")
        return 0

    backup = _backup_db(db_path)
    print(f"\nbackup: {backup}")
    now = _now_iso()
    n = 0
    for r in rows:
        notes = _decode_notes(r["notes"])
        notes["superseded_at"] = now
        notes["superseded_by"] = "supersede_options_adoption_artifacts"
        notes["superseded_reason"] = SUPERSEDED_REASON
        conn.execute(
            "UPDATE trades SET reconcile_status = 'superseded', notes = ? "
            "WHERE id = ? AND COALESCE(reconcile_status,'') != 'superseded'",
            (json.dumps(notes, ensure_ascii=False)[:1000], int(r["id"])),
        )
        n += conn.total_changes and 1 or 0
    conn.commit()
    # Re-count to report the true applied total (total_changes is cumulative).
    applied = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE reconcile_status='superseded' "
        "AND setup_type='adopted_orphan' AND account_id=? "
        "AND notes LIKE ?",
        (account_id, '%"superseded_reason": "' + SUPERSEDED_REASON + '"%'),
    ).fetchone()[0]
    print(f"applied: {len(rows)} row(s) flagged reconcile_status='superseded' "
          f"(total artifact rows now superseded by this tool: {applied}).")
    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Write the supersede flags (default: dry-run). "
                             "Takes a timestamped DB backup first.")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or ./trade_journal.db).")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT,
                        help=f"Options-expressing account id (default "
                             f"{DEFAULT_ACCOUNT}).")
    parser.add_argument("--ids", default=None,
                        help="Optional comma-separated trade-id allowlist to "
                             "further restrict the match.")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
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

    return run(db_path, apply=args.apply, account_id=args.account, ids=ids)


if __name__ == "__main__":
    sys.exit(main())
