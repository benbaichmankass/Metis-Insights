#!/usr/bin/env python3
# wiring: manual-only - an on-demand re-read of the coverage matrix against the
# CURRENT e35 corpus. No scheduled consumer; invoked by a session doing the
# stale-ref re-check. Registered in RESEARCH-CAPABILITY-INDEX.md.
"""Re-read `exit-refinement-coverage.json`'s `bracket_geometry` column against the corpus.

WHAT QUESTION THIS ANSWERS, and why it is not the guard's question.
`scripts/ci/check_matrix_bracket_values.py` asks *"does a **shipped** cell's id match
`config/strategies.yaml`?"* — a claim about CONFIG. This asks the other one: *"is the
matrix's verdict still what the CURRENT measurement says?"* — a claim about DATA. A cell
can pass the guard perfectly while resting on a run three generations old.

⚠️ **A `shipped` LEG SHOWING ZERO PASSING CELLS IS THE EXPECTED SIGNATURE, NOT A
REGRESSION.** The sweep's baseline is config-exact, so once geometry is DECLARED the
base *is* that geometry and the winning cell can no longer beat itself. Reading that as
"the shipped lever stopped working" is the trap this docstring exists to stop, and it is
checkable rather than assertable: `--b4-outcome` measures whether the base ROSE by the
improvement the shipped cell claimed. Measured 2026-08-26 -> 2026-08-29 over the 8 legs
PR #10419 shipped: 3 reproduce to four decimal places EXACTLY, all 8 land 0.89-1.03x,
median 1.00.

⚠️ **STATE THE CONTROL'S DENOMINATOR.** Only 10 legs carry BOTH runs, and of the 2
non-B4 legs among them, `mgc_trend_1h` and `xauusd_trend_1h` are **byte-identical across
all 199 cells** — `PROXY_DATA` maps MGC *and* XAUUSD to the same `GC_F` series, so they
are ONE observation wearing two leg names. The control is therefore **n=1 independent
leg**, not 2. Anything that counts those two rows as agreeing evidence is
double-counting.

⚠️ **A `to<N>` COMPONENT MAKES A WINNER UNSHIPPABLE REGARDLESS OF ITS NUMBERS.** No live
trend/pullback/squeeze unit implements a bar-count exit
(BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES),
so a passing cell carrying one is evidence about the HARNESS, not a shippable candidate.
The reverse-direction report splits on exactly that axis and never totals across it.

⚠️ **"NOT REPRODUCED" IS NOT "REFUTED".** Only the per-axis/joint argmax cells are carried
into the gate on a given run, so a cell that was gated on an old run and is not an argmax
on a new one has NO fresh walk-forward verdict. Its surface `d_net_r` moving negative is
real and reportable; calling that a failed walk-forward would assert a measurement nobody
took.

Usage:
    python3 scripts/research/e35_matrix_recheck.py            # the full re-check
    python3 scripts/research/e35_matrix_recheck.py --b4-outcome
    python3 scripts/research/e35_matrix_recheck.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "docs" / "research" / "e35-bracket-corpus.jsonl"
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"

#: The two gate verdicts that mean the cell CLEARED the m20 gate.
PASS_VERDICTS = {"wf_pass", "path_b_wf_pass"}

#: A cell component naming the timeout axis. Deliberately anchored so `to400`
#: matches and a leg name containing "to" does not.
_TIMEOUT_AXIS = re.compile(r"(^|_)to\d")

#: The cell id as written in a ref. Refs use BOTH spellings: a backticked
#: ``cell `tp3_sm2` `` and a bold ``**tp2_sm3.5_to48**``. Reading only one
#: silently drops the other, so both are tried, backtick first.
_CELL_BACKTICK = re.compile(r"cell\s+`([A-Za-z0-9_.]+)`")
_CELL_BOLD = re.compile(r"\*\*((?:tp|sm|to)[0-9][A-Za-z0-9_.]*)\*\*")

_DATE = re.compile(r"20\d\d-\d\d-\d\d")


def cell_id_from_ref(ref: str) -> str | None:
    """The cell id a ref names, or None. Backtick spelling wins if both appear."""
    for pat in (_CELL_BACKTICK, _CELL_BOLD):
        m = pat.search(ref or "")
        if m:
            return m.group(1)
    return None


def is_shippable(cell: str) -> bool:
    """False when the cell prescribes a bar-count exit no live unit implements."""
    return not _TIMEOUT_AXIS.search(cell or "")


def load_corpus(path: Path = CORPUS) -> dict[str, list[dict]]:
    by_leg: dict[str, list[dict]] = collections.defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            by_leg[row["leg"]].append(row)
    return dict(by_leg)


def newest_run(rows: list[dict]) -> str:
    return max(r["sweep_generated_at"] for r in rows)[:10]


def ref_is_stale(ref: str, rows: list[dict]) -> bool | None:
    """True when the corpus holds a run NEWER than any date the ref cites.

    None when it cannot be decided — no date in the ref, or no corpus rows.
    'We could not tell' is not 'it is current'.
    """
    if not rows:
        return None
    cited = _DATE.findall(ref or "")
    if not cited:
        return None
    return newest_run(rows) > max(cited)


def passing_cells(rows: list[dict], run: str) -> list[dict]:
    return [r for r in rows
            if r["sweep_generated_at"][:10] == run
            and r.get("gate_verdict") in PASS_VERDICTS]


def recheck(matrix: dict, by_leg: dict[str, list[dict]]) -> dict[str, Any]:
    stale, reverse, unshippable_claim, unmeasured = [], [], [], []
    for row in matrix.get("rows", []):
        leg = row.get("strategy")
        cell_obj = row.get("bracket_geometry")
        if not isinstance(cell_obj, dict):
            continue
        rows = by_leg.get(leg, [])
        if not rows:
            unmeasured.append(leg)
            continue
        ref, status = str(cell_obj.get("ref", "")), cell_obj.get("status")
        if ref_is_stale(ref, rows):
            stale.append({"leg": leg, "status": status,
                          "ref_newest": max(_DATE.findall(ref)),
                          "corpus_newest": newest_run(rows)})
        run = newest_run(rows)
        winners = passing_cells(rows, run)
        claimed = cell_id_from_ref(ref)
        if status == "passed_unshipped" and claimed and not is_shippable(claimed):
            hit = [r for r in rows
                   if r["cell"] == claimed and r["sweep_generated_at"][:10] == run]
            unshippable_claim.append({
                "leg": leg, "cell": claimed,
                "d_net_r_now": hit[0].get("d_net_r") if hit else None,
                "gated_on_this_run": bool(hit and hit[0].get("gate_verdict")),
            })
        if status != "shipped" and winners:
            ship = [w for w in winners if is_shippable(w["cell"])]
            if ship:
                reverse.append({
                    "leg": leg, "status": status, "run": run,
                    "cells": [{"cell": w["cell"],
                               "wf_wins_effective": w.get("wf_wins_effective"),
                               "d_net_r": w.get("d_net_r"),
                               "verdict": w.get("gate_verdict")}
                              for w in sorted(ship, key=lambda z:
                                              -(z.get("wf_wins_effective") or 0))],
                    "timeout_only_dropped": len(winners) - len(ship),
                })
    return {"stale": stale, "reverse_direction": reverse,
            "unshippable_claims": unshippable_claim, "legs_not_in_corpus": unmeasured}


def b4_outcome(by_leg: dict[str, list[dict]], matrix: dict,
               legs: list[str], before: str, after: str) -> dict[str, Any]:
    """Did the base RISE by the improvement each shipped cell claimed?"""
    mat = {r["strategy"]: r for r in matrix.get("rows", [])}
    out, ratios = [], []
    for leg in legs:
        rows = by_leg.get(leg, [])
        obs = {r["base_net_total_r"] for r in rows
               if r["sweep_generated_at"][:10] == before
               and r.get("base_net_total_r") is not None}
        nbs = {r["base_net_total_r"] for r in rows
               if r["sweep_generated_at"][:10] == after
               and r.get("base_net_total_r") is not None}
        if len(obs) != 1 or len(nbs) != 1:
            out.append({"leg": leg, "state": "ambiguous_base"})
            continue
        old_b, new_b = obs.pop(), nbs.pop()
        cell = cell_id_from_ref(str(mat[leg]["bracket_geometry"].get("ref", "")))
        claim = [r.get("d_net_r") for r in rows
                 if r["cell"] == cell and r["sweep_generated_at"][:10] == before]
        c = claim[0] if claim and claim[0] is not None else None
        rise = new_b - old_b
        ratio = (rise / c) if c else None
        if ratio is not None:
            ratios.append(ratio)
        out.append({"leg": leg, "cell": cell, "claimed_d_net_r": c,
                    "base_rise": rise, "ratio": ratio})
    return {"legs": out,
            "ratio_median": statistics.median(ratios) if ratios else None,
            "n_ratios": len(ratios)}


def _self_test() -> int:
    """Each case is one this tool must not soften."""
    # 1. Timeout axis makes a cell unshippable; a bare stop/tp cell does not.
    assert not is_shippable("tp3_sm1.5_to24") and not is_shippable("to96")
    assert is_shippable("tp3_sm2") and is_shippable("sm1.5")
    # 2. POSITIVE CONTROL for the anchor — a leg name containing "to" is not an axis.
    assert is_shippable("sm2"), "sm2 must remain shippable"
    assert _TIMEOUT_AXIS.search("sm1.5_to400"), "to400 must be recognised"
    # 3. BOTH ref spellings are read; neither is silently dropped.
    assert cell_id_from_ref("SHIPPED as cell `tp3_sm2` — PR #1") == "tp3_sm2"
    assert cell_id_from_ref("**tp2_sm3.5_to48** (tp+stop+timeout) path A") \
        == "tp2_sm3.5_to48"
    assert cell_id_from_ref("no cell named here") is None
    # 4. Staleness is THREE-state: a ref with no date is undecidable, NOT current.
    rows = [{"sweep_generated_at": "2026-08-29T00:00:00+00:00"}]
    assert ref_is_stale("run 2026-08-20", rows) is True
    assert ref_is_stale("run 2026-08-29", rows) is False
    assert ref_is_stale("no date at all", rows) is None
    assert ref_is_stale("run 2026-08-20", []) is None
    # 5. Only the two PASS verdicts count; a fail is not a winner.
    rs = [{"sweep_generated_at": "2026-08-29T0", "cell": "sm2",
           "gate_verdict": "path_b_wf_pass"},
          {"sweep_generated_at": "2026-08-29T0", "cell": "tp4",
           "gate_verdict": "is_oos_fail"}]
    assert [r["cell"] for r in passing_cells(rs, "2026-08-29")] == ["sm2"]
    # 6. A shipped leg with no winners is NOT reported as reverse-direction.
    m = {"rows": [{"strategy": "L", "bracket_geometry":
                   {"status": "shipped", "ref": "run 2026-08-29 cell `sm2`"}}]}
    assert recheck(m, {"L": rs})["reverse_direction"] == []
    # 7. A non-shipped leg WITH a shippable winner IS reported.
    m["rows"][0]["bracket_geometry"]["status"] = "honest_negative"
    assert len(recheck(m, {"L": rs})["reverse_direction"]) == 1
    # 8. A leg absent from the corpus is UNMEASURED, never graded agreeing.
    assert recheck(m, {})["legs_not_in_corpus"] == ["L"]
    print("e35_matrix_recheck self-test: OK (8 cases)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--b4-outcome", action="store_true",
                    help="measure the base rise against each shipped cell's claim")
    ap.add_argument("--before", default="2026-08-26")
    ap.add_argument("--after", default="2026-08-29")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    by_leg = load_corpus()
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))

    if args.b4_outcome:
        legs = [r["strategy"] for r in matrix["rows"]
                if isinstance(r.get("bracket_geometry"), dict)
                and r["bracket_geometry"].get("status") == "shipped"]
        res = b4_outcome(by_leg, matrix, legs, args.before, args.after)
        print(f"B4 outcome check — base {args.before} -> {args.after}, "
              f"{len(legs)} shipped leg(s)")
        for r in res["legs"]:
            if r.get("state"):
                print(f"  {r['leg']:24} {r['state']}")
                continue
            c = r["claimed_d_net_r"]
            print(f"  {r['leg']:24} {str(r['cell']):16} "
                  f"claimed {c if c is None else round(c, 4)!s:>10}  "
                  f"base rise {r['base_rise']:+9.4f}  "
                  f"ratio {'—' if r['ratio'] is None else round(r['ratio'], 2)}")
        print(f"  ratio median {res['ratio_median']:.2f} over n={res['n_ratios']}"
              if res["ratio_median"] else "  no ratio computable")
        return 0

    res = recheck(matrix, by_leg)
    print(f"STALE REFS: {len(res['stale'])} "
          f"(corpus holds a run newer than any date the ref cites)")
    for s in res["stale"]:
        print(f"  {s['leg']:24} {s['status']:30} ref {s['ref_newest']} "
              f"< corpus {s['corpus_newest']}")
    print(f"\nUNSHIPPABLE `passed_unshipped` CLAIMS: {len(res['unshippable_claims'])} "
          f"(the claimed winner carries a timeout axis)")
    for u in res["unshippable_claims"]:
        print(f"  {u['leg']:24} {u['cell']:18} d_net_r now "
              f"{u['d_net_r_now']}  gated_on_this_run={u['gated_on_this_run']}")
    print(f"\nREVERSE DIRECTION: {len(res['reverse_direction'])} leg(s) the matrix "
          f"records negative/blocked with a SHIPPABLE passing cell now")
    for r in res["reverse_direction"]:
        cells = ", ".join(f"{c['cell']}(wf{c['wf_wins_effective']}, "
                          f"+{c['d_net_r']:.1f})" for c in r["cells"])
        print(f"  {r['leg']:24} {r['status']:30} {cells}"
              + (f"   [+{r['timeout_only_dropped']} timeout-only dropped]"
                 if r["timeout_only_dropped"] else ""))
    graded = sum(1 for r in matrix.get("rows", [])
                 if isinstance(r.get("bracket_geometry"), dict))
    missing = res["legs_not_in_corpus"]
    print(f"\nCORPUS COVERAGE: {graded - len(missing)} of {graded} matrix leg(s) carry "
          f"corpus rows; {len(missing)} do NOT and are UNGRADED — not clean, not "
          f"negative, simply unmeasured. Every count above ranges over the "
          f"{graded - len(missing)} graded legs only.")
    for leg in missing:
        print(f"  ungraded: {leg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
