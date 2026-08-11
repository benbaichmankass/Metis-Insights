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

# ---------------------------------------------------------------- derived axes
#
# THE FLOOR IS AIMED AT `base_rate`; THE MECHANISM IS `dN / N_b`.
#
# `allowed = D_b x (dN / N_b)`. Read it as a fraction: the cell is granted that
# FRACTION of the base book's entire drawdown. So the permissive case is not
# "the rate is low" — it is "the cell's net_R gain is large relative to the base
# book's net_R", which grants a proportional share of D_b however efficient that
# book is. Both are free parameters; only this one names the thing going wrong.
#
# Measured on the fleet (2026-08-10), the two come apart cleanly:
#
#     case                                     rate   dN/N_b   allowed vs D_b
#     tlt_pullback_1h    trail4       (IS)     0.75     1.70            170%
#     eth_pullback_prop_2h decay_stall10_t1.8  0.91     1.08            108%
#     scha_trend_long_1d decay_stall6_t2       0.96     0.97             97%
#     eth_pullback_2h    trail6                2.41     0.18             18%
#     trend_donchian_sol trail6                3.46     0.30             30%
#
# A rate floor would reject rows 1-3 AND row 4-5's neighbours on a leg property;
# a ratio cap rejects exactly the rows that ask for most of the book's drawdown.
#
# IT IS ALSO A BETTER-CONDITIONED PREDICTOR. `base_rate` is a property of the
# LEG, so cells within a leg are re-measurements of one number and MIN_LEGS_PER_ARM
# binds hard. `dN / N_b` varies per CELL (the numerator is the cell's own gain),
# so the corpus carries far more distinct values. The clustering is REDUCED, NOT
# REMOVED — the denominator is still per-leg — so MIN_LEGS_PER_ARM still applies
# and this file does not pretend the rows became independent.
#
# `None`, never a number, when the base book is unprofitable: a negative
# denominator inverts the ratio's meaning, and `drawdown_exchange_rate` already
# refuses that book outright (`base_unprofitable`). Substituting a value would
# fabricate a comparison on a book the criterion never grades.
def _dn_over_nb(row: dict, win: str):
    nb, dn = row.get(f"base_net_r_{win}"), row.get(f"d_net_r_{win}")
    if not isinstance(nb, (int, float)) or not isinstance(dn, (int, float)):
        return None
    if nb <= 0:
        return None
    return round(dn / nb, 4)


DERIVED_AXES = {
    "dn_over_nb_IS": lambda r: _dn_over_nb(r, "IS"),
    "dn_over_nb_OOS": lambda r: _dn_over_nb(r, "OOS"),
}


def axis_value(row: dict, axis: str):
    """The predictor's value for a row — stored column or derived, never both.

    A derived axis is computed here rather than written into the corpus so the
    corpus stays a record of what the sweep MEASURED; a ratio of two measured
    columns is an analysis choice and belongs with the analysis.
    """
    fn = DERIVED_AXES.get(axis)
    return fn(row) if fn else row.get(axis)


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


