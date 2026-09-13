#!/usr/bin/env python3
"""Is a leg's KILL/DEMOTE case built on rows anyone can stand behind?

A kill argument is almost always made from a HEADLINE dollar loss. But this
fleet's PnL is largely not measured: `/api/bot/performance` publishes
`totalPnl` beside `totalPnlMeasured`, and the second one sums only the
MEASURED and ESTIMATED rows (`src/web/api/routers/performance.py:490`) --
fabricated and unverified rows are excluded from it and included in the first.

So a leg can carry a large headline loss whose ADMISSIBLE part is a GAIN. A
kill argued off that headline is a kill argued off rows that are not
measurements. This grades that, per leg, per block, without deciding anything:
it says whether the case is ADMISSIBLE, never whether to kill.

⚠️ IT PROPOSES NO DISPOSITION. Retiring or demoting a leg is Tier-3 and the
operator's. `loss_survives` is not a recommendation to kill; it means the
argument may be made on this evidence.

⚠️ AND IT IS NOT AN EDGE TEST. Admissibility is about PROVENANCE, not about
whether the strategy is good. A leg whose loss survives may still be worth
keeping, and one whose loss inverts may still be worth killing on other
grounds -- offline OOS edge, an unrepaired defect, a routing decision.

Population: whatever window the caller pulled. Every figure it prints carries
its own n, because the whole point is that a number without its denominator is
what produced the stale headline this was written to catch.
"""
from __future__ import annotations

import argparse
import json
import sys

#: Whether the kill case may be argued from this block's rows.
#: Never collapsed -- `no_admissible_rows` is *we could not look*, and folding
#: it into `loss_inverts` would assert the loss is unreal, which is a different
#: and unsupported claim.
VERDICTS = (
    "loss_survives",            # headline < 0 and the admissible part < 0 too
    "loss_inverts",             # headline < 0 but the admissible part is a GAIN
    "gain_inverts",             # headline > 0 but the admissible part is a LOSS
    "agrees_positive",          # both > 0 -- no kill case to argue
    "no_admissible_rows",       # zero MEASURED+ESTIMATED rows: we could not look
    "insufficient_admissible_n",  # some, but too few for a sign
    "absent_from_block",        # the leg has no row here at all: 0 closes
    "flat",                     # headline is exactly 0
)

#: The smallest admissible row count that may carry a SIGN. Chosen, not tuned:
#: below this a single row flips the verdict, and the verdict is the whole
#: output. It deliberately does NOT match the M7 gate's 20 live closes -- that
#: floor is about edge, this one is about whether a sum has a stable sign.
MIN_ADMISSIBLE = 5


def admissible(row: dict) -> tuple[int, int]:
    """(admissible_n, unmeasured_n) for one perStrategy row.

    Admissible == MEASURED + ESTIMATED, matching `totalPnlMeasured`'s own
    definition. `unmeasured` is everything else -- fabricated AND unverified,
    which the per-strategy payload does not split, so they are reported
    together rather than guessed apart.
    """
    n = int(row.get("trades") or 0)
    m = int(row.get("pnlMeasuredCount") or 0)
    e = int(row.get("pnlEstimatedCount") or 0)
    return m + e, max(n - m - e, 0)


def grade(row: dict | None, min_admissible: int = MIN_ADMISSIBLE) -> tuple[str, dict]:
    """Verdict + the terms behind it. `row` is None when the leg is absent."""
    if row is None:
        return "absent_from_block", {}
    adm_n, unmeasured_n = admissible(row)
    headline = row.get("totalPnl")
    adm_pnl = row.get("totalPnlMeasured")
    terms = {
        "trades": int(row.get("trades") or 0),
        "admissible_n": adm_n,
        "unmeasured_n": unmeasured_n,
        "headline_pnl": headline,
        "admissible_pnl": adm_pnl,
        # What the rows we CANNOT stand behind are carrying. None, never 0.0,
        # when either term is missing -- 0.0 would read as "they carry nothing".
        "unmeasured_pnl": (None if headline is None or adm_pnl is None
                           else round(headline - adm_pnl, 4)),
        "pnl_coverage": row.get("pnlCoverage"),
    }
    if adm_n == 0:
        return "no_admissible_rows", terms
    if headline is None or adm_pnl is None:
        return "no_admissible_rows", terms
    if adm_n < min_admissible:
        return "insufficient_admissible_n", terms
    if headline == 0:
        return "flat", terms
    if headline < 0:
        return ("loss_survives" if adm_pnl < 0 else "loss_inverts"), terms
    return ("agrees_positive" if adm_pnl > 0 else "gain_inverts"), terms


