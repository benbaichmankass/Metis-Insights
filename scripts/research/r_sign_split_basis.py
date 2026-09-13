#!/usr/bin/env python3
# wiring: manual-only — a one-shot adjudication of a SPECIFIC open claim
#   (PB-20260906) against a live /api/bot/performance payload. Scheduling it
#   would re-ask a question whose answer is a property of one window.
"""MI-278 U36 — is expectancyR disagreeing with totalPnl a DEFECT, or risk normalisation working?

THE CLAIM THIS ADJUDICATES
--------------------------
`PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG`
(severity `high`, open) makes two statements and this module separates them,
because they are not the same and only one survives:

  (1) THE MECHANISM — `trades.stop_loss` holds the CURRENT trailed stop, so
      `|entry - stop|` collapses on a trade trailed to breakeven and `pnl/risk`
      explodes. Rows so affected grade `contaminated`.
  (2) THE CRITERION — resolution requires that the endpoint "publishes an
      expectancyR that AGREES IN SIGN with its own totalPnl and profitFactor".

⚠️ (2) IS NOT A SOUND CRITERION AND (1) CAN BE TESTED DIRECTLY
---------------------------------------------------------------
**R is pnl divided by the trade's own risk.** Its entire purpose is to stop a
large-risk trade and a small-risk trade being compared on dollars. So a book
whose WINNING legs are sized smaller than its LOSING legs will show positive
mean R and negative dollars — and that is the metric WORKING, not failing.
Requiring the two to agree in sign would require R to stop being
risk-normalised, i.e. the fix would break the number.

Whether (1) is operating is not a matter of opinion: the endpoint publishes
`rBasis`, which counts WHICH risk each published R was actually divided by. If
`storedStop` is 0 then no published R touched the trailed stop and the named
mechanism CANNOT be responsible for anything in that window — whatever
`rProvenance.contaminated` says, because that field grades the STORED STOP and
not the basis used. The route's own comment states this distinction; this
module is the reader that applies it.

WHAT IT DOES
------------
Given a `/api/bot/performance` payload it reports three orthogonal facts, never
collapsed into one verdict:

  * `sign_state`      — do expectancyR and totalPnl actually disagree?
  * `mechanism_state` — CAN the trailed-stop mechanism be operating here?
  * `explanation`     — is a disagreement accounted for by risk-size
                        heterogeneity across legs, or is it UNEXPLAINED?

⚠️ `$/R` IS A RATIO OF LEG AGGREGATES, NOT A PER-TRADE RISK, and it is
ungradeable when a leg's PnL and R have OPPOSITE signs — a leg holding both
outcomes has no single dollars-per-R and the module says so rather than
printing a negative number that looks like a risk.

WHAT IT DOES NOT DO
-------------------
It changes nothing. `/api/bot/performance` is `src/web/` — **Tier-2** — and the
three dashboard routes MI-278 U12 named are deliberately untouched. It proposes
no parameter and grades no strategy.

Usage:
  python3 scripts/research/r_sign_split_basis.py --self-test
  python3 scripts/research/r_sign_split_basis.py --payload perf.json
  python3 scripts/research/r_sign_split_basis.py --payload perf.json --json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

#: Do the headline R and the headline dollars point the same way?
SIGN_STATES = ("agree", "disagree", "not_computable")

#: CAN the trailed-stop mechanism `PB-20260906` names be operating in this
#: window? Read from `rBasis`, which counts what each R was DIVIDED BY —
#: never from `rProvenance.contaminated`, which grades the stored stop and is a
#: different question the route's own comment is explicit about.
MECHANISM_STATES = (
    "can_operate",       # at least one published R used the stored stop
    "cannot_operate",    # every published R used a declared initial risk
    "not_gradeable",     # no rBasis block — we could not look
)

#: The four states `src/runtime/r_provenance.py::classify_r` can return, and the
#: payload key each is published under. ALL FOUR are reported, never just the
#: alarming one: this module's whole thesis is that `contaminated` does NOT
#: decide the verdict, and a reader can only see that if they can see the rest
#: of the distribution beside it. `collapsed-state-guard` caught the earlier
#: version naming only `contaminated`, and it was right to.
R_PROVENANCE_STATES = {
    "contaminated": "contaminated",
    "confirmed_initial": "confirmedInitial",
    "unverified": "unverified",
    "no_basis": "noBasis",
}

#: Is a disagreement accounted for?
EXPLANATION_STATES = (
    "risk_size_heterogeneity",  # winners are sized smaller than losers, cleanly
    "partial",                  # the leg ranking overlaps — direction only
    "unexplained",              # no separation; the disagreement needs a cause
    "not_applicable",           # the signs agree, so nothing to explain
    "not_computable",           # too few gradeable legs
)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sign_state(payload: dict) -> tuple[str, dict]:
    """Do expectancyR and totalPnl disagree? profitFactor corroborates."""
    er, pnl = _f(payload.get("expectancyR")), _f(payload.get("totalPnl"))
    pf = _f(payload.get("profitFactor"))
    if er is None or pnl is None or er == 0 or pnl == 0:
        return "not_computable", {"expectancyR": er, "totalPnl": pnl,
                                  "profitFactor": pf}
    state = "agree" if er * pnl > 0 else "disagree"
    return state, {"expectancyR": er, "totalPnl": pnl, "profitFactor": pf,
                   # `None`, never a bare bool, when profitFactor is absent:
                   # a missing corroborator is not a disagreeing one.
                   "profit_factor_corroborates": (
                       None if pf is None else ((pf < 1.0) == (pnl < 0)))}


def mechanism_state(payload: dict) -> tuple[str, dict]:
    """CAN the trailed-stop mechanism be operating? Read `rBasis`, not `contaminated`."""
    rb = payload.get("rBasis")
    if not isinstance(rb, dict) or not rb:
        return "not_gradeable", {"rBasis": None}
    stored = int(rb.get("storedStop") or 0)
    rp = payload.get("rProvenance") or {}
    detail = {
        "rBasis": rb,
        # Reported BESIDE the verdict and never used AS the verdict — these
        # grade the STORED STOP, which a declared-basis R never consults. All
        # four states are surfaced, with `None` (never 0) for one the payload
        # does not carry, so an absent count cannot read as a measured zero.
        "r_provenance": {
            state: (rp[key] if key in rp else None)
            for state, key in R_PROVENANCE_STATES.items()
        },
        # `None`, never False, when the block is absent: we could not look.
        "r_provenance_read": (True if rp else None),
    }
    return ("can_operate" if stored > 0 else "cannot_operate"), detail


def leg_risk_table(payload: dict) -> list[dict]:
    """Dollars per R for each leg. `None` when the leg's PnL and R disagree."""
    out = []
    for v in payload.get("perStrategy") or []:
        p, r = _f(v.get("totalPnl")), _f(v.get("totalR"))
        gradeable = (p is not None and r not in (None, 0) and p * r > 0)
        out.append({
            "leg": v.get("name"), "trades": v.get("trades"),
            "total_pnl": p, "total_r": r,
            "expectancy_r": _f(v.get("expectancyR")),
            # A leg holding both winning and losing outcomes has no single
            # dollars-per-R; a negative ratio there would read as a risk.
            "dollars_per_r": (abs(p / r) if gradeable else None),
            "dollars_per_r_state": ("measured" if gradeable
                                    else "ungradeable_mixed_sign"),
            "r_basis": v.get("rBasis"),
        })
    return out


