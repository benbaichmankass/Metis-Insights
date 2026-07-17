#!/usr/bin/env python3
"""Backfill the fixed-model round-trip cost estimate onto historical closed trades.

The live close path stamps ``trades.fee_taker_usd`` + ``cost_source='estimate'``
on every close (``database._record_trade_cost_estimate``, M18 P0a), but that
writer only went live recently — trades that closed *before* it have no cost at
all (as of 2026-07-17: only 86/798 closed real trades carried a cost; 712 were
uncosted, and ``funding_paid_usd`` was universally NULL). A net-R label over the
historical book is therefore missing cost for ~89% of trades, which biases any
ranker trained on that history (MB-20260629-ALLOC-COSTCAP).

This one-shot backfill applies the SAME pure estimator the live writer uses
(``src.runtime.trade_costs.estimate_roundtrip_fee_usd`` over the trade's own
``entry_price``/``position_size``/``contract_value_usd``) to every closed,
non-backtest row that lacks a cost, giving the whole book a consistent modelled
cost. It is:

* **observability-only** — writes only ``fee_taker_usd`` + ``cost_source``; never
  touches ``pnl``, the order path, or any live-trading state;
* **idempotent + non-destructive** — skips any row that already carries a cost
  (``cost_source`` set OR ``fee_taker_usd`` present), so it NEVER overwrites a
  broker-truth value or a prior estimate, and a second run is a no-op;
* **dry-run by default** — prints the plan; ``--apply`` performs the write.

It does NOT populate ``funding_paid_usd`` / ``fee_maker_usd`` — those need the
broker-truth writer (the Slice-B follow-up), not a fixed estimate.

Usage:
  python scripts/ops/backfill_trade_cost_estimates.py            # dry-run
  python scripts/ops/backfill_trade_cost_estimates.py --apply    # write
  python scripts/ops/backfill_trade_cost_estimates.py --db /path/to/trade_journal.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime.local_pnl import contract_value_usd_for  # noqa: E402
from src.runtime.trade_costs import estimate_roundtrip_fee_usd  # noqa: E402
from src.utils.paths import trade_journal_db_path  # noqa: E402


def backfill(db_path: str, *, apply: bool) -> dict[str, int]:
    """Backfill estimate costs on uncosted closed non-backtest trades.

    Returns a summary dict: {candidates, written, skipped_uncomputable}. In
    dry-run mode nothing is written but the same candidates/skips are counted.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, entry_price, position_size, symbol FROM trades "
            "WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
            "AND cost_source IS NULL AND fee_taker_usd IS NULL"
        ).fetchall()
        candidates = len(rows)
        written = 0
        skipped = 0
        for r in rows:
            fee = estimate_roundtrip_fee_usd(
                entry_price=r["entry_price"],
                qty=r["position_size"],
                contract_value_usd=contract_value_usd_for(r["symbol"]),
            )
            if fee is None:
                skipped += 1  # un-derivable (missing entry/qty) — leave NULL
                continue
            if apply:
                conn.execute(
                    "UPDATE trades SET fee_taker_usd = ?, cost_source = ? "
                    "WHERE id = ? AND cost_source IS NULL AND fee_taker_usd IS NULL",
                    (round(float(fee), 8), "estimate", int(r["id"])),
                )
            written += 1
        if apply:
            conn.commit()
    finally:
        conn.close()
    return {"candidates": candidates, "written": written, "skipped_uncomputable": skipped}


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
    print(f"  uncosted closed non-backtest candidates: {summary['candidates']}")
    print(f"  would-write estimate cost: {summary['written']}")
    print(f"  skipped (entry/qty un-derivable, left NULL): {summary['skipped_uncomputable']}")
    if not args.apply and summary["written"]:
        print("  re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
