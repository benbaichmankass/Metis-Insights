#!/usr/bin/env python3
# wiring: manual-only — a live READ a session runs to answer "does the
# performance endpoint still publish an expectancyR that contradicts its own
# PnL?". It is the runnable form of PB-20260906's resolution criterion (1), so
# a later session can check whether the Tier-2 fix landed instead of
# re-deriving the whole decomposition. Not a scheduled job.
"""Does `/api/bot/performance` publish an expectancyR that agrees with its own PnL?

MI-278 U25, for `PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG`
and its sibling `PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN`.

⚠️ THIS CANNOT CLOSE EITHER ROW. Their fix is in
`src/web/api/routers/performance.py` — Tier-2. What this does is turn criterion
(1) — *"publishes an expectancyR that AGREES IN SIGN with its own totalPnl and
profitFactor, verified by reading the LIVE endpoint, not a test"* — into
something runnable, so the next session checks in one command.

WHAT IT FOUND, and it is not what the row implies:

  window  n     totalPnl    expectancyR  profitFactor  contaminated  agree
  7d      5     -12.4015    -0.6904      0.0            0            YES
  30d     41    -14.5552    +0.1113      0.8251        11            NO
  90d     429   -81.9330    -0.3197      0.6958        14            YES
  365d    429   -81.9330    -0.3197      0.6958        14            YES

⚠️ **THE DEFECT IS SHARE-DRIVEN, NOT COUNT-DRIVEN, AND THE ROW'S FRAMING BY
COUNT WOULD MISLEAD.** 90d carries MORE contaminated rows than 30d (14 vs 11)
and its sign AGREES. What separates them is the SHARE — 11/41 = 26.8% against
14/429 = 3.3%. So a reader told "124 contaminated rows journal-wide" would
reasonably expect the long windows to be the worst; they are the safest, and
the damage concentrates in the SHORT window an operator is most likely to open.

⚠️ **`rCoverage` READS 1.0 BESIDE THE WRONG NUMBER.** The endpoint asserts full
R coverage on the same payload, so nothing on the surface warns the reader that
26.8% of the rows carrying that R are graded `contaminated` by the endpoint's
own `rProvenance` block. The provenance is published and the aggregate ignores
it — which is what the row says, observed live rather than quoted.

⚠️ **AGREEMENT IS NOT A PASS.** 7d agrees while carrying zero contaminated rows
and `profitFactor: 0.0`; 90d agrees because the contamination is diluted. A
window can agree and still be computed the broken way, so this probe reports
`agrees` as an OBSERVATION and never as evidence the defect is fixed — that is
what `contaminated_share` beside it is for.

Run: python3 scripts/research/performance_sign_agreement.py --self-test
     python3 scripts/research/performance_sign_agreement.py --payload <saved.json>
"""
from __future__ import annotations

import argparse
import json
from typing import Any

# Never collapsed. `agrees` and `disagrees` are verdicts about a payload we
# could read; the other two are about not being able to grade it at all.
AGREEMENT_STATES = (
    "agrees",              # expectancyR's sign matches totalPnl's
    "disagrees",           # the defect
    "not_gradeable_zero",  # a term is exactly 0.0 — no sign to compare
    "not_gradeable_absent",  # a term is missing — we could not look
)


def _sign(x: Any) -> int | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return 1 if v > 0 else (-1 if v < 0 else 0)


def grade(payload: dict) -> dict:
    """Grade ONE window payload. Reports every term it used, never a bare verdict."""
    pnl = payload.get("totalPnl")
    er = payload.get("expectancyR")
    pf = payload.get("profitFactor")
    exp = payload.get("expectancy")
    s_pnl, s_er = _sign(pnl), _sign(er)

    if s_pnl is None or s_er is None:
        state = "not_gradeable_absent"
    elif s_pnl == 0 or s_er == 0:
        state = "not_gradeable_zero"
    elif s_pnl == s_er:
        state = "agrees"
    else:
        state = "disagrees"

    rp = payload.get("rProvenance") or {}
    n = payload.get("totalTrades")
    contaminated = rp.get("contaminated")
    share = None
    if isinstance(contaminated, (int, float)) and isinstance(n, (int, float)) and n:
        share = round(float(contaminated) / float(n), 4)

    return {
        "window": payload.get("window"),
        "state": state,
        "totalTrades": n,
        "totalPnl": pnl,
        "expectancy": exp,
        "expectancyR": er,
        "profitFactor": pf,
        # Published on the same payload, and the aggregate ignores it.
        "rCoverage": payload.get("rCoverage"),
        "contaminated": contaminated,
        # THE load-bearing term: the flip tracks this, not the raw count.
        "contaminated_share": share,
        # ⚠️ Not a pass. See the module docstring.
        "agreement_is_not_evidence_of_a_fix": True,
    }


def summarise(graded: list[dict]) -> dict:
    dis = [g for g in graded if g["state"] == "disagrees"]
    gradeable = [g for g in graded if g["state"] in ("agrees", "disagrees")]
    return {
        "windows_read": len(graded),
        "windows_gradeable": len(gradeable),
        "windows_disagreeing": len(dis),
        "disagreeing_windows": [g["window"] for g in dis],
        # A count-vs-share contrast, because the row frames it by count.
        "count_predicts_the_flip": _count_predicts(gradeable),
        "verdict": ("defect_live_on_at_least_one_window" if dis else
                    "no_disagreement_on_the_windows_read"),
        "caveat": ("windows_read is NOT the set of windows that exist; a window "
                   "that agrees may still be computed the broken way"),
    }