def analyse(rows: list[dict], axis: str, direction: str = "floor") -> dict:
    """Test whether `axis` separates generalising cells from non-generalising ones.

    `direction` is which side of a threshold a POLICY would keep:
      * ``floor`` — admit `axis >= t` (a base-rate floor: reject weak books).
      * ``cap``   — admit `axis <= t` (a `dN/N_b` cap: reject cells that ask for
        most of the base book's drawdown).

    The two are not interchangeable relabellings. Fisher is one-sided in the
    direction of "the admitted arm generalises better", so the arms must be
    assigned by the policy being tested; running a cap through the floor code
    would test the opposite hypothesis and report it under the cap's name — the
    semantic-substitution defect (`diagnostic-provenance-guard` sub-class A).
    """
    if direction not in ("floor", "cap"):
        raise ValueError(f"direction must be 'floor' or 'cap', got {direction!r}")
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
    # The axis value is resolved ONCE per row and carried alongside it, so the
    # population count, the grid and the per-leg block can never disagree about
    # what a row's predictor was (a derived axis recomputed at three call sites
    # is three chances to drift).
    usable = [(r, v, axis_value(r, axis)) for r, v in graded if v is not None]
    usable = [t for t in usable if t[2] is not None]
    pop["cells_missing_axis"] = pop["cells_walkforwarded"] - len(usable)
    pop["analysed"] = len(usable)
    pop["axis"] = axis
    pop["axis_is_derived"] = axis in DERIVED_AXES
    pop["direction"] = direction
    pop["legs_represented"] = len({r.get("leg") for r, _, _ in usable})
    # HOW MUCH OF THIS POPULATION HAS AN UNGATED BASE BOOK (added 2026-08-11).
    #
    # Every axis here is derived from the base book (`base_rate` is net_R per
    # drawdown OF THE BASE), and the sweep measures that base with the regime
    # hard gate OFF while the live router is baseline-on. For a leg named in
    # `config/regime_policy.yaml` the base therefore includes trades production
    # refuses, so its predictor value describes a book the live leg never trades.
    #
    # REPORTED, never silently excluded — the `rCoverage`/`pnlCoverage`
    # discipline: transparency, not a quiet population change. A verdict that
    # dropped these rows without saying so would be selection over an unstated
    # denominator, which is the thing this whole script exists to refuse.
    #
    # Three states, because a corpus predating the field must not read as "none
    # are gated": None = not recorded on these rows, else the real count.
    deltas = {r.get("regime_gate_delta") for r, _, _ in usable}
    if deltas == {None}:
        pop["cells_ungated_base"] = None
        pop["legs_ungated_base"] = None
        pop["ungated_base_why"] = "regime_gate_delta not recorded on this corpus"
    else:
        ung = [r for r, _, _ in usable if r.get("regime_gate_delta") == "narrower_live"]
        pop["cells_ungated_base"] = len(ung)
        pop["legs_ungated_base"] = len({r.get("leg") for r in ung})
        pop["ungated_base_why"] = None

    out: dict = {"population": pop, "alpha_is_a_convention": ALPHA,
                 "min_arm": MIN_ARM, "grid": [], "verdict": None,
                 "verdict_why": None, "recommended_floor": None,
                 "direction": direction}
    if not usable:
        out["verdict"] = "insufficient_population"
        out["verdict_why"] = (
            f"no walk-forwarded cell carries `{axis}` — nothing was compared. "
            "This is 'we did not look', not 'the predictor does not predict'.")
        return out

    vals = sorted({round(x, 4) for _, _, x in usable})
    out["axis_distribution"] = {
        "n": len(usable), "min": vals[0], "max": vals[-1],
        "median": vals[len(vals) // 2],
        "n_distinct": len(vals),
        "overall_wf_pass_rate": round(
            sum(1 for _, v, _ in usable if v) / len(usable), 4),
    }
    # THE RATE'S OWN DENOMINATOR. A base rate is net_R/maxDD over the leg's base
    # book, and that book can be 800 trades or 4. Quoting "the lowest rate is
    # 1.08" without saying how many trades produced it is an unstated
    # denominator one level below the one this file already guards — measured
    # here after `splg_trend_long_1d` came back with an OOS base of FOUR trades.
    # Reported, never filtered: dropping thin legs would silently redefine the
    # population, and which legs are thin is itself part of the answer.
    #
    # A DERIVED PER-CELL AXIS HAS NO SINGLE PER-LEG VALUE. `base_rate_IS` is one
    # number per leg, so `rate` is exactly it; `dn_over_nb_IS` varies cell to
    # cell, and reporting the first cell's value under the same key would be a
    # per-cell quantity wearing a per-leg label. So the SPAN ships beside it and
    # `rate` is None for a derived axis rather than a silently-picked member.
    per_leg: dict = {}
    for r, _, x in usable:
        leg = r.get("leg")
        if leg not in per_leg:
            per_leg[leg] = {"rate": None if pop["axis_is_derived"] else x,
                            "axis_min": x, "axis_max": x,
                            "base_trades_IS": r.get("base_trades_IS"),
                            "base_trades_OOS": r.get("base_trades_OOS"),
                            "cells_analysed": 0}
        e = per_leg[leg]
        e["axis_min"] = min(e["axis_min"], x)
        e["axis_max"] = max(e["axis_max"], x)
        e["cells_analysed"] += 1
    out["per_leg"] = dict(sorted(
        per_leg.items(), key=lambda kv: -(kv[1]["axis_max"] or 0)))
    trade_counts = [v["base_trades_IS"] for v in per_leg.values()
                    if isinstance(v["base_trades_IS"], (int, float))]
    out["axis_distribution"]["base_trades_IS_min"] = (
        min(trade_counts) if trade_counts else None)
    out["axis_distribution"]["legs_missing_trade_count"] = (
        len(per_leg) - len(trade_counts))
    # The grid is the OBSERVED values, not an invented ladder — a floor can only
    # be set where the data actually changes which cells are admitted.
    thin_on_legs = 0
    for f in vals:
        # `admitted` is always the arm the POLICY KEEPS, so Fisher's one-sided
        # direction means the same thing under both settings.
        if direction == "floor":
            adm = [t for t in usable if t[2] >= f]
            rej = [t for t in usable if t[2] < f]
        else:
            adm = [t for t in usable if t[2] <= f]
            rej = [t for t in usable if t[2] > f]
        if len(adm) < MIN_ARM or len(rej) < MIN_ARM:
            continue
        legs_adm = len({r.get("leg") for r, _, _ in adm})
        legs_rej = len({r.get("leg") for r, _, _ in rej})
        if legs_adm < MIN_LEGS_PER_ARM or legs_rej < MIN_LEGS_PER_ARM:
            # Counted, not silently skipped: "many cells but few legs" is a
            # specific, actionable state (sweep MORE LEGS, not more cells) and
            # it must not read as "no floor was testable".
            thin_on_legs += 1
            continue
        a = sum(1 for _, v, _ in adm if v)
        b = len(adm) - a
        c = sum(1 for _, v, _ in rej if v)
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
                f"{thin_on_legs} threshold(s) had enough CELLS but fewer than "
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
                f"no threshold splits the {len(usable)} analysed cells into two arms of "
                f">= {MIN_ARM}. The corpus is too small or too concentrated to test a "
                "threshold at all — WE DID NOT LOOK. Widen the sweep before reading "
                "anything into the absence of a floor.")
        return out

    # DIRECTION-AWARE VOCABULARY. The first cap run printed its best threshold
    # as ">= 0.5128" under the heading "a floor is unsupported" — the comparison
    # INVERTED and the policy misnamed, while every number was correct. That is
    # `diagnostic-provenance-guard` sub-class A in this file's own output, found
    # by reading the run rather than the code, so the words are derived from
    # `direction` here instead of being written twice.
    word = "floor" if direction == "floor" else "cap"
    op = ">=" if direction == "floor" else "<="
    adjusted = ALPHA / k  # Bonferroni over the thresholds actually tried.
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
                f" NOTE: {len(tied)} {word}s tie at this p "
                f"({out['tied_floors'][0]}..{out['tied_floors'][-1]}) — the data "
                "does not distinguish them; the lowest is reported and the "
                "operator picks within the tie.")
        out["verdict_why"] = (
            f"{pop['axis']} {op} {best['floor']} generalises at "
            f"{best['admitted_rate']:.0%} ({best['admitted_wf_pass']}/"
            f"{best['admitted_n']}) vs {best['rejected_rate']:.0%} "
            f"({best['rejected_wf_pass']}/{best['rejected_n']}) below it; "
            f"p={best['p_one_sided']} clears the Bonferroni bar {adjusted:.5f} "
            f"over {k} {word}s tried.{span}")
    else:
        out["verdict"] = "no_separation"
        out["verdict_why"] = (
            f"{k} {word}s tested over {len(usable)} cells; the best "
            f"({op} {best['floor']}, p={best['p_one_sided']}) does not clear the "
            f"Bonferroni bar {adjusted:.5f}. WE LOOKED AND FOUND NOTHING: on this "
            f"evidence a {word} on {pop['axis']} is unsupported, and adding one "
            "would re-introduce "
            "the free parameter the derived criterion removed. Document the "
            "asymmetry instead.")
    return out


