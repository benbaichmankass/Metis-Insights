#!/usr/bin/env python3
"""Backfill the ``trades.broker_order_id`` join key from the ``notes`` JSON blob.

Slice B / B0 (MB-20260629-ALLOC-COSTCAP). The broker's *entry* order id has
always been captured on the trade row — but only inside the ``notes`` JSON
(``notes.trade_id``, written by ``execute._log_trade_to_journal``). The Slice-B
broker-truth cost sweep needs it as a first-class, indexable column
(``trades.broker_order_id``, added by ``database._migrate_add_broker_order_id``)
so its join to the exchange-fills store
(``exchange_fills.order_id`` = Bybit ``orderId``) is EXACT rather than a fuzzy
``(account, symbol, side, qty, time-window)`` heuristic that could double-count
fills across overlapping same-symbol trades.

Forward rows get ``broker_order_id`` at open (the insert now writes it); this
one-shot backfill fills it for the historical book by copying
``json_extract(notes, '$.trade_id')`` into the column wherever it's still NULL.

It is:

* **observability-only** — writes only ``broker_order_id``; never touches
  ``pnl`` / cost columns / the order path / any live-trading state;
* **idempotent + non-destructive** — only fills rows where ``broker_order_id``
  IS NULL and ``notes.trade_id`` is a non-empty string, so a re-run is a no-op
  and an already-populated column is never overwritten;
* **dry-run by default** — prints the plan; ``--apply`` performs the write.

It does NOT write any cost. The broker-truth fee/funding sweep that consumes
this key (B2) is a separate follow-up.

Usage:
  python scripts/ops/backfill_broker_order_id.py            # dry-run
  python scripts/ops/backfill_broker_order_id.py --apply    # write
  python scripts/ops/backfill_broker_order_id.py --db /path/to/trade_journal.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.paths import trade_journal_db_path  # noqa: E402


def backfill(db_path: str, *, apply: bool) -> dict[str, int]:
    """Copy ``notes.trade_id`` → ``broker_order_id`` on rows still missing it.

    Returns a summary dict: {candidates, written, skipped_no_id}. In dry-run
    mode nothing is written but the same candidates/skips are counted.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        # Only rows whose column is unset. json_extract raises on a malformed
        # blob, so gate it behind json_valid — a non-JSON/absent notes yields
        # NULL (skipped) instead of erroring the whole sweep.
        rows = conn.execute(
            "SELECT id, "
            "CASE WHEN json_valid(notes) "
            "THEN json_extract(notes, '$.trade_id') ELSE NULL END AS oid "
            "FROM trades WHERE broker_order_id IS NULL"
        ).fetchall()
        candidates = 0
        written = 0
        skipped = 0
        for r in rows:
            oid = r["oid"]
            if oid is None or str(oid).strip() == "":
                skipped += 1  # no recoverable entry orderId in notes
                continue
            candidates += 1
            if apply:
                conn.execute(
                    "UPDATE trades SET broker_order_id = ? "
                    "WHERE id = ? AND broker_order_id IS NULL",
                    (str(oid), int(r["id"])),
                )
            written += 1
        if apply:
            conn.commit()
    finally:
        conn.close()
    return {"candidates": candidates, "written": written, "skipped_no_id": skipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="trade_journal.db path (default: canonical resolver)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = ap.parse_args()

    db_path = args.db or str(trade_journal_db_path())
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    summary = backfill(db_path, apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN (no write)"
    print(f"[{mode}] db={db_path}")
    print(f"  rows missing broker_order_id with a recoverable notes.trade_id: {summary['candidates']}")
    print(f"  would-write broker_order_id: {summary['written']}")
    print(f"  skipped (no notes.trade_id — nothing to copy): {summary['skipped_no_id']}")
    if not args.apply and summary["written"]:
        print("  re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