def explanation(payload: dict, sign: str) -> tuple[str, dict]:
    """Is the disagreement accounted for by winners being sized smaller?

    THRESHOLD-FREE. It asks only whether the positive-R legs' dollars-per-R and
    the negative-R legs' OVERLAP — a separation statement, not a magnitude one,
    so it carries no tuned constant.
    """
    if sign == "agree":
        return "not_applicable", {}
    table = [t for t in leg_risk_table(payload)
             if t["dollars_per_r"] is not None and t["total_r"] is not None]
    win = [t for t in table if t["total_r"] > 0]
    lose = [t for t in table if t["total_r"] < 0]
    detail = {
        "gradeable_legs": len(table),
        "ungradeable_legs": sum(
            1 for t in leg_risk_table(payload) if t["dollars_per_r"] is None),
        "winner_dollars_per_r": sorted(t["dollars_per_r"] for t in win),
        "loser_dollars_per_r": sorted(t["dollars_per_r"] for t in lose),
    }
    if not win or not lose:
        return "not_computable", detail
    wmax, lmin = max(detail["winner_dollars_per_r"]), min(detail["loser_dollars_per_r"])
    allv = detail["winner_dollars_per_r"] + detail["loser_dollars_per_r"]
    detail["spread"] = round(max(allv) / min(allv), 4) if min(allv) > 0 else None
    detail["separated"] = wmax < lmin
    if wmax < lmin:
        detail["gap"] = round(lmin - wmax, 4)
        return "risk_size_heterogeneity", detail
    # Direction without separation: the winners' MEAN is smaller but the ranges
    # overlap, so the effect is present and does not account for the whole gap.
    wmean = sum(detail["winner_dollars_per_r"]) / len(win)
    lmean = sum(detail["loser_dollars_per_r"]) / len(lose)
    detail["winner_mean"], detail["loser_mean"] = round(wmean, 4), round(lmean, 4)
    return ("partial" if wmean < lmean else "unexplained"), detail


