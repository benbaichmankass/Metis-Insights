#!/usr/bin/env python3
"""One-shot repair for the Jun-2026 netted-position misattribution rows
(BL-20260720-ICTSCALP-PASTSTOP-EXITS + BL-20260720-PAPER-PNL-CROSSWRITE).

Root cause (full record in
docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md): several journal
trades shared one netted Bybit position; each position-level bracket fire
flattened everything but closed only the newest journal row. The phantom-open
siblings were later mis-resolved — some with OTHER trades' closed-pnl records
(byte-identical pnl/exit pairs across rows), some with resolution-time mark
prices days after the real close. Their stored pnl / exit_price are therefore
NOT measurements of those trades.

Repair policy — honest-null with provenance, never fabricate:
  * pnl, pnl_percent -> NULL ("not measured" — the repo-wide contract),
  * exit_price -> NULL,
  * notes.netted_repair = {original values, why, probable_true_exit where the
    forensic reconstruction supports one, backlog id, repaired_at},
  * exit_reason -> 'netted_misattributed' so analytics can filter explicitly.

The script REFUSES to touch a row whose current values don't match the
expected corrupt signature (safe to re-run; safe against an already-repaired
or since-changed DB). Dry-run by default; --apply writes.

Usage:
  python scripts/ops/repair_netted_misattributed_rows.py --db <path>          # dry-run
  python scripts/ops/repair_netted_misattributed_rows.py --db <path> --apply

Tier 2 (money-DB writeback) — run against the LIVE trade_journal.db only
with operator approval. Validate on the trainer's synced copy first.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

BACKLOG_ID = "BL-20260720-ICTSCALP-PASTSTOP-EXITS"

# (trade_id, expected_pnl, expected_exit_price, why, probable_true_exit)
TARGETS = [
    (2453, -2970.986, 63122.9,
     "carries trade 2529's pnl (paper cross-write; own geometry implies ~-258)",
     None),
    (2757, -5.2701, 62402.2,
     "orphan resolved 2026-06-25 with local_markprice days after the real close",
     "position share flattened by a position-level bracket fire on 2026-06-21/22"),
    (2762, -2.3047, 62402.2,
     "orphan resolved 2026-06-25 with local_markprice days after the real close",
     None),
    (2764, -3350.54070614, 64255.9,
     "carries trade 2769's closed-pnl record (closed 2026-06-22 21:23)",
     None),
    (2765, -1.63903789, 62724.0,
     "carries trade 2799's closed-pnl record (closed 2026-06-23 06:21)",
     "share flattened at the 2026-06-22 11:37 TP fire ~64729 (small profit)"),
    (2770, -2.7142, 62402.2,
     "orphan resolved 2026-06-25 with local_markprice days after the real close",
     None),
    (2783, -6.9717, 62402.2,
     "local_compute from an artifact exit price that first printed 2026-06-23 08:00",
     "share flattened at the 2026-06-22 21:23 SL fire ~64250 (~-0.9R)"),
    (2796, -828.84744566, 62725.0,
     "carries trade 2798's closed-pnl record (closed 2026-06-23 06:21)",
     None),
]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to trade_journal.db")
    ap.add_argument("--apply", action="store_true",
                    help="Write the repair (default: dry-run report only)")
    args = ap.parse_args(argv[1:])

    mode = "rw" if args.apply else "ro"
    conn = sqlite3.connect(f"file:{args.db}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()

    repaired = skipped = 0
    for tid, exp_pnl, exp_exit, why, probable in TARGETS:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
        if row is None:
            print(f"[skip] {tid}: row not found")
            skipped += 1
            continue
        cur_pnl = row["pnl"]
        cur_exit = row["exit_price"]
        sig_ok = (
            cur_pnl is not None and abs(float(cur_pnl) - exp_pnl) < 1e-6
            and cur_exit is not None and abs(float(cur_exit) - exp_exit) < 1e-6
        )
        if not sig_ok:
            print(f"[skip] {tid}: current (pnl={cur_pnl}, exit={cur_exit}) does "
                  f"not match expected corrupt signature (pnl={exp_pnl}, "
                  f"exit={exp_exit}) — already repaired or changed; refusing")
            skipped += 1
            continue
        try:
            notes = json.loads(row["notes"] or "{}")
        except Exception:
            notes = {"unparseable_prior_notes": True}
        notes["netted_repair"] = {
            "backlog_id": BACKLOG_ID,
            "repaired_at": now,
            "original_pnl": cur_pnl,
            "original_pnl_percent": row["pnl_percent"],
            "original_exit_price": cur_exit,
            "original_exit_reason": row["exit_reason"],
            "why": why,
            **({"probable_true_exit": probable} if probable else {}),
        }
        print(f"[{'APPLY' if args.apply else 'dry'}] {tid}: pnl {cur_pnl} -> NULL, "
              f"exit {cur_exit} -> NULL ({why})")
        if args.apply:
            conn.execute(
                "UPDATE trades SET pnl=NULL, pnl_percent=NULL, exit_price=NULL, "
                "exit_reason='netted_misattributed', notes=? WHERE id=?",
                (json.dumps(notes), tid),
            )
        repaired += 1
    if args.apply:
        conn.commit()
    conn.close()
    print(f"\n{'applied' if args.apply else 'dry-run'}: "
          f"{repaired} repaired, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
