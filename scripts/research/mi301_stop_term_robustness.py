#!/usr/bin/env python3
# wiring: manual-only — this grades a DATED population (the e35 split,
# 2026-08-30T08:53:19Z) to check the ROBUSTNESS of one published scalar. A
# scheduled runner would re-answer it against a moving population and turn a
# recorded verdict into a drifting one, which is the defect this file measures.
"""Is the 2026-08-30 verdict's STOP-OUT term robust? (MI-301)

WHAT IS ALREADY ESTABLISHED, AND IS NOT RE-DERIVED HERE
--------------------------------------------------------
PR #12205 (MI-278 U41) ran the discriminating measurement
`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
asks for and verdicted **`both_contribute`**. That PR is OPEN and
`mergeable_state: dirty`, conflicting on six shared registers and on no code.
Its verdict is NOT re-derived here and this file proposes no replacement for it.

`m20-u15-stop-integrity-both-arms-2026-09-12.md` (on `main`) established that
the declared-only stop-rate DiD ranges **4.3pp to 28.2pp on identical data**
across two analyst choices — the declared-vs-amended split, and which fan-out
row is graded — and that **the SIGN is positive under all six combinations**.
That range is established and is NOT re-derived here either.

THE GAP THIS FILE CLOSES
-------------------------
`m20_u41_break_attribution_verdict.verdict()` branches on
`stop_rose = stop_did_pp > 0`, and `stop_did_pp` is **not computed by that
instrument** — it enters as a hand-typed `--stop-did-pp 11.6` CLI scalar. So the
whole difference between `both_contribute` ("e35 geometry is implicated on top")
and `market_regime` ("the market moved") is the SIGN of a number supplied from
outside, whose population and provenance mix appear nowhere in the artifact that
consumes it.

U15 tested that sign against **analyst choices**. Nobody has tested it against
**SAMPLING**, which is the axis that matters when U15 also records that the
treated pre-era baseline holds **2 stop-outs in total**. Those are different
questions: a number can be perfectly stable across every way of slicing it and
still be one coin-flip from changing sign.

So this file measures three things the consuming artifact does not state:

1. **The provenance mix** of the population the term is computed on, through
   `src.runtime.provenance` — the canonical module, never a bespoke predicate.
   This matters because `adjudicate_exit` decides `reached_stop` by comparing
   **`row["exit_price"]`** against the stop, and `exit_price` is exactly the
   field `exit_price_source` classifies. A mark-substituted exit price compared
   to a stop level is a coin toss wearing a verdict.
   ⚠️ `bleed_attribution_2026_09_11.population` filters **no** provenance at
   all, and `e35_break_attribution.population` filters one `exit_reason` value
   while naming that census bucket `fabricated_close` — which reads as "the
   fabricated rows are gone" and is not what it tests.

2. **Sampling fragility**: how many additional stop-outs in the treated pre-era
   cell flip the DiD's sign, and therefore the verdict.

3. **Wilson intervals** on all four rates. U15 says the interval "was never
   computed because at n=2 it would span everything". Computing it is strictly
   better than asserting it: an interval that visibly spans everything is
   evidence, and the assertion is not.

STATES, NEVER COLLAPSED
------------------------
A rate is `measured` · `insufficient_n` (the cell exists but is too small to be
an estimate — **not** a rate of 0.0, and never a number) · `empty` (*no unit
reached this cell*, which is not the same as a cell of zeros). A DiD is graded
`measured` · `ungradeable_cell` (some cell is not an estimate) · `sign_fragile`
(the sign flips within the sampling tolerance below). `sign_fragile` is
deliberately NOT a pass: it is the finding.

⚠️ **A FRAGILE SIGN IS NOT EVIDENCE THE TERM IS ZERO.** It says the population
cannot decide it, which forecloses `both_contribute` AND `market_regime` on this
term — not one of them. Reporting it as "it was the market" would be the exact
substitution `OI-20260911` forecloses by name.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys
from typing import Any, Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import bleed_attribution_2026_09_11 as BA           # noqa: E402
import stop_integrity_both_arms as SI               # noqa: E402
from src.runtime import provenance as P             # noqa: E402

#: The two arms `stop_integrity_both_arms` grades, in report order.
GROUPS = ("e35", "untouched_control")
ERAS = ("pre", "post")

#: MI-275's tolerance, the same value `stop_integrity_both_arms` defaults to.
DEFAULT_TOL = 0.002

#: A cell smaller than this is not an estimate. Basis, stated rather than tuned:
#: U15 records that the treated pre cell "holds 2 stop-outs in total" and calls a
#: DiD built on it "a direction, not an estimate". The binding quantity is the
#: NUMERATOR, not the cell — 2/8 and 2/80 are equally undecided about the rate —
#: so both a cell floor and a numerator floor are declared.
MIN_CELL = 8
MIN_STOPS = 5

#: Wilson z for a two-sided 95% interval.
Z = 1.959963985

RATE_STATES = ("measured", "insufficient_n", "empty")
DID_STATES = ("measured", "ungradeable_cell", "sign_fragile")


def wilson(successes: int, n: int) -> tuple[float, float] | None:
    """Wilson score interval as percentages, or None when there is no cell.

    None (not (0.0, 0.0)) on an empty cell, so "no unit reached this cell" stays
    distinguishable from "an interval pinned at zero".
    """
    if n <= 0:
        return None
    p = successes / n
    denom = 1.0 + (Z * Z) / n
    centre = (p + (Z * Z) / (2 * n)) / denom
    half = (Z / denom) * math.sqrt(p * (1.0 - p) / n + (Z * Z) / (4 * n * n))
    return (round(100.0 * max(0.0, centre - half), 2),
            round(100.0 * min(1.0, centre + half), 2))


def rate(units: list[dict], numerator: Callable[[dict], bool]) -> dict:
    """Stop-out rate for one cell, carrying its own state and interval."""
    n = len(units)
    s = sum(1 for u in units if numerator(u))
    if n == 0:
        return {"n": 0, "stops": 0, "rate_pp": None, "state": "empty", "ci": None}
    state = "measured" if (n >= MIN_CELL and s >= MIN_STOPS) else "insufficient_n"
    return {"n": n, "stops": s, "rate_pp": round(100.0 * s / n, 4),
            "state": state, "ci": wilson(s, n)}


def did(cells: dict) -> dict:
    """Difference-in-differences over four cells, graded.

    ⚠️ The arithmetic is computed whenever all four cells are non-empty, and the
    STATE is what says whether it may be read as an estimate. Returning None for
    the value on an underpowered cell would hide the very number whose fragility
    is the finding.
    """
    got = [cells[g][e] for g in GROUPS for e in ERAS]
    if any(c["rate_pp"] is None for c in got):
        return {"did_pp": None, "state": "ungradeable_cell",
                "why": "at least one cell is empty, so no difference exists"}
    t = cells["e35"]["post"]["rate_pp"] - cells["e35"]["pre"]["rate_pp"]
    c = (cells["untouched_control"]["post"]["rate_pp"]
         - cells["untouched_control"]["pre"]["rate_pp"])
    value = round(t - c, 4)
    state = "measured"
    why = "every cell clears the declared floors"
    if any(cc["state"] != "measured" for cc in got):
        state = "ungradeable_cell"
        why = "a cell is below the declared floor (cell %d / stops %d)" % (MIN_CELL, MIN_STOPS)
    return {"did_pp": value, "treated_delta_pp": round(t, 4),
            "control_delta_pp": round(c, 4), "state": state, "why": why}


def flip_threshold(cells: dict) -> dict:
    """How many extra stop-outs in the TREATED PRE cell flip the DiD's sign?

    The treated pre cell is chosen because U15 records it as the smallest
    (2 stop-outs), so it is where one observation buys the most movement. This
    is a SAMPLING sensitivity, not a claim that those stop-outs exist.
    """
    pre, post = cells["e35"]["pre"], cells["e35"]["post"]
    ctl = (cells["untouched_control"]["post"]["rate_pp"]
           - cells["untouched_control"]["pre"]["rate_pp"])
    if pre["n"] <= 0 or post["rate_pp"] is None:
        return {"flips_at": None, "state": "ungradeable_cell", "ladder": []}
    ladder = []
    flips_at = None
    for k in range(0, pre["n"] - pre["stops"] + 1):
        s = pre["stops"] + k
        r = 100.0 * s / pre["n"]
        d = (post["rate_pp"] - r) - ctl
        ladder.append({"extra_stop_outs": k, "treated_pre_pp": round(r, 2),
                       "did_pp": round(d, 2), "stop_rose": d > 0})
        if d <= 0 and flips_at is None:
            flips_at = k
    return {"flips_at": flips_at, "control_delta_pp": round(ctl, 4),
            "state": "measured", "ladder": ladder}


def verdict_term(did_pp: float | None, state: str, flips_at: int | None) -> str:
    """What this term licenses. It is ONE of the verdict's two terms, never the verdict.

    `sign_fragile` forecloses BOTH `both_contribute` and `market_regime` on this
    term. It does not choose between them.
    """
    if did_pp is None or state == "ungradeable_cell":
        return "cannot_discriminate"
    if flips_at is not None and flips_at <= 1:
        return "sign_fragile"
    return "stop_rose" if did_pp > 0 else "stop_did_not_rise"


def provenance_mix(units: list[dict], by_id: dict) -> dict:
    """Provenance of the exit_price the adjudication actually compared.

    ⚠️ Classified on the row `adjudicate_exit` GRADED (`trade_ids[0]`, which
    `stop_integrity_both_arms.build` has already sorted by `closed_at`), not on
    the package — a package's siblings can carry different exit provenance, and
    the term is computed from the graded row.
    """
    rows = [by_id.get(u["trade_ids"][0]) or {} for u in units]
    counts = P.split_counts(rows, "exit_price_source")
    raw = collections.Counter()
    for r in rows:
        raw[P.classify_row(r, "exit_price_source")[1]] += 1
    unparseable = sum(1 for r in rows if r.get("notes") and not _notes_ok(r.get("notes")))
    return {"counts": counts, "coverage": P.coverage(counts),
            "raw_sources": dict(raw.most_common()),
            "notes_unparseable": unparseable}


def _notes_ok(notes: Any) -> bool:
    if isinstance(notes, dict):
        return True
    try:
        return isinstance(json.loads(notes), dict)
    except (ValueError, TypeError):
        return False


def per_leg(units: list[dict], numerator: Callable[[dict], bool]) -> dict:
    """Per-leg stop-out counts. `OI-20260911` asks for PER-LEG, and the
    published artifacts report per-ARM; a per-arm number can be one leg."""
    out: dict[str, dict] = {}
    for u in units:
        d = out.setdefault(u["leg"], {"n": 0, "stops": 0, "symbols": set()})
        d["n"] += 1
        d["symbols"].add(str(u.get("symbol") or ""))
        if numerator(u):
            d["stops"] += 1
    for leg, d in out.items():
        d["symbols"] = sorted(d["symbols"])
        d["rate_pp"] = round(100.0 * d["stops"] / d["n"], 2) if d["n"] else None
        d["state"] = "measured" if (d["n"] >= MIN_CELL and d["stops"] >= MIN_STOPS) else "insufficient_n"
    return out


#: The two numerators, named so a reader can see which one a figure used.
NUMERATORS = {
    "all_stop_outs": lambda u: u["adjudicated"] == "reached_stop",
    "declared_only": lambda u: (u["adjudicated"] == "reached_stop"
                                and u["stop_integrity"] == "stop_is_entry_declared"),
}


def measure(trades: list[dict], pkgs: dict, tol: float) -> dict:
    by_id = {r["id"]: r for r in trades if isinstance(r.get("id"), int)}
    built = {g: SI.build(trades, pkgs, g, tol) for g in GROUPS}
    ids = [r["id"] for r in trades if isinstance(r.get("id"), int)]
    res: dict[str, Any] = {
        "population": {
            "trades_rows": len(trades), "packages_rows": len(pkgs),
            "trade_id_min": min(ids) if ids else None,
            "trade_id_max": max(ids) if ids else None,
            "ba_population": len(BA.population(trades)),
            "event": BA.E35_DEPLOY_UTC,
            "split_basis": "created_at (OPEN time) -- a leg carries the geometry it was opened under",
            "tol": tol, "min_cell": MIN_CELL, "min_stops": MIN_STOPS,
        },
        "skipped": {g: dict(built[g]["skipped"]) for g in GROUPS},
        "provenance": {g: {e: provenance_mix(built[g][e], by_id) for e in ERAS}
                       for g in GROUPS},
        "bases": {},
    }
    for basis, num in NUMERATORS.items():
        cells = {g: {e: rate(built[g][e], num) for e in ERAS} for g in GROUPS}
        d = did(cells)
        flip = flip_threshold(cells)
        fa = flip.get("flips_at")
        # ⚠️ REPORTED SEPARATELY, NEVER FOLDED INTO `term`. The cell floor and the
        # sign margin are DIFFERENT facts and the floor binds first in
        # `verdict_term`, so a fragility finding would otherwise be silently
        # swallowed by `cannot_discriminate` -- the collapse this file is about.
        fragility = ("not_graded" if fa is None and flip["state"] != "measured"
                     else "robust" if fa is None
                     else "knife_edge" if fa <= 1 else "fragile" if fa <= 2 else "robust")
        res["bases"][basis] = {
            "cells": cells, "did": d, "flip": flip, "fragility": fragility,
            "term": verdict_term(d.get("did_pp"), d["state"], fa),
            "per_leg_e35": {e: per_leg(built["e35"][e], num) for e in ERAS},
        }
    return res


def report(res: dict) -> list[str]:
    p = res["population"]
    out = [
        "MI-301 — is the 2026-08-30 verdict's STOP-OUT term robust?",
        "",
        "POPULATION: %s trades rows (ids %s-%s), %s package rows; "
        "BA.population -> %s units; event %s; tol %s; floors cell>=%d stops>=%d"
        % (p["trades_rows"], p["trade_id_min"], p["trade_id_max"],
           p["packages_rows"], p["ba_population"], p["event"], p["tol"],
           p["min_cell"], p["min_stops"]),
        "",
        "PROVENANCE of the exit_price the adjudication compared "
        "(src.runtime.provenance, canonical):",
    ]
    for g in GROUPS:
        for e in ERAS:
            m = res["provenance"][g][e]
            c = m["counts"]
            # provenance: P.split_counts — buckets the GRADED row's exit_price_source; coverage is measured/total
            out.append(
                "  %-18s %-4s total=%3d measured=%3d estimated=%3d fabricated=%2d "
                "unverified=%2d coverage=%s notes_unparseable=%d"
                % (g, e, c["total"], c["measured"], c["estimated"],
                   c["fabricated"], c["unverified"], m["coverage"],
                   m["notes_unparseable"]))
    for basis, b in res["bases"].items():
        out += ["", "BASIS: %s" % basis]
        for g in GROUPS:
            for e in ERAS:
                cc = b["cells"][g][e]
                out.append("  %-18s %-4s %s/%s rate=%s ci95=%s state=%s"
                           % (g, e, cc["stops"], cc["n"], cc["rate_pp"],
                              cc["ci"], cc["state"]))
        d = b["did"]
        out.append("  DiD = %s pp (treated %s, control %s) state=%s -- %s"
                   % (d.get("did_pp"), d.get("treated_delta_pp"),
                      d.get("control_delta_pp"), d["state"], d.get("why")))
        f = b["flip"]
        if f.get("flips_at") is not None:
            out.append("  SIGN FLIPS at +%d extra stop-out(s) in the treated PRE cell"
                       % f["flips_at"])
        else:
            out.append("  sign does not flip anywhere in the treated PRE cell's range")
        out.append("  TERM: %s  |  SIGN FRAGILITY: %s" % (b["term"], b.get("fragility")))
        if b["term"] == "sign_fragile":
            out.append("      ⚠️ This forecloses BOTH `both_contribute` AND "
                       "`market_regime` on this term. It does not choose between them.")
    return out


def self_test() -> int:
    fails = []

    def ok(label: str, cond: bool) -> None:
        print("  %s %s" % ("ok  " if cond else "FAIL", label))
        if not cond:
            fails.append(label)

    print("wilson")
    ok("an empty cell is None, NEVER (0.0, 0.0)", wilson(0, 0) is None)
    lo, hi = wilson(2, 8)
    ok("2/8 spans more than 40pp (it is not an estimate)", (hi - lo) > 40.0)
    ok("2/8 lower bound is above 0", lo > 0.0)
    lo2, hi2 = wilson(22, 47)
    ok("the treated pre interval CONTAINS the control pre rate",
       lo <= (100.0 * 22 / 47) <= hi)
    ok("a larger cell gives a tighter interval", (hi2 - lo2) < (hi - lo))
    ok("0/10 has a lower bound of exactly 0", wilson(0, 10)[0] == 0.0)
    ok("10/10 has an upper bound of exactly 100", wilson(10, 10)[1] == 100.0)

    print("rate")
    u = [{"adjudicated": "reached_stop"}] * 6 + [{"adjudicated": "neither"}] * 4
    r = rate(u, NUMERATORS["all_stop_outs"])
    ok("a cell of 10 with 6 stops is measured", r["state"] == "measured")
    ok("its rate is 60.0", r["rate_pp"] == 60.0)
    r2 = rate([{"adjudicated": "reached_stop"}] * 2
              + [{"adjudicated": "neither"}] * 6, NUMERATORS["all_stop_outs"])
    ok("a cell of 8 with 2 stops is insufficient_n, not a rate of 25",
       r2["state"] == "insufficient_n")
    ok("...and it STILL reports the number for the reader", r2["rate_pp"] == 25.0)
    r3 = rate([], NUMERATORS["all_stop_outs"])
    ok("an empty cell is `empty`, never `insufficient_n`", r3["state"] == "empty")
    ok("an empty cell's rate is None, never 0.0", r3["rate_pp"] is None)
    big = [{"adjudicated": "reached_stop"}] * 4 + [{"adjudicated": "neither"}] * 40
    ok("4 stops in 44 is insufficient_n -- the NUMERATOR floor binds, not the cell",
       rate(big, NUMERATORS["all_stop_outs"])["state"] == "insufficient_n")

    print("declared_only numerator")
    mixed = [{"adjudicated": "reached_stop", "stop_integrity": "stop_amended_tighter"}]
    ok("an AMENDED stop-out is not a declared-geometry stop-out",
       not NUMERATORS["declared_only"](mixed[0]))
    ok("...but it IS an all-stop-outs stop-out",
       NUMERATORS["all_stop_outs"](mixed[0]))

    print("did + flip")
    cells = {
        "e35": {"pre": rate([{"adjudicated": "reached_stop"}] * 2
                            + [{"adjudicated": "neither"}] * 6,
                            NUMERATORS["all_stop_outs"]),
                "post": rate([{"adjudicated": "reached_stop"}] * 11
                             + [{"adjudicated": "neither"}] * 9,
                             NUMERATORS["all_stop_outs"])},
        "untouched_control": {
            "pre": rate([{"adjudicated": "reached_stop"}] * 22
                        + [{"adjudicated": "neither"}] * 25,
                        NUMERATORS["all_stop_outs"]),
            "post": rate([{"adjudicated": "reached_stop"}] * 59
                         + [{"adjudicated": "neither"}] * 44,
                         NUMERATORS["all_stop_outs"])},
    }
    d = did(cells)
    ok("the DiD arithmetic is +19.53pp on the live cell shape",
       abs(d["did_pp"] - 19.5314) < 0.01)
    ok("an underpowered cell makes the DiD ungradeable_cell, not measured",
       d["state"] == "ungradeable_cell")
    f = flip_threshold(cells)
    ok("two extra stop-outs flip the sign on the live cell shape", f["flips_at"] == 2)
    ok("the ladder's first rung is the observed value", f["ladder"][0]["did_pp"] == 19.53)
    ok("the rung at the flip is not stop_rose",
       not f["ladder"][f["flips_at"]]["stop_rose"])

    print("verdict_term")
    ok("a flip at 1 row is sign_fragile, never stop_rose",
       verdict_term(5.31, "measured", 1) == "sign_fragile")
    ok("a flip at 0 rows is sign_fragile too",
       verdict_term(0.5, "measured", 0) == "sign_fragile")
    ok("a robust positive term is stop_rose",
       verdict_term(19.5, "measured", 6) == "stop_rose")
    ok("a negative robust term is stop_did_not_rise",
       verdict_term(-8.0, "measured", None) == "stop_did_not_rise")
    ok("an ungradeable cell cannot produce a term",
       verdict_term(19.5, "ungradeable_cell", 6) == "cannot_discriminate")
    ok("a None DiD cannot produce a term",
       verdict_term(None, "measured", 6) == "cannot_discriminate")

    print("planted defects")
    ok("PLANT: dropping the flip check would return stop_rose on a 1-row margin "
       "-- i.e. license a Tier-3 revert on a coin flip",
       ("stop_rose" if 5.31 > 0 else "x") == "stop_rose"
       and verdict_term(5.31, "measured", 1) == "sign_fragile")
    ok("PLANT: an empty cell reported as 0.0 would make the DiD computable "
       "from a cell nobody observed",
       rate([], NUMERATORS["all_stop_outs"])["rate_pp"] is None)
    ok("PLANT: a cell floor alone would pass 4/44; the numerator floor catches it",
       rate(big, NUMERATORS["all_stop_outs"])["n"] >= MIN_CELL
       and rate(big, NUMERATORS["all_stop_outs"])["state"] == "insufficient_n")

    print("report states its population")
    text = "\n".join(report({
        "population": {"trades_rows": 1, "packages_rows": 1, "trade_id_min": 1,
                       "trade_id_max": 2, "ba_population": 1, "event": "e",
                       "tol": 0.002, "min_cell": MIN_CELL, "min_stops": MIN_STOPS},
        "skipped": {}, "bases": {},
        "provenance": {g: {e: {"counts": {"total": 0, "measured": 0, "estimated": 0,
                                          "fabricated": 0, "unverified": 0},
                               "coverage": None, "raw_sources": {},
                               "notes_unparseable": 0}
                           for e in ERAS} for g in GROUPS},
    }))
    ok("the report names its population", "POPULATION:" in text)
    ok("the report names the canonical provenance module",
       "src.runtime.provenance" in text)

    print("\n%d control(s) failed" % len(fails))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trades")
    ap.add_argument("--packages")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.trades or not args.packages:
        ap.error("--trades and --packages are required (or --self-test)")
    trades = json.loads(pathlib.Path(args.trades).read_text())
    pkg_rows = json.loads(pathlib.Path(args.packages).read_text())
    pkgs = {p["order_package_id"]: p for p in pkg_rows if p.get("order_package_id")}
    res = measure(trades, pkgs, args.tol)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print("\n".join(report(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
