#!/usr/bin/env python3
# wiring: manual-only — a session runs this to answer ONE row's attribution
# clause. It is not scheduled, nothing imports it on the order path, and it
# proposes no change.
"""Was the 2026-08-30 break the e35 GEOMETRY or the MARKET? The discriminating read.

THE ROW THIS ANSWERS, AND ITS EXACT DEMAND
-------------------------------------------
`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
(`loud: true`; 17 consecutive losing days, −$38,851.81) clears only when:

    "The break is ATTRIBUTED by the discriminating measurement: per-leg stop-out
     rate and MFE-at-stop, e35 legs vs NON-e35 legs, before vs after 2026-08-30,
     with the verdict and its population recorded."

and it forecloses three shortcuts by name: *profitability returning clears it
not at all*; *"the market chopped" is not a verdict without the non-e35
control*; *reverting e35 without the control clears nothing, because
`ict_scalp_*` — which e35 never touched — would stay unexplained.* Its own
latest observation (2026-09-12) records that this measurement **has still not
been run**.

WHY THE TWO TERMS DISCRIMINATE — THE ARGUMENT THE WHOLE MODULE RESTS ON
-----------------------------------------------------------------------
e35 changed the STOP GEOMETRY (`atr_stop_mult` 2.5 → 2.0) on 9 legs. That
changes **when a trade exits**. It cannot change **how far the market travels**.

`m20_u5_excursion_regime` measures MFE and MAE in **ATR units over a FIXED
window from entry** — it needs no exit price, no stop, no provenance, and does
not care which lever closed the trade. **So the excursion term is immune to the
geometry change by construction.** That is not a convenience; it is what makes
the two terms independent:

* **excursion moved in BOTH arms** → the market moved. e35 cannot reach the
  control arm, so a common fall is a regime read.
* **stop-rate DiD positive AND excursion FLAT in the control** → the geometry
  did it: trades are stopping out more without the market travelling worse.
* **both** → both contribute, and neither can be reverted away alone.

⚠️ **A NEGATIVE VERDICT IS NOT A CLEAN BILL.** `cannot_discriminate` is a real
state and, at the cell sizes this population offers, the likely one. The row
demands an ATTRIBUTION, so `cannot_discriminate` **does not clear it** — it says
what n would. Reporting it as a pass would be the substitution the row warns
against.

WHAT IT COMPOSES RATHER THAN RE-DERIVES
----------------------------------------
* `e35_break_attribution.arm_of` — the arm rule, so the treated/control split is
  the one already published, not a second opinion.
* `stop_integrity_both_arms.build` / `census` — the stop-rate half, ALREADY
  carrying the declared-vs-amended split that
  `BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT`
  requires. A stop-out at a level a trailing amend had already moved is not
  evidence about the DECLARED geometry, and folding the two would let the trail
  masquerade as e35.
* `m20_u5_excursion_regime.grade` — the excursion half, in ATR over a fixed
  window (its own docstring explains why a lifetime window manufactures the
  finding: a tighter stop closes a trade sooner, which mechanically lowers MFE).

⚠️ **THE CANDLE INTERVAL IS ONE CHOICE APPLIED TO BOTH ARMS, AND THAT IS THE
POINT.** The legs span 5m to 4h, and a per-leg interval would make the treated
and control arms measured by different instruments — which is precisely the
confound a control exists to remove. A coarser bar under-resolves a 5m scalp's
travel; it under-resolves it **identically in both arms**, so the DIFFERENCE
stays interpretable while the LEVEL does not. The interval is reported.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import statistics
import sys
from typing import Any

REPO_SCRIPTS = "scripts"

#: The e35 bracket-geometry deploy. Verified in the work object and the row.
EVENT_ISO = "2026-08-30T08:53:19+00:00"

#: The arms, in the order the report prints them.
ARMS = ("e35", "control_same_family", "control_other")
ERAS = ("pre", "post")

#: The attribution verdict. `cannot_discriminate` is a real answer, not a
#: fallback, and it does NOT clear the row.
VERDICTS = (
    "e35_geometry",
    "market_regime",
    "both_contribute",
    "neither_explains",
    "cannot_discriminate",
)

#: Whether a single term could be read at all.
TERM_STATES = ("measured", "insufficient_n", "not_measured")

#: The smallest cell that may carry a term. Chosen, not tuned: below this a
#: difference of one package moves the rate by more than the effect under study.
MIN_CELL = 8


def _parse(ts: Any) -> dt.datetime | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(float(ts) / 1000, tz=dt.timezone.utc)
    try:
        out = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


def era_of(when: dt.datetime | None, event: dt.datetime) -> str | None:
    """`pre` / `post` / `None` when the timestamp is unreadable.

    `None` is *we could not place this package in time* and is counted, never
    defaulted into an era — defaulting would load one cell with every
    unparseable row.
    """
    if when is None:
        return None
    return "pre" if when < event else "post"


def cell_key(strategy: str, when: dt.datetime | None, event: dt.datetime,
             arm_fn) -> tuple[str, str] | None:
    era = era_of(when, event)
    return None if era is None else (arm_fn(strategy), era)


def excursion_terms(rows: list[dict], window_h: int) -> dict:
    """Mean MFE:MAE per (arm, era) at one window. `None` where no row exists."""
    by: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for r in rows:
        if r.get("window_h") != window_h:
            continue
        ratio = r.get("fa_ratio")
        if ratio is None:            # mae == 0: no adverse travel, ratio undefined
            continue
        by[(r["arm"], r["era"])].append(float(ratio))
    out = {}
    for arm in ARMS:
        for era in ERAS:
            vals = by.get((arm, era)) or []
            out[(arm, era)] = {
                "n": len(vals),
                # None, never 0.0 — an empty cell has no mean, and 0.0 would
                # read as "the market offered no favourable travel".
                "mean_fa": round(statistics.fmean(vals), 4) if vals else None,
                "median_fa": round(statistics.median(vals), 4) if vals else None,
            }
    return out


def term_state(cell_a: dict, cell_b: dict, min_cell: int = MIN_CELL) -> str:
    if cell_a.get("mean_fa") is None or cell_b.get("mean_fa") is None:
        return "not_measured"
    if cell_a["n"] < min_cell or cell_b["n"] < min_cell:
        return "insufficient_n"
    return "measured"


def arm_delta(terms: dict, arm: str, min_cell: int = MIN_CELL) -> tuple[str, float | None]:
    """post − pre for one arm's excursion, with its own gradeability state."""
    pre, post = terms[(arm, "pre")], terms[(arm, "post")]
    state = term_state(pre, post, min_cell)
    if state != "measured":
        return state, None
    return state, round(post["mean_fa"] - pre["mean_fa"], 4)