def blocks_of(payload: dict) -> dict:
    """Named blocks that carry a perStrategy list. The TOP LEVEL is one of them.

    ⚠️ The top-level block is the real-money book; `demo` and `paper` are the
    same numbers under two names on this payload. Both are reported rather than
    de-duplicated, because which one a reader quotes is exactly the population
    choice this repo keeps getting wrong.
    """
    out = {}
    if isinstance(payload.get("perStrategy"), list):
        out["real_money(top)"] = payload
    for name in ("demo", "paper", "paperPortfolio", "prop"):
        b = payload.get(name)
        if isinstance(b, dict) and isinstance(b.get("perStrategy"), list):
            out[name] = b
    return out


def find(block: dict, leg: str) -> dict | None:
    for r in block.get("perStrategy") or []:
        if r.get("name") == leg:
            return r
    return None


def assess(payload: dict, legs: list[str],
           min_admissible: int = MIN_ADMISSIBLE) -> dict:
    bl = blocks_of(payload)
    # POSITIVE CONTROL: a reader that finds nothing must not read as "no rows".
    control = {name: len(b.get("perStrategy") or []) for name, b in bl.items()}
    rows = {}
    for leg in legs:
        rows[leg] = {name: grade(find(b, leg), min_admissible)
                     for name, b in bl.items()}
    return {"window": payload.get("window"), "since": payload.get("since"),
            "control": control, "legs": rows,
            "min_admissible": min_admissible}


def report(res: dict) -> list[str]:
    out = ["kill-case admissibility — does the loss survive restriction to rows we can stand behind?",
           "  POPULATION: window %s, since %s" % (res.get("window"), res.get("since")),
           "  POSITIVE CONTROL — strategies each block returned: %s" % res["control"],
           "  admissible = MEASURED + ESTIMATED (totalPnlMeasured's own basis); "
           "floor %d admissible rows to carry a sign" % res["min_admissible"],
           "  ⚠️ ADMISSIBLE IS NOT A RECOMMENDATION. This grades the EVIDENCE, "
           "never the disposition — a kill is Tier-3 and the operator's."]
    if not any(n for n in res["control"].values()):
        out.append("  ⚠️ EVERY BLOCK IS EMPTY — this is a READ FAILURE, not a quiet fleet.")
    for leg, blocks in res["legs"].items():
        out.append("  %s" % leg)
        for bname, (v, t) in blocks.items():
            if v == "absent_from_block":
                out.append("      %-18s absent — 0 closes in this window "
                           "(the block returned %d other strategies)"
                           % (bname, res["control"].get(bname, 0)))
                continue
            out.append("      %-18s %-26s n=%d adm=%d unmeasured=%d | headline %s  "
                       "admissible %s  carried-by-unmeasured %s"
                       % (bname, v, t["trades"], t["admissible_n"], t["unmeasured_n"],
                          t["headline_pnl"], t["admissible_pnl"], t["unmeasured_pnl"]))
            if v == "loss_inverts":
                out.append("          ⚠️ THE DEFENSIBLE RECORD IS A GAIN. A kill argued from "
                           "the headline here is argued from rows that are not measurements.")
            if v == "no_admissible_rows":
                out.append("          ⚠️ WE COULD NOT LOOK — zero measured-or-estimated rows. "
                           "This is NOT evidence the loss is unreal, and NOT evidence it is real.")
    return out


# --------------------------------------------------------------------- self-test

