#!/usr/bin/env python3
# wiring: manual-only - this grades a CLOSED question against DATED artifacts
# (the 2026-09-08 ranking-key A/B) plus one lifetime soak read. A scheduled
# runner would re-answer a closed question against a moving population, which
# is exactly what the design file it reads refuses to be re-queued for.
"""Has BL-20260831-TRADE-PRIORITISATION-IS-UNPROVEN already been answered? (MI-278 U28)

THE QUESTION, AND WHY IT IS NOT "RUN THE STUDY"
------------------------------------------------
`BL-20260831-TRADE-PRIORITISATION-IS-UNPROVEN-CONFIDENCE-IS-A-START-NOT-A-RESULT`
reads as an open build: *"(1) Build the two blockers the design names… (2) Run
the four arms…"*. **The study has run.**
`docs/research/trade-prioritisation-research-DESIGN.yaml` carries
`status: answered`, `answered_on: '2026-09-08'`, and the row's own text says
**"⚠️ A NULL RESULT CLOSES THIS ROW — it is an answer, not a failure to be
re-run until something wins."**

So the deliverable is not another study. It is to CHECK the four criteria
against the artifacts, one at a time, and either close the row or say which
criterion is genuinely unmet. Building the harness again would be
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`.

FOUR CRITERION STATES, NEVER COLLAPSED
----------------------------------------
`met` · `met_by_another_route` (satisfied, but not the way the row specified —
recorded rather than smoothed over) · `unmet` · **`ungradeable`** (*the artifact
needed to judge it could not be read* — never `met`, and never `unmet` either).

⚠️ **THE STRATIFIED READ IS THE FINDING, AND THE POOLED HEADLINE HIDES IT.**
The design's `answer` is *"NO. Arbitration ORDER does not measurably drive
outcome"*, a POOLED conclusion. Criterion (3) exists because a pooled number
here is a blend of two populations, and the artifacts stratify exactly as it
asks. On the powered run, `confidence_first` overall is expectancy −0.278R —
but the contests confidence actually DECIDED are −0.1361R over 864 trades,
while the 266 that fell through to `declared_priority` are −0.739R and carry
**81% of the arm's whole loss**.

⚠️ **THAT IS A WITHIN-ARM COMPARISON AND IS CONFOUNDED, WHICH IS THE POINT
RATHER THAN A CAVEAT ON IT.** The two strata are different contests: confidence
ties on the saturated ones. So this does NOT say confidence is a good key — it
says the arm's pooled number is a blend, which is precisely what the row's
criterion (3) warned would make it uninterpretable. The null still stands as a
between-arm result; what narrows is what it can be quoted as meaning.

⚠️ **AND THE STUDY'S POPULATION IS NOT THE LIVE ONE.** The powered run's
`confidence_first` was decided by confidence on **71%** of elections. The live
`conviction_arbitration` soak has the top two candidates at EXACTLY equal
confidence on **57.7%** of contests, so live falls through far more often than
the study did. Since the fall-through stratum is the worse one, the live blend
is worse than the study's. **The two are NOT the same unit** — elections vs
contest events, and an exact tie is not the only way `decided_by` leaves
`confidence` — so this is a transportability FLAG, never a corrected number.

⚠️ **THE SOAK IS DEGENERATE AND THE ROW PREDICTED IT.** Monthly agreement
between the conviction winner and the actual winner: 0.277 (Jun) → 0.607 (Jul)
→ 0.496 (Aug) → **1.000 (Sep, 114 of 114)**. The row says *"now that conviction
IS the live key its agreement figure largely degenerates"* — that is now
measured, not predicted. A September reading of this soak measures nothing
about the open question, so nobody should quote its agreement rate as evidence.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

CRITERION_STATES = ("met", "met_by_another_route", "unmet", "ungradeable")

REPO = pathlib.Path(__file__).resolve().parents[2]
AB_DIR = REPO / "docs/research/ranking-key-ab/2026-09-08-secondary-powered-ttl96"
DESIGN = REPO / "docs/research/trade-prioritisation-research-DESIGN.yaml"
ARMS = ("confidence_first", "priority_first", "recent_pnl_first", "random_tiebreak")


def _load(path: pathlib.Path) -> Any | None:
    try:
        return json.loads(path.read_text())
    # allow-silent: None here is the DECLARED `ungradeable` input, not a swallowed
    # error. Every caller grades a None artifact as `ungradeable` -- *we could not
    # read it* -- which BLOCKS the close, so an unreadable file can never be
    # mistaken for a met criterion. Controls 2 and 3 assert exactly that.
    except Exception:  # noqa: BLE001
        return None


def criterion_1(repo: pathlib.Path = REPO) -> dict:
    """A ranking-key arm on a harness, and a workflow that fires it.

    ⚠️ The row names `scripts/backtest_system.py`. It was built on
    `scripts/research/nbook_portfolio.py` instead. That is a real deviation and
    is graded `met_by_another_route` rather than `met`, because a later reader
    following the row to `backtest_system.py` will not find it there.
    """
    wf = repo / ".github/workflows/ranking-key-ab.yml"
    if not wf.exists():
        return {"state": "unmet", "why": "no ranking-key workflow exists"}
    text = wf.read_text()
    named = "scripts/research/nbook_portfolio.py" in text
    in_row_target = "ranking_key" in (repo / "scripts/backtest_system.py").read_text() \
        if (repo / "scripts/backtest_system.py").exists() else False
    if in_row_target:
        return {"state": "met", "why": "the arm is on the harness the row named"}
    if named:
        return {"state": "met_by_another_route",
                "why": "the arm was built on scripts/research/nbook_portfolio.py, not on "
                       "scripts/backtest_system.py as the row specified; the workflow "
                       ".github/workflows/ranking-key-ab.yml fires it",
                "row_named": "scripts/backtest_system.py",
                "actually_built_on": "scripts/research/nbook_portfolio.py"}
    return {"state": "ungradeable",
            "why": "a ranking-key workflow exists but names no harness this check recognises"}


def criterion_2(arms: dict) -> dict:
    """Four arms, each reporting net PnL, expectancy R, maxDD and the ACHIEVED n."""
    need = ("net_pnl_usd", "expectancy_r", "max_drawdown_usd", "trades")
    missing = {}
    for name in ARMS:
        a = arms.get(name)
        if a is None:
            missing[name] = ["ARM ABSENT"]
            continue
        c = a.get("contested") or {}
        gaps = [k for k in need if c.get(k) is None]
        if gaps:
            missing[name] = gaps
    if any(v == ["ARM ABSENT"] for v in missing.values()):
        return {"state": "ungradeable", "why": "an arm's artifact could not be read",
                "missing": missing}
    if missing:
        return {"state": "unmet", "why": "an arm does not report every required metric",
                "missing": missing}
    return {"state": "met",
            "why": "all four arms report net PnL, expectancy R, maxDD and an ACHIEVED "
                   "contested count",
            "achieved_n": {k: (arms[k].get("contested") or {}).get("trades") for k in ARMS}}


def criterion_3(arms: dict) -> dict:
    """Stratified by `decided_by` — not pooled."""
    have = {k: bool((arms.get(k) or {}).get("contested_by_decided_by")) for k in ARMS}
    if not any(have.values()):
        return {"state": "unmet", "why": "no arm carries a decided_by stratification",
                "per_arm": have}
    if not all(have.values()):
        return {"state": "unmet", "why": "some arms are stratified and some are not",
                "per_arm": have}
    return {"state": "met", "why": "every arm carries contested_by_decided_by",
            "per_arm": have}


def criterion_4(report: dict | None) -> dict:
    """Evaluated on the CONTESTED subset only."""
    if report is None:
        return {"state": "ungradeable", "why": "REPORT.json could not be read"}
    note = str(report.get("population_note") or "")
    if "CONTESTED subset" in note:
        return {"state": "met", "why": "the report declares the contested subset",
                "population_note": note}
    return {"state": "unmet", "why": "the report does not declare a contested-only population"}


def stratified_read(arms: dict) -> dict:
    """The within-arm split criterion (3) exists to expose. NEVER a between-arm claim."""
    out = {}
    for name in ARMS:
        a = arms.get(name) or {}
        bd = a.get("contested_by_decided_by") or {}
        tot = (a.get("contested") or {}).get("net_pnl_usd")
        strata = {}
        for k, v in bd.items():
            if isinstance(v, dict):
                strata[k] = {"trades": v.get("trades"),
                             "expectancy_r": v.get("expectancy_r"),
                             "net_pnl_usd": v.get("net_pnl_usd")}
        worst = None
        if strata and tot not in (None, 0):
            worst = max(strata.items(),
                        key=lambda kv: abs(kv[1]["net_pnl_usd"] or 0.0))
        out[name] = {
            "pooled_expectancy_r": (a.get("contested") or {}).get("expectancy_r"),
            "pooled_net_pnl_usd": tot,
            "strata": strata,
            "largest_loss_stratum": worst[0] if worst else None,
            "largest_loss_share": (round(abs(worst[1]["net_pnl_usd"]) / abs(tot), 4)
                                   if worst and tot else None),
            # ⚠️ NEVER a between-arm or causal reading. The strata are different
            # contests, so this is a statement about the POOLED NUMBER's
            # composition and nothing else.
            "is_a_within_arm_composition_note_not_an_effect": True,
        }
    return out


def soak_decided_share(soak_lines: list[str], tol: float = 0.0) -> dict:
    """Live exact-tie rate among the top two candidates, from the arbitration soak.

    ⚠️ A TIE IS NOT THE COMPLEMENT OF `decided_by == confidence`, and the two
    units differ (contest events here, elections there). Returned as a FLAG,
    with both facts stated, never as a corrected share.
    """
    rows = []
    unparseable = 0
    for ln in soak_lines:
        try:
            rows.append(json.loads(ln))
        # allow-silent: an unparseable soak line is COUNTED into `unparseable` and
        # reported beside every rate, never dropped. Control 17 asserts the count,
        # and an all-unparseable file returns `ungradeable`, never a 0.0 tie rate
        # (control 19).
        except Exception:  # noqa: BLE001
            unparseable += 1
    if not rows:
        return {"state": "ungradeable", "why": "no parseable soak rows",
                "unparseable": unparseable}
    ties = 0
    gradeable = 0
    for r in rows:
        cs = sorted((i.get("confidence") for i in (r.get("per_intent") or [])
                     if i.get("confidence") is not None), reverse=True)
        if len(cs) < 2:
            continue
        gradeable += 1
        if abs(cs[0] - cs[1]) <= tol:
            ties += 1
    monthly: dict[str, list[int]] = {}
    for r in rows:
        m = str(r.get("ts") or "")[:7]
        cell = monthly.setdefault(m, [0, 0])
        cell[0] += 1 if r.get("agrees_with_actual") else 0
        cell[1] += 1
    return {
        "state": "computed",
        "n_rows": len(rows),
        "unparseable": unparseable,
        "gradeable_for_tie": gradeable,
        "exact_ties": ties,
        "tie_rate": round(ties / gradeable, 4) if gradeable else None,
        "agreement_by_month": {k: {"agree": v[0], "n": v[1],
                                   "rate": round(v[0] / v[1], 4)}
                               for k, v in sorted(monthly.items())},
        "tie_is_not_the_complement_of_decided_by_confidence": True,
        "units_differ_contest_events_vs_elections": True,
    }


def discharge(arms: dict, report: dict | None, repo: pathlib.Path = REPO) -> dict:
    crit = {
        "1_harness_and_workflow": criterion_1(repo),
        "2_four_arms_with_achieved_n": criterion_2(arms),
        "3_stratified_by_decided_by": criterion_3(arms),
        "4_contested_subset_only": criterion_4(report),
    }
    states = [c["state"] for c in crit.values()]
    if any(s == "ungradeable" for s in states):
        verdict = "cannot_grade"
    elif all(s in ("met", "met_by_another_route") for s in states):
        verdict = "criteria_met_row_should_close"
    else:
        verdict = "criteria_not_met_row_stays_open"
    return {
        "criteria": crit,
        "verdict": verdict,
        # ⚠️ The row's OWN text is what makes a null closing rather than failing.
        "closes_on_a_null_by_the_rows_own_text": True,
        "stratified": stratified_read(arms),
    }


def _load_arms(d: pathlib.Path = AB_DIR) -> tuple[dict, dict | None]:
    return ({n: _load(d / f"{n}.json") for n in ARMS}, _load(d / "REPORT.json"))


# --------------------------------------------------------------------------
def _selftest() -> int:
    fails = 0
    n = 0

    def check(label: str, cond: bool) -> None:
        nonlocal fails, n
        n += 1
        if cond:
            print(f"  ok   {n:>3} {label}")
        else:
            fails += 1
            print(f"  FAIL {n:>3} {label}")

    def arm(trades=100, exp=-0.1, pnl=-10.0, dd=20.0, strat=True):
        a = {"contested": {"trades": trades, "expectancy_r": exp,
                           "net_pnl_usd": pnl, "max_drawdown_usd": dd}}
        if strat:
            a["contested_by_decided_by"] = {
                "confidence": {"trades": 80, "expectancy_r": -0.05, "net_pnl_usd": -2.0},
                "declared_priority": {"trades": 20, "expectancy_r": -0.5, "net_pnl_usd": -8.0}}
        return a

    full = {k: arm() for k in ARMS}
    rep = {"population_note": "Every number is the CONTESTED subset."}

    check("all four criteria met -> the row should close",
          discharge(full, rep)["verdict"] == "criteria_met_row_should_close")
    check("a missing arm is `ungradeable`, never `unmet`",
          criterion_2({**full, "priority_first": None})["state"] == "ungradeable")
    check("...and an ungradeable criterion blocks the close",
          discharge({**full, "priority_first": None}, rep)["verdict"] == "cannot_grade")
    check("an arm missing maxDD is `unmet`, which is a different fact",
          criterion_2({**full, "priority_first": arm(dd=None)})["state"] == "unmet")
    check("no stratification anywhere -> criterion 3 unmet",
          criterion_3({k: arm(strat=False) for k in ARMS})["state"] == "unmet")
    check("PARTIAL stratification is unmet too, not met",
          criterion_3({**full, "priority_first": arm(strat=False)})["state"] == "unmet")
    check("an unreadable REPORT is `ungradeable`, never `unmet`",
          criterion_4(None)["state"] == "ungradeable")
    check("a report with no contested declaration is `unmet`",
          criterion_4({"population_note": "all trades"})["state"] == "unmet")
    check("criterion 1 grades the DEVIATION rather than smoothing it",
          criterion_1()["state"] in ("met", "met_by_another_route"))
    check("...and names both the row's target and what was built",
          criterion_1().get("actually_built_on") == "scripts/research/nbook_portfolio.py"
          or criterion_1()["state"] == "met")
    check("every declared criterion state is a real value",
          all(c["state"] in CRITERION_STATES for c in discharge(full, rep)["criteria"].values()))

    st = stratified_read(full)["confidence_first"]
    check("the stratified read names the largest-loss stratum",
          st["largest_loss_stratum"] == "declared_priority")
    check("...and its share of the pooled loss", st["largest_loss_share"] == 0.8)
    check("...and marks itself as a composition note, not an effect",
          st["is_a_within_arm_composition_note_not_an_effect"] is True)

    soak = [json.dumps({"ts": "2026-09-01T00:00:00Z", "agrees_with_actual": True,
                        "per_intent": [{"confidence": 0.5}, {"confidence": 0.5}]}),
            json.dumps({"ts": "2026-09-01T00:00:00Z", "agrees_with_actual": False,
                        "per_intent": [{"confidence": 0.9}, {"confidence": 0.1}]}),
            "{not json"]
    s = soak_decided_share(soak)
    check("the soak read counts an exact tie", s["exact_ties"] == 1)
    check("...over a stated gradeable denominator", s["gradeable_for_tie"] == 2)
    check("...counts an unparseable line rather than dropping it", s["unparseable"] == 1)
    check("...and reports agreement per month", s["agreement_by_month"]["2026-09"]["n"] == 2)
    check("an empty soak is `ungradeable`, never a 0.0 tie rate",
          soak_decided_share([])["state"] == "ungradeable")
    check("a one-candidate row is excluded from the tie denominator, not counted as no-tie",
          soak_decided_share([json.dumps({"ts": "x", "per_intent": [{"confidence": 1.0}]})]
                             )["gradeable_for_tie"] == 0)
    check("the tie flag declares it is NOT the complement of decided_by",
          s["tie_is_not_the_complement_of_decided_by_confidence"] is True)
    check("...and that the two units differ",
          s["units_differ_contest_events_vs_elections"] is True)

    print(f"\nself-test: {n} controls, {fails} failure(s)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--soak", help="conviction_arbitration log_file JSON from the diag relay")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _selftest()

    arms, report = _load_arms()
    out = discharge(arms, report)
    if a.soak:
        blob = json.loads(pathlib.Path(a.soak).read_text())
        out["live_soak"] = soak_decided_share(blob.get("lines") or [])
    if a.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"verdict: {out['verdict']}")
    for k, v in out["criteria"].items():
        print(f"  {k:<32} {v['state']}")
    for name, s in out["stratified"].items():
        if s["largest_loss_stratum"]:
            print(f"  {name:<18} pooled exp_r={s['pooled_expectancy_r']} · "
                  f"largest-loss stratum {s['largest_loss_stratum']} "
                  f"({s['largest_loss_share']:.0%} of the arm's loss)")
    if "live_soak" in out:
        ls = out["live_soak"]
        print(f"  live soak: {ls['exact_ties']}/{ls['gradeable_for_tie']} exact ties "
              f"= {ls['tie_rate']} (n_rows={ls['n_rows']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
