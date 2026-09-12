#!/usr/bin/env python3
# wiring: manual-only — this is a DECLARATION plus the instrument that checks
# it, not a scheduled job. A session runs it when it is about to publish a rate
# over trades that share an order package, and imports `package_exit_verdict` /
# `did_band` from it rather than re-deciding which sibling row to believe.
"""What a fan-out package's EXIT is — declared once, so nothing decides it by accident.

THE DEFECT THIS ANSWERS
-----------------------
An order package's sibling `trades` rows are ONE price path fanned across
several accounts, and they DO NOT CLOSE TOGETHER. `adjudicate_exit` grades ONE
row, so *which* row it grades decides whether the package counts as a stop-out.

MEASURED 2026-09-12 (MI-278 U15, `docs/research/m20-u15-stop-integrity-both-arms-2026-09-12.md`)
— same population, same adjudicator, only the row-selection rule varied:

    all stop-outs   DiD  21.2pp (population order) / 28.2pp (earliest) / 17.9pp (latest)
    declared-only   DiD   7.6pp                    / 13.2pp            /  4.3pp

⚠️ The TREATED arm is insensitive — 35.9pp under all three. The entire swing
comes through the CONTROL (7.7 / 14.8 / 18.0pp), because the packages whose
siblings close far apart are concentrated there. That is why single-arm studies
never saw it: they read a perfectly stable number with no way to know the choice
mattered.

⚠️ And "population order" is NOT A CHOICE ANYBODY MADE. It is whatever
`/api/diag/journal` returned — id-DESC today. A paginated read, a different pull
order or a re-sorted input would move a published causal statistic with no data
change and nothing to notice.

THE RULE THIS FILE DECLARES
---------------------------
**You do not select a row. The unit is the PACKAGE.**

A package's exit is `unanimous` (every gradeable sibling agrees), `disagreement`
(they do not), or `ungradeable` (no sibling could be graded at all). A
disagreement is **REPORTED, NEVER ARBITRATED** — picking a winner among
contradicting siblings is a choice wearing the clothes of a fact.

This removes input order from the answer BY CONSTRUCTION rather than by pinning
a sort somewhere. A rate then comes back as a BAND (`did_band`), counting the
disagreements both ways, which is the same idiom the e35 sign-survival test
already uses.

WHY NOT JUST PICK ONE
---------------------
Earliest, latest and "the row the strategy owns" are all defensible, and
choosing between them needs an argument about what a package's exit IS — when
the first leg closes, when the last does, or when the managed leg does. This
file does not pretend that argument has been settled. What it refuses is the
status quo, where the answer is decided by the order rows arrived in.

`row_selection_spread` reproduces all three rules ANYWAY, for one reason only:
so the measured spread above stays reproducible on any later population instead
of being a historical claim nobody can re-check.

⚠️ THREE SELECTIONS AGREEING CLOSES NOTHING. They agree whenever no fan-out
package in that population has divergent closes, which is a property of that
week's data and not of the code. `spread_verdict` says so in its own output.

WHAT THE BAND DOES NOT COVER
----------------------------
`did_band` bounds the DISAGREEMENT packages. It does NOT bound the packages
excluded as `ungradeable` — those leave the denominator entirely, and that
exclusion can carry its own selection effect. The counts ride in the output so
the exclusion is never invisible; bounding it needs a different instrument.

Run: python3 scripts/research/fanout_exit_unit.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.bleed_attribution_2026_09_11 import adjudicate_exit  # noqa: E402

# The three row-level exits `adjudicate_exit` returns that are an ANSWER about
# the price path. Its other three (`ungradeable_no_price`, `..._no_direction`,
# `..._no_levels`, `..._bracket_degenerate`) are "we could not look".
GRADEABLE_ROW_EXITS = ("reached_stop", "reached_target", "neither")

# Never collapsed. `disagreement` is NOT a kind of ungradeable: ungradeable is
# "no sibling could be graded", disagreement is "they were graded and they
# contradict each other", and the second is a finding while the first is a gap.
PACKAGE_VERDICTS = ("unanimous", "disagreement", "ungradeable")

# Reproduced only to keep the measured spread checkable. Not a menu to pick from.
ROW_SELECTION_RULES = ("population_order", "earliest_close", "latest_close")

# Whether a row could be placed on the timeline at all. `unorderable` is the
# state that stops `earliest_close` silently degrading into population order
# when `closed_at` is missing -- which would reintroduce the exact defect.
ORDERABILITY_STATES = ("orderable", "unorderable")

# Whether a row could be attributed to a package at all.
PACKAGING_STATES = ("packaged", "unpackaged")


def _ms(v: Any) -> int | None:
    """Epoch-ms from an epoch-ms number or an ISO string. None when unreadable."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        import datetime as _dt
        try:
            t = s.replace("Z", "+00:00")
            return int(_dt.datetime.fromisoformat(t).timestamp() * 1000)
        except ValueError:
            return None
    return None