def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    def row(**kw):
        base = {"name": "x", "trades": 10, "pnlMeasuredCount": 5,
                "pnlEstimatedCount": 5, "totalPnl": -100.0,
                "totalPnlMeasured": -80.0, "pnlCoverage": 0.5}
        base.update(kw)
        return base

    ok("a loss that stays a loss on admissible rows is loss_survives",
       grade(row())[0] == "loss_survives")
    ok("a loss whose admissible part is a GAIN is loss_inverts, never loss_survives",
       grade(row(totalPnlMeasured=+120.0))[0] == "loss_inverts")
    ok("a GAIN whose admissible part is a loss is its own verdict, not folded into loss_inverts",
       grade(row(totalPnl=+100.0, totalPnlMeasured=-20.0))[0] == "gain_inverts")
    ok("both positive is agrees_positive — there is no kill case to argue",
       grade(row(totalPnl=+100.0, totalPnlMeasured=+80.0))[0] == "agrees_positive")
    ok("zero admissible rows is no_admissible_rows — *we could not look*, NOT loss_inverts",
       grade(row(pnlMeasuredCount=0, pnlEstimatedCount=0,
                 totalPnlMeasured=0.0))[0] == "no_admissible_rows")
    ok("a few admissible rows is insufficient_admissible_n, not a sign",
       grade(row(pnlMeasuredCount=1, pnlEstimatedCount=1))[0] == "insufficient_admissible_n")
    ok("the n floor is checked BEFORE the sign, so a tiny cell reports its real reason",
       grade(row(pnlMeasuredCount=1, pnlEstimatedCount=1,
                 totalPnlMeasured=+120.0))[0] == "insufficient_admissible_n")
    ok("an absent leg is absent_from_block — 0 closes, NEVER a zero-PnL reading",
       grade(None)[0] == "absent_from_block")
    ok("an exactly-flat headline is flat, not a loss",
       grade(row(totalPnl=0.0, totalPnlMeasured=0.0))[0] == "flat")
    ok("a missing totalPnlMeasured cannot be read as 0.0 — it grades no_admissible_rows",
       grade(row(totalPnlMeasured=None))[0] == "no_admissible_rows")
    ok("unmeasured_pnl is None, never 0.0, when a term is missing",
       grade(row(totalPnlMeasured=None))[1]["unmeasured_pnl"] is None)
    ok("unmeasured_pnl is headline minus admissible",
       grade(row())[1]["unmeasured_pnl"] == -20.0)
    ok("unmeasured_n counts the rows that are neither measured nor estimated",
       grade(row(trades=10, pnlMeasuredCount=2,
                 pnlEstimatedCount=3))[1]["unmeasured_n"] == 5)
    ok("a row claiming more measured than trades cannot make unmeasured_n negative",
       grade(row(trades=2, pnlMeasuredCount=5, pnlEstimatedCount=5))[1]["unmeasured_n"] == 0)
    ok("every verdict returned is in the declared vocabulary",
       all(grade(r)[0] in VERDICTS for r in
           (None, row(), row(totalPnlMeasured=+1.0), row(totalPnl=+1.0),
            row(pnlMeasuredCount=0, pnlEstimatedCount=0), row(totalPnl=0.0))))

    # --- block discovery ----------------------------------------------------
    payload = {"window": "30d", "since": "t0",
               "perStrategy": [{"name": "a", "trades": 1}],
               "paper": {"perStrategy": [{"name": "b", "trades": 1}]},
               "nothing": {"no_per_strategy": True}}
    bl = blocks_of(payload)
    ok("the TOP LEVEL counts as a block — it is the real-money book",
       "real_money(top)" in bl)
    ok("a named block with a perStrategy list is discovered", "paper" in bl)
    ok("a dict with no perStrategy is not a block", "nothing" not in bl)

    res = assess(payload, ["a", "b", "zzz"])
    ok("the positive control counts what each block returned",
       res["control"] == {"real_money(top)": 1, "paper": 1})
    ok("a leg absent everywhere grades absent_from_block in every block, not silently dropped",
       all(v == "absent_from_block" for v, _ in res["legs"]["zzz"].values()))

    text = "\n".join(report(res))
    ok("the report states the population", "POPULATION" in text)
    ok("the report prints the positive control, so an empty read cannot pass as a quiet fleet",
       "POSITIVE CONTROL" in text)
    ok("the report says admissible is not a recommendation",
       "NOT A RECOMMENDATION" in text)
    ok("an absent leg renders as absent with the control beside it, never as 0",
       "absent — 0 closes" in text)

    inv = assess({"window": "w", "since": "s",
                  "perStrategy": [row(name="L", totalPnlMeasured=+120.0)]}, ["L"])
    itext = "\n".join(report(inv))
    ok("loss_inverts is called out loudly in the report, in its own words",
       "THE DEFENSIBLE RECORD IS A GAIN" in itext)
    nal = assess({"window": "w", "since": "s",
                  "perStrategy": [row(name="L", pnlMeasuredCount=0,
                                      pnlEstimatedCount=0, totalPnlMeasured=0.0)]}, ["L"])
    ntext = "\n".join(report(nal))
    ok("no_admissible_rows says it is neither evidence for nor against",
       "NOT evidence the loss is unreal" in ntext and "NOT evidence it is real" in ntext)
    empty = assess({"window": "w", "since": "s", "paper": {"perStrategy": []}}, ["L"])
    ok("an all-empty read is called a READ FAILURE rather than rendering as a clean negative",
       "READ FAILURE" in "\n".join(report(empty)))

    for name, good in checks:
        print(("ok    " if good else "FAIL  ") + name)
    bad = sum(1 for _, g in checks if not g)
    print("\nself-test: %d checks, %d passed, %d failed (denominator = %d)"
          % (len(checks), len(checks) - bad, bad, len(checks)))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--performance", help="a /api/bot/performance JSON pull")
    ap.add_argument("--legs", help="comma-separated strategy names")
    ap.add_argument("--min-admissible", type=int, default=MIN_ADMISSIBLE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.performance or not args.legs:
        ap.error("--performance and --legs are required")

    with open(args.performance) as fh:
        payload = json.load(fh)
    legs = [s.strip() for s in args.legs.split(",") if s.strip()]
    res = assess(payload, legs, args.min_admissible)
    print(json.dumps(res, indent=2, default=str) if args.json
          else "\n".join(report(res)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