def report(payload: dict) -> dict:
    s, s_detail = sign_state(payload)
    m, m_detail = mechanism_state(payload)
    e, e_detail = explanation(payload, s)
    return {
        "window": payload.get("window"),
        "since": payload.get("since"),
        "n_trades": payload.get("totalTrades"),
        "sign_state": s, "sign_detail": s_detail,
        "mechanism_state": m, "mechanism_detail": m_detail,
        "explanation": e, "explanation_detail": e_detail,
        "legs": leg_risk_table(payload),
        # The one sentence a reader needs, derived rather than asserted.
        "verdict": _verdict(s, m, e),
    }


def _verdict(s: str, m: str, e: str) -> str:
    if s == "not_computable":
        return ("the headline is not gradeable — expectancyR or totalPnl is "
                "absent or zero")
    if s == "agree":
        return "expectancyR and totalPnl agree in sign; PB-20260906's criterion is met"
    if m == "can_operate":
        return ("the signs disagree AND at least one published R used the "
                "STORED stop — PB-20260906's mechanism is live and is a "
                "candidate cause")
    if m == "not_gradeable":
        return ("the signs disagree and rBasis is absent — we could not "
                "establish whether the mechanism is operating")
    if e == "risk_size_heterogeneity":
        return ("the signs disagree, NO published R used the stored stop, and "
                "every profitable leg carries a smaller dollars-per-R than "
                "every losing one — this is risk normalisation working, not "
                "contamination")
    if e == "partial":
        return ("the signs disagree, no published R used the stored stop, and "
                "winners are sized smaller ON AVERAGE but the ranges overlap — "
                "the effect is present and does not account for the whole gap")
    return ("the signs disagree, no published R used the stored stop, and risk "
            "size does not account for it — the cause is UNESTABLISHED")


def render(v: dict) -> str:
    L = [f"r-sign-split-basis: window={v['window']} n={v['n_trades']} since={v['since']}",
         ""]
    d = v["sign_detail"]
    L.append(f"  sign_state      : {v['sign_state']}  "
             f"(expectancyR {d.get('expectancyR')} vs totalPnl {d.get('totalPnl')}, "
             f"profitFactor {d.get('profitFactor')})")
    md = v["mechanism_detail"]
    L.append(f"  mechanism_state : {v['mechanism_state']}  rBasis={md.get('rBasis')}")
    L.append(f"                    rProvenance={md.get('r_provenance')}  "
             f"(read={md.get('r_provenance_read')})")
    L.append( "                    ^ ALL FOUR grade the STORED STOP, NOT the "
              "basis used — none of them drives the verdict above")
    L.append(f"  explanation     : {v['explanation']}  {json.dumps(v['explanation_detail'])}")
    L += ["", f"  {'leg':<24} {'n':>3} {'totalPnl':>10} {'totalR':>9} "
              f"{'expR':>8} {'$/R':>8}"]
    for t in sorted(v["legs"], key=lambda x: (x["total_r"] if x["total_r"] is not None else 0)):
        dpr = ("n/a (mixed sign)" if t["dollars_per_r"] is None
               else f"{t['dollars_per_r']:8.3f}")
        L.append(f"  {str(t['leg']):<24} {str(t['trades']):>3} "
                 f"{t['total_pnl']:>10.3f} {t['total_r']:>9.3f} "
                 f"{(t['expectancy_r'] if t['expectancy_r'] is not None else 0):>8.3f} {dpr}")
    L += ["", f"  VERDICT: {v['verdict']}", "",
          "  ⚠️ `$/R` is a ratio of LEG AGGREGATES, not a per-trade risk, and it is",
          "     ungradeable when a leg holds both winning and losing outcomes.",
          "  ⚠️ THIS CHANGES NOTHING. /api/bot/performance is src/web/ — Tier-2 — and is",
          "     one of the three dashboard routes MI-278 U12 named as untouchable here."]
    return "\n".join(L)


