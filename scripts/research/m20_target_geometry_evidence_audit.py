#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit answering "does the M20 walk-forward
# evidence base actually support a target-geometry decision?", run by a session
# before proposing (or refusing) a Tier-3 tp change. Not a scheduled job.
"""Can the existing M20 walk-forward evidence license a target-geometry change?

MI-278 U23. The object's `done_condition` requires *"at least one lever reaches a
Tier-3 proposal with IS/OOS + walk-forward evidence behind it"*, and MI-278 U2
established that the only mechanism with enough attributed mass is the
**take-profit** — no exit lever is cutting winners short (the lever family is 3
of 49 winners, and the two `giveback_stop` fires BANKED +$2,471.70).

So the question is narrow and answerable: does
`docs/research/m20-fold-dispersion-arms-consolidated.jsonl` — the M20
walk-forward arm set, which carries `tp_geometry` — support a decision about
target geometry?

THE ANSWER IS NO, AND THE REASON IS STRUCTURAL RATHER THAN WEAK EFFECT SIZES.

`tp_geometry` is PERFECTLY CONFOUNDED with `family` and with `block_unit`:

    live_parity_uncapped  <=>  family == scalp                <=>  per_leg
    live_parity_capped    <=>  family in {donchian, pullback} <=>  family_pooled

No family carries both geometries, so the within-family comparison does not
exist in the dataset. The headline that invites the mistake is real and
uninterpretable: uncapped arms are 75% `candidate` against capped arms' 30%,
which is equally a scalp-vs-others comparison and a per-leg-vs-pooled one.

AND THERE IS NO DOSE EITHER: every one of the 218 capped arms carries
`tp_cap_pct == 0.099`, the venue clamp that
`docs/research/bracket-target-reachability-2026-08-24.md` calls "a hard target
nobody chose". One value is not a dose-response, so the fallback of arguing the
geometry from its magnitude is also unavailable.

A THIRD CONFOUND rides along and is reported rather than buried: `candidate`
arms have MORE DATA than `honest_negative` ones (median `usable_folds` 23 vs 16,
median `n_oos` 288 vs 222), so verdict tracks how much the arm got to see.

⚠️ THIS DOES NOT SAY THE GEOMETRY DOES NOT MATTER. It says this evidence base
cannot tell you, which is a different and more useful statement — and it is the
reason the proposal in the accompanying memo asks for two specific arms rather
than a parameter change.

Run: python3 scripts/research/m20_target_geometry_evidence_audit.py --self-test
     python3 scripts/research/m20_target_geometry_evidence_audit.py \
         --arms docs/research/m20-fold-dispersion-arms-consolidated.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics as st
from typing import Any

# Never collapsed. `not_present` is "this dataset has no such arm", which is NOT
# the same as "the arms are present and indistinguishable" (`confounded`) and NOT
# the same as a real comparison that came out null (`separable_null`).
SEPARABILITY_STATES = (
    "separable",              # both levels of the factor appear within some stratum
    "confounded",             # both levels appear, but never inside one stratum
    "single_level",           # only one level exists at all — no contrast to draw
    "not_present",            # the factor is absent from the rows
)

# What a dose needs: more than one level of the continuous knob.
DOSE_STATES = ("dose_present", "single_value_no_dose", "not_present")


def load_arms(path: str) -> list[dict]:
    with open(path) as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def separability(rows: list[dict], factor: str, stratum: str) -> dict:
    """Can `factor` be compared while holding `stratum` fixed?

    Returns a state plus the evidence, never a bare boolean — "we cannot compare
    these" and "we compared them and found nothing" are different findings and a
    boolean would erase the difference.
    """
    if not rows or any(factor not in r for r in rows):
        return {"state": "not_present", "factor": factor, "stratum": stratum,
                "levels": [], "strata_with_both": []}
    levels = sorted({str(r[factor]) for r in rows})
    if len(levels) < 2:
        return {"state": "single_level", "factor": factor, "stratum": stratum,
                "levels": levels, "strata_with_both": []}
    by_stratum: dict[str, set] = collections.defaultdict(set)
    for r in rows:
        by_stratum[str(r.get(stratum))].add(str(r[factor]))
    both = sorted(k for k, v in by_stratum.items() if len(v) > 1)
    return {
        "state": "separable" if both else "confounded",
        "factor": factor,
        "stratum": stratum,
        "levels": levels,
        "strata_with_both": both,
        "level_to_strata": {lv: sorted({str(r.get(stratum)) for r in rows
                                        if str(r[factor]) == lv}) for lv in levels},
    }


def dose(rows: list[dict], knob: str) -> dict:
    if not rows or any(knob not in r for r in rows):
        return {"state": "not_present", "knob": knob, "values": {}}
    c = collections.Counter(r[knob] for r in rows)
    return {
        "state": "dose_present" if len(c) > 1 else "single_value_no_dose",
        "knob": knob,
        "values": {str(k): v for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))},
    }


def verdict_rates(rows: list[dict], by: str) -> dict:
    out: dict[str, Any] = {}
    for key in sorted({str(r.get(by)) for r in rows}):
        sub = [r for r in rows if str(r.get(by)) == key]
        cand = sum(1 for r in sub if r.get("verdict") == "candidate")
        out[key] = {"n": len(sub), "candidate": cand,
                    "candidate_rate": round(cand / len(sub), 4) if sub else None}
    return out


def data_volume_by_verdict(rows: list[dict]) -> dict:
    """⚠️ The third confound: does `verdict` track how much the arm got to see?"""
    out: dict[str, Any] = {}
    for v in sorted({str(r.get("verdict")) for r in rows}):
        sub = [r for r in rows if str(r.get("verdict")) == v]
        folds = [r["usable_folds"] for r in sub if r.get("usable_folds") is not None]
        noos = [r["n_oos"] for r in sub if r.get("n_oos") is not None]
        out[v] = {
            "n": len(sub),
            "usable_folds_median": st.median(folds) if folds else None,
            "n_oos_median": st.median(noos) if noos else None,
        }
    return out


def audit(rows: list[dict]) -> dict:
    sep_family = separability(rows, "tp_geometry", "family")
    sep_block = separability(rows, "tp_geometry", "block_unit")
    d = dose(rows, "tp_cap_pct")
    gradeable = (sep_family["state"] == "separable"
                 and sep_block["state"] == "separable")
    return {
        "population": {
            "arms": len(rows),
            "legs": len({r.get("leg") for r in rows}),
            "families": sorted({str(r.get("family")) for r in rows}),
        },
        "geometry_vs_family": sep_family,
        "geometry_vs_block_unit": sep_block,
        "cap_dose": d,
        "candidate_rate_by_geometry": verdict_rates(rows, "tp_geometry"),
        "candidate_rate_by_family": verdict_rates(rows, "family"),
        "data_volume_by_verdict": data_volume_by_verdict(rows),
        # The whole point. A verdict, not a number to be quoted out of context.
        "licenses_a_target_geometry_decision": gradeable,
        "why": ("separable on both strata" if gradeable else
                "tp_geometry cannot be held apart from family and/or block_unit "
                "in this dataset, so a geometry contrast is also a family and "
                "blocking contrast"),
    }


# --------------------------------------------------------------------------
def _r(geom, fam, blk, verdict="candidate", cap=0.099, folds=20, noos=300):
    return {"tp_geometry": geom, "family": fam, "block_unit": blk,
            "verdict": verdict, "tp_cap_pct": cap, "usable_folds": folds,
            "n_oos": noos, "leg": fam + "_leg", "mean_auc": 0.6}


def self_test() -> int:
    ok = fail = 0

    def ck(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
            print("  PASS %s" % name)
        else:
            fail += 1
            print("  FAIL %s\n    got  %r\n    want %r" % (name, got, want))

    print("m20-target-geometry-evidence-audit self-test")

    conf = [_r("uncapped", "scalp", "per_leg"), _r("capped", "pullback", "pooled")]
    ck("1 a perfectly confounded factor is `confounded`, never `separable`",
       separability(conf, "tp_geometry", "family")["state"], "confounded")
    ck("2 ...and it names that no stratum holds both",
       separability(conf, "tp_geometry", "family")["strata_with_both"], [])
    ck("3 CONTROL: add one arm inside an existing family and it becomes separable",
       separability(conf + [_r("uncapped", "pullback", "pooled")],
                    "tp_geometry", "family")["state"], "separable")
    ck("4 ...and the stratum that rescues it is NAMED",
       separability(conf + [_r("uncapped", "pullback", "pooled")],
                    "tp_geometry", "family")["strata_with_both"], ["pullback"])
    ck("5 one level of the factor is `single_level`, NOT `confounded`",
       separability([_r("capped", "a", "x"), _r("capped", "b", "x")],
                    "tp_geometry", "family")["state"], "single_level")
    ck("6 an absent factor is `not_present`, NOT `single_level`",
       separability([{"family": "a"}], "tp_geometry", "family")["state"], "not_present")
    ck("7 empty input is `not_present`, never a clean `separable`",
       separability([], "tp_geometry", "family")["state"], "not_present")
    ck("8 the four separability states are exactly the declared vocabulary",
       sorted(SEPARABILITY_STATES),
       sorted(["separable", "confounded", "single_level", "not_present"]))

    ck("9 one cap value is `single_value_no_dose`, NOT a dose",
       dose(conf, "tp_cap_pct")["state"], "single_value_no_dose")
    ck("10 two cap values IS a dose",
       dose(conf + [_r("capped", "d", "pooled", cap=0.05)], "tp_cap_pct")["state"],
       "dose_present")
    ck("11 an absent knob is `not_present`, not `single_value_no_dose`",
       dose([{"family": "a"}], "tp_cap_pct")["state"], "not_present")

    ck("12 a confounded dataset does NOT license a decision",
       audit(conf)["licenses_a_target_geometry_decision"], False)
    ck("13 CONTROL: a separable one DOES",
       audit([_r("uncapped", "p", "pooled"), _r("capped", "p", "pooled"),
              _r("uncapped", "p", "per_leg"), _r("capped", "p", "per_leg")]
             )["licenses_a_target_geometry_decision"], True)
    ck("14 a refusal always carries its reason",
       "cannot be held apart" in audit(conf)["why"], True)

    rates = verdict_rates([_r("uncapped", "scalp", "per_leg"),
                           _r("capped", "pullback", "pooled", verdict="honest_negative")],
                          "tp_geometry")
    ck("15 candidate rates are computed per level",
       (rates["uncapped"]["candidate_rate"], rates["capped"]["candidate_rate"]),
       (1.0, 0.0))
    ck("16 ...and the rate ships WITH its denominator, never alone",
       "n" in rates["uncapped"], True)

    vol = data_volume_by_verdict([_r("uncapped", "s", "p", folds=20, noos=300),
                                  _r("capped", "d", "q", verdict="honest_negative",
                                     folds=10, noos=100)])
    ck("17 the data-volume confound is reported per verdict",
       (vol["candidate"]["usable_folds_median"],
        vol["honest_negative"]["usable_folds_median"]), (20, 10))

    ck("18 the population is always stated", "arms" in audit(conf)["population"], True)
    ck("19 an empty arm set does not read as a licensed decision",
       audit([])["licenses_a_target_geometry_decision"], False)

    print("\nself-test: PASS %d · FAIL %d" % (ok, fail))
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--arms", help="m20-fold-dispersion-arms-consolidated.jsonl")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.arms:
        ap.error("pass --arms <file.jsonl> or --self-test")
    print(json.dumps(audit(load_arms(a.arms)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
