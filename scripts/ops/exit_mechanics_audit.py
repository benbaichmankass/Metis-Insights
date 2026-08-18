#!/usr/bin/env python3
"""Audit whether every OPEN leg is actually reachable by the exit path.

Why this exists
---------------
``src/runtime/order_monitor.py`` drives exits **per ORDER PACKAGE**: the loop
selects ``get_order_packages_by_strategy(strategy, status="open")`` and both
effectuation branches of ``_apply_update`` then resolve ONE trade row from
``open_pkg["linked_trade_id"]``.  A package that fanned out across accounts
(``Coordinator.multi_account_execute``) has N trade rows and one
``linked_trade_id``, so N-1 legs are never modified and never closed by the
monitor — and once the linked leg closes, the PACKAGE flips to ``closed`` and
the loop's ``status="open"`` filter drops the whole package forever.

That condition is invisible on every existing surface: ``/api/bot/positions``
renders the stranded leg as a normal open position, and the package row renders
as a normal closed package.  This script is the missing denominator.

It MEASURES ONLY.  No writes, no order path, no socket — one read-only SQLite
connection (or plain GETs in ``--api`` mode).  It cannot refuse a trade.

The three findings, each with its population stated
---------------------------------------------------
1. ``stranded``  — an OPEN trade whose parent package is CLOSED.  Permanently
   outside the monitor loop; its only remaining exit is its own resting venue
   bracket or the reconciler.
2. ``divergent`` — a multi-leg OPEN package whose sibling ``trades.stop_loss``
   values disagree.  Every disagreement is a modify that reached one leg only.
3. ``at_risk``   — a multi-leg OPEN package whose stops still AGREE.  Not a
   defect today and deliberately counted separately: it is the population that
   becomes ``divergent`` the moment a trail first fires.  Reporting only (1)+(2)
   would understate exposure by exactly this set.

``linked_missing`` is its own state rather than folded into ``stranded``: a
package naming a ``linked_trade_id`` we cannot find is *we could not look*, not
*we looked and the leg is fine*.

Exit status is 0 whether or not findings exist — this is an audit, not a gate.
A non-zero exit is reserved for "the audit could not run", so an unreadable
journal can never be mistaken for a clean book.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from typing import Any, Dict, List, Optional

# --- row loading -----------------------------------------------------------


def _load_sqlite() -> tuple[List[Dict], Dict[str, Dict]]:
    """Read open trades + their packages from the canonical journal.

    Path comes from the single canonical resolver; the ``canonical-db-resolver``
    CI guard forbids an inline env-read or a CWD-relative fallback here.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from src.utils.paths import trade_journal_db_path  # noqa: E402

    path = str(trade_journal_db_path())
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        trades = [
            dict(r)
            for r in conn.execute(
                "SELECT id, account_id, symbol, direction, status, entry_price, "
                "       stop_loss, take_profit_1, position_size, order_package_id, "
                "       strategy_name, created_at "
                "  FROM trades "
                " WHERE status='open' AND COALESCE(is_backtest,0)=0"
            )
        ]
        pkgs = {
            r["order_package_id"]: dict(r)
            for r in conn.execute(
                "SELECT order_package_id, strategy_name, symbol, status, sl, tp, "
                "       linked_trade_id, close_reason, updated_at "
                "  FROM order_packages"
            )
        }
    finally:
        conn.close()
    return trades, pkgs


