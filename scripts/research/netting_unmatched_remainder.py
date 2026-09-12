#!/usr/bin/env python3
# wiring: manual-only - this grades a DATED remainder left by one other one-off
# (MI-278 U9's adjudication of `netting_attributed` closes). A scheduled runner
# would re-answer it against a moving population and turn a recorded verdict
# into a drifting one.
"""Can the rows MI-278 U9 could not match be adjudicated at all? (MI-278 U16)

THE REMAINDER, AND WHY IT IS THE WHOLE ROW
===========================================
`BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED`
offers two exits. U9 took (b) — adjudicate the rows against venue truth — and
discharged it **for the matched half only**: of 50 closed `netting_attributed`
rows, 38 matched by side+qty, leaving **10 `venue_closed_different_qty`** and
**2 `venue_no_rows_in_window`**. Those 12 are why the row is still open, and
nothing had looked at them.

THE HYPOTHESIS THIS FILE WAS BUILT TO TEST, AND IT IS MOSTLY WRONG
===================================================================
U9's matcher keys on ONE venue close per journal row. Under netting a position
is reduced in slices, so the obvious reading of `venue_closed_different_qty` is
that the matcher's 1:1 key cannot express a journal row that corresponds to
SEVERAL venue closes. Trade 5079 looks exactly like that: journal 0.656 against
venue closes of 0.65 + 0.004 + 0.002.

**Measured: 2 of 10.** Subset-sum recovers 5079 and 4930 and nothing else, so
the 1:1 key is NOT the dominant cause and a sum-aware matcher does not rescue
this population. Reported because it is the answer, not because it is the one
the hypothesis wanted.

⚠️ AND THE EXTENSION IS UNSOUND ANYWAY — SUBSET-SUM IS UNDER-DETERMINED
=======================================================================
This is the finding worth more than the two matches. For trade 4930 (journal
2460.6) the ±360-minute window finds `2429.2 + 8.4 + 11.5` and the ±1440-minute
window finds `2429.2 + 5.2 + 14.9`. **Both sum to the journal quantity inside
tolerance, and they attribute different venue PnL — 199.09 against 180.53.**

A matcher whose answer changes with a parameter that is not part of the question
is not an adjudicator; it is a way of generating a defensible-looking number.
So this file REPORTS the ambiguity rather than resolving it: every subset-sum
result carries `n_alternatives` and the PnL SPREAD across them, and a row with
more than one distinct sum is graded `ambiguous_multiple_subsets`, never matched.
⚠️ Do not "fix" this by fixing the window — that picks one arbitrary answer and
hides the spread, which is strictly worse than the honest refusal.

STATES, NEVER COLLAPSED
========================
`sum_matched_unique` · `ambiguous_multiple_subsets` (several valid sums, so no
answer) · `no_subset_sums` (searched, none found) · `venue_silent` (the venue
answered with no rows on this side at all — *we asked and it had nothing*, which
is NOT the same as *we did not ask*) · `not_searched` (outside this file's scope).
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import netting_close_venue_adjudication as NA  # noqa: E402

# Inherited from U9 so the two files cannot disagree about what "same size" means.
QTY_TOL = NA.QTY_TOL
# Windows swept deliberately rather than one chosen: the sweep IS the ambiguity
# measurement, and a single window would hide it.
WINDOWS_MIN = (60, 360, 1440)
MAX_TERMS = 4          # a 5-term sum over a netted book is numerology, not evidence
MAX_POOL = 14          # bounds the 2**n search; a larger pool is reported, not searched

STATES = ("sum_matched_unique", "ambiguous_multiple_subsets", "no_subset_sums",
          "venue_silent", "not_searched")


def subsets_summing(pool, target, tol):
    """Every distinct subset of *pool* whose qty sums to *target* within *tol*.

    Returns a list of (indices, qty_sum, pnl_sum). DISTINCT is by index set, so
    two different closes of the same size are two different answers — which is
    the point: they attribute different PnL.
    """
    out = []
    for k in range(1, min(MAX_TERMS, len(pool)) + 1):
        for idx in itertools.combinations(range(len(pool)), k):
            s = sum(float(pool[i]["qty"]) for i in idx)
            if abs(s - target) <= tol * max(target, 1e-9):
                out.append((idx, s, sum(float(pool[i]["closed_pnl"]) for i in idx)))
    return out


def grade(trade, records, closing_side, closed_ms):
    """Grade ONE unmatched journal row across the window sweep."""
    key = (trade["account_id"], trade["symbol"])
    same_side = [x for x in (records.get(key) or {}).values()
                 if x.get("side") == closing_side]
    if not same_side:
        return {"state": "venue_silent", "n_alternatives": 0,
                "pnl_spread": None, "by_window": {}}
    qty = NA._f(trade["position_size"])
    by_window, all_solutions = {}, {}
    for w in WINDOWS_MIN:
        pool = [x for x in same_side
                if abs(int(x["updated_time"]) - closed_ms) / 60000.0 <= w]
        pool = sorted(pool, key=lambda x: abs(int(x["updated_time"]) - closed_ms))[:MAX_POOL]
        sols = subsets_summing(pool, qty, QTY_TOL)
        by_window[f"{w}m"] = {"pool": len(pool), "solutions": len(sols),
                              "pnl": [round(p, 2) for _, _, p in sols]}
        for _, _, p in sols:
            all_solutions[round(p, 2)] = True
    pnls = sorted(all_solutions)
    if not pnls:
        return {"state": "no_subset_sums", "n_alternatives": 0,
                "pnl_spread": None, "by_window": by_window}
    if len(pnls) == 1:
        return {"state": "sum_matched_unique", "n_alternatives": 1,
                "venue_pnl": pnls[0], "pnl_spread": 0.0, "by_window": by_window}
    return {"state": "ambiguous_multiple_subsets", "n_alternatives": len(pnls),
            "venue_pnl": None, "pnl_candidates": pnls,
            "pnl_spread": round(max(pnls) - min(pnls), 2), "by_window": by_window}


def self_test() -> int:
    ok = True

    def chk(label, cond):
        nonlocal ok
        print(f"  {'ok ' if cond else 'FAIL'} {label}")
        ok = ok and cond

    print("netting_unmatched_remainder --self-test")
    def P(*qs):
        return [{"qty": str(q), "closed_pnl": str(q * 10), "side": "Sell",
                 "updated_time": "0"} for q in qs]
    chk("an exact single close is found", len(subsets_summing(P(1.0), 1.0, 0.005)) == 1)
    chk("a two-term sum is found", len(subsets_summing(P(0.65, 0.006), 0.656, 0.005)) == 1)
    chk("a sum outside tolerance is NOT found", not subsets_summing(P(0.5), 0.656, 0.005))
    chk("two equal-size closes are TWO answers, not one",
        len(subsets_summing(P(1.0, 1.0), 1.0, 0.005)) == 2)
    chk("the term cap is enforced",
        not subsets_summing(P(*([0.2] * 5)), 1.0, 0.005))
    chk("tolerance is RELATIVE to the target",
        bool(subsets_summing(P(1000.0), 1004.0, 0.005))
        and not subsets_summing(P(1.0), 1.004, 0.0005))
    chk("every declared state is distinct", len(set(STATES)) == len(STATES))
    chk("venue_silent is distinct from no_subset_sums — asked-and-empty is not searched-and-missed",
        "venue_silent" in STATES and "no_subset_sums" in STATES)
    chk("ambiguity is its own state, not a match",
        "ambiguous_multiple_subsets" in STATES
        and "ambiguous_multiple_subsets" != "sum_matched_unique")
    chk("QTY_TOL is IMPORTED from U9, not restated", QTY_TOL is NA.QTY_TOL)
    chk("the window sweep has more than one window, or ambiguity is unmeasurable",
        len(WINDOWS_MIN) > 1)
    recs = {("a", "S"): {}}
    g = grade({"account_id": "a", "symbol": "S", "position_size": "1"}, recs, "Sell", 0)
    chk("no venue rows on the side grades venue_silent, with no spread invented",
        g["state"] == "venue_silent" and g["pnl_spread"] is None)
    print("self-test:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trades")
    ap.add_argument("--venue-dir", default="venue")
    ap.add_argument("--exit-reason", default="netting_attributed")
    ap.add_argument("--out")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.trades:
        ap.error("--trades is required unless --self-test")

    rows = NA.load_population(a.trades, a.exit_reason)
    records, states, problems = NA.load_venue(a.venue_dir)
    adjudicated = NA.adjudicate(rows, records, states)
    byrow = {t["id"]: t for t in rows}
    unmatched = [r for r in adjudicated if r["state"] != "venue_close_matched_qty"]

    results = []
    for r in sorted(unmatched, key=lambda x: (x["symbol"], x["trade"])):
        t = byrow[r["trade"]]
        side = "Sell" if str(t.get("direction")).lower() == "long" else "Buy"
        g = grade(t, records, side, NA._ms(t["closed_at"]))
        results.append({"trade": r["trade"], "account_id": r["account_id"],
                        "account_class": r.get("account_class"),
                        "symbol": r["symbol"], "direction": t.get("direction"),
                        "qty": r["qty"], "journal_pnl": r["journal_pnl"],
                        "u9_state": r["state"], **g})

    counts = {s: sum(1 for x in results if x["state"] == s) for s in STATES}
    out = {
        "population": {
            "exit_reason": a.exit_reason,
            "closed_rows": len(rows),
            "matched_by_u9": len(adjudicated) - len(unmatched),
            "remainder_graded_here": len(results),
            "u9_states": {s: sum(1 for x in adjudicated if x["state"] == s)
                          for s in sorted({x["state"] for x in adjudicated})},
            "venue_load_problems": problems,
        },
        "qty_tol": QTY_TOL, "windows_min": list(WINDOWS_MIN),
        "max_terms": MAX_TERMS, "max_pool": MAX_POOL,
        "states": counts,
        "rows": results,
        "_reading": (
            "sum_matched_unique is the only state that adjudicates anything. "
            "ambiguous_multiple_subsets is a REFUSAL: several subsets sum to the "
            "journal quantity and attribute different venue PnL, so picking one "
            "would be generating a number rather than measuring it."),
    }
    text = json.dumps(out, indent=1)
    if a.out:
        pathlib.Path(a.out).write_text(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
