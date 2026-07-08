#!/usr/bin/env python3
r"""One-shot writeback: void-flag paper-account RESET orphan-adoption artifacts.

CONTEXT (incident 2026-07-07, root-caused in the 2026-07-08 session).
The ``alpaca_paper`` paper account was **reset externally** (Alpaca re-seeded
it with a default ETF portfolio the bot never opened). The reverse reconciler
saw those unfamiliar positions on the exchange and **adopted them as bare
``adopted_orphan`` trades** — ``strategy_name='orphan_adopt'``, no order
package. When a seeded position later vanished (the reset settling, or a second
adoption of the same position), the row was "closed" and
``order_monitor._sweep_local_pnl_for_unpriced`` priced it with the **equity
formula** (``local_markprice`` x qty x ``contract_value_usd=1.0``), fabricating
phantom realised PnL that pollutes the paper KPIs. The classic case was a
1360-share SLV short (entry 53.94) adopted **twice** (trades 3265 + 3266), each
"closed" at −693.6 — one physical phantom double-counted as −1387.2.

The live-path fix (PR #5951, ``_reconcile_orphan_exchange_positions``) now
detects a wholesale RESET (>= N positions vanishing on one account in a single
snapshot pass), tags them ``exchange_reset_flat`` and fires ONE consolidated
alert — so no NEW strategy-attributed reset artifacts are produced. This script
cleans up the **historical bare-orphan phantoms** the reset already wrote,
marking them ``reconcile_status='superseded'`` (the canonical void-flag —
excluded from analytics), mirroring ``reconcile_orphan_history.py``'s contract.

WHAT IT TARGETS (precise signature — paper-only, never real money, never a
genuinely-reattached orphan):

    is_demo = 1
    AND setup_type = 'adopted_orphan'
    AND strategy_name = 'orphan_adopt'          -- bare adoption, NOT reattached
    AND order_package_id IS NULL                -- no real decision behind it
    AND status = 'closed'
    AND notes LIKE '%"pnl_source": "local_compute"%'   (the equity-priced marker)
    AND COALESCE(reconcile_status,'') != 'superseded'  (idempotent)
    [AND account_id = <--account, default 'alpaca_paper'>]

The ``strategy_name='orphan_adopt'`` + ``order_package_id IS NULL`` pair is what
distinguishes a bare phantom from a genuinely-reattached adopted orphan: a
reattached orphan (e.g. trade 3250, ``reverse_reconciler_reattached_to_strategy``)
carries its real strategy name (``slv_trend_1h``) AND a real
``order_package_id`` (``pkg-...``) and ``reconcile_status='reconciled'`` — it is
categorically excluded here and must never be touched.

``--ids a,b,c`` further restricts to an explicit allowlist (eyeball the dry-run,
then pin exactly those ids on apply).

SAFETY (same contract as reconcile_orphan_history.py / the options superseder):
  * **Dry-run by default.** ``--apply`` is required to write.
  * On ``--apply`` a timestamped ``cp`` backup of the DB is taken first.
  * Real-money rows are categorically excluded (``is_demo=1`` predicate).
  * Idempotent — a second run skips already-superseded rows (SQL guard).
  * Pure journal hygiene: never opens/closes an exchange position, never deletes
    a row; only flips ``reconcile_status`` + appends ``superseded_*`` to notes.

Usage:
    python3 scripts/ops/supersede_reset_orphan_artifacts.py                 # dry-run
    python3 scripts/ops/supersede_reset_orphan_artifacts.py --apply         # write (backup first)
    python3 scripts/ops/supersede_reset_orphan_artifacts.py --ids 3265,3266 # pin the confirmed rows

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

SUPERSEDED_REASON = "paper_reset_orphan_adoption_artifact_20260707"
DEFAULT_ACCOUNT = "alpaca_paper"

# The equity-priced marker the local-PnL sweep stamps on every fabricated row.
# Matching on the notes JSON keeps the predicate tight: only rows the equity
# formula actually priced are touched.
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
    conn: sqlite3.Connection, *, account_id: Optional[str], ids: Optional[List[int]],
) -> List[sqlite3.Row]:
    sql = (
        """
        SELECT id, symbol, direction, entry_price, exit_price, position_size,
               status, setup_type, strategy_name, order_package_id, pnl,
               account_id, is_demo, closed_at, notes, reconcile_status
        FROM trades
        WHERE COALESCE(is_backtest, 0) = 0
          AND COALESCE(is_demo, 0) = 1
          AND setup_type = 'adopted_orphan'
          AND strategy_name = 'orphan_adopt'
          AND order_package_id IS NULL
          AND status = 'closed'
          AND notes LIKE ?
          AND COALESCE(reconcile_status, '') != 'superseded'
        """
    )
    params: list = [_LOCAL_COMPUTE_MARKER]
    if account_id:
        sql += " AND account_id = ?"
        params.append(account_id)
    if ids:
        placeholders = ",".join("?" for _ in ids)
        sql += f" AND id IN ({placeholders})"
        params.extend(ids)
    sql += " ORDER BY id ASC"
    return conn.execute(sql, params).fetchall()


def _backup_db(db_path: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = f"{db_path}.bak-supersede-reset-orphan-artifacts-{ts}"
    shutil.copy2(db_path, dest)
    return dest


def run(db_path: str, *, apply: bool, account_id: Optional[str],
        ids: Optional[List[int]]) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = _candidate_rows(conn, account_id=account_id, ids=ids)

    print(f"paper-reset orphan-adoption artifact superseder — "
          f"account={account_id or '(all paper)'} db={db_path}")
    if ids:
        print(f"  restricted to ids: {sorted(ids)}")
    print(f"  matched {len(rows)} bare-orphan phantom row(s):")
    total_pnl = 0.0
    for r in rows:
        try:
            total_pnl += float(r["pnl"]) if r["pnl"] is not None else 0.0
        except (TypeError, ValueError):
            pass
        print(f"    id={r['id']:<6} {str(r['symbol']):<6} {str(r['direction']):<5} "
              f"{str(r['strategy_name'] or ''):<14} acct={str(r['account_id'] or ''):<18} "
              f"pnl={r['pnl']} reconcile_status={r['reconcile_status']}")
    print(f"  fabricated PnL these rows currently contribute to paper KPIs: "
          f"{total_pnl:.2f}")

    if not rows:
        print("nothing to do (already clean / idempotent no-op).")
        conn.close()
        return 0

    if not apply:
        print("\ndry-run — pass --apply to write (a DB backup is taken first).")
        conn.close()
        return 0

    backup = _backup_db(db_path)
    print(f"\nbackup: {backup}")
    now = _now_iso()
    for r in rows:
        notes = _decode_notes(r["notes"])
        notes["superseded_at"] = now
        notes["superseded_by"] = "supersede_reset_orphan_artifacts"
        notes["superseded_reason"] = SUPERSEDED_REASON
        conn.execute(
            "UPDATE trades SET reconcile_status = 'superseded', notes = ? "
            "WHERE id = ? AND COALESCE(reconcile_status,'') != 'superseded'",
            (json.dumps(notes, ensure_ascii=False)[:1000], int(r["id"])),
        )
    conn.commit()
    applied = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE reconcile_status='superseded' "
        "AND setup_type='adopted_orphan' "
        "AND notes LIKE ?",
        ('%"superseded_reason": "' + SUPERSEDED_REASON + '"%',),
    ).fetchone()[0]
    print(f"applied: {len(rows)} row(s) flagged reconcile_status='superseded' "
          f"(total reset-artifact rows now superseded by this tool: {applied}).")
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
                        help=f"Restrict to this paper account id (default "
                             f"{DEFAULT_ACCOUNT}); pass '' / 'all' for every "
                             f"paper account.")
    parser.add_argument("--ids", default=None,
                        help="Optional comma-separated trade-id allowlist to "
                             "further restrict the match.")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    account = args.account
    if account is not None and account.strip().lower() in ("", "all"):
        account = None

    ids: Optional[List[int]] = None
    if args.ids:
        try:
            ids = [int(x) for x in args.ids.split(",") if x.strip()]
        except ValueError:
            print(f"error: --ids must be comma-separated integers, got "
                  f"{args.ids!r}", file=sys.stderr)
            return 2

    return run(db_path, apply=args.apply, account_id=account, ids=ids)


if __name__ == "__main__":
    sys.exit(main())