def verdict(stop_did_pp: float | None, treated_ex: float | None,
            control_ex: float | None, *, treated_state: str,
            control_state: str) -> tuple[str, str]:
    """Combine the two independent terms into the row's attribution.

    ⚠️ THE CONTROL'S EXCURSION IS THE DECIDING TERM, because e35 cannot reach it.
    A fall there is a market read no geometry change can produce.
    """
    if control_state != "measured" or treated_state != "measured":
        return "cannot_discriminate", (
            "the excursion term is %s in the treated arm and %s in the control, so the "
            "market half cannot be read; the stop-rate half alone cannot separate "
            "geometry from regime — which is the substitution the row forecloses"
            % (treated_state, control_state))
    control_fell = control_ex < 0
    treated_fell = treated_ex < 0
    stop_rose = stop_did_pp is not None and stop_did_pp > 0
    if control_fell and treated_fell and not stop_rose:
        return "market_regime", (
            "excursion fell in BOTH arms and the stop-rate difference-in-differences did "
            "not rise, so the market travelled worse for legs e35 never touched")
    if control_fell and treated_fell and stop_rose:
        return "both_contribute", (
            "excursion fell in BOTH arms (a market move e35 cannot reach) AND the "
            "stop-rate DiD rose, so neither can be reverted away alone")
    if not control_fell and stop_rose:
        return "e35_geometry", (
            "the control's excursion did NOT fall while the stop-rate DiD rose: trades "
            "are stopping out more without the market travelling worse")
    if not control_fell and not stop_rose:
        return "neither_explains", (
            "the control's excursion did not fall and the stop-rate DiD did not rise, so "
            "neither candidate is supported and the loss is elsewhere")
    return "cannot_discriminate", (
        "the terms point in incompatible directions (control excursion %s, treated %s, "
        "stop DiD %s) and no single cause is supported"
        % (control_ex, treated_ex, stop_did_pp))


