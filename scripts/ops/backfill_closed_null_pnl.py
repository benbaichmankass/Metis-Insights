"""One-shot backfill for trades that closed via the reconciler's
fallback path with ``pnl`` left NULL.

The reconciler's fallback branch (``order_monitor.py:3131-3151``)
gates a position-flat verdict but, when the broker close-pnl lookup
fails, stamps ``status='closed'`` + ``exit_reason='reconciler_filled'``
without computing PnL. Those rows then render as ``$0.00`` closed
trades in the dashboard — indistinguishable from a real breakeven.

The 2026-06-04 reporting-cleanup sprint made the read side honest
(``/api/bot/trades/closed`` emits ``realizedPnl: null`` instead of
coercing to 0; ``/api/bot/performance`` skips ``pnl IS NULL`` from
aggregates). This script applies the same Bybit-closed-pnl recovery
the existing ``backfill_orphan_pnl.py`` does — recover ``exit_price``
+ ``pnl`` from Bybit's ``/v5/position/closed-pnl`` endpoint —
retroactively, for any row in the 7-day Bybit retention window.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/backfill_closed_null_pnl.py            # dry-run
    python3 scripts/ops/backfill_closed_null_pnl.py --apply    # write

What this fixes:
  * ``pnl=NULL`` → recovered ``closed_pnl`` (net of fees, from Bybit)
  * ``exit_price=NULL`` → recovered ``avg_exit_price`` from Bybit
  * ``pnl_percent=NULL`` → recomputed from notional + the recovered pnl
  * ``exit_reason='reconciler_filled'`` → ``'backfill_closed_pnl_recovery'``
    so back-filled rows are distinguishable from native reconciler closes
  * ``notes`` JSON gains the same backfill audit stamps as the orphan
    backfill (``backfilled_at`` / ``backfilled_by`` / ``backfilled_source``
    / ``backfilled_pnl`` / ``backfilled_closed_at`` /
    ``exit_price_source='bybit_closed_pnl_backfill'``).

Safety:
  * Idempotent. WHERE clause filters ``pnl IS NULL``, so once a row
    is backfilled it no longer matches and re-runs are a no-op.
  * Skips rows where ``account_closed_pnl_for_trade`` returns ``None`` —
    typically Bybit's 7-day window expired. The row stays as-is and is
    listed in the dry-run output.
  * Skips rows where the recovered ``avg_exit_price`` is 0 or negative.
  * Backtest rows (``is_backtest=1``) are NOT touched.
  * Each row is its own UPDATE — partial completion is safe and a
    re-run picks up where it left off.

Shares the broker-lookup logic + audit-stamp helpers with
``backfill_orphan_pnl.py`` (``_plan_row``, ``_apply_updates``,
``_warn_if_silent_credential_failure``) so the two stay in lock-step
on recovery semantics.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

# Ensure src/ is on path before importing helpers from the orphan
# script — same prologue the orphan script uses.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config.accounts_loader import load_accounts_dict  # noqa: E402

# Reuse the broker-lookup + audit-stamp helpers from the orphan backfill.
# The recovery semantics (Bybit closed-pnl lookup, notes stamping,
# pnl_percent recomputation, silent-credential-failure warning) are
# identical between the two; only the candidate filter differs.
from backfill_orphan_pnl import (  # noqa: E402
    _apply_updates,
    _connect,
    _plan_row,
    _warn_if_silent_credential_failure,
)


def _candidate_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Rows that this backfill targets.

    Filter: closed + pnl IS NULL + non-backtest. We do NOT require
    ``exit_price IS NULL`` because the reconciler's fallback may have
    stamped one path's exit_price from a stale verdict while still
    leaving pnl NULL; either way the broker lookup is the authoritative
    source for both.
    """
    cur = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, exit_reason, pnl, pnl_percent,
               is_backtest, strategy_name, account_id, created_at,
               timestamp, notes
        FROM trades
        WHERE status = 'closed'
          AND pnl IS NULL
          AND COALESCE(is_backtest, 0) = 0
        ORDER BY id ASC
        """
    )
    return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the backfill (default: dry-run).")
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
        print(f"no candidate rows in {db_path} — nothing to backfill")
        return 0

    cfgs = load_accounts_dict()

    plans: List[Tuple[int, Dict[str, Any]]] = []
    skipped: List[Tuple[int, str]] = []
    for row in rows:
        cfg = cfgs.get(str(row["account_id"])) if row["account_id"] else None
        updates, reason = _plan_row(row, cfg)
        if updates is None:
            skipped.append((row["id"], reason or "unknown"))
            continue
        plans.append((row["id"], updates))

    print(f"db: {db_path}")
    print(f"candidates: {len(rows)} | recoverable: {len(plans)} | "
          f"skipped: {len(skipped)}")
    print()
    if plans:
        print("would update:")
        for trade_id, u in plans[:20]:
            row = next(r for r in rows if r["id"] == trade_id)
            pnl = u.get("pnl")
            exit_price = u.get("exit_price")
            print(f"  id={trade_id} {str(row['direction'] or '?'):>5} "
                  f"{str(row['symbol'] or '?'):<10} "
                  f"acct={row['account_id']!s:<10} "
                  f"size={row['position_size']!s:<8} "
                  f"entry={row['entry_price']!s} "
                  f"→ exit={exit_price:.4f} "
                  f"pnl={pnl:+.4f}")
        if len(plans) > 20:
            print(f"  ... and {len(plans) - 20} more")
        print()
    if skipped:
        print("skipped:")
        for trade_id, why in skipped[:50]:
            print(f"  id={trade_id}: {why}")
        if len(skipped) > 50:
            print(f"  ... and {len(skipped) - 50} more")
        print()

    _warn_if_silent_credential_failure(plans, skipped)

    if not args.apply:
        print("dry-run — pass --apply to write.")
        return 0

    n = _apply_updates(conn, plans)
    print(f"wrote {n} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
