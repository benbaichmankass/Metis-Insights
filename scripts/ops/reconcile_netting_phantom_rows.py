#!/usr/bin/env python3
"""W1 reconciliation: close the bybit_1 netting phantom-open rows
(BL-20260731-W1-JOURNAL-EXCHANGE-DIVERGENCE-MAP, operator-approved 2026-08-01).

The one-way-netting partial-close class: several journal trades share one
netted Bybit position; position-level exits flattened shares without the
constituent journal rows ever being reduced, leaving rows "open" long after
their exchange share died. Measured same-moment on 2026-08-01T06:40Z
(exchange snapshot vm-diag #8218 vs journal targets trainer-diag #8227):
every pairs-sleeve row matches the exchange EXACTLY, and the four ict_scalp
rows below are precisely the surplus — after closing them, bybit_1's
per-symbol open sums equal the exchange to the last decimal:

    BTCUSDT: journal 0.01 + [4179: 1.543]  vs exchange 0.01
    ETHUSDT: journal 3.16 + [4255: 106.76] vs exchange 3.16
    SOLUSDT: journal 55.4 + [4220: 1844.6, 4243: 936.1] vs exchange 55.4

(The BNBUSDT pairs-row surplus of 3.71 is inside the pairs sleeve's own
rows and is NOT touched here — that goes with the root-cause item. The
ib_paper MGC 99-lot self-resolved via the position-snapshot reconciler.)

Repair policy — honest-null with provenance, never fabricate:
  * status -> 'closed', reconcile_status -> 'superseded',
  * exit_reason -> 'netting_phantom_reconciled',
  * pnl, pnl_percent, exit_price stay NULL (UNMEASURED — the real closes
    happened at unknown moments inside position-level exits; never priced
    from a mark, per the provenance contract),
  * notes.netting_phantom_reconcile = {original values, exchange evidence,
    backlog id, reconciled_at} (existing notes preserved).

The script REFUSES a row whose live values don't match the pinned
signature (id + account + symbol + direction + position_size + status
'open'), so it is idempotent and safe against a since-changed DB.
Dry-run by default; --apply writes.

Usage:
  python scripts/ops/reconcile_netting_phantom_rows.py --db <path>          # dry-run
  python scripts/ops/reconcile_netting_phantom_rows.py --db <path> --apply

Tier 2 (money-DB writeback; paper account, but the rows feed ML datasets
and paper analytics) — operator-approved 2026-08-01 in chat.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

BACKLOG_ID = "BL-20260731-W1-JOURNAL-EXCHANGE-DIVERGENCE-MAP"
EXIT_REASON = "netting_phantom_reconciled"

# (trade_id, account_id, symbol, direction, expected_position_size,
#  exchange_size_at_check, why)
TARGETS = [
    (4179, "bybit_1", "BTCUSDT", "long", 1.543, 0.01,
     "ict_scalp_5m row; pairs row 4112 (0.01) alone matches the exchange"),
    (4255, "bybit_1", "ETHUSDT", "short", 106.76, 3.16,
     "ict_scalp_eth_15m row; pairs rows 4160+4214 (1.02+2.14=3.16) alone "
     "match the exchange"),
    (4220, "bybit_1", "SOLUSDT", "long", 1844.6, 55.4,
     "ict_scalp_sol_15m row; pairs row 4213 (55.4) alone matches the exchange"),
    (4243, "bybit_1", "SOLUSDT", "long", 936.1, 55.4,
     "ict_scalp_sol_5m row; pairs row 4213 (55.4) alone matches the exchange"),
]

EVIDENCE = (
    "same-moment check 2026-08-01T06:40:37Z: exchange snapshot vm-diag #8218 "
    "vs journal targets trainer-diag #8227"
)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to trade_journal.db")
    ap.add_argument("--apply", action="store_true",
                    help="Write the reconcile (default: dry-run report only)")
    args = ap.parse_args(argv[1:])

    mode = "rw" if args.apply else "ro"
    conn = sqlite3.connect(f"file:{args.db}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()

    matched, refused = [], []
    for trade_id, account, symbol, direction, size, exch_size, why in TARGETS:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        if row is None:
            refused.append((trade_id, "row not found"))
            continue
        checks = {
            "account_id": (row["account_id"], account),
            "symbol": (row["symbol"], symbol),
            "direction": (row["direction"], direction),
            "position_size": (row["position_size"], size),
            "status": (row["status"], "open"),
        }
        bad = {k: v for k, v in checks.items() if v[0] != v[1]}
        if bad:
            refused.append((trade_id, f"signature mismatch: {bad}"))
            continue
        matched.append((trade_id, row, why, exch_size))

    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: "
          f"{len(matched)}/{len(TARGETS)} matched, {len(refused)} refused")
    for tid, reason in refused:
        print(f"  REFUSED {tid}: {reason}")
    for tid, row, why, exch_size in matched:
        print(f"  {'closing' if args.apply else 'would close'} {tid} "
              f"{row['account_id']}/{row['symbol']}/{row['direction']} "
              f"size={row['position_size']} ({why})")
        if not args.apply:
            continue
        try:
            notes = json.loads(row["notes"]) if row["notes"] else {}
            if not isinstance(notes, dict):
                notes = {"_original_notes": notes}
        except (json.JSONDecodeError, TypeError):
            notes = {"_original_notes_raw": row["notes"]}
        notes["netting_phantom_reconcile"] = {
            "backlog_id": BACKLOG_ID,
            "reconciled_at": now,
            "evidence": EVIDENCE,
            "why": why,
            "exchange_size_at_check": exch_size,
            "original": {
                "status": row["status"],
                "position_size": row["position_size"],
                "pnl": row["pnl"],
                "exit_price": row["exit_price"],
                "exit_reason": row["exit_reason"],
            },
        }
        conn.execute(
            """UPDATE trades
               SET status = 'closed',
                   reconcile_status = 'superseded',
                   exit_reason = ?,
                   closed_at = ?,
                   notes = ?
               WHERE id = ?""",
            (EXIT_REASON, now, json.dumps(notes), tid),
        )
    if args.apply:
        conn.commit()
        print("committed.")
    conn.close()
    # Refusals are informational on a re-run (already-repaired rows refuse on
    # status!='open'); a FULL refusal with zero matches on the FIRST apply
    # would mean the DB diverged from the pinned evidence — still exit 0,
    # the report above is the deliverable either way.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
