#!/usr/bin/env python3
"""Re-attach the audit F-39 stranded package legs. **Tier-2 money-DB writeback.**

WHAT IS WRONG
-------------
``order_monitor`` drives exits per ORDER PACKAGE, selecting
``get_order_packages_by_strategy(strategy, status="open")`` and resolving one
row from ``linked_trade_id``. Three packages were flipped to ``closed`` while
their legs were still open, so the loop's ``status="open"`` filter can never
select them again and those legs are managed by nobody. MEASURED
2026-09-10T01:0xZ, and re-measured against the venue the same minute:

    pkg-32a5162bbbdd4ef6  SPY      closed exchange_flat_reconciled  -> 4347 alpaca_paper
    pkg-785d9a600c894c81  TLT      closed exchange_flat_reconciled  -> 5265 alpaca_paper
    pkg-293021e2e84a48db  XRPUSDT  closed reconciler_filled         -> 5474 bybit_2 (REAL MONEY)
                                                                    -> 5475 bybit_portfolio

⚠️ **ALL FOUR LEGS ARE BACKED BY A REAL VENUE POSITION** — this is not a stale
journal. Measured on ``/api/diag/exchange_positions``: ``bybit_2`` XRPUSDT
Buy 58.5, ``bybit_portfolio`` XRPUSDT Buy 11903.8, ``alpaca_paper`` SPY long
11.0 and TLT short 707.0 — each matching its journal row exactly. Two of the
packages closed with ``exchange_flat_reconciled`` while the exchange holds
precisely that position, so the CLOSURES were wrong, not the trade rows.

WHAT THIS DOES, AND WHAT IT EMPHATICALLY DOES NOT
-------------------------------------------------
It flips ``status`` back to ``open`` and clears the now-contradictory
``close_reason``, preserving both prior values under ``meta.reattach_repair``.
That is the whole change.

⚠️ **IT PLACES, MODIFIES AND CANCELS NOTHING.** No broker call, no order path,
no venue read of its own. The operator's approval on
``WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS``
(``chosen: detector_and_reattach``) is scoped to a JOURNAL/STATE repair in
terms, and the standing prohibition on remediating by cancelling resting legs
(``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``)
applies here without exception.

⚠️ **THE INTENDED CONSEQUENCE IS THAT THE EXIT PATH CAN ACT ON THESE LEGS
AGAIN, INCLUDING REAL-MONEY TRADE 5474.** That is the point of re-attaching --
they are unmanaged today -- but it is a live consequence and is stated rather
than buried: once the package is ``open``, the strategy's ``monitor()`` may
trail, tighten or CLOSE the linked leg on the next exit pass. Re-opening also
restores the BUG-046 strategy-monocle suppression for that (strategy, symbol),
which is the correct state while a position is genuinely open.

⚠️ **RE-ATTACHING FIXES THE PACKAGE, NOT THE SIBLING GAP.** The loop still
manages only ``linked_trade_id``, so 5475 stays unmanaged in fact even once its
package is open again -- that is ``BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG``,
a Tier-3 order-path repair this script does not attempt and must not be read as
having fixed.

WHY VENUE EVIDENCE IS MANDATORY
-------------------------------
``--exchange-positions`` is REQUIRED and the script refuses without it. A leg
that has since closed at the venue must be reconciled as CLOSED, not
re-attached -- re-opening a package over a position that no longer exists would
hand the exit path a phantom. The evidence is passed in as a captured payload
rather than fetched, so this tool never opens a socket.

**An unreadable account is a REFUSAL, never a flat.** ``error`` set, or
``positions`` absent, means *we could not look*; treating that as "no position"
is the collapse that would turn a read failure into a repair decision.

SAFETY
------
Every target carries its expected CURRENT signature (status, close_reason,
linked_trade_id, and the open legs it must still hold). A row that does not
match is REFUSED and named -- so the script is safe to re-run, and safe against
a DB that has moved since this was written. Dry-run by default.

Writes go through the canonical ``Database.update_order_package``, not raw SQL.
``scripts/ops/`` IS allowlisted for raw SQL by ``check_writer_conformance.py``
and the sibling ``repair_*.py`` tools use it, but that allowance exists for
stdlib-only portability and this script has no such need: the canonical helper
bumps ``updated_at`` and encodes ``meta`` in one place, which is the invariant
WC-6 exists to protect. Precedent audited and deliberately not copied.

USAGE
    # dry-run — prints the exact diff and writes nothing
    python3 scripts/ops/reattach_stranded_package_legs.py \\
        --db /data/bot-data/trade_journal.db \\
        --exchange-positions exchange_positions.json

    # apply (Tier-2 — operator approval on the EXACT diff required)
    ... --apply
"""
# wiring: manual-only - a one-shot Tier-2 money-DB writeback the operator runs
# by hand, ONCE, after reviewing the exact dry-run diff. It must never acquire a
# scheduled caller: WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS
# makes the operator's approval conditional on seeing that diff first, so a cron
# or unit firing this would defeat the condition it was approved under. The
# sibling repair_*/backfill_* tools under scripts/ops/ are dispatched the same
# way. Deliberately NOT wired -- this is the answer to the guard, not a dodge.

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