def report(res: dict) -> list[str]:
    out = [
        "e35 break attribution — the discriminating read",
        "  POPULATION: %s" % res["population"],
        "  EVENT: %s · candle interval %s · windows %s"
        % (res["event"], res["interval"], res["windows"]),
    ]
    out.append("  packages by (arm, era), and %d could not be placed in time:"
               % res["unplaceable"])
    for arm in ARMS:
        out.append("      %-22s pre %4d  post %4d"
                   % (arm, res["cells"].get((arm, "pre"), 0), res["cells"].get((arm, "post"), 0)))
    for w in res["windows"]:
        t = res["excursion"][w]
        out.append("  excursion MFE:MAE in ATR at %dh (mean, n) — geometry-FREE by "
                   "construction, so e35 cannot move it:" % w)
        for arm in ARMS:
            pre, post = t[(arm, "pre")], t[(arm, "post")]
            out.append("      %-22s pre %s (n=%d)   post %s (n=%d)   delta %s"
                       % (arm,
                          "  none" if pre["mean_fa"] is None else "%6.3f" % pre["mean_fa"],
                          pre["n"],
                          "  none" if post["mean_fa"] is None else "%6.3f" % post["mean_fa"],
                          post["n"],
                          res["deltas"][w].get(arm, (None, None))[1]))
    out.append("  stop-rate DiD (declared-only, package unit): %s"
               % ("not supplied" if res["stop_did_pp"] is None
                  else "%+.1f pp" % res["stop_did_pp"]))
    out.append("  VERDICT (%dh): %s" % (res["verdict_window"], res["verdict"]))
    out.append("      %s" % res["verdict_why"])
    if res["verdict"] == "cannot_discriminate":
        out.append("      ⚠️ THIS DOES NOT CLEAR THE ROW. The row demands an ATTRIBUTION; "
                   "'we could not tell' is an honest answer and an unmet clause. Reporting "
                   "it as a pass would be the substitution the row forecloses.")
    return out