def _self_test() -> int:
    fails: list[str] = []

    def ok(c, label):
        if not c:
            fails.append(label)
        print(f"  {'ok ' if c else 'FAIL'} {label}")

    def leg(name, pnl, r, n=5):
        return {"name": name, "trades": n, "totalPnl": pnl, "totalR": r,
                "expectancyR": (r / n if n else None),
                "rBasis": {"declaredInitial": n, "storedStop": 0,
                           "refusedWrongSide": 0, "noBasis": 0}}

    CLEAN = {"declaredInitial": 41, "storedStop": 0, "refusedWrongSide": 0,
             "noBasis": 0}
    DIRTY = {"declaredInitial": 30, "storedStop": 11, "refusedWrongSide": 0,
             "noBasis": 0}

    # --- the sign test -------------------------------------------------------
    ok(sign_state({"expectancyR": 0.11, "totalPnl": -14.6})[0] == "disagree",
       "positive R against negative dollars is a DISAGREEMENT")
    ok(sign_state({"expectancyR": -0.2, "totalPnl": -14.6})[0] == "agree",
       "…and both negative is agreement")
    ok(sign_state({"expectancyR": 0.0, "totalPnl": -1.0})[0] == "not_computable"
       and sign_state({"expectancyR": None, "totalPnl": -1.0})[0] == "not_computable",
       "…and a zero or absent term is not_computable, never a silent 'agree'")
    _, sd = sign_state({"expectancyR": 0.11, "totalPnl": -14.6, "profitFactor": 0.83})
    ok(sd["profit_factor_corroborates"] is True,
       "profitFactor<1 with negative dollars corroborates the loss")
    _, sd = sign_state({"expectancyR": 0.11, "totalPnl": -14.6})
    ok(sd["profit_factor_corroborates"] is None,
       "…and an ABSENT profitFactor is None, never False — a missing "
       "corroborator is not a disagreeing one")

    # --- the mechanism test, and why it does NOT read `contaminated` ---------
    ok(mechanism_state({"rBasis": CLEAN})[0] == "cannot_operate",
       "storedStop=0 means no published R touched the trailed stop")
    ok(mechanism_state({"rBasis": DIRTY})[0] == "can_operate",
       "…and storedStop>0 means the named mechanism IS a candidate")
    ok(mechanism_state({})[0] == "not_gradeable",
       "…and an absent rBasis is `we could not look`, never `cannot_operate`")
    FULL_RP = {"contaminated": 11, "confirmedInitial": 8, "unverified": 22,
               "noBasis": 0}
    st, det = mechanism_state({"rBasis": CLEAN, "rProvenance": FULL_RP})
    ok(st == "cannot_operate" and det["r_provenance"]["contaminated"] == 11,
       "a HIGH `contaminated` count does NOT flip the verdict — that field "
       "grades the stored stop, which a declared-basis R never consults")
    ok(det["r_provenance"] == {"contaminated": 11, "confirmed_initial": 8,
                               "unverified": 22, "no_basis": 0},
       "…and ALL FOUR r_provenance states are reported, not just the alarming "
       "one — a reader can only see that `contaminated` did not drive the "
       "verdict if they can see the rest of the distribution")
    _, det2 = mechanism_state({"rBasis": CLEAN,
                               "rProvenance": {"contaminated": 11}})
    ok(det2["r_provenance"]["no_basis"] is None
       and det2["r_provenance"]["unverified"] is None,
       "…and a state the payload omits reports None, NEVER 0 — an absent count "
       "must not read as a measured zero")
    _, det3 = mechanism_state({"rBasis": CLEAN})
    ok(det3["r_provenance_read"] is None
       and all(v is None for v in det3["r_provenance"].values()),
       "…and an absent rProvenance block reads `we could not look`, never a "
       "clean distribution of zeros")

    # --- the explanation, and its threshold-free separation ------------------
    #     Winners sized SMALLER than every loser -> separated.
    p = {"expectancyR": 0.11, "totalPnl": -14.6, "profitFactor": 0.83,
         "rBasis": CLEAN,
         "perStrategy": [leg("win_small", 16.9, 11.4), leg("win_mid", 8.0, 2.7),
                         leg("lose_a", -26.3, -6.9), leg("lose_b", -8.0, -1.9)]}
    v = report(p)
    ok(v["explanation"] == "risk_size_heterogeneity",
       "winners cheaper per R than EVERY loser grades risk_size_heterogeneity")
    ok(v["explanation_detail"]["separated"] is True
       and v["explanation_detail"]["gap"] > 0,
       "…and the separation is reported with its gap, not asserted")
    ok("risk normalisation working, not contamination" in v["verdict"],
       "…and the verdict says so in one sentence")

    #     Overlapping ranges -> partial, never risk_size_heterogeneity.
    p2 = dict(p, perStrategy=[leg("win_a", 10.0, 2.0), leg("win_b", 2.0, 10.0),
                              leg("lose_a", -10.0, -3.0), leg("lose_b", -2.0, -8.0)])
    v2 = report(p2)
    ok(v2["explanation"] in ("partial", "unexplained"),
       "overlapping ranges are NOT graded as clean separation")
    ok(v2["explanation_detail"]["separated"] is False,
       "…and `separated` says so explicitly")

    #     Winners sized LARGER -> unexplained.
    p3 = dict(p, perStrategy=[leg("win", 40.0, 2.0), leg("lose", -2.0, -8.0)])
    v3 = report(p3)
    ok(v3["explanation"] == "unexplained"
       and "UNESTABLISHED" in v3["verdict"],
       "winners sized LARGER leaves the disagreement UNEXPLAINED — the module "
       "does not manufacture a cause")

    # --- a mixed-sign leg has no dollars-per-R -------------------------------
    p4 = dict(p, perStrategy=p["perStrategy"] + [leg("mixed", -0.33, 0.28, 4)])
    v4 = report(p4)
    mx = [t for t in v4["legs"] if t["leg"] == "mixed"][0]
    ok(mx["dollars_per_r"] is None
       and mx["dollars_per_r_state"] == "ungradeable_mixed_sign",
       "a leg whose PnL and R disagree has NO dollars-per-R — a negative ratio "
       "there would read as a risk")
    ok(v4["explanation_detail"]["ungradeable_legs"] == 1,
       "…and it is COUNTED, not dropped")
    ok(v4["explanation"] == "risk_size_heterogeneity",
       "…and excluding it does not change a verdict the other legs already "
       "separate")

    # --- the mechanism DOMINATES the explanation -----------------------------
    v5 = report(dict(p, rBasis=DIRTY))
    ok(v5["mechanism_state"] == "can_operate"
       and "mechanism is live" in v5["verdict"],
       "when the stored stop IS in use the verdict names it, whatever the leg "
       "ranking says — an available real cause is not displaced by a benign one")

    # --- agreement short-circuits --------------------------------------------
    v6 = report({"expectancyR": -0.2, "totalPnl": -14.6, "rBasis": CLEAN,
                 "perStrategy": []})
    ok(v6["sign_state"] == "agree" and v6["explanation"] == "not_applicable"
       and "criterion is met" in v6["verdict"],
       "agreeing signs need no explanation and the criterion is reported met")

    # --- declared states ------------------------------------------------------
    # ⚠️ THE DENOMINATOR IS PRINTED, not implied. "every state is declared" over
    # an unstated population is the C sub-class of UNPROVENANCED DIAGNOSTIC
    # OUTPUT — an empty loop would print the same reassuring line — and
    # diagnostic-provenance-guard caught exactly that here, in the module whose
    # own subject is that class.
    checked = [v, v2, v3, v4, v5, v6]
    graded = [(vv["sign_state"], vv["mechanism_state"], vv["explanation"])
              for vv in checked]
    undeclared = [t for t in graded
                  if t[0] not in SIGN_STATES or t[1] not in MECHANISM_STATES
                  or t[2] not in EXPLANATION_STATES]
    ok(len(graded) == 6 and not undeclared,
       f"all 3 state fields are in their declared tuples over "
       f"{len(graded)} report(s) = {3 * len(graded)} gradings, "
       f"{len(undeclared)} undeclared")

    ok("Tier-2" in render(v),
       "render states that the route is Tier-2 and untouched")

    print(f"self-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--payload", help="a saved /api/bot/performance response")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.payload:
        print("::error::--payload is required: save "
              "https://ict-bot.duckdns.org/api/bot/performance?window=30d to a "
              "file and pass it. This script does NOT fetch, so it cannot report "
              "a verdict over a payload it failed to read.", file=sys.stderr)
        return 2
    payload = json.loads(pathlib.Path(a.payload).read_text())
    v = report(payload)
    print(json.dumps(v, indent=2) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
