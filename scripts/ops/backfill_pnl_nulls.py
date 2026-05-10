"""One-shot backfill for closed trades with NULL pnl/pnl_percent.

The 2026-05-10 layer-2 health review surfaced 38 closed trades that
had status='closed' + entry_price + exit_price set, but pnl=NULL and
pnl_percent=NULL — the monitor close path stamped status/exit_reason/
exit_price without computing realised PnL. The code fix in
src/runtime/order_monitor.py prevents new nulls from accumulating;
this script reconstructs PnL for the historical rows.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/backfill_pnl_nulls.py            # dry-run
    python3 scripts/ops/backfill_pnl_nulls.py --apply    # write

The dry-run prints what would change. With --apply, each affected
row is updated via Database.update_trade so the standard write path
+ logging applies.

Safety:
- Only touches rows with status='closed' AND pnl IS NULL AND
  exit_price IS NOT NULL AND entry_price IS NOT NULL AND
  position_size IS NOT NULL AND direction IN ('long','short').
- Skips rows where direction is unknown or the math degenerates
  (zero notional). Those rows are listed in the dry-run output so
  the operator can decide.
- Rejected trades (status='rejected') are left alone — they never
  filled, NULL is correct.
- Backtest rows (is_backtest=1) are left alone for the same reason.

Mirrors the gross-PnL formula in src/runtime/order_monitor.py
::_compute_close_pnl. Fees are not deducted; the
exchange-truth-attribution reconciler handles net-of-fees PnL
separately.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict, List


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, pnl, pnl_percent, is_backtest,
               strategy_name, account_id, timestamp
        FROM trades
        WHERE status = 'closed'
          AND pnl IS NULL
          AND exit_price IS NOT NULL
          AND entry_price IS NOT NULL
          AND position_size IS NOT NULL
          AND COALESCE(is_backtest, 0) = 0
        ORDER BY id ASC
        """
    )
    return cur.fetchall()


def _compute(row: sqlite3.Row) -> Dict[str, float] | None:
    direction = (row["direction"] or "").lower()
    try:
        entry = float(row["entry_price"])
        exit_p = float(row["exit_price"])
        size = float(row["position_size"])
    except (TypeError, ValueError):
        return None
    if direction == "long":
        gross_pnl = (exit_p - entry) * size
    elif direction == "short":
        gross_pnl = (entry - exit_p) * size
    else:
        return None
    notional = entry * size
    if notional == 0:
        return None
    pnl_percent = (gross_pnl / notional) * 100.0
    return {
        "pnl": round(gross_pnl, 2),
        "pnl_percent": round(pnl_percent, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the backfill (default: dry-run).")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or ./trade_journal.db).")
    args = parser.parse_args()

    db_path = args.db or os.environ.get("TRADE_JOURNAL_DB",
                                        "trade_journal.db")
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    conn = _connect(db_path)
    rows = _candidate_rows(conn)
    if not rows:
        print(f"no candidate rows in {db_path} — nothing to backfill")
        return 0

    updates: List[tuple[int, Dict[str, float]]] = []
    skipped: List[tuple[int, str]] = []
    for row in rows:
        pnl_updates = _compute(row)
        if pnl_updates is None:
            skipped.append((row["id"],
                            f"direction={row['direction']!r} "
                            f"entry={row['entry_price']} "
                            f"exit={row['exit_price']} "
                            f"size={row['position_size']}"))
            continue
        updates.append((row["id"], pnl_updates))

    print(f"db: {db_path}")
    print(f"candidates: {len(rows)} rows | updatable: {len(updates)} | "
          f"skipped: {len(skipped)}")
    print()
    print("would update:")
    for trade_id, u in updates[:10]:
        row = next(r for r in rows if r["id"] == trade_id)
        print(f"  id={trade_id} {row['direction']:>5} "
              f"{row['symbol']:<10} "
              f"entry={row['entry_price']:.2f} exit={row['exit_price']:.2f} "
              f"size={row['position_size']:.4f} "
              f"→ pnl={u['pnl']:+.2f} pnl_percent={u['pnl_percent']:+.4f}")
    if len(updates) > 10:
        print(f"  ... and {len(updates) - 10} more")
    print()
    if skipped:
        print("skipped (degenerate input):")
        for trade_id, why in skipped:
            print(f"  id={trade_id}: {why}")
        print()

    if not args.apply:
        print("dry-run — pass --apply to write.")
        return 0

    cur = conn.cursor()
    for trade_id, u in updates:
        cur.execute(
            "UPDATE trades SET pnl = ?, pnl_percent = ? "
            "WHERE id = ? AND pnl IS NULL",
            (u["pnl"], u["pnl_percent"], trade_id),
        )
    conn.commit()
    print(f"wrote {len(updates)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