# --------------------------------------------------------------------- self-test


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    ev = _parse(EVENT_ISO)

    # --- era placement ------------------------------------------------------
    ok("a package before the event is pre", era_of(_parse("2026-08-29T00:00:00Z"), ev) == "pre")
    ok("a package after the event is post", era_of(_parse("2026-08-31T00:00:00Z"), ev) == "post")
    ok("the event instant itself is POST, stated rather than left to a boundary guess",
       era_of(ev, ev) == "post")
    ok("an unreadable timestamp is None, NEVER defaulted into an era",
       era_of(_parse("not-a-date"), ev) is None)
    ok("a None timestamp is None too", era_of(None, ev) is None)

    # --- excursion terms ----------------------------------------------------
    rows = [
        {"arm": "e35", "era": "pre", "window_h": 24, "fa_ratio": 2.0},
        {"arm": "e35", "era": "pre", "window_h": 24, "fa_ratio": 1.0},
        {"arm": "e35", "era": "post", "window_h": 24, "fa_ratio": 0.5},
        {"arm": "control_other", "era": "pre", "window_h": 24, "fa_ratio": 3.0},
        {"arm": "control_other", "era": "post", "window_h": 24, "fa_ratio": 1.0},
        {"arm": "e35", "era": "pre", "window_h": 48, "fa_ratio": 9.9},
    ]
    t = excursion_terms(rows, 24)
    ok("the mean is over the requested window only", t[("e35", "pre")]["mean_fa"] == 1.5)
    ok("a window with no rows for a cell reports None, NEVER 0.0",
       t[("control_same_family", "pre")]["mean_fa"] is None)
    ok("an empty cell reports n=0 beside the None", t[("control_same_family", "pre")]["n"] == 0)
    ok("a row whose fa_ratio is None (mae==0) is skipped, not counted as zero travel",
       excursion_terms(rows + [{"arm": "e35", "era": "pre", "window_h": 24,
                                "fa_ratio": None}], 24)[("e35", "pre")]["n"] == 2)
    ok("every arm and era appears in the terms, present or not",
       set(t) == {(a, e) for a in ARMS for e in ERAS})

    # --- term gradeability --------------------------------------------------
    big = {"n": 20, "mean_fa": 1.0}
    small = {"n": 3, "mean_fa": 1.0}
    empty = {"n": 0, "mean_fa": None}
    ok("two full cells are measured", term_state(big, big) == "measured")
    ok("a small cell is insufficient_n, NOT measured", term_state(big, small) == "insufficient_n")
    ok("an empty cell is not_measured, which is a DIFFERENT fact from insufficient_n",
       term_state(big, empty) == "not_measured")
    ok("every term state is in the declared vocabulary",
       all(term_state(a, b) in TERM_STATES for a, b in ((big, big), (big, small), (big, empty))))
    ok("an ungradeable arm delta returns None, never 0.0",
       arm_delta({("e35", "pre"): big, ("e35", "post"): empty}, "e35")[1] is None)

    # --- the verdict --------------------------------------------------------
    M = {"treated_state": "measured", "control_state": "measured"}
    ok("both arms falling with no stop rise is a MARKET read",
       verdict(-2.0, -0.5, -0.4, **M)[0] == "market_regime")
    ok("both arms falling WITH a stop rise is both_contribute",
       verdict(+5.0, -0.5, -0.4, **M)[0] == "both_contribute")
    ok("a flat control with a stop rise is the GEOMETRY",
       verdict(+5.0, -0.5, +0.1, **M)[0] == "e35_geometry")
    ok("a flat control and no stop rise supports NEITHER candidate",
       verdict(-1.0, +0.2, +0.1, **M)[0] == "neither_explains")
    ok("an unmeasured control CANNOT be discriminated, whatever the stop term says",
       verdict(+20.0, -0.9, None, treated_state="measured",
               control_state="insufficient_n")[0] == "cannot_discriminate")
    ok("an unmeasured TREATED arm is equally undiscriminable",
       verdict(+20.0, None, -0.9, treated_state="not_measured",
               control_state="measured")[0] == "cannot_discriminate")
    ok("THE CONTROL IS THE DECIDING TERM: the same stop rise gives OPPOSITE verdicts "
       "depending only on whether the control fell",
       verdict(+5.0, -0.5, -0.4, **M)[0] != verdict(+5.0, -0.5, +0.1, **M)[0])
    # The case that MATTERS and that the pair above does not reach: the control
    # FELL (a market move e35 cannot cause) while the treated arm did NOT, with a
    # stop rise. Reporting `e35_geometry` there blames the geometry for a move
    # the control proves is market-wide. A plant that dropped the `not
    # control_fell` guard passed every other control in this suite.
    ok("a FALLING control with a RISING treated arm is NEVER the geometry",
       verdict(+5.0, +0.2, -0.4, **M)[0] != "e35_geometry")
    ok("and that case is cannot_discriminate, because the terms conflict",
       verdict(+5.0, +0.2, -0.4, **M)[0] == "cannot_discriminate")
    ok("a missing stop term is not read as a rise",
       verdict(None, -0.5, +0.1, **M)[0] == "neither_explains")
    ok("every verdict returned is in the declared vocabulary",
       all(verdict(s, a, b, **M)[0] in VERDICTS
           for s, a, b in ((-2.0, -0.5, -0.4), (5.0, -0.5, -0.4), (5.0, -0.5, 0.1),
                           (-1.0, 0.2, 0.1))))
    ok("every verdict carries a WHY, never a bare label",
       all(verdict(s, a, b, **M)[1].strip()
           for s, a, b in ((-2.0, -0.5, -0.4), (5.0, -0.5, 0.1))))

    # --- report -------------------------------------------------------------
    res = {
        "population": "fixture", "event": EVENT_ISO, "interval": "15",
        "windows": [24], "unplaceable": 0,
        "cells": {("e35", "pre"): 2, ("e35", "post"): 1,
                  ("control_other", "pre"): 1, ("control_other", "post"): 1},
        "excursion": {24: t},
        "deltas": {24: {a: arm_delta(t, a) for a in ARMS}},
        "stop_did_pp": None, "verdict_window": 24,
        "verdict": "cannot_discriminate", "verdict_why": "fixture",
    }
    text = "\n".join(report(res))
    ok("the report states the population", "POPULATION: fixture" in text)
    ok("the report names the candle interval it used", "candle interval 15" in text)
    ok("the report says the excursion term is geometry-free", "geometry-FREE" in text)
    ok("the report counts packages it could not place in time", "could not be placed in time" in text)
    ok("an empty cell renders as 'none', not as 0.000", "none" in text)
    ok("a cannot_discriminate verdict says it does NOT clear the row",
       "THIS DOES NOT CLEAR THE ROW" in text)
    ok("and names the substitution it would be", "substitution the row forecloses" in text)

    failed = [n for n, good in checks if not good]
    for name, good in checks:
        print("%s  %s" % ("ok  " if good else "FAIL", name))
    print("\nself-test: %d checks, %d passed, %d failed (denominator = %d)"
          % (len(checks), len(checks) - len(failed), len(failed), len(checks)))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packages")
    ap.add_argument("--trades", help="restrict to the DECISION POPULATION (U5's own filter)")
    ap.add_argument("--interval", default="15", help="candle interval, bybit-style")
    ap.add_argument("--window", type=int, default=24, help="which window carries the verdict")
    ap.add_argument("--stop-did-pp", type=float, default=None,
                    help="the declared-only stop-rate DiD in pp, from stop_integrity_both_arms")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.packages:
        ap.error("--packages is required unless --self-test is given")

    sys.path.insert(0, REPO_SCRIPTS + "/research")
    from e35_break_attribution import arm_of
    from m20_u5_excursion_regime import WINDOWS_H, decision_population_pkg_ids, grade

    packages = json.loads(open(args.packages, encoding="utf-8").read())
    pkg_filter = None
    population = "ALL %d packages (NOT the decision population)" % len(packages)
    if args.trades:
        pkg_filter = decision_population_pkg_ids(
            json.loads(open(args.trades, encoding="utf-8").read()))
        population = ("decision population — %d package ids (closed, non-backtest, "
                      "pnl NOT NULL, non-pairs) out of %d packages read"
                      % (len(pkg_filter), len(packages)))

    event = _parse(EVENT_ISO)
    cells: dict[tuple[str, str], int] = collections.Counter()
    arm_era: dict[str, tuple[str, str]] = {}
    unplaceable = 0
    for p in packages:
        s = str(p.get("strategy_name") or "")
        if s.startswith("pairs_"):
            continue
        key = cell_key(s, _parse(p.get("created_at")), event, arm_of)
        if key is None:
            unplaceable += 1
            continue
        cells[key] += 1
        arm_era[str(p.get("order_package_id"))] = key

    rows = grade(packages, args.interval, None, verbose=True, pkg_id_filter=pkg_filter)
    for r in rows:
        key = arm_era.get(str(r.get("order_package_id")))
        r["arm"], r["era"] = (key if key else ("unplaceable", "unplaceable"))
    rows = [r for r in rows if r["arm"] in ARMS]

    excursion = {w: excursion_terms(rows, w) for w in WINDOWS_H}
    deltas = {w: {a: arm_delta(excursion[w], a) for a in ARMS} for w in WINDOWS_H}
    w = args.window
    t_state, t_delta = deltas[w]["e35"]
    # The control is BOTH non-e35 arms pooled: the row's demand is "e35 legs vs
    # NON-e35 legs", and same_family alone is the smaller, noisier half.
    pooled = {("control", e): {
        "n": excursion[w][("control_same_family", e)]["n"]
             + excursion[w][("control_other", e)]["n"],
        "mean_fa": None} for e in ERAS}
    for e in ERAS:
        vals = [float(r["fa_ratio"]) for r in rows
                if r["window_h"] == w and r["era"] == e
                and r["arm"] in ("control_same_family", "control_other")
                and r.get("fa_ratio") is not None]
        pooled[("control", e)]["mean_fa"] = round(statistics.fmean(vals), 4) if vals else None
        pooled[("control", e)]["n"] = len(vals)
    c_state = term_state(pooled[("control", "pre")], pooled[("control", "post")])
    c_delta = (None if c_state != "measured"
               else round(pooled[("control", "post")]["mean_fa"]
                          - pooled[("control", "pre")]["mean_fa"], 4))
    v, why = verdict(args.stop_did_pp, t_delta, c_delta,
                     treated_state=t_state, control_state=c_state)

    res = {"population": population, "event": EVENT_ISO, "interval": args.interval,
           "windows": list(WINDOWS_H), "unplaceable": unplaceable, "cells": dict(cells),
           "excursion": excursion, "deltas": deltas, "stop_did_pp": args.stop_did_pp,
           "verdict_window": w, "verdict": v, "verdict_why": why,
           "control_pooled": {f"{k[0]}_{k[1]}": val for k, val in pooled.items()},
           "control_state": c_state, "control_delta": c_delta,
           "treated_state": t_state, "treated_delta": t_delta}
    if args.json:
        print(json.dumps({k: (str(val) if isinstance(val, dict)
                              and any(isinstance(x, tuple) for x in val) else val)
                          for k, val in res.items()}, indent=2, default=str))
    else:
        print("\n".join(report(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