def _count_predicts(graded: list[dict]) -> bool | None:
    """Does a HIGHER contaminated COUNT go with disagreement? Reported, not assumed.

    Returns None when there is nothing to compare — never False, which would
    read as 'we checked and it does not', a different claim.
    """
    dis = [g["contaminated"] for g in graded
           if g["state"] == "disagrees" and g["contaminated"] is not None]
    agr = [g["contaminated"] for g in graded
           if g["state"] == "agrees" and g["contaminated"] is not None]
    if not dis or not agr:
        return None
    return min(dis) > max(agr)


# --------------------------------------------------------------------------
def _p(pnl=None, er=None, n=10, contaminated=0, pf=None, window="30d"):
    return {"window": window, "totalPnl": pnl, "expectancyR": er,
            "profitFactor": pf, "totalTrades": n,
            "rProvenance": {"contaminated": contaminated}}


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

    print("performance-sign-agreement self-test")

    ck("1 negative pnl with positive R is the DEFECT",
       grade(_p(pnl=-14.5, er=0.11))["state"], "disagrees")
    ck("2 both negative agrees", grade(_p(pnl=-12.4, er=-0.69))["state"], "agrees")
    ck("3 both positive agrees", grade(_p(pnl=5.0, er=0.2))["state"], "agrees")
    ck("4 positive pnl with negative R is ALSO the defect",
       grade(_p(pnl=5.0, er=-0.2))["state"], "disagrees")
    ck("5 a zero term is not_gradeable_zero, NOT agrees",
       grade(_p(pnl=0.0, er=0.11))["state"], "not_gradeable_zero")
    ck("6 an absent term is not_gradeable_absent, NOT agrees",
       grade(_p(pnl=None, er=0.11))["state"], "not_gradeable_absent")
    ck("7 CONTROL: absent and zero are DIFFERENT states",
       grade(_p(pnl=None, er=1.0))["state"] == grade(_p(pnl=0.0, er=1.0))["state"], False)
    ck("8 a non-numeric term grades absent, never crashes",
       grade(_p(pnl="oops", er=0.11))["state"], "not_gradeable_absent")
    ck("9 the four states are exactly the declared vocabulary",
       sorted(AGREEMENT_STATES),
       sorted(["agrees", "disagrees", "not_gradeable_zero", "not_gradeable_absent"]))

    g = grade(_p(pnl=-14.5, er=0.11, n=41, contaminated=11))
    ck("10 the contaminated SHARE is computed", g["contaminated_share"], round(11 / 41, 4))
    ck("11 a zero denominator yields None, never a 0.0 share",
       grade(_p(pnl=-1.0, er=1.0, n=0, contaminated=3))["contaminated_share"], None)
    ck("12 the verdict ships with the terms it used", g["totalPnl"], -14.5)
    ck("13 agreement is explicitly flagged as not-a-pass",
       grade(_p(pnl=-1.0, er=-1.0))["agreement_is_not_evidence_of_a_fix"], True)

    # the live shape: 30d disagrees on FEWER contaminated rows than 90d, which agrees
    live = [grade(_p(pnl=-14.5552, er=0.1113, n=41, contaminated=11, window="30d")),
            grade(_p(pnl=-81.933, er=-0.3197, n=429, contaminated=14, window="90d"))]
    s = summarise(live)
    ck("14 the disagreeing window is NAMED", s["disagreeing_windows"], ["30d"])
    ck("15 the verdict says the defect is live",
       s["verdict"], "defect_live_on_at_least_one_window")
    ck("16 CONTROL: a HIGHER contaminated COUNT does NOT predict the flip",
       s["count_predicts_the_flip"], False)
    ck("17 ...while the SHARE does (26.8% disagrees vs 3.3% agrees)",
       live[0]["contaminated_share"] > live[1]["contaminated_share"], True)
    ck("18 with nothing to compare, count_predicts is None, never False",
       summarise([grade(_p(pnl=-1.0, er=-1.0, contaminated=1))])["count_predicts_the_flip"],
       None)
    ck("19 all-agreeing reads as no-disagreement, not as a fix",
       summarise([grade(_p(pnl=-1.0, er=-1.0))])["verdict"],
       "no_disagreement_on_the_windows_read")
    ck("20 the summary carries its own caveat",
       "may still be computed the broken way" in summarise(live)["caveat"], True)
    ck("21 ungradeable windows are excluded from the gradeable count",
       summarise([grade(_p(pnl=None, er=1.0))])["windows_gradeable"], 0)

    print("\nself-test: PASS %d · FAIL %d" % (ok, fail))
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--payload", action="append", default=[],
                    help="a saved /api/bot/performance JSON; repeatable, one per window")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.payload:
        ap.error("pass --payload <file.json> (repeatable) or --self-test")
    graded = []
    for path in a.payload:
        with open(path) as fh:
            graded.append(grade(json.load(fh)))
    print(json.dumps({"windows": graded, "summary": summarise(graded)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