WORK_OBJECT = "WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS"
AUDIT_FINDING = "F-39"

#: Each target names the signature it expects to find. A mismatch REFUSES.
TARGETS = [
    {"order_package_id": "pkg-32a5162bbbdd4ef6",
     "status": "closed", "close_reason": "exchange_flat_reconciled",
     "linked_trade_id": 4347, "account_id": "alpaca_paper", "symbol": "SPY",
     "open_legs": [4347]},
    {"order_package_id": "pkg-785d9a600c894c81",
     "status": "closed", "close_reason": "exchange_flat_reconciled",
     "linked_trade_id": 5265, "account_id": "alpaca_paper", "symbol": "TLT",
     "open_legs": [5265]},
    {"order_package_id": "pkg-293021e2e84a48db",
     "status": "closed", "close_reason": "reconciler_filled",
     "linked_trade_id": 5474, "account_id": "bybit_2", "symbol": "XRPUSDT",
     "open_legs": [5474, 5475]},
]


def _norm(sym: str) -> str:
    return "".join(c for c in str(sym or "").upper() if c.isalnum())


def _venue_index(payload) -> tuple:
    """``({(account, symbol): size}, {accounts we could NOT read})``.

    The unreadable set is returned SEPARATELY and never merged into the sizes
    map, so a caller can never mistake *we could not look* for *flat*.
    """
    sizes = {}
    blind = set()
    for a in (payload or {}).get("accounts", []) or []:
        aid = str(a.get("account_id") or "")
        positions = a.get("positions")
        if a.get("error") or positions is None:
            blind.add(aid)
            continue
        for p in positions:
            sizes[(aid, _norm(p.get("symbol")))] = abs(float(p.get("size") or 0.0))
    return sizes, blind


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="path to trade_journal.db")
    ap.add_argument("--exchange-positions", required=True,
                    help="captured /api/diag/exchange_positions JSON — REQUIRED; "
                         "a leg flat at the venue is reconciled as closed, "
                         "never re-attached")
    ap.add_argument("--apply", action="store_true",
                    help="write (default: dry-run)")
    a = ap.parse_args(argv)

    venue_payload = json.loads(Path(a.exchange_positions).read_text())
    sizes, blind = _venue_index(venue_payload)

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        planned, refused = [], []
        for t in TARGETS:
            pid = t["order_package_id"]
            row = conn.execute(
                "SELECT order_package_id, status, close_reason, linked_trade_id, "
                "       strategy_name, symbol, meta "
                "  FROM order_packages WHERE order_package_id = ?", (pid,)
            ).fetchone()
            if row is None:
                refused.append(f"{pid}: no such package row")
                continue
            if row["status"] != t["status"]:
                refused.append(f"{pid}: status is {row['status']!r}, expected "
                               f"{t['status']!r} — already repaired or moved")
                continue
            if row["close_reason"] != t["close_reason"]:
                refused.append(f"{pid}: close_reason is {row['close_reason']!r}, "
                               f"expected {t['close_reason']!r}")
                continue
            if int(row["linked_trade_id"] or -1) != t["linked_trade_id"]:
                refused.append(f"{pid}: linked_trade_id is "
                               f"{row['linked_trade_id']!r}, expected "
                               f"{t['linked_trade_id']}")
                continue

            legs = conn.execute(
                "SELECT id, status, position_size, account_id, symbol FROM trades "
                " WHERE order_package_id = ? AND status='open' "
                "   AND COALESCE(is_backtest,0)=0", (pid,)
            ).fetchall()
            leg_ids = sorted(int(r["id"]) for r in legs)
            if leg_ids != sorted(t["open_legs"]):
                refused.append(f"{pid}: open legs are {leg_ids}, expected "
                               f"{sorted(t['open_legs'])} — the book moved")
                continue

            # VENUE EVIDENCE, per leg. Blind is a refusal, never a flat.
            bad = None
            for leg in legs:
                acct, sym = str(leg["account_id"]), _norm(leg["symbol"])
                if acct in blind:
                    bad = (f"{pid}: {acct} could NOT be read on the venue "
                           f"payload — refusing (this is not evidence of flat)")
                    break
                size = sizes.get((acct, sym))
                if size is None or size <= 0:
                    bad = (f"{pid}: trade {leg['id']} ({acct}/{sym}) is FLAT at "
                           f"the venue — it must be reconciled as CLOSED, not "
                           f"re-attached")
                    break
                declared = abs(float(leg["position_size"] or 0.0))
                if abs(size - declared) > max(1e-9, 1e-4 * declared):
                    bad = (f"{pid}: trade {leg['id']} ({acct}/{sym}) declares "
                           f"{declared:g} but the venue holds {size:g}")
                    break
            if bad:
                refused.append(bad)
                continue

            planned.append((t, row, leg_ids))
    finally:
        conn.close()

    print(f"=== reattach stranded package legs · audit {AUDIT_FINDING} ===")
    print(f"db={a.db}  mode={'APPLY' if a.apply else 'DRY-RUN'}")
    print(f"venue payload captured_at={venue_payload.get('captured_at')} "
          f"· accounts unreadable: {sorted(blind) or 'none'}\n")

    for t, row, leg_ids in planned:
        pid = t["order_package_id"]
        print(f"UPDATE order_packages WHERE order_package_id = {pid!r}")
        print(f"    status       : {row['status']!r} -> 'open'")
        print(f"    close_reason : {row['close_reason']!r} -> None")
        print("    meta         : + reattach_repair{...} (prior values kept)")
        print(f"    -> re-attaches open leg(s) {leg_ids} "
              f"[{t['account_id']}/{t['symbol']}]"
              + ("   ** REAL MONEY **" if t["account_id"] == "bybit_2" else ""))
        print()
    if refused:
        print("REFUSED (nothing written for these):")
        for r in refused:
            print(f"  !! {r}")
        print()
    print(f"{len(planned)} package(s) would be updated, {len(refused)} refused.")

    if not a.apply:
        print("\nDRY-RUN — nothing was written. Re-run with --apply "
              "ONLY with operator approval on this exact diff (Tier-2).")
        return 0
    if not planned:
        print("\nnothing to apply.")
        return 0

    sys.path.insert(0, str(REPO))
    from src.units.db.database import Database  # noqa: E402

    db = Database(a.db)
    stamp = datetime.now(timezone.utc).isoformat()
    for t, row, leg_ids in planned:
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
            if not isinstance(meta, dict):
                meta = {"_prior_meta": row["meta"]}
        except (TypeError, ValueError):
            meta = {"_prior_meta": row["meta"]}
        meta["reattach_repair"] = {
            "previous_status": row["status"],
            "previous_close_reason": row["close_reason"],
            "reattached_open_legs": leg_ids,
            "why": "package was closed while these legs were open, so "
                   "order_monitor's status='open' filter could never select "
                   "it again; every leg verified backed by a live venue "
                   "position before the write",
            "audit_finding": AUDIT_FINDING,
            "work_object": WORK_OBJECT,
            "venue_evidence_captured_at": venue_payload.get("captured_at"),
            "repaired_at": stamp,
        }
        n = db.update_order_package(
            t["order_package_id"],
            {"status": "open", "close_reason": None, "meta": meta},
        )
        print(f"applied {t['order_package_id']}: {n} row(s)")
    print("\nAPPLIED. Verify by re-reading /api/bot/positions AND the journal — "
          "never from this script's own output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