def render(res: dict) -> str:
    p = res["population"]
    # The heading names the ACTUAL hypothesis tested, not the file's original
    # one: a cap run printed under "is a base-rate floor supported" would be a
    # label that does not describe what was computed.
    kind = ("floor" if res.get("direction", "floor") == "floor" else "cap")
    L = [f"# Path B — is a `{p['axis']}` {kind} supported by the data?", "",
         f"**Verdict: `{res['verdict']}`** — {res['verdict_why']}", "",
         "## Population (every exclusion counted)", "",
         f"- corpus rows: **{p['corpus_rows']}** "
         f"({p['cells']} cells + {p['leg_status_rows']} leg-status)",
         f"- cells with no walk-forward: **{p['cells_no_walkforward']}** "
         "(no evidence about generalisation — excluded, NOT counted as failures)",
         f"- walk-forwarded: **{p['cells_walkforwarded']}**, "
         f"of which missing `{p['axis']}`: **{p['cells_missing_axis']}**",
         f"- **analysed: {p['analysed']}** across **{p['legs_represented']}** legs"]
    # WHICH BOOK the predictor describes. Printed unconditionally — a reader must
    # never have to know to ask, and "not recorded" is itself an answer.
    if p.get("cells_ungated_base") is None:
        L.append(f"- ungated-base share: **not recorded** "
                 f"({p.get('ungated_base_why') or 'field absent'}) — the sweep runs "
                 "the harness at `--regime-router off` while the live router is "
                 "baseline-on, so a policy-named leg's base includes trades "
                 "production refuses")
    else:
        L.append(f"- ungated base book (live gate would narrow it): "
                 f"**{p['cells_ungated_base']} cells / {p['legs_ungated_base']} legs** "
                 "— reported, NOT excluded; their axis value describes a book the "
                 "live leg does not trade")
    d = res.get("axis_distribution")
    if d:
        L += ["", f"`{p['axis']}` over the analysed cells: min {d['min']} · "
              f"median {d['median']} · max {d['max']} · {d['n_distinct']} distinct "
              f"· overall walk-forward pass rate **{d['overall_wf_pass_rate']:.0%}**"]
    if res.get("grid"):
        L += ["", f"## Thresholds tried: {res['floors_tried']} "
              f"(alpha {res['alpha_is_a_convention']} is a STATED CONVENTION; "
              f"Bonferroni bar {res['bonferroni_threshold']})", "",
              f"| {kind} | admitted n | admitted WF pass | rejected n | "
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
                         "OTHER unset Path B threshold. dn_over_nb_IS is "
                         "DERIVED (d_net_r/base_net_r) — the fraction of the "
                         "base book's drawdown the criterion grants.")
    ap.add_argument("--direction", default="floor", choices=("floor", "cap"),
                    help="Which side a policy would KEEP: floor admits "
                         ">= t (base-rate floor), cap admits <= t (dN/N_b cap). "
                         "Not a relabelling — it assigns the arms Fisher tests.")
    ap.add_argument("--json", dest="json_out", help="Also write the full result JSON here.")
    a = ap.parse_args(argv[1:])

    corpus = Path(a.corpus)
    if not corpus.exists():
        print(f"error: no corpus at {corpus}. Run m20_corpus_extract.py first — "
              "an absent corpus is not an empty one.", file=sys.stderr)
        return 1
    res = analyse(load(corpus), a.axis, a.direction)
    print(render(res))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=1))
        print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
