#!/usr/bin/env python3
"""M20 U2 — which mechanism actually ENDED each winning trade, provenance-graded.

# wiring: manual-only — a research session runs this against a journal pull.

U1 (`m20_u1_winner_close_inventory.py`) established what CAN end a winner per
leg. This asks what DID, over the post-2026-08-27 population, and reports the
share that cannot be attributed at all -- which is the headline, not a caveat.

THE LABEL RECOVERY, AND WHY IT NEEDS A SENSITIVITY CURVE RATHER THAN A CONSTANT
------------------------------------------------------------------------------
U1 measured that ``order_monitor._classify_broker_exit`` refuses on 27 of 27
broker-truth ``reconciler_filled`` rows because each fill lands a median
0.55-0.79 bp SHORT of the level its strict inequality (``px <= sl`` / ``px >=
tp``) tests. This module re-grades those rows with a TOLERANCE.

⚠️ **A tolerance is a threshold, and picking the one that maximises recovery is
exactly the move `CLAUDE-RULES-CANONICAL` forbids** ("never lower a
pre-registered bar to manufacture a verdict"). So:

  * the tolerance is expressed in **R** (self-normalising; no venue tick data
    needed, and the tick could NOT be derived here -- computed SL/TP levels
    carry float noise, so a decimal-places probe measures the arithmetic rather
    than the venue grid, and that failure is reported rather than fudged);
  * ``--sensitivity`` prints the recovery AND every downstream verdict across
    the whole tolerance range, so a reader sees which conclusions are stable
    and which are an artifact of the choice;
  * a conclusion that moves across the range is reported as **ungradeable**,
    not resolved at a convenient point.

WHAT THE ATTRIBUTION CLASSES MEAN, AND WHY THEY ARE NOT POOLED
--------------------------------------------------------------
Four outcomes, never collapsed -- the distinction is the finding:

  ``attributed``            a named per-leg mechanism ended it (U1's catalog)
  ``account_level``         an account/venue closer ended it -- real, but not a
                            per-leg mechanism, so it says nothing about levers
  ``unattributable_price``  the exit price is an ESTIMATED anchor
                            (``candle_at_close``), so it cannot be graded
                            against a level at all -- *we could not look*
  ``unattributable_level``  a BROKER-TRUTH fill that sits near no declared
                            level -- a real measurement that the close was not
                            a bracket exit, and a different fact from the above
  ``contaminated``          ``netting_attributed`` -- 100% ESTIMATED and
                            OI-20260908's own population, a LIVE unfixed defect

Pooling ``unattributable_price`` with ``unattributable_level`` would collapse
*"we did not look"* into *"we looked and found nothing"*, which is the class
`CLAUDE-RULES-CANONICAL` § "Collapsed states" exists for.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: exit prices we treat as broker truth. Kept aligned with provenance.py's
#: MEASURED sources; `recorded_exit_price` is deliberately NOT here (it moved
#: MEASURED -> ESTIMATED on 2026-08-24 and was never broker truth).
BROKER_TRUTH = {"exchange_fill", "bybit_closed_pnl", "ib_execution", "exchange"}

#: per-leg mechanisms from U1's catalog (the ones that write an exit_reason)
PER_LEG_REASONS = {"tp", "tp_cross", "sl", "sl_cross", "stale_stop",
                   "giveback_stop", "exit_head", "vwap_cross", "time_decay"}
CONTAMINATED_REASONS = {"netting_attributed"}


def _notes(row: Dict[str, Any]) -> Dict[str, Any]:
    n = row.get("notes")
    if isinstance(n, str):
        try:
            n = json.loads(n)
        # NOT swallowed — an unparseable notes blob yields {}, and every caller then
        # reads exit_price_source as absent, which routes the row to the
        # `unattributable_price` class rather than to a confident label. A narrow
        # except is wrong here: notes is free-form JSON written by several producers,
        # so ValueError, TypeError and AttributeError are all reachable, and a NEW
        # shape must degrade this row rather than abort the attribution.
        except Exception:  # noqa: BLE001  # allow-silent: degrades the row to unattributable, never to a label
            return {}
    return n if isinstance(n, dict) else {}


def _prov_row(row: Dict[str, Any]) -> Dict[str, Any]:
    n = _notes(row)
    d = dict(row)
    d["pnl_source"] = n.get("pnl_source")
    d["exit_price_source"] = n.get("exit_price_source")
    return d


def geometry(row: Dict[str, Any], pkgs: Dict[str, Dict[str, Any]]
             ) -> Optional[Tuple[float, float, float]]:
    """(R at exit, declared tp in R, |entry-sl|) on the ENTRY-FROZEN basis.

    ⚠️ `trades.stop_loss` is the TRAILED stop and MUST NOT be the denominator --
    MI-277 measured one trailed winner moving a mean R from 7.3 to 102.6.
    """
    pkg = pkgs.get(str(row.get("order_package_id")))
    if not pkg:
        return None
    entry, sl, tp = pkg.get("entry"), pkg.get("sl"), pkg.get("tp")
    px, direction = row.get("exit_price"), str(row.get("direction") or "").lower()
    if not all([entry, sl, tp, px]) or direction not in ("long", "short"):
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    r_exit = (px - entry) / risk if direction == "long" else (entry - px) / risk
    r_tp = (tp - entry) / risk if direction == "long" else (entry - tp) / risk
    return r_exit, r_tp, risk


def recovered_label(row: Dict[str, Any], pkgs: Dict[str, Dict[str, Any]],
                    tolerance_r: float) -> Optional[str]:
    """'sl'/'tp' when a BROKER-TRUTH fill sits within tolerance*risk of a level."""
    if row.get("exit_reason") != "reconciler_filled":
        return None
    if _notes(row).get("exit_price_source") not in BROKER_TRUTH:
        return None
    pkg = pkgs.get(str(row.get("order_package_id"))) or {}
    entry, sl, tp, px = pkg.get("entry"), pkg.get("sl"), pkg.get("tp"), row.get("exit_price")
    if not all([entry, sl, tp, px]):
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    d_sl, d_tp = abs(px - sl) / risk, abs(px - tp) / risk
    if min(d_sl, d_tp) > tolerance_r:
        return None
    return "sl" if d_sl < d_tp else "tp"


def classify(row: Dict[str, Any], pkgs: Dict[str, Dict[str, Any]],
             tolerance_r: float) -> Tuple[str, str]:
    """(final_mechanism, attribution_class) — five classes, never pooled."""
    rec = recovered_label(row, pkgs, tolerance_r)
    final = rec or row.get("exit_reason")
    if final in PER_LEG_REASONS:
        return final, "attributed"
    if final in CONTAMINATED_REASONS:
        return final, "contaminated"
    if final == "reconciler_filled":
        if _notes(row).get("exit_price_source") in BROKER_TRUTH:
            return final, "unattributable_level"
        return final, "unattributable_price"
    return final, "account_level"


def population(trades: List[Dict[str, Any]], cut: str) -> List[Dict[str, Any]]:
    """MI-277's population(), restated identically, then date-cut."""
    return [r for r in trades
            if r.get("status") == "closed"
            and not r.get("is_backtest")
            and r.get("pnl") is not None
            and not str(r.get("strategy_name") or "").startswith("pairs_")
            and (r.get("created_at") or "")[:10] >= cut]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--packages", required=True)
    ap.add_argument("--cut", default="2026-08-27", help="created_at >= this date")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="label-recovery tolerance in R (report the sensitivity too)")
    ap.add_argument("--sensitivity", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    from src.runtime import provenance

    trades = json.loads(Path(args.trades).read_text())
    pkgs = {str(r.get("order_package_id")): r
            for r in json.loads(Path(args.packages).read_text())}
    pop = population(trades, args.cut)
    winners = [r for r in pop if r["pnl"] > 0]

    # POSITIVE CONTROL — assert the provenance probe finds every bucket it will
    # later report as absent. A quiet cell is only a negative if the probe works.
    buckets = collections.Counter(provenance.classify_pnl(_prov_row(r))[0] for r in pop)
    print(f"positive control — provenance buckets over the {len(pop)}-row population: {dict(buckets)}")
    if len(buckets) < 2:
        print("  ⚠️ the probe returned a single bucket; treat every provenance split below as UNVERIFIED",
              file=sys.stderr)

    print(f"\nPOPULATION: closed, non-backtest, pnl NOT NULL, pairs excluded, created_at >= {args.cut}")
    print(f"  {len(pop)} closes · {len(winners)} winners · accounts "
          f"{dict(collections.Counter(r.get('account_id') for r in winners))}")

    if args.sensitivity:
        print("\n=== SENSITIVITY — how the attribution moves with the tolerance ===")
        print("  tol(R)   attributed  acct-level  unattr(price)  unattr(level)  contaminated")
        for tol in (0.0, 0.002, 0.005, 0.01, 0.02, 0.05, 0.10, 0.25):
            c = collections.Counter(classify(r, pkgs, tol)[1] for r in winners)
            print(f"  {tol:>6.3f}   {c['attributed']:10d}  {c['account_level']:10d}  "
                  f"{c['unattributable_price']:13d}  {c['unattributable_level']:13d}  "
                  f"{c['contaminated']:12d}")
        print("  ⚠️ read this before quoting any single row: a conclusion that moves across")
        print("     the range is UNGRADEABLE, not resolved at whichever tolerance suits it.")

    rows = [(r,) + classify(r, pkgs, args.tolerance) for r in winners]
    print(f"\n=== ATTRIBUTION at tolerance {args.tolerance}R ===")
    by_class = collections.Counter(c for _, _, c in rows)
    for cls, n in by_class.most_common():
        pnl = sum(r["pnl"] for r, _, c in rows if c == cls)
        print(f"  {n:3d} ({n / len(rows) * 100:4.1f}%)  pnl={pnl:>11,.2f}   {cls}")

    print("\n=== BY FINAL MECHANISM ===")
    for mech, n in collections.Counter(m for _, m, _ in rows).most_common():
        pnl = sum(r["pnl"] for r, m, _ in rows if m == mech)
        print(f"  {n:3d}  {str(mech):26s} pnl={pnl:>11,.2f}")

    # Did it reach its own declared target, and if not, what ended it?
    reached, short = [], []
    for r, mech, cls in rows:
        g = geometry(r, pkgs)
        if not g:
            continue
        r_exit, r_tp, _ = g
        (reached if r_exit >= r_tp * 0.98 else short).append((r, mech, cls, r_exit / r_tp))
    print("\n=== DID THE WINNER REACH ITS OWN DECLARED TARGET? (>= 98% of tp_r) ===")
    print(f"  reached: {len(reached)}   fell short: {len(short)}   (ungradeable geometry: "
          f"{len(rows) - len(reached) - len(short)})")
    print("\n  fell short, by what ended them:")
    for mech, n in collections.Counter(m for _, m, _, _ in short).most_common():
        fr = statistics.median([f for _, m, _, f in short if m == mech])
        pnl = sum(r["pnl"] for r, m, _, _ in short if m == mech)
        print(f"    {n:3d}  {str(mech):26s} median achieved/declared tp_r = {fr:5.3f}  pnl={pnl:>10,.2f}")
    unknown = [x for x in short if x[2] in ("unattributable_price", "unattributable_level", "contaminated")]
    if short:
        print(f"\n  ⚠️ {len(unknown)} of the {len(short)} short-of-target winners ({len(unknown)/len(short)*100:.0f}%) "
              "carry an UNATTRIBUTABLE or CONTAMINATED label —\n     for those, the mechanism that stopped the run "
              "is NOT KNOWN from the journal. That gap is the finding.")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "population": {"cut": args.cut, "closes": len(pop), "winners": len(winners),
                           "trades_source": args.trades, "packages_source": args.packages},
            "tolerance_r": args.tolerance,
            "by_class": dict(by_class),
            "by_mechanism": dict(collections.Counter(m for _, m, _ in rows)),
            "reached_target": len(reached), "fell_short": len(short),
            "short_unknown_mechanism": len(unknown),
            "rows": [{"id": r["id"], "strategy": r.get("strategy_name"),
                      "account": r.get("account_id"), "pnl": r["pnl"],
                      "mechanism": m, "class": c} for r, m, c in rows],
        }, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
