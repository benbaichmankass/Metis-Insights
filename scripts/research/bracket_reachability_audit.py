#!/usr/bin/env python3
"""Is a declared bracket target REACHED, or does the venue clamp get there first?

`bracket_expectation_census.py` answers *what does the config declare*. This
answers the question one level down, against measured books: **does declaring
that target change anything at all**, or is the 9.9% venue clamp
(`_TP_SENTINEL_CAP_PCT`) the operative exit either way?

Two axes, deliberately NOT collapsed into one — they are different claims with
different evidence, and merging them is how a derived label starts getting
quoted as a measurement:

  TRUNCATION (derived, median basis)   reachable | truncated | no_cap_basis
      `tp_r` vs `cap_r`, where cap_r is rescaled from a MEASURED per-leg cap_r
      at a reference stop. cap_r is inversely proportional to the stop
      (`bracket_expectation_census.cap_r_for` owns that relationship; this
      module asserts against it rather than re-deriving it). ⚠️ The measured
      cap_r is a MEDIAN over the leg's trades — ATR varies per trade, so a
      `tp_r` above the median cap_r still binds on the low-ATR trades. So
      `truncated` means *"clamped on more than half the trades"*, never
      *"never reached"*.

  COSMETIC (observed, exact)           cosmetic | not_cosmetic | no_baseline
      Byte-identical net_R and max_dd to the SAME stop with NO target declared.
      This is an observed identity, not an inference: the declaration provably
      changed nothing. `no_baseline` is *we could not look* — there is no
      same-stop no-tp cell to compare against — and is never folded into
      `not_cosmetic`.

The relationship is one-way and is asserted, not assumed: every `cosmetic` cell
must also be `truncated` (a target the clamp never reaches cannot be reached),
while many `truncated` cells are NOT cosmetic (the median is not the maximum).
A run where that implication fails means the cap basis is wrong for that leg,
and the audit says so rather than reporting the counts.

⚠️ POSITIVE CONTROL. Reporting "N cells pass the gate" is worthless without
knowing the gate passes anywhere, and reporting "0 pass" is actively misleading
when the filter could not have matched — a real incident on this corpus: a
Path-A filter returned 0 across all four legs, and Path A passes 0 of 2204 rows
corpus-wide. So the audit refuses to print a zero-pass result unless it has
first shown the verdict it filters on occurring somewhere.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.bracket_expectation_census import cap_r_for  # noqa: E402

EPS = 1e-9

# Measured clamp-imposed target (median R) per leg, at the stop it was measured
# at. Source: docs/research/bracket-expectation-construction-2026-08-23.md
# section 6.5, from the e35 base runs (full history, cap ON = live conditions).
# A leg absent here has NO cap basis and is graded `no_cap_basis`, never
# silently defaulted to a reference ATR — "we did not look" is a state.
MEASURED_CAP_R: Dict[str, float] = {
    "trend_donchian": 5.98,
    "trend_donchian_1h": 5.38,
    "trend_donchian_eth": 4.08,
    "trend_donchian_sol": 3.22,
    "trend_donchian_avax_4h": 1.48,
    "trend_donchian_eth_4h": 2.04,
    "trend_donchian_xrp_4h": 2.11,
    "trend_donchian_ada_4h": 1.57,
    "trend_donchian_sol_4h": 1.44,
}
MEASURED_CAP_AT_STOP = 2.5  # every leg above is live at atr_stop_mult 2.5


def cap_r_at(leg: str, stop_mult: Optional[float]) -> Optional[float]:
    """Rescale the leg's MEASURED cap_r to a different stop.

    cap_r = clamp_pct * entry / (stop_mult * ATR), so it scales as 1/stop_mult
    with everything else held. Rather than restate that, the factor is taken
    from the canonical `cap_r_for` so a change there cannot silently diverge
    from this module.
    """
    base = MEASURED_CAP_R.get(leg)
    if base is None or not stop_mult or stop_mult <= 0:
        return None
    ref_num = cap_r_for(MEASURED_CAP_AT_STOP, 0.02)
    ref_den = cap_r_for(stop_mult, 0.02)
    if not ref_num or not ref_den:
        return None
    return base * (ref_den / ref_num)


def truncation_state(tp_r: Optional[float], cap: Optional[float]) -> str:
    if cap is None:
        return "no_cap_basis"
    if tp_r is None:
        return "no_target_declared"
    return "reachable" if tp_r < cap else "truncated"


def classify(rows: List[dict]) -> List[dict]:
    """Attach both axes to every joint (tp + stop) cell that has a book."""
    baselines: Dict[tuple, dict] = {}
    for r in rows:
        if (r.get("tp_r") is None and r.get("stop_mult") is not None
                and r.get("timeout") is None and r.get("net_total_r") is not None):
            baselines[(r["leg"], r["stop_mult"])] = r

    out: List[dict] = []
    for r in rows:
        if r.get("tp_r") is None or r.get("stop_mult") is None:
            continue
        if r.get("net_total_r") is None or r.get("state") != "measured":
            continue
        cap = cap_r_at(r["leg"], r["stop_mult"])
        base = baselines.get((r["leg"], r["stop_mult"]))
        if base is None or r.get("timeout") is not None:
            # A timeout cell has no same-shape no-tp baseline, so its identity
            # to one would not be a statement about the TARGET.
            cosmetic = "no_baseline"
        elif (abs(r["net_total_r"] - base["net_total_r"]) < EPS
              and abs((r.get("max_drawdown_r") or 0.0)
                      - (base.get("max_drawdown_r") or 0.0)) < EPS):
            cosmetic = "cosmetic"
        else:
            cosmetic = "not_cosmetic"
        out.append({
            "leg": r["leg"], "cell": r["cell"], "tp_r": r["tp_r"],
            "stop_mult": r["stop_mult"], "timeout": r.get("timeout"),
            "cap_r": cap, "truncation": truncation_state(r["tp_r"], cap),
            "cosmetic": cosmetic, "net_total_r": r["net_total_r"],
            "max_drawdown_r": r.get("max_drawdown_r"),
            "baseline_net_total_r": base["net_total_r"] if base else None,
            "gate_verdict": r.get("gate_verdict"),
        })
    return out


def implication_violations(cells: List[dict]) -> List[dict]:
    """Every `cosmetic` cell THAT HAS A CAP BASIS must also be `truncated`.

    ⚠️ The `no_cap_basis` qualifier is load-bearing, and its absence was a real
    defect in this function's first version: it read `truncation != "truncated"`,
    which lumped *we could not look* in with *we looked and it was reachable* and
    refused on nine cells whose only property was an unmeasured leg. That is the
    exact collapse this module exists to keep apart, made in the checker for it.
    A cosmetic cell with no cap basis is UNVERIFIABLE, not a violation — see
    `cosmetic_unverifiable`.
    """
    return [c for c in cells
            if c["cosmetic"] == "cosmetic" and c["truncation"] == "reachable"]


def cosmetic_unverifiable(cells: List[dict]) -> List[dict]:
    """Cosmetic cells the implication cannot be checked against, because the leg
    has no measured cap_r. Reported separately so the count of checked cells is
    never inflated by cells nothing checked."""
    return [c for c in cells
            if c["cosmetic"] == "cosmetic" and c["truncation"] == "no_cap_basis"]


def gate_positive_control(rows: List[dict], verdict: str) -> Dict[str, Any]:
    """Does `verdict` occur ANYWHERE? A filter that cannot match is not a
    negative result."""
    graded = [r for r in rows if r.get("gate_verdict") is not None]
    hits = [r for r in graded if r["gate_verdict"] == verdict]
    return {
        "rows": len(rows), "graded": len(graded),
        "ungraded_we_did_not_look": len(rows) - len(graded),
        "verdict": verdict, "occurrences": len(hits),
        "control_ok": bool(hits),
        "verdicts_present": sorted({r["gate_verdict"] for r in graded}),
    }


def audit(rows: List[dict], verdict: str) -> Dict[str, Any]:
    cells = classify(rows)
    return {
        "population": {
            "corpus_rows": len(rows),
            "joint_cells_with_a_book": len(cells),
            "legs_with_a_cap_basis": sorted(
                {c["leg"] for c in cells if c["cap_r"] is not None}),
            "legs_without_a_cap_basis": sorted(
                {c["leg"] for c in cells if c["cap_r"] is None}),
        },
        "positive_control": gate_positive_control(rows, verdict),
        "truncation": _counts(cells, "truncation"),
        "cosmetic": _counts(cells, "cosmetic"),
        "implication_violations": implication_violations(cells),
        "cosmetic_unverifiable": cosmetic_unverifiable(cells),
        "cells": cells,
    }


def _counts(cells: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in cells:
        out[c[key]] = out.get(c[key], 0) + 1
    return dict(sorted(out.items()))


def report(a: Dict[str, Any], out=sys.stdout) -> None:
    def p(*x):
        print(*x, file=out)

    pop, pc = a["population"], a["positive_control"]
    p("POPULATION: %d corpus rows -> %d joint (tp+stop) cells with a book"
      % (pop["corpus_rows"], pop["joint_cells_with_a_book"]))
    p("  legs WITH a measured cap basis   :", ", ".join(pop["legs_with_a_cap_basis"]) or "(none)")
    p("  legs WITHOUT one (we did not look):",
      ", ".join(pop["legs_without_a_cap_basis"]) or "(none)")
    p("")
    p("POSITIVE CONTROL for gate verdict %r" % pc["verdict"])
    p("  graded %d of %d rows; %d UNGRADED (we did not look)"
      % (pc["graded"], pc["rows"], pc["ungraded_we_did_not_look"]))
    p("  verdicts actually present:", ", ".join(pc["verdicts_present"]) or "(none)")
    p("  occurrences of %r: %d -> control %s"
      % (pc["verdict"], pc["occurrences"], "OK" if pc["control_ok"] else "FAILED"))
    if not pc["control_ok"]:
        p("  ⚠️ This verdict never occurs. Any per-leg zero below would be a filter")
        p("     that could not match, NOT a measured negative. Not reporting one.")
    p("")
    p("TRUNCATION (derived, median basis):", a["truncation"])
    p("COSMETIC   (observed, exact)      :", a["cosmetic"])
    v = a["implication_violations"]
    if v:
        p("")
        p("⚠️ REFUSAL: %d cosmetic cell(s) are NOT truncated. A target the clamp" % len(v))
        p("   never reaches cannot be unreachable-yet-in-range, so the cap basis is")
        p("   wrong for these legs. Counts above are not trustworthy until fixed:")
        for c in v[:10]:
            p("     %-26s %-18s tp_r=%s cap_r=%s" % (c["leg"], c["cell"], c["tp_r"], c["cap_r"]))
    else:
        unv = a["cosmetic_unverifiable"]
        checked = a["cosmetic"].get("cosmetic", 0) - len(unv)
        p("   implication holds on the %d cosmetic cells that HAVE a cap basis"
          % checked)
        if unv:
            p("   %d further cosmetic cell(s) are UNVERIFIABLE (no measured cap_r"
              % len(unv))
            p("     for their leg) - not counted as holding: %s"
              % ", ".join(sorted({c["leg"] for c in unv})))


def load(path: Path) -> List[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def selftest() -> int:
    ok = fail = 0

    def chk(label, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print("FAIL %s: got %r want %r" % (label, got, want))

    # cap_r rescaling rides the canonical inverse-proportionality.
    chk("cap_r doubles when the stop halves",
        round(cap_r_at("trend_donchian_sol_4h", 1.25) / 1.44, 6), 2.0)
    chk("cap_r at the measured stop is the measured value",
        round(cap_r_at("trend_donchian_sol_4h", 2.5), 6), 1.44)
    chk("unknown leg has NO cap basis", cap_r_at("not_a_leg", 2.0), None)
    chk("zero stop has no cap basis", cap_r_at("trend_donchian_sol_4h", 0.0), None)

    # The two axes stay separate, and neither collapses into the other.
    chk("no cap basis is its own state", truncation_state(2.0, None), "no_cap_basis")
    chk("no target is its own state", truncation_state(None, 3.0), "no_target_declared")
    chk("tp below cap is reachable", truncation_state(1.5, 1.8), "reachable")
    chk("tp at cap is truncated", truncation_state(1.8, 1.8), "truncated")

    base = {"leg": "L", "cell": "sm2", "tp_r": None, "stop_mult": 2.0,
            "timeout": None, "net_total_r": 10.0, "max_drawdown_r": 4.0,
            "state": "measured"}
    same = dict(base, cell="tp9_sm2", tp_r=9.0)
    diff = dict(base, cell="tp1_sm2", tp_r=1.0, net_total_r=12.0)
    # A cell matching on net_R but NOT on drawdown is NOT cosmetic — one
    # coincidence on one metric must not be read as "changed nothing".
    ddonly = dict(base, cell="tp8_sm2", tp_r=8.0, max_drawdown_r=9.9)
    cells = {c["cell"]: c for c in classify([base, same, diff, ddonly])}
    chk("identical net_R AND dd -> cosmetic", cells["tp9_sm2"]["cosmetic"], "cosmetic")
    chk("different book -> not_cosmetic", cells["tp1_sm2"]["cosmetic"], "not_cosmetic")
    chk("net_R matches but dd does not -> not_cosmetic",
        cells["tp8_sm2"]["cosmetic"], "not_cosmetic")
    chk("no-tp baseline row is not itself a joint cell", "sm2" in cells, False)

    # No same-stop baseline => `no_baseline`, never `not_cosmetic`.
    orphan = classify([dict(base, cell="tp1_sm7", tp_r=1.0, stop_mult=7.0)])
    chk("missing baseline is we-did-not-look", orphan[0]["cosmetic"], "no_baseline")

    # A timeout cell has no same-shape baseline, so it cannot be graded cosmetic.
    to = classify([base, dict(base, cell="tp9_sm2_to96", tp_r=9.0, timeout=96)])
    chk("timeout cell is not graded cosmetic",
        [c["cosmetic"] for c in to if c["cell"].endswith("to96")], ["no_baseline"])

    # The implication is asserted, and a violation is detectable.
    bad = [{"leg": "L", "cell": "x", "tp_r": 1.0, "stop_mult": 2.0, "cap_r": 5.0,
            "truncation": "reachable", "cosmetic": "cosmetic"}]
    chk("cosmetic-but-reachable is a violation", len(implication_violations(bad)), 1)
    chk("cosmetic-and-truncated is not",
        len(implication_violations([dict(bad[0], truncation="truncated")])), 0)
    # The regression that fired on the real corpus: a cosmetic cell on a leg
    # with NO cap basis is unverifiable, NOT a violation. Collapsing the two
    # refused on nine cells whose only property was an unmeasured leg.
    nocap = [dict(bad[0], truncation="no_cap_basis", cap_r=None)]
    chk("cosmetic with no cap basis is NOT a violation",
        len(implication_violations(nocap)), 0)
    chk("cosmetic with no cap basis IS reported unverifiable",
        len(cosmetic_unverifiable(nocap)), 1)
    chk("a genuine violation is not counted as unverifiable",
        len(cosmetic_unverifiable(bad)), 0)

    # The positive control is what stops a zero reading as a measured negative.
    rows = [{"gate_verdict": None}, {"gate_verdict": "is_oos_fail"}]
    chk("absent verdict fails the control",
        gate_positive_control(rows, "path_b_wf_pass")["control_ok"], False)
    chk("present verdict passes it",
        gate_positive_control(rows, "is_oos_fail")["control_ok"], True)
    chk("ungraded rows are counted, not dropped",
        gate_positive_control(rows, "is_oos_fail")["ungraded_we_did_not_look"], 1)

    print("selftest: %d pass, %d fail" % (ok, fail))
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="docs/research/e35-bracket-corpus.jsonl")
    ap.add_argument("--verdict", default="path_b_wf_pass",
                    help="gate verdict to positive-control before reporting on it")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    res = audit(load(Path(a.corpus)), a.verdict)
    if a.json:
        json.dump(res, sys.stdout, indent=1, default=str)
        print()
    else:
        report(res)
    return 1 if res["implication_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
