#!/usr/bin/env python3
"""Mark journal rows carrying a DUPLICATED netted broker PnL.

BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS. Operator-approved 2026-08-06.

THE DEFECT. Under one-way netting the broker returns ONE closed-pnl record for
the whole netted position. Three code sites persist such a record onto a trade
row; two prorated by qty share, the third did not (fixed forward in
``order_monitor._prorate_netted_broker_pnl``). The historical rows that path and
its pre-fix predecessors left behind carry the record's FULL magnitude on EVERY
sibling — the same figure on rows whose quantities differ by orders of magnitude
— **and** an ``exit_price_source`` of the broker, so they classify MEASURED and
flow into the fidelity calibration set, every R metric, the ML label builders,
and the ``totalPnlMeasured`` promotion gate.

WHAT THIS DOES, AND WHAT IT REFUSES TO DO. It stamps
``notes.exit_price_source = "netted_duplicate_unattributed"`` (FABRICATED, so
``pnl_is_trustworthy`` refuses the row) and records the original source under
``notes.pre_remediation_exit_price_source``.

It does **NOT** rewrite ``pnl``. There is no defensible per-row value to write:
the broker record's magnitude belongs to the netted position, and splitting it
now — days or months after the close, with no per-row fill to anchor to — would
be the proration assumption dressed as a correction. The honest operation is to
mark the number untrustworthy and leave it visible, exactly as
``provenance.py``'s UNMEASURED_MARKER contract does for an anchorless close.
Deleting or zeroing the rows would be worse still: it would silently change
historical aggregates rather than disqualifying them.

SELECTION. A cluster is ``(account_id, symbol, ROUND(pnl, 2))`` with 2+ closed
non-backtest rows. A cluster is SUSPECT only when its quantities differ by more
than ``--qty-spread`` (default 1.5x). That discriminator matters: without it the
raw cluster count conflates real duplication with **rounding collisions** —
independent small scalps that happen to round to the same cent. Measured
2026-08-06 the raw count called 236/408 real-money rows "clustered"; the spread
filter cut that to 79 rows totalling $45.52, while paper ``bybit_1`` held 31 rows
totalling $24,272.18. Quote the filtered figure or neither.

The spread threshold is ALSO why ``--min-abs-pnl`` exists. At scalp sizes a 1.5x
spread is ordinary (0.002 vs 0.005 BTC is 2.5x), so the low-|pnl| tail of the
suspect set is probably still mostly false positives. Marking a genuinely-correct
row costs real information — it drops out of every measured aggregate — so the
default declines to touch rows below $1.00 rather than over-marking.

DRY-RUN BY DEFAULT. ``--apply`` is required to write. The dry run prints the exact
row list so it can be reviewed before anything mutates the money DB.
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

MARKER = "netted_duplicate_unattributed"
PRE_KEY = "pre_remediation_exit_price_source"


def _decode(raw):
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return dict(val) if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def find_suspect_rows(conn, *, qty_spread: float, min_abs_pnl: float):
    """Rows in a duplicate-pnl cluster whose quantities differ materially.

    Returns ``(rows, stats)``. A cluster with near-identical quantities is a
    rounding collision and is EXCLUDED — see the module docstring; conflating the
    two is what produced a 236/408 figure that was ~2/3 artifact.
    """
    conn.row_factory = sqlite3.Row
    all_rows = conn.execute(
        "SELECT id, account_id, symbol, pnl, position_size, notes, "
        "       ROUND(pnl, 2) AS pnl_r "
        "  FROM trades "
        " WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
        "   AND pnl IS NOT NULL AND ABS(pnl) > 0.01"
    ).fetchall()

    groups = collections.defaultdict(list)
    for r in all_rows:
        groups[(r["account_id"], r["symbol"], r["pnl_r"])].append(r)

    suspect, stats = [], collections.Counter()
    for _key, g in groups.items():
        if len(g) < 2:
            continue
        qtys = [abs(float(r["position_size"] or 0)) for r in g]
        qtys = [q for q in qtys if q > 0]
        if len(qtys) < 2:
            stats["skipped_no_qty"] += len(g)
            continue
        if max(qtys) / min(qtys) <= qty_spread:
            stats["benign_collision"] += len(g)
            continue
        if abs(float(g[0]["pnl"] or 0)) < min_abs_pnl:
            # Below the floor the spread test is not discriminating (see the
            # docstring): a 1.5x spread is ordinary at scalp size, so marking
            # here would likely destroy good rows to catch few bad ones.
            stats["below_min_abs_pnl"] += len(g)
            continue
        stats["suspect"] += len(g)
        suspect.extend(g)
    return suspect, stats


def already_marked(row) -> bool:
    return _decode(row["notes"]).get("exit_price_source") == MARKER


def mark(conn, rows) -> int:
    """Stamp the marker, preserving the original source. Idempotent."""
    n = 0
    for r in rows:
        notes = _decode(r["notes"])
        if notes.get("exit_price_source") == MARKER:
            continue
        # Record the original ONCE — re-running must not overwrite the true
        # original with the marker on a second pass.
        if PRE_KEY not in notes:
            notes[PRE_KEY] = notes.get("exit_price_source")
        notes["exit_price_source"] = MARKER
        notes["remediation"] = "BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS"
        conn.execute(
            "UPDATE trades SET notes=? WHERE id=?",
            (json.dumps(notes)[:2000], int(r["id"])),
        )
        n += 1
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=None,
                   help="trade journal path (default: the canonical resolver)")
    p.add_argument("--qty-spread", type=float, default=1.5,
                   help="max_qty/min_qty above which a cluster is SUSPECT")
    p.add_argument("--min-abs-pnl", type=float, default=1.0,
                   help="skip clusters below this |pnl| — the spread test does "
                        "not discriminate at scalp size")
    p.add_argument("--account", default=None, help="restrict to one account_id")
    p.add_argument("--apply", action="store_true",
                   help="WRITE. Without this the script only reports.")
    a = p.parse_args(argv)

    db = a.db
    if db is None:
        from src.utils.paths import trade_journal_db_path
        db = str(trade_journal_db_path())

    # Read-only unless --apply, so a dry run cannot mutate the money DB even by
    # accident (a bug in the selection would otherwise be a live write).
    uri = f"file:{db}?mode={'rw' if a.apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows, stats = find_suspect_rows(
            conn, qty_spread=a.qty_spread, min_abs_pnl=a.min_abs_pnl,
        )
        if a.account:
            rows = [r for r in rows if r["account_id"] == a.account]

        by_acct = collections.defaultdict(lambda: {"rows": 0, "usd": 0.0})
        for r in rows:
            d = by_acct[r["account_id"]]
            d["rows"] += 1
            d["usd"] += abs(float(r["pnl"] or 0))

        print(f"db={db}")
        print(f"qty_spread={a.qty_spread}  min_abs_pnl={a.min_abs_pnl}  "
              f"account={a.account or 'ALL'}")
        print(f"cluster stats: {dict(stats)}")
        print(f"suspect rows selected: {len(rows)}")
        for acct in sorted(by_acct):
            d = by_acct[acct]
            print(f"  {acct}: {d['rows']} rows, {d['usd']:.2f} USD")
        unmarked = [r for r in rows if not already_marked(r)]
        print(f"already marked: {len(rows) - len(unmarked)}  "
              f"to mark: {len(unmarked)}")
        for r in unmarked:
            print(f"    id={r['id']} {r['account_id']} {r['symbol']} "
                  f"pnl={r['pnl']} qty={r['position_size']}")

        if not a.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to mark.")
            return 0

        n = mark(conn, rows)
        conn.commit()
        print(f"\nAPPLIED: marked {n} row(s) as {MARKER}.")
        print("pnl is deliberately UNCHANGED — see the module docstring.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