def group_by_package(rows: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """Group rows into packages. Returns (groups, states).

    A row with no `order_package_id` becomes its OWN single-row group under a
    synthetic key and is counted as `unpackaged` -- never silently merged with
    other unpackaged rows, and never dropped. "We could not group this" and
    "this genuinely traded alone" are different facts and the counts keep them
    apart.
    """
    groups: dict[str, list[dict]] = {}
    states = {"packaged": 0, "unpackaged": 0}
    for i, r in enumerate(rows):
        pid = r.get("order_package_id")
        if pid:
            states["packaged"] += 1
            groups.setdefault(str(pid), []).append(r)
        else:
            states["unpackaged"] += 1
            groups["__unpackaged__%d" % i] = [r]
    return groups, states


def package_exit_verdict(rows: list[dict], tol: float) -> tuple[str, str | None, dict]:
    """THE DECLARED RULE. Adjudicate a package without selecting a row.

    Returns (verdict, exit_or_None, detail). `exit_or_None` is non-None only for
    `unanimous` -- a disagreement has no single exit, and returning one would be
    the arbitration this file exists to refuse.
    """
    per_row = [adjudicate_exit(r, tol) for r in rows]
    gradeable = [v for v in per_row if v in GRADEABLE_ROW_EXITS]
    detail = {
        "rows": len(rows),
        "per_row": per_row,
        "gradeable_rows": len(gradeable),
        "distinct_gradeable": sorted(set(gradeable)),
    }
    if not gradeable:
        return "ungradeable", None, detail
    if len(set(gradeable)) == 1:
        return "unanimous", gradeable[0], detail
    return "disagreement", None, detail


def package_census(rows: list[dict], tol: float) -> dict:
    """Every package in `rows`, graded by the declared rule. States the population."""
    groups, pkg_states = group_by_package(rows)
    verdicts: dict[str, dict] = {}
    counts = {v: 0 for v in PACKAGE_VERDICTS}
    fanout = 0
    for pid, rs in groups.items():
        v, ex, detail = package_exit_verdict(rs, tol)
        counts[v] += 1
        if len(rs) > 1:
            fanout += 1
        verdicts[pid] = {"verdict": v, "exit": ex, **detail}
    return {
        "rule": "package_is_the_unit_no_row_selection",
        "tol": tol,
        "rows_in": len(rows),
        "packages": len(groups),
        "fanout_packages": fanout,
        "row_packaging_states": pkg_states,
        "verdict_counts": counts,
        "packages_detail": verdicts,
        "row_inflation": (len(rows) / len(groups)) if groups else None,
    }


def _arm_terms(census: dict) -> dict:
    """Stop-out terms for one arm, from a `package_census`."""
    u_stop = u_other = dis = ung = 0
    for d in census["packages_detail"].values():
        if d["verdict"] == "ungradeable":
            ung += 1
        elif d["verdict"] == "disagreement":
            dis += 1
        elif d["exit"] == "reached_stop":
            u_stop += 1
        else:
            u_other += 1
    denom = u_stop + u_other + dis
    return {
        "unanimous_stop": u_stop,
        "unanimous_other": u_other,
        "disagreement": dis,
        "ungradeable_excluded": ung,
        "denominator": denom,
        "rate_low": (u_stop / denom) if denom else None,
        "rate_high": ((u_stop + dis) / denom) if denom else None,
    }


def rate_band(rows: list[dict], tol: float) -> dict:
    """One cell's stop-rate as a BAND, disagreements counted both ways.

    `rate_low` counts every disagreement package as NOT a stop-out; `rate_high`
    counts every one as a stop-out. Ungradeable packages leave the denominator
    and are reported separately -- the band does NOT cover that exclusion.
    """
    return _arm_terms(package_census(rows, tol))


def did_band(treated_pre: list[dict], treated_post: list[dict],
             control_pre: list[dict], control_post: list[dict],
             tol: float) -> dict:
    """A real four-cell difference-in-differences, as a band.

        DiD = (treated_post - treated_pre) - (control_post - control_pre)

    ⚠️ FOUR cells, not two. An earlier draft of this function took two arms and
    computed `treated - control` while calling the result a DiD -- the label
    naming a quantity the code did not compute, which is exactly the
    unprovenanced-diagnostic-output class this repo files as sub-class A. The
    parameters are named for their cells so the call site cannot repeat it.

    The extremes pair each cell against the direction that widens the interval:
    the maximum takes treated_post high, treated_pre low, control_post low and
    control_pre high.

    `sign_survives` is `did_low > 0` -- the claim holds however every
    disagreement is resolved. It invents no alpha and is NOT a significance
    test: it is insensitive to n and can pass a comparison with no power:
    BL-20260912-THE-SIGN-SURVIVAL-CRITERION-IS-INSENSITIVE-TO-N-SO-IT-CAN-PASS-A-COMPARISON-WITH-NO-POWER.
    Read every cell's `denominator` before quoting it.
    """
    cells = {
        "treated_pre": _arm_terms(package_census(treated_pre, tol)),
        "treated_post": _arm_terms(package_census(treated_post, tol)),
        "control_pre": _arm_terms(package_census(control_pre, tol)),
        "control_post": _arm_terms(package_census(control_post, tol)),
    }
    empty = [k for k, v in cells.items() if v["denominator"] == 0]
    ung = sum(v["ungradeable_excluded"] for v in cells.values())
    if empty:
        return {
            "cells": cells,
            "state": "not_gradeable_empty_cell",
            "empty_cells": sorted(empty),
            "did_low": None, "did_high": None, "sign_survives": None,
            "band_covers": "disagreement_only",
            "ungradeable_outside_band": ung,
        }
    tp, tq = cells["treated_post"], cells["treated_pre"]
    cp, cq = cells["control_post"], cells["control_pre"]
    hi = (tp["rate_high"] - tq["rate_low"]) - (cp["rate_low"] - cq["rate_high"])
    lo = (tp["rate_low"] - tq["rate_high"]) - (cp["rate_high"] - cq["rate_low"])
    return {
        "cells": cells,
        "state": "graded",
        "did_low": lo,
        "did_high": hi,
        "sign_survives": lo > 0,
        # Named so nobody reads the band as covering the exclusions too.
        "band_covers": "disagreement_only",
        "ungradeable_outside_band": ung,
    }


def _selector(rule: str) -> Callable[[list[dict]], tuple[dict | None, str]]:
    def pick(rs: list[dict]) -> tuple[dict | None, str]:
        if rule == "population_order":
            return (rs[0] if rs else None), "orderable"
        keyed = [(_ms(r.get("closed_at")), r) for r in rs]
        if any(k is None for k, _ in keyed):
            # earliest/latest are UNDEFINED without a timeline. Falling back to
            # population order here is exactly the defect.
            return None, "unorderable"
        keyed.sort(key=lambda kv: kv[0])
        return (keyed[0][1] if rule == "earliest_close" else keyed[-1][1]), "orderable"
    return pick


def row_selection_spread(rows: list[dict], tol: float) -> dict:
    """Reproduce the three row-selection rules, ONLY to report their spread.

    This is a diagnostic about how much the undeclared choice would matter on
    THIS population. It is not an alternative to the declared rule and must not
    be used to pick whichever rule gives the nicest number -- the measured range
    is recorded in the module docstring precisely so that cannot be done quietly.
    """
    groups, _ = group_by_package(rows)
    out: dict[str, Any] = {"packages": len(groups), "by_rule": {}}
    for rule in ROW_SELECTION_RULES:
        pick = _selector(rule)
        counts = {"reached_stop": 0, "reached_target": 0, "neither": 0,
                  "ungradeable_row": 0, "unorderable": 0}
        for rs in groups.values():
            row, state = pick(rs)
            if state == "unorderable" or row is None:
                counts["unorderable"] += 1
                continue
            v = adjudicate_exit(row, tol)
            counts[v if v in GRADEABLE_ROW_EXITS else "ungradeable_row"] += 1
        gradeable = counts["reached_stop"] + counts["reached_target"] + counts["neither"]
        counts["stop_rate"] = (counts["reached_stop"] / gradeable) if gradeable else None
        counts["gradeable_packages"] = gradeable
        out["by_rule"][rule] = counts
    rates = [c["stop_rate"] for c in out["by_rule"].values() if c["stop_rate"] is not None]
    out["spread_pp"] = ((max(rates) - min(rates)) * 100.0) if len(rates) > 1 else None
    out["spread_verdict"] = spread_verdict(out["spread_pp"])
    return out


def spread_verdict(spread_pp: float | None) -> str:
    """⚠️ Agreement is a property of THIS population, never of the code."""
    if spread_pp is None:
        return "not_measured_fewer_than_two_rules_reported"
    if spread_pp == 0.0:
        return ("selections_agree_on_this_population_only__closes_nothing__"
                "they_agree_whenever_no_fanout_package_has_divergent_closes")
    return "selections_disagree__row_selection_is_load_bearing_here"


def restate_u15(trades_path: str, pkgs_path: str, tol: float) -> dict:
    """Re-state MI-278 U15's published DiD under the DECLARED rule.

    Imports U15's OWN arm splitter (`stop_integrity_both_arms.build`) unmodified,
    so the population, the era split and the leg membership are its choices and a
    difference in the answer is attributable to the UNIT RULE and nothing else.
    That is the same discipline U19 used when re-deriving U18.
    """
    from research.stop_integrity_both_arms import build, census

    with open(trades_path) as fh:
        trades = json.load(fh)
    with open(pkgs_path) as fh:
        raw = json.load(fh)
    pk_rows = raw["rows"] if isinstance(raw, dict) and "rows" in raw else raw
    pkgs = {p["order_package_id"]: p for p in pk_rows if p.get("order_package_id")}
    by_id = {r["id"]: r for r in trades}

    cells: dict[str, list[dict]] = {}
    u15_census: dict[str, dict] = {}
    for group, arm in (("e35", "treated"), ("untouched_control", "control")):
        b = build(trades, pkgs, group, tol)
        for era in ("pre", "post"):
            cells["%s_%s" % (arm, era)] = [by_id[t] for u in b[era]
                                           for t in u["trade_ids"] if t in by_id]
            u15_census["%s_%s" % (arm, era)] = {
                k: v for k, v in census(b[era]).items() if not isinstance(v, (list, dict))
            }

    band = did_band(cells["treated_pre"], cells["treated_post"],
                    cells["control_pre"], cells["control_post"], tol)
    spread = {k: row_selection_spread(v, tol) for k, v in cells.items()}
    points = {}
    for rule in ROW_SELECTION_RULES:
        def rate(cell: str) -> float | None:
            return spread[cell]["by_rule"][rule]["stop_rate"]
        vals = [rate(c) for c in ("treated_post", "treated_pre",
                                  "control_post", "control_pre")]
        if any(v is None for v in vals):
            points[rule] = None          # a rule that cannot report says so
        else:
            points[rule] = (vals[0] - vals[1]) - (vals[2] - vals[3])
    return {
        "population": {"trades_rows": len(trades), "packages_indexed": len(pkgs),
                       "tol": tol},
        "u15_own_census_on_this_pull": u15_census,
        "declared_rule_band": band,
        "row_selection_points_pp": {k: (None if v is None else round(v * 100, 1))
                                    for k, v in points.items()},
        "note": ("row_selection_points_pp is reproduced ONLY so the spread stays "
                 "checkable; the declared answer is declared_rule_band."),
    }


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _row(pid, direction="long", px=None, sl=None, tp=None, closed=None):
    return {"order_package_id": pid, "direction": direction, "exit_price": px,
            "stop_loss": sl, "take_profit_1": tp, "closed_at": closed}


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

    print("fanout-exit-unit self-test")
    tol = 0.001

    # --- the declared rule -------------------------------------------------
    stop = _row("p1", px=90.0, sl=90.0, tp=110.0, closed=1000)
    stop2 = _row("p1", px=90.0, sl=90.0, tp=110.0, closed=2000)
    targ = _row("p1", px=110.0, sl=90.0, tp=110.0, closed=3000)
    nada = _row("p1", px=100.0, sl=90.0, tp=110.0, closed=4000)
    blind = _row("p1", px=None, sl=90.0, tp=110.0, closed=5000)

    v, ex, d = package_exit_verdict([stop, stop2], tol)
    ck("1 two siblings both stop -> unanimous/reached_stop", (v, ex), ("unanimous", "reached_stop"))
    ck("2 unanimous reports both rows", d["rows"], 2)

    v, ex, _ = package_exit_verdict([stop, targ], tol)
    ck("3 stop vs target -> disagreement", v, "disagreement")
    ck("4 a disagreement carries NO exit (never arbitrated)", ex, None)

    v, ex, _ = package_exit_verdict([stop, nada], tol)
    ck("5 stop vs neither is also a disagreement", v, "disagreement")

    v, ex, _ = package_exit_verdict([blind], tol)
    ck("6 sole ungradeable row -> ungradeable", (v, ex), ("ungradeable", None))

    v, ex, _ = package_exit_verdict([stop, blind], tol)
    ck("7 one gradeable + one blind -> unanimous on the gradeable one",
       (v, ex), ("unanimous", "reached_stop"))

    v, _, _ = package_exit_verdict([blind, dict(blind)], tol)
    ck("8 all-blind package is ungradeable, NOT 'neither'", v, "ungradeable")

    # order-independence: the whole point
    a, _, _ = package_exit_verdict([stop, targ, nada], tol)
    b, _, _ = package_exit_verdict([nada, targ, stop], tol)
    c, _, _ = package_exit_verdict([targ, stop, nada], tol)
    ck("9 verdict is INVARIANT under input order", (a, b, c),
       ("disagreement", "disagreement", "disagreement"))
    a2, e2, _ = package_exit_verdict([stop, stop2], tol)
    b2, e3, _ = package_exit_verdict([stop2, stop], tol)
    ck("10 unanimous exit is invariant under input order too", (a2, e2), (b2, e3))

    ck("11 the three verdicts are exactly the declared vocabulary",
       sorted(PACKAGE_VERDICTS), sorted(["unanimous", "disagreement", "ungradeable"]))

    # --- grouping ----------------------------------------------------------
    g, st = group_by_package([_row("p1"), _row("p1"), _row("p2")])
    ck("12 rows group by package id", (len(g), st["packaged"]), (2, 3))
    g, st = group_by_package([_row(None), _row(None)])
    ck("13 two unpackaged rows do NOT merge into one group", len(g), 2)
    ck("14 unpackaged rows are counted, never dropped", st["unpackaged"], 2)
    g, st = group_by_package([_row("p1"), _row(None)])
    ck("15 mixed grouping keeps both states apart", (st["packaged"], st["unpackaged"]), (1, 1))
    ck("16 empty input groups to nothing", group_by_package([])[0], {})

    # --- census ------------------------------------------------------------
    cen = package_census([stop, stop2, _row("p2", px=110.0, sl=90.0, tp=110.0)], tol)
    ck("17 census counts packages not rows", (cen["rows_in"], cen["packages"]), (3, 2))
    ck("18 census names the fan-out packages", cen["fanout_packages"], 1)
    ck("19 census reports row inflation", round(cen["row_inflation"], 3), 1.5)
    ck("20 census verdict counts", cen["verdict_counts"]["unanimous"], 2)
    ck("21 census declares which rule produced it",
       cen["rule"], "package_is_the_unit_no_row_selection")

    # --- the band ----------------------------------------------------------
    def stops(pid_prefix, n):
        return [_row("%s%d" % (pid_prefix, i), px=90.0, sl=90.0, tp=110.0) for i in range(n)]

    def flats(pid_prefix, n):
        return [_row("%s%d" % (pid_prefix, i), px=100.0, sl=90.0, tp=110.0) for i in range(n)]

    rb = rate_band(stops("s", 2) + flats("f", 2), tol)
    ck("22 a clean cell's band collapses to a point",
       (rb["rate_low"], rb["rate_high"]), (0.5, 0.5))
    ck("23 a clean cell has no disagreements", rb["disagreement"], 0)

    # treated goes 0 -> 1, control stays 0: DiD = +1.0, no disagreements anywhere
    b = did_band(flats("tq", 2), stops("tp", 2), flats("cq", 2), flats("cp", 2), tol)
    ck("24 four clean cells: the DiD band collapses to a point",
       (b["did_low"], b["did_high"]), (1.0, 1.0))
    ck("25 four clean cells: sign survives", b["sign_survives"], True)
    ck("26 the DiD reports all four cells by name",
       sorted(b["cells"]), ["control_post", "control_pre", "treated_post", "treated_pre"])

    # a disagreement in the CONTROL POST cell widens the band
    dis = [_row("cd", px=90.0, sl=90.0, tp=110.0), _row("cd", px=110.0, sl=90.0, tp=110.0)]
    b2 = did_band(flats("tq", 2), stops("tp", 2), flats("cq", 2), flats("cp", 2) + dis, tol)
    ck("27 a control-post disagreement widens the band", b2["did_low"] < b2["did_high"], True)
    ck("28 the disagreement stays in that cell's denominator",
       b2["cells"]["control_post"]["denominator"], 3)
    ck("29 a widened band still names what it does not cover",
       b2["band_covers"], "disagreement_only")

    # CONTROL: the SAME disagreement moved to control_pre must move the band the OTHER way
    b3 = did_band(flats("tq", 2), stops("tp", 2), flats("cq", 2) + dis, flats("cp", 2), tol)
    ck("30 CONTROL: which cell holds the disagreement changes the band's direction",
       (round(b2["did_low"], 4) == round(b3["did_low"], 4)), False)

    # a sign that does NOT survive: treated barely moves, control disagreement can swamp it
    b4 = did_band(flats("tq", 2), flats("tp", 2), flats("cq", 2), flats("cp", 2) + dis, tol)
    ck("31 a flat treated arm cannot survive a control that may have risen",
       b4["sign_survives"], False)

    ck("32 an empty cell is NOT graded as zero",
       did_band([], stops("tp", 2), flats("cq", 2), flats("cp", 2), tol)["state"],
       "not_gradeable_empty_cell")
    ck("33 an empty cell is NAMED, not just flagged",
       did_band([], stops("tp", 2), flats("cq", 2), flats("cp", 2), tol)["empty_cells"],
       ["treated_pre"])
    ck("34 an empty cell yields no sign verdict",
       did_band([], stops("tp", 2), flats("cq", 2), flats("cp", 2), tol)["sign_survives"], None)

    b5 = did_band(flats("tq", 2), stops("tp", 2), flats("cq", 2),
                  flats("cp", 2) + [_row("cz", px=None, sl=90.0, tp=110.0)], tol)
    ck("35 ungradeable packages leave the denominator",
       b5["cells"]["control_post"]["denominator"], 2)
    ck("36 ...and are counted outside the band", b5["ungradeable_outside_band"], 1)

    # ⚠️ These two exist because PLANTING the mislabelled-DiD defect (returning
    # `treated_post - control_post` while calling it a DiD) failed to trip ANY
    # control: every fixture above has both `pre` cells at zero, where the
    # four-cell formula and the two-arm one coincide. A harness that cannot see
    # the defect its module was written to refuse is not a harness.
    mixed = did_band(stops("mq", 1) + flats("mqf", 3),    # treated_pre  = 0.25
                     stops("mp", 3) + flats("mpf", 1),    # treated_post = 0.75
                     stops("nq", 2) + flats("nqf", 2),    # control_pre  = 0.50
                     stops("np", 3) + flats("npf", 1),    # control_post = 0.75
                     tol)
    ck("37 CONTROL: a four-cell DiD is (Tpost-Tpre)-(Cpost-Cpre), not Tpost-Cpost",
       (round(mixed["did_low"], 4), round(mixed["did_high"], 4)), (0.25, 0.25))
    ck("38 CONTROL: ...and that answer is NOT the two-arm difference (which is 0.0)",
       round(mixed["did_low"], 4) == 0.0, False)

    # --- the spread diagnostic --------------------------------------------
    # one fan-out package whose siblings disagree AND close at different times
    div = [_row("d1", px=90.0, sl=90.0, tp=110.0, closed=1000),
           _row("d1", px=110.0, sl=90.0, tp=110.0, closed=9000)]
    sp = row_selection_spread(div, tol)
    ck("39 earliest picks the stop", sp["by_rule"]["earliest_close"]["reached_stop"], 1)
    ck("40 latest picks the target", sp["by_rule"]["latest_close"]["reached_target"], 1)
    ck("41 the two rules therefore differ by the full 100pp", sp["spread_pp"], 100.0)
    ck("42 a real spread is reported as load-bearing",
       sp["spread_verdict"], "selections_disagree__row_selection_is_load_bearing_here")

    agree = [_row("a1", px=90.0, sl=90.0, tp=110.0, closed=1000),
             _row("a1", px=90.0, sl=90.0, tp=110.0, closed=9000)]
    sp2 = row_selection_spread(agree, tol)
    ck("43 agreement is reported as closing NOTHING",
       sp2["spread_verdict"].startswith("selections_agree_on_this_population_only__closes_nothing"),
       True)
    ck("44 ...and its spread really is zero", sp2["spread_pp"], 0.0)

    no_time = [_row("n1", px=90.0, sl=90.0, tp=110.0, closed=None),
               _row("n1", px=110.0, sl=90.0, tp=110.0, closed=None)]
    sp3 = row_selection_spread(no_time, tol)
    ck("45 without closed_at, earliest is UNORDERABLE, not a silent fallback",
       sp3["by_rule"]["earliest_close"]["unorderable"], 1)
    ck("46 ...while population order still answers (that IS the defect)",
       sp3["by_rule"]["population_order"]["reached_stop"], 1)
    ck("47 an unorderable rule reports no stop_rate rather than 0.0",
       sp3["by_rule"]["latest_close"]["stop_rate"], None)

    # population order is order-DEPENDENT -- the control proving the defect is real
    sp4 = row_selection_spread(list(reversed(div)), tol)
    ck("48 CONTROL: population order flips when the input is reversed",
       (sp["by_rule"]["population_order"]["reached_stop"],
        sp4["by_rule"]["population_order"]["reached_stop"]), (1, 0))
    cen_a = package_census(div, tol)
    cen_b = package_census(list(reversed(div)), tol)
    ck("49 CONTROL: the DECLARED rule does not flip on the same reversal",
       cen_a["verdict_counts"], cen_b["verdict_counts"])

    ck("50 ISO closed_at parses", _ms("2026-09-12T00:00:00Z") > 0, True)
    ck("51 unparseable closed_at is None, never 0", _ms("not-a-date"), None)
    ck("52 epoch-ms passes through", _ms(1757635200000), 1757635200000)
    ck("53 empty string is unreadable, not epoch 0", _ms(""), None)

    ck("54 spread_verdict declares its own not-measured state",
       spread_verdict(None), "not_measured_fewer_than_two_rules_reported")

    print("\nself-test: PASS %d · FAIL %d" % (ok, fail))
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--trades", help="JSON array from /api/diag/journal?table=trades")
    ap.add_argument("--packages", help="JSON from /api/diag/journal?table=order_packages")
    ap.add_argument("--restate-u15", action="store_true",
                    help="re-state MI-278 U15's DiD under the declared rule "
                         "(needs --trades and --packages)")
    ap.add_argument("--tol", type=float, default=0.001)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.restate_u15:
        if not (a.trades and a.packages):
            ap.error("--restate-u15 needs both --trades and --packages")
        print(json.dumps(restate_u15(a.trades, a.packages, a.tol), indent=2))
        return 0
    if not a.trades:
        ap.error("pass --trades <file.json>, --restate-u15, or --self-test")
    with open(a.trades) as fh:
        rows = json.load(fh)
    print(json.dumps({
        "census": {k: v for k, v in package_census(rows, a.tol).items()
                   if k != "packages_detail"},
        "row_selection_spread": row_selection_spread(rows, a.tol),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
