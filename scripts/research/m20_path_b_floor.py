#!/usr/bin/env python3
"""Does a leg's own base rate predict whether an exit-lever gain GENERALISES?

THE QUESTION, AND WHY IT IS NOT A GUESS. Path A admits a cell that deepens
drawdown only if it does not worsen the book's net_R-per-drawdown rate
(`N_c/D_c >= N_b/D_b`), which makes the drawdown allowance **derived per leg**
rather than a fleet scalar — `allowed = D_b x (dN / N_b)`. That removed the free
parameter, and introduced a known asymmetry: **the weaker the base book, the more
permissive the criterion.** Measured 2026-08-10, `eth_pullback_2h vt_cold10_t2.5`
sat on an IS base of 6.62R over a 16.41R drawdown — rate **0.40** — and cleared
with **+43.59R of headroom**. Almost anything clears a book that inefficient.

The obvious patch is a FLOOR on the base rate. That would re-introduce exactly the
free parameter the derivation removed, so the operator's standing directive
applies (2026-08-10): *"let's try and use any optimization of the capital
utilization and PnL to decide what the correct number is ... database decisions
and not arbitrary guesses."*

So this script does not propose a floor. It asks whether the data supports one:
**among cells that were walk-forwarded, does the base rate separate the ones whose
gain held up across folds from the ones whose did not?** If it does not, the
honest answer is that a floor is unsupported — and the criterion should keep its
asymmetry, documented, rather than gain a number with nothing behind it.

THREE OUTCOMES, NEVER COLLAPSED (docs/CLAUDE-RULES-CANONICAL.md, "Collapsed
states"). "We could not look" and "we looked and found nothing" are opposite
findings, and a floor recommendation is worthless if they share a bucket:

  * ``insufficient_population`` — no floor in the grid splits the corpus into two
    arms that are both large enough to compare. **We did not look.**
  * ``no_separation``          — arms compared; the rate does not predict. **We
    looked and found nothing.**
  * ``separation``             — it predicts, with the floor, the effect and its p.

THE SELECTION DENOMINATOR IS PART OF THE ANSWER. Scanning K floors and reporting
the best p-value is selection over an unstated denominator — the same defect this
repo keeps finding one level down (a winning cell reported without the count of
cells it beat). So K is always printed, the raw p is always printed beside a
Bonferroni-adjusted threshold, and a `separation` verdict requires clearing the
ADJUSTED bar. ALPHA IS A STATED CONVENTION, NOT A MEASUREMENT — it is the one
number here that is chosen rather than derived, and it is surfaced as such.

Usage:
    python3 scripts/research/m20_path_b_floor.py \
        --corpus docs/research/m20-sweep-corpus.jsonl
    python3 scripts/research/m20_path_b_floor.py --axis d_cap_day_OOS  # 2nd threshold
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

CORPUS_DEFAULT = "docs/research/m20-sweep-corpus.jsonl"
# The sweep's own promotion rule, restated in ONE place so a drift in either
# cannot go unnoticed: >= 4 usable folds and >= 2/3 of them won.
WF_MIN_USABLE = 4
WF_WIN_NUM, WF_WIN_DEN = 2, 3
# Minimum rows per arm for a comparison to be attempted at all. Below this a
# proportion is not an estimate — a 2/2 arm reads as 100% and means nothing.
MIN_ARM = 5
# MINIMUM DISTINCT LEGS PER ARM — the one that actually binds here.
#
# The predictor is a property of the LEG, not the cell: every cell swept on
# `eth_pullback_2h` carries that leg's single base rate. So a sweep of 10 legs x
# ~10 cells yields ~100 rows over TEN distinct predictor values, and the cells
# within a leg are anything but independent — they are re-measurements of one
# book under different levers, sharing its trades, its regime and its drawdown.
#
# Feeding those 100 rows to a test that assumes independence inflates the
# effective sample size by ~10x, and Fisher would hand back a tiny p for what is
# really a 10-point comparison. That is the same "unstated denominator" defect
# this file exists to avoid, arriving through the back door as a CLUSTERING
# violation rather than a missing count — and it would be worse than guessing a
# floor, because it would come dressed as significance.
#
# So a floor must separate at least this many distinct LEGS on each side, and
# the leg counts ride in every grid row beside the cell counts.
MIN_LEGS_PER_ARM = 4
ALPHA = 0.05  # STATED CONVENTION. The only chosen number in this file.


def wf_pass(row: dict) -> bool | None:
    """The sweep's fold verdict for a row. None when no walk-forward ran.

    None is not False. A cell that never reached a walk-forward carries no
    evidence about generalisation, and folding it into the failures would
    manufacture a negative out of an absence.
    """
    if not row.get("wf_ran"):
        return None
    w, u = row.get("wf_wins"), row.get("wf_usable")
    if w is None or u is None:
        return None
    return u >= WF_MIN_USABLE and w * WF_WIN_DEN >= u * WF_WIN_NUM


def fisher_exact_greater(a: int, b: int, c: int, d: int) -> float:
    """One-sided Fisher exact p for a 2x2 [[a,b],[c,d]], stdlib only.

    P(as extreme or more, in the direction of a higher pass-rate in the first
    row). No scipy on the runner, and a chi-square approximation is wrong at
    exactly the small counts this corpus has.
    """
    n = a + b + c + d
    if n == 0:
        return 1.0
    r1, c1 = a + b, a + c
    lo = max(0, c1 - (n - r1))
    hi = min(r1, c1)
    denom = math.comb(n, c1)
    if denom == 0:
        return 1.0
    p = 0.0
    for k in range(a, hi + 1):
        if k < lo:
            continue
        p += math.comb(r1, k) * math.comb(n - r1, c1 - k)
    return min(1.0, p / denom)


def load(corpus: Path) -> list[dict]:
    rows = []
    for line in corpus.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def analyse(rows: list[dict], axis: str) -> dict:
    cells = [r for r in rows if r.get("kind") == "cell"]
    graded = [(r, wf_pass(r)) for r in cells]
    # ALWAYS STATE THE POPULATION. Every exclusion below is counted, so the
    # analysed n is reachable from the corpus size by arithmetic.
    pop = {
        "corpus_rows": len(rows),
        "leg_status_rows": sum(1 for r in rows if r.get("kind") == "leg_status"),
        "cells": len(cells),
        "cells_no_walkforward": sum(1 for _, v in graded if v is None),
        "cells_walkforwarded": sum(1 for _, v in graded if v is not None),
    }
    usable = [(r, v) for r, v in graded
              if v is not None and r.get(axis) is not None]
    pop["cells_missing_axis"] = pop["cells_walkforwarded"] - len(usable)
    pop["analysed"] = len(usable)
    pop["axis"] = axis
    pop["legs_represented"] = len({r.get("leg") for r, _ in usable})

    out: dict = {"population": pop, "alpha_is_a_convention": ALPHA,
                 "min_arm": MIN_ARM, "grid": [], "verdict": None,
                 "verdict_why": None, "recommended_floor": None}
    if not usable:
        out["verdict"] = "insufficient_population"
        out["verdict_why"] = (
            "no walk-forwarded cell carries the axis — nothing was compared. "
            "This is 'we did not look', not 'the rate does not predict'.")
        return out

    vals = sorted({round(r[axis], 4) for r, _ in usable})
    out["axis_distribution"] = {
        "n": len(usable), "min": vals[0], "max": vals[-1],
        "median": vals[len(vals) // 2],
        "n_distinct": len(vals),
        "overall_wf_pass_rate": round(
            sum(1 for _, v in usable if v) / len(usable), 4),
    }
    # The grid is the OBSERVED values, not an invented ladder — a floor can only
    # be set where the data actually changes which cells are admitted.
    thin_on_legs = 0
    for f in vals:
        adm = [(r, v) for r, v in usable if r[axis] >= f]
        rej = [(r, v) for r, v in usable if r[axis] < f]
        if len(adm) < MIN_ARM or len(rej) < MIN_ARM:
            continue
        legs_adm = len({r.get("leg") for r, _ in adm})
        legs_rej = len({r.get("leg") for r, _ in rej})
        if legs_adm < MIN_LEGS_PER_ARM or legs_rej < MIN_LEGS_PER_ARM:
            # Counted, not silently skipped: "many cells but few legs" is a
            # specific, actionable state (sweep MORE LEGS, not more cells) and
            # it must not read as "no floor was testable".
            thin_on_legs += 1
            continue
        a = sum(1 for _, v in adm if v)
        b = len(adm) - a
        c = sum(1 for _, v in rej if v)
        d = len(rej) - c
        out["grid"].append({
            "floor": f, "admitted_n": len(adm), "admitted_wf_pass": a,
            "admitted_rate": round(a / len(adm), 4),
            "admitted_legs": legs_adm, "rejected_legs": legs_rej,
            "rejected_n": len(rej), "rejected_wf_pass": c,
            "rejected_rate": round(c / len(rej), 4),
            # NOT round(p, 5): a p of 3e-7 rounds to exactly 0.0, and "p = 0.0"
            # is a claim of impossibility rather than of a small probability.
            "p_one_sided": float(f"{fisher_exact_greater(a, b, c, d):.3g}"),
        })
    k = len(out["grid"])
    out["floors_tried"] = k
    out["floors_rejected_thin_on_legs"] = thin_on_legs
    if k == 0:
        # WHICH scarcity blocked it decides what to do next, so the two are
        # separate messages rather than one "too small".
        if thin_on_legs:
            out["verdict"] = "insufficient_population"
            out["verdict_why"] = (
                f"{thin_on_legs} floor(s) had enough CELLS but fewer than "
                f"{MIN_LEGS_PER_ARM} distinct LEGS on one side. The predictor is a "
                "property of the leg — every cell on a leg shares its base rate — "
                "so cells within a leg are re-measurements of one book, not "
                f"independent samples. Testing them as {len(usable)} independent "
                "rows would inflate the effective n by roughly the cells-per-leg "
                "ratio and hand back a significant-looking p for what is really a "
                f"{pop['legs_represented']}-point comparison. WE DID NOT LOOK. "
                "Fix by sweeping MORE LEGS, not more cells per leg.")
        else:
            out["verdict"] = "insufficient_population"
            out["verdict_why"] = (
                f"no floor splits the {len(usable)} analysed cells into two arms of "
                f">= {MIN_ARM}. The corpus is too small or too concentrated to test a "
                "floor at all — WE DID NOT LOOK. Widen the sweep before reading "
                "anything into the absence of a floor.")
        return out

    adjusted = ALPHA / k  # Bonferroni over the floors actually tried.
    out["bonferroni_threshold"] = round(adjusted, 6)
    best_p = min(g["p_one_sided"] for g in out["grid"])
    tied = [g for g in out["grid"] if g["p_one_sided"] == best_p]
    best = tied[0]
    out["best_floor_by_p"] = best
    # A tie is the COMMON case under perfect or near-perfect separation: every
    # floor in the gap between the two clusters admits the same cells and scores
    # identically. Reporting `tied[0]` alone as "the floor" would be false
    # precision -- the data does not distinguish the endpoints of that gap, and
    # a reader would take the lowest as though it had been selected.
    out["tied_floors"] = [g["floor"] for g in tied]
    if best["p_one_sided"] <= adjusted:
        out["verdict"] = "separation"
        out["recommended_floor"] = best["floor"]
        span = ("" if len(tied) == 1 else
                f" NOTE: {len(tied)} floors tie at this p "
                f"({out['tied_floors'][0]}..{out['tied_floors'][-1]}) — the data "
                "does not distinguish them; the lowest is reported and the "
                "operator picks within the tie.")
        out["verdict_why"] = (
            f"base rate >= {best['floor']} generalises at "
            f"{best['admitted_rate']:.0%} ({best['admitted_wf_pass']}/"
            f"{best['admitted_n']}) vs {best['rejected_rate']:.0%} "
            f"({best['rejected_wf_pass']}/{best['rejected_n']}) below it; "
            f"p={best['p_one_sided']} clears the Bonferroni bar {adjusted:.5f} "
            f"over {k} floors tried.{span}")
    else:
        out["verdict"] = "no_separation"
        out["verdict_why"] = (
            f"{k} floors tested over {len(usable)} cells; the best "
            f"(>= {best['floor']}, p={best['p_one_sided']}) does not clear the "
            f"Bonferroni bar {adjusted:.5f}. WE LOOKED AND FOUND NOTHING: on this "
            "evidence a floor is unsupported, and adding one would re-introduce "
            "the free parameter the derived criterion removed. Document the "
            "asymmetry instead.")
    return out


def render(res: dict) -> str:
    p = res["population"]
    L = ["# Path B — is a base-rate floor supported by the data?", "",
         f"**Verdict: `{res['verdict']}`** — {res['verdict_why']}", "",
         "## Population (every exclusion counted)", "",
         f"- corpus rows: **{p['corpus_rows']}** "
         f"({p['cells']} cells + {p['leg_status_rows']} leg-status)",
         f"- cells with no walk-forward: **{p['cells_no_walkforward']}** "
         "(no evidence about generalisation — excluded, NOT counted as failures)",
         f"- walk-forwarded: **{p['cells_walkforwarded']}**, "
         f"of which missing `{p['axis']}`: **{p['cells_missing_axis']}**",
         f"- **analysed: {p['analysed']}** across **{p['legs_represented']}** legs"]
    d = res.get("axis_distribution")
    if d:
        L += ["", f"`{p['axis']}` over the analysed cells: min {d['min']} · "
              f"median {d['median']} · max {d['max']} · {d['n_distinct']} distinct "
              f"· overall walk-forward pass rate **{d['overall_wf_pass_rate']:.0%}**"]
    if res.get("grid"):
        L += ["", f"## Floors tried: {res['floors_tried']} "
              f"(alpha {res['alpha_is_a_convention']} is a STATED CONVENTION; "
              f"Bonferroni bar {res['bonferroni_threshold']})", "",
              "| floor | admitted n | admitted WF pass | rejected n | "
              "rejected WF pass | p (1-sided) |", "|--:|--:|--:|--:|--:|--:|"]
        for g in res["grid"]:
            L.append(f"| {g['floor']} | {g['admitted_n']} | "
                     f"{g['admitted_wf_pass']} ({g['admitted_rate']:.0%}) | "
                     f"{g['rejected_n']} | "
                     f"{g['rejected_wf_pass']} ({g['rejected_rate']:.0%}) | "
                     f"{g['p_one_sided']} |")
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=CORPUS_DEFAULT)
    ap.add_argument("--axis", default="base_rate_IS",
                    help="Predictor to test (default base_rate_IS, the Path A "
                         "exchange-rate denominator). d_cap_day_OOS tests the "
                         "OTHER unset Path B threshold.")
    ap.add_argument("--json", dest="json_out", help="Also write the full result JSON here.")
    a = ap.parse_args(argv[1:])

    corpus = Path(a.corpus)
    if not corpus.exists():
        print(f"error: no corpus at {corpus}. Run m20_corpus_extract.py first — "
              "an absent corpus is not an empty one.", file=sys.stderr)
        return 1
    res = analyse(load(corpus), a.axis)
    print(render(res))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=1))
        print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
