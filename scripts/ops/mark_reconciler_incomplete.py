"""One-shot DB update: mark closed+NULL-pnl rows as
``exit_reason='reconciler_incomplete'`` so the dashboard surfaces them
as **"PnL unknown"** instead of an ambiguous ``reconciler_filled`` /
``$0`` close.

Background — the reconciler fallback path
(``order_monitor.py:3131-3151``) gates a position-flat verdict but,
when the broker close-pnl lookup fails, stamps ``status='closed'`` +
``exit_reason='reconciler_filled'`` without computing PnL. These rows
look identical on the wire to a real closed-at-breakeven trade.

The 2026-06-04 reporting-cleanup sprint:

  * #2759 made the read side honest (``realizedPnl: null`` on the
    wire, ``/performance`` excludes ``pnl IS NULL`` from aggregates).
  * #2774 added the Bybit-closed-pnl backfill so historical rows can
    recover real numbers where Bybit still has the record. For demo
    accounts that run in merge-mode position mode, however, Bybit's
    closed-pnl records don't map 1:1 to the bot's trade rows (the
    matcher rejects them on entry-price + side mismatches even when
    creds work), so a large fraction of historic rows are
    unrecoverable.

This script is the "be honest" pass: any row that is still
``status='closed' AND pnl IS NULL`` after the backfill attempt gets
re-stamped ``exit_reason='reconciler_incomplete'`` so it is clearly
distinguishable from a native close. The matching wire-side guard
(``realizedPnl: null``) already keeps these rows out of the
aggregates; this script just makes the trade-row label honest too.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/mark_reconciler_incomplete.py            # dry-run
    python3 scripts/ops/mark_reconciler_incomplete.py --apply    # write

Safety:
  * Filters: ``status='closed' AND pnl IS NULL AND
    COALESCE(is_backtest,0)=0 AND exit_reason='reconciler_filled'``.
  * Idempotent — once a row's ``exit_reason`` is
    ``reconciler_incomplete`` it no longer matches. Re-runs are
    no-ops.
  * Skips backtest rows.
  * No notes mutation, no PnL writes, no other column changes —
    this script ONLY rewrites ``exit_reason``.

Single UPDATE in one transaction; partial failure rolls back.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def _candidate_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM trades
        WHERE status = 'closed'
          AND pnl IS NULL
          AND exit_reason = 'reconciler_filled'
          AND COALESCE(is_backtest, 0) = 0
        """
    )
    return int(cur.fetchone()[0])


def _list_candidates(conn: sqlite3.Connection, limit: int = 20):
    cur = conn.execute(
        """
        SELECT id, account_id, symbol, direction, strategy_name, timestamp
        FROM trades
        WHERE status = 'closed'
          AND pnl IS NULL
          AND exit_reason = 'reconciler_filled'
          AND COALESCE(is_backtest, 0) = 0
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the update (default: dry-run).")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or ./trade_journal.db).")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    try:
        pre = _candidate_count(conn)
        print(f"db: {db_path}")
        print(f"candidates: {pre}")
        if pre == 0:
            print("nothing to mark — exiting clean.")
            return 0

        print()
        print("sample (first 20):")
        for row in _list_candidates(conn, limit=20):
            print(f"  id={row[0]:>5} acct={row[1]:<10} sym={row[2]:<10} "
                  f"dir={row[3] or '?':<5} strat={row[4] or '?'} ts={row[5]}")
        if pre > 20:
            print(f"  ... and {pre - 20} more")
        print()

        if not args.apply:
            print("dry-run — pass --apply to write.")
            return 0

        conn.execute(
            """
            UPDATE trades
               SET exit_reason = 'reconciler_incomplete'
             WHERE status = 'closed'
               AND pnl IS NULL
               AND exit_reason = 'reconciler_filled'
               AND COALESCE(is_backtest, 0) = 0
            """
        )
        conn.commit()
        post = _candidate_count(conn)
        wrote = pre - post
        print(f"wrote {wrote} row(s). (pre={pre} → post={post})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