def _get(base: str, path: str, token: Optional[str]) -> Any:
    req = urllib.request.Request(base.rstrip("/") + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as fh:
        return json.loads(fh.read().decode())


def _load_api(base: str, token: Optional[str]) -> tuple[List[Dict], Dict[str, Dict]]:
    """Read the same two populations over the Data Explorer routes.

    ``filter_state`` is asserted, never assumed: an unknown filter column is
    IGNORED by that route rather than erroring, and the unfiltered ``total``
    then looks exactly like a filter that matched every row.
    """
    t = _get(base, "/api/bot/db/table/trades?filter_col=status&filter_op=eq"
                   "&filter_val=open&limit=500&order_by=id&order_dir=DESC", token)
    # collapsed-state: applied — this reader needs only that the status filter FORMED.
    # `order_state` is deliberately not branched on: the audit reads every returned row
    # and its findings are order-independent, so a dropped `order_by` changes presentation
    # and not one verdict. `filter_state` is the opposite — a dropped filter silently
    # returns the whole table, so it is asserted rather than trusted.
    if t.get("filter_state") != "applied":
        raise SystemExit(
            f"refusing to report: trades filter_state={t.get('filter_state')!r} "
            "— the status filter was DROPPED, so these rows are not the open book"
        )
    trades = [r for r in t.get("rows", []) if not r.get("is_backtest")]
    pkgs: Dict[str, Dict] = {}
    page, limit = 0, 500
    while page < 20:
        p = _get(base, f"/api/bot/db/table/order_packages?limit={limit}"
                       f"&offset={page * limit}&order_by=created_at&order_dir=DESC", token)
        rows = p.get("rows", [])
        for r in rows:
            pkgs.setdefault(r["order_package_id"], r)
        if len(rows) < limit or all(t.get("order_package_id") in pkgs for t in trades):
            break
        page += 1
    return trades, pkgs


# --- analysis --------------------------------------------------------------


def audit(trades: List[Dict], pkgs: Dict[str, Dict]) -> Dict[str, Any]:
    """Delegate the verdict to the ONE assessor the live alert also uses.

    ``src.runtime.package_leg_coverage.assess`` owns what `stranded` /
    `divergent` / `linked_unresolvable` / `managed` mean. This script must not
    re-derive them: an offline report and a live alarm that disagree about a
    package is the failure mode `src/runtime/dead_leg.py` exists to prevent,
    and it is the reason that module is shared rather than duplicated.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from src.runtime.package_leg_coverage import assess, summarize

    verdicts = assess(trades, pkgs)
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "stranded": [], "divergent": [], "managed": [], "linked_unresolvable": [],
    }
    for pkg_id, row in verdicts.items():
        buckets.setdefault(row["verdict"], []).append({**row, "order_package_id": pkg_id})
    # `managed` splits for reporting only — multi-leg-but-agreeing is not a
    # defect today, and IS the population that becomes `divergent` the moment a
    # trail first fires. Reporting only the defects would understate exposure by
    # exactly this set.
    agreeing = [e for e in buckets["managed"] if int(e.get("leg_count") or 0) > 1]
    return {
        "summary": summarize(verdicts),
        "stranded_packages": buckets["stranded"],
        "divergent_sibling_stops": buckets["divergent"],
        "multi_leg_agreeing_now": agreeing,
        "linked_unresolvable": buckets["linked_unresolvable"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", metavar="BASE",
                    help="read over HTTP instead of SQLite, e.g. https://ict-bot.duckdns.org")
    ap.add_argument("--token", default=os.environ.get("DIAG_READ_TOKEN"))
    ap.add_argument("--json", action="store_true", help="emit the raw result")
    args = ap.parse_args()

    try:
        trades, pkgs = (_load_api(args.api, args.token) if args.api else _load_sqlite())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        # Could-not-look must never print as a clean book.
        print(f"exit-mechanics audit COULD NOT RUN: {exc}", file=sys.stderr)
        return 2

    res = audit(trades, pkgs)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0

    sm = res["summary"]
    bv = sm["by_verdict"]
    print("EXIT-MECHANICS AUDIT — is every open leg reachable by the exit path?")
    print(f"  population: {sm['open_legs']} open legs across {sm['packages']} "
          f"packages  |  by verdict: {bv}\n")

    print(f"[1] STRANDED — open legs under a CLOSED package: "
          f"{sm['stranded_legs']} leg(s) in {bv.get('stranded', 0)} package(s)")
    for e in res["stranded_packages"]:
        print(f"    {e['order_package_id']} {e['strategy']} {e['symbol']} "
              f"(closed: {e['close_reason']}, linked={e['linked_trade_id']})")
        for leg in e["legs"]:
            print(f"        trade {leg['trade_id']:<6} {str(leg['account']):<18} "
                  f"qty={leg['qty']} SL={leg['stop_loss']}")

    print(f"\n[2] DIVERGENT — open multi-leg packages whose sibling stops "
          f"disagree: {sm['divergent_packages']}")
    for e in res["divergent_sibling_stops"]:
        print(f"    {e['order_package_id']} {e['strategy']} {e['symbol']} "
              f"pkg_sl={e['package_sl']}")
        for leg in e["legs"]:
            print(f"        trade {leg['trade_id']:<6} {str(leg['account']):<18} "
                  f"SL={leg['stop_loss']}"
                  f"{'   <-- linked/managed' if leg.get('is_linked') else ''}")

    print(f"\n[3] AGREEING NOW — multi-leg open packages not yet diverged: "
          f"{len(res['multi_leg_agreeing_now'])}")
    print("    (not a defect today; this is the set that diverges when a trail "
          "first fires)")
    for e in res["multi_leg_agreeing_now"]:
        print(f"    {e['order_package_id']} {e['strategy']} {e['symbol']} "
              f"legs={[g['account'] for g in e['legs']]}")

    if res["linked_unresolvable"]:
        print(f"\n[!] COULD NOT LOOK — {len(res['linked_unresolvable'])} package(s) "
              f"whose managed leg could not be resolved (NOT graded clean):")
        for e in res["linked_unresolvable"]:
            print(f"    {e['order_package_id']}: {e.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
