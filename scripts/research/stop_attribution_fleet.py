#!/usr/bin/env python3
"""How often is a stop-out graded against a stop that a lever had already moved?

MI-275 measured 2 of 7 on the post-e35 population. This asks the same question
of the whole journal tail, through the one owner
(``src/research/stop_attribution.py``) rather than a second copy of the rule.

USAGE — both inputs are read-only diag pulls, so this needs no VM mutation:

    bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=order_packages&limit=1000' > pkgs.json
    bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=trades&limit=1000'         > trades.json
    python3 scripts/research/stop_attribution_fleet.py --packages pkgs.json --trades trades.json

⚠️ STATE THE POPULATION WHEN QUOTING ANY NUMBER THIS PRINTS. ``/api/diag/journal``
serves a TAIL, so the answer is "the newest N rows", never "the fleet lifetime",
and the header line says so with the dates it actually covers. A trade whose
package fell outside the package tail is REPORTED as unlinked rather than
dropped — an analysis over 599 of 1000 rows that prints 599 as its denominator
is the unasserted-denominator failure this repo keeps paying for.

⚠️ IT CLASSIFIES WHICH STOP WAS IN FORCE, NOT WHETHER THE TRADE WAS A STOP-OUT.
The per-``exit_reason`` table exists so a caller can pick its own stop-out
population; ``exit_reason`` alone does not settle it (MI-275 found
``reconciler_filled`` rows that were stop-outs and graded two ``reached_stop``
rows the other way).

Tier-1: research tooling. Reads two JSON files, prints a report. No DB write,
no network, no order path.

# wiring: manual-only - its two inputs are read-only diag pulls a session makes
# by hand, and there is nothing for a scheduled runner to do with the answer:
# the report is read by a person deciding whether a stop-out population is
# admissible evidence about geometry. A cron would print a rate nobody asked
# for. The RULE it implements is enforced where it can be -- by
# src/research/stop_attribution.py being the one owner and being imported by
# scripts/research/stop_width_counterfactual_2026_09_11.py, which IS exercised
# (its --self-test runs, and tests/test_stop_attribution.py pins the join).
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from src.research.stop_attribution import (  # noqa: E402
    classify, declared_stop, format_split, split,
)

#: Exit reasons whose LABEL claims a declared stop ended the trade. Used only to
#: cut one extra table; every reason is reported individually above it, so this
#: list narrows the headline without hiding anything.
DECLARED_STOP_LABELS = ("sl", "sl_cross")


def _load(path: str) -> list[dict]:
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected a JSON list of rows, got {type(data).__name__}")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packages", required=True,
                    help="JSON from /api/diag/journal?table=order_packages")
    ap.add_argument("--trades", required=True,
                    help="JSON from /api/diag/journal?table=trades")
    a = ap.parse_args(argv)

    packages = _load(a.packages)
    trades = _load(a.trades)
    by_id = {p.get("order_package_id"): p for p in packages if p.get("order_package_id")}

    closed = [t for t in trades if str(t.get("status") or "").lower() == "closed"]
    linked = [t for t in closed if t.get("order_package_id") in by_id]
    unlinked = len(closed) - len(linked)

    dates = sorted(p.get("created_at") or "" for p in packages if p.get("created_at"))
    print("POPULATION")
    print(f"  packages read       : {len(packages)}"
          + (f"  ({dates[0][:19]} -> {dates[-1][:19]})" if dates else ""))
    print(f"  trade rows read     : {len(trades)}")
    print(f"  closed trades       : {len(closed)}")
    print(f"  ...linked to a package in the SAME tail: {len(linked)}")
    print(f"  ...NOT linked (package outside the tail): {unlinked}"
          "   <- excluded, and this is why the denominator is not 'the fleet'")
    if not linked:
        print("\nNOTHING GRADEABLE — this is 'we could not look', not 'no stop was amended'.")
        return 1

    records = []
    for t in linked:
        r = classify(by_id[t["order_package_id"]], t)
        r["exit_reason"] = t.get("exit_reason") or "(none)"
        r["strategy"] = t.get("strategy_name") or "(none)"
        records.append(r)

    print()
    print("HEADLINE")
    print(" ", format_split(split(records), "all closed package-linked trades"))

    print()
    print("BY exit_reason")
    print(f"  {'exit_reason':<30}{'n':>5}{'declared':>10}{'tighter':>9}"
          f"{'wider':>7}{'ungrade':>9}{'amended%':>10}")
    groups = collections.defaultdict(list)
    for r in records:
        groups[r["exit_reason"]].append(r)
    for reason, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        s = split(rows)
        af = s["amended_frac"]
        print(f"  {reason:<30}{s['total']:>5}{s['entry_declared']:>10}"
              f"{s['amended_tighter']:>9}{s['amended_wider']:>7}{s['ungradeable']:>9}"
              f"{('n/a' if af is None else f'{af:.1%}'):>10}")

    labelled = [r for r in records if r["exit_reason"] in DECLARED_STOP_LABELS]
    print()
    print(f"EXITS LABELLED BY A DECLARED-STOP FAMILY {DECLARED_STOP_LABELS}")
    print(" ", format_split(split(labelled), "sl + sl_cross"))

    print()
    print("WHY THE OBVIOUS REPAIR DOES NOT WORK")
    trailed = same = 0
    for t in linked:
        pkg = by_id[t["order_package_id"]]
        frozen, source = declared_stop(pkg)
        if source != "exit_plan_stop_entry_frozen" or pkg.get("sl") is None:
            continue
        if abs(float(pkg["sl"]) - frozen) > 1e-9:
            trailed += 1
        else:
            same += 1
    total = trailed + same
    pct = f"{trailed / total:.1%}" if total else "n/a"
    print(f"  order_packages.sl != exit_plan.stop.price on {trailed} of {total} ({pct})")
    print(f"  -- on those {trailed} of {total}, 'read the package instead of the trade'")
    print("     grades a trailed level against itself and reports a zero move;")
    print(f"     on the other {same} it happens to agree, which is why the failure is silent.")

    print()
    print("WHY THE TOLERANCE IS NOT ATR-BASED")
    with_atr = sum(1 for r in records if r["atr"] is not None)
    print(f"  packages carrying an entry-frozen meta.atr > 0: {with_atr} of {len(records)}"
          f" ({with_atr / len(records):.1%})")
    print("  -- an ATR tolerance cannot grade the rest at all.")

    disagree = 0
    comparable = 0
    for r in records:
        if r["moved_frac"] is None or r["atr"] is None:
            continue
        comparable += 1
        if (r["moved_frac"] <= r["tolerance_frac"]) != ((r["moved_abs"] / r["atr"]) <= 0.02):
            disagree += 1
    print(f"  this rule vs MI-275's 0.02-ATR rule: {disagree} disagreement(s)"
          f" over the {comparable} rows where BOTH can be evaluated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
