#!/usr/bin/env python3
# wiring: manual-only — a one-shot re-derivation answering "does the e35 break
#   attribution's one-sided binomial survive a PACKAGE denominator?". Run BY A
#   SESSION against a journal pull; scheduling it would re-ask a settled question
#   over a window nobody chose.
"""MI-278 U32 — re-derive `e35_break_attribution`'s binomial per ORDER PACKAGE.

THE DEBT THIS PAYS
------------------
`BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`
asks that a price-behaviour rate state its distinct package count beside its row
count, or be computed per package. MI-278 U12 named 21 surfaces; U19 paid the
dose table and U31 paid the bleed record. This pays the third:
`scripts/research/e35_break_attribution.py`, whose one inferential statement is a
one-sided binomial computed entirely on row counts.

WHAT IS DIFFERENT HERE, AND IT IS NOT A REPEAT OF U31
-----------------------------------------------------
U31's Fisher took its 2x2 cells from rows: one term, one substitution. This
statistic takes **TWO** terms from rows and they are not the same term:

    P(<= k wins in n | p)      k, n  come from the E35 arm's POST rows
                               p     is the CONTROL arm's OWN POST rate,
                                     itself a rows/rows quotient

So fan-out reaches the sample size AND the null hypothesis, by different paths,
and a single re-derivation cannot tell you which one moved the answer. This
module therefore computes **four cells** — every combination of (n from rows |
packages) x (p from rows | packages) — and classifies WHICH TERM the crossing of
alpha depends on. The two mixed cells are not decoration: they are the only way
to attribute the change, and reporting only rows-vs-packages would leave a reader
knowing the answer moved and not knowing why.

⚠️ THE ATTRIBUTION IS THRESHOLD-FREE ON PURPOSE. It does not compare magnitudes
against a chosen epsilon — a tuned constant here would be a judgement wearing the
clothes of a measurement. It asks only which substitutions carry the p-value
across ALPHA, which is the decision-relevant question and needs no free parameter.

⚠️ THE REDUCTION NEVER PICKS A ROW. Collapsing a package to one observation means
choosing among its rows, and MI-278 U15 measured that WHICH row you choose swings
the headline (4.3-28.2pp on identical data). So `package_win` grades whether the
package's rows AGREE and `disagreement` is its own state, reported and never
resolved by taking the first row, the real-money row, or the majority.

⚠️ IT GRADES **WINS**, WHICH IS WHY IT DOES NOT REUSE U19's `package_verdict`.
That function adjudicates EXIT verdicts (reached_stop / reached_target / neither)
from the price path. This one adjudicates a P&L SIGN, which is an account fact and
can legitimately differ across a package's rows — different fills, different fees,
different provenance. Reusing U19's grader would silently answer a different
question; the two are deliberately separate graders with the same *shape*.

WHAT IT DOES NOT DO
-------------------
It changes no published figure and re-grades nothing live. Both denominators are
reported side by side, which is the clause the backlog row actually asks for. It
does not touch the three live dashboard routes U12 named (`/api/bot/stats`,
`/api/bot/performance`, `/api/bot/attribution`) — those are `src/web/`, Tier-2,
and changing a number the operator reads is a decision, not a side effect.

Usage:
  python3 scripts/research/e35_binomial_package_denominator.py --self-test
  python3 scripts/research/e35_binomial_package_denominator.py --journal j.json
  python3 scripts/research/e35_binomial_package_denominator.py --journal j.json --account bybit_1
  python3 scripts/research/e35_binomial_package_denominator.py --journal j.json --json
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import os
import pathlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Import the SAME adjudicators, never a second copy — a second copy of the
# population filter or the arm rule is how two analyses of one event come to
# disagree about which rows they were even looking at.
BA = _load("_e35_break_u32", "e35_break_attribution.py")
PD = _load("_pkg_rederive_u32", "package_denominator_rederive.py")

population = BA.population
arm_of = BA.arm_of
ARMS = BA.ARMS
DEPLOY = BA.DEPLOY
_parse = BA._parse
by_package = PD.by_package

#: Significance threshold. The value `e35_break_attribution`'s reader is
#: implicitly applying when they call 0.0586 "not quite there".
ALPHA = 0.05

#: How a PACKAGE's rows resolve to one win/loss observation. Never collapsed,
#: and `disagreement` is deliberately NOT resolved — see the module docstring.
PACKAGE_WIN_STATES = (
    "unanimous",      # every gradeable row in the package has the same pnl sign
    "disagreement",   # gradeable rows disagree — reported, never arbitrated
    "ungradeable",    # no row in the package carries a usable pnl
)

#: Why a package could not be assigned to one (arm, era) cell. These are NOT
#: dropped silently: each is censused by name, because a package straddling the
#: deploy boundary or two arms is a finding about the data, not a nuisance.
PACKAGE_PLACEMENT_STATES = ("placed", "mixed_arm", "mixed_era", "no_era")

#: Which substitution carries the p-value across ALPHA. Threshold-free: every
#: state is defined by a crossing, never by a magnitude.
DRIVER_STATES = (
    "no_crossing",     # nothing crosses: the published figure is genuinely stable
    "offsetting",      # the pure bases agree, but a MIXED cell crosses — the two
                       # substitutions push OPPOSITE ways and cancel, so the
                       # published figure is stable by coincidence, not by
                       # robustness. Collapsing this into `no_crossing` would
                       # report a cancellation as a confirmation.
    "n_alone",         # swapping only the sample size crosses; swapping only p does not
    "p_alone",         # swapping only the null rate crosses; swapping only n does not
    "either_alone",    # either substitution alone crosses
    "both_needed",     # only the joint substitution crosses
    "not_computable",  # some cell has an empty arm
)


def _win(row: dict):
    """True / False / None. `None` is *we could not look*, never a loss."""
    v = row.get("pnl")
    if v is None:
        return None
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return None


def package_win(rows: list[dict]) -> tuple[str, bool | None]:
    """(state, outcome). `outcome` is None unless the package is unanimous.

    A package with one gradeable row is unanimous by construction — that is not a
    special case, it is what unanimity means at n=1, and giving it its own state
    would split the population on a distinction with no content.
    """
    grades = [g for g in (_win(r) for r in rows) if g is not None]
    if not grades:
        return "ungradeable", None
    if len(set(grades)) == 1:
        return "unanimous", grades[0]
    return "disagreement", None


def _era(row: dict) -> str | None:
    t = _parse(row.get("created_at"))
    if t is None:
        return None
    return "pre" if t < DEPLOY else "post"


def package_cells(pop: list[dict]) -> dict:
    """Reduce the graded population to one observation per package.

    Returns a `cells` map keyed `(arm, era)` holding `{n, wins}`, plus a census
    naming every package that could not be placed and why.
    """
    pkgs, rows_without_package = by_package(pop)
    cells: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"n": 0, "wins": 0})
    census: collections.Counter = collections.Counter()
    disagreeing: list[str] = []
    straddling: list[str] = []
    for pid, rs in sorted(pkgs.items()):
        arms = {arm_of(str(r.get("strategy_name") or "")) for r in rs}
        eras = {_era(r) for r in rs}
        if len(arms) > 1:
            census["mixed_arm"] += 1
            continue
        if None in eras:
            census["no_era"] += 1
            continue
        if len(eras) > 1:
            # A package whose rows straddle the deploy is REPORTED rather than
            # assigned: the whole design of the parent script is that geometry is
            # fixed at ENTRY, so a package with two entry eras has no single one.
            census["mixed_era"] += 1
            straddling.append(pid)
            continue
        state, outcome = package_win(rs)
        census[f"win_{state}"] += 1
        if state != "unanimous":
            if state == "disagreement":
                disagreeing.append(pid)
            continue
        census["placed"] += 1
        cell = cells[f"{arms.pop()}|{eras.pop()}"]
        cell["n"] += 1
        cell["wins"] += 1 if outcome else 0
    return {
        "cells": {k: dict(v) for k, v in sorted(cells.items())},
        "packages_seen": len(pkgs),
        "rows_without_package": rows_without_package,
        "placement": dict(census),
        "packages_disagreeing": disagreeing,
        "packages_straddling_deploy": straddling,
    }


def row_cells(pop: list[dict]) -> dict:
    """The same cells on the ROW denominator — the parent script's own basis."""
    cells: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"n": 0, "wins": 0})
    census: collections.Counter = collections.Counter()
    for r in pop:
        era = _era(r)
        if era is None:
            census["no_era"] += 1
            continue
        g = _win(r)
        if g is None:
            census["win_ungradeable"] += 1
            continue
        cell = cells[f"{arm_of(str(r.get('strategy_name') or ''))}|{era}"]
        cell["n"] += 1
        cell["wins"] += 1 if g else 0
        census["placed"] += 1
    return {"cells": {k: dict(v) for k, v in sorted(cells.items())},
            "placement": dict(census)}


def binomial_le(k: int, n: int, p: float) -> float | None:
    """P(X <= k) for X ~ Bin(n, p). `None` when the question is not asked.

    The same summation the parent script performs inline, extracted so both this
    module and its tests grade the identical arithmetic. `n == 0` returns None
    rather than 1.0: with no trials there is no probability to report, and 1.0
    reads as "certainly not worse than expected", which is the opposite of
    "we did not look".
    """
    if n <= 0 or k < 0 or k > n or not (0.0 <= p <= 1.0):
        return None
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def _cell(basis: dict, arm: str, era: str) -> dict:
    return basis["cells"].get(f"{arm}|{era}", {"n": 0, "wins": 0})


def four_cells(rows_basis: dict, pkg_basis: dict) -> dict:
    """Every combination of (n from rows | packages) x (p from rows | packages).

    Named `<n-basis>_<p-basis>`: `rows_rows` is the parent script's published
    figure and `packages_packages` is the fully re-derived one. The two mixed
    cells are what makes the change attributable to a term.
    """
    src = {"rows": rows_basis, "packages": pkg_basis}
    out: dict[str, dict] = {}
    for n_basis in ("rows", "packages"):
        e = _cell(src[n_basis], "e35", "post")
        for p_basis in ("rows", "packages"):
            c = _cell(src[p_basis], "control_same_family", "post")
            p = (c["wins"] / c["n"]) if c["n"] else None
            val = binomial_le(e["wins"], e["n"], p) if p is not None else None
            out[f"{n_basis}_{p_basis}"] = {
                "n_basis": n_basis, "p_basis": p_basis,
                "k": e["wins"], "n": e["n"],
                "control_wins": c["wins"], "control_n": c["n"],
                "p_null": p,
                "p_value_one_sided": val,
                # `None`, never False: an uncomputable cell has not been shown
                # to be non-significant, and False would read as if it had.
                "significant": (val < ALPHA) if val is not None else None,
                # A null rate of exactly 0 or 1 makes the test VACUOUS — with
                # p=0 every P(X<=k) is 1.0, which is real arithmetic carrying no
                # evidence. Flagged rather than refused: the count behind it is
                # still worth reading, and refusing would hide that the control
                # arm won nothing at all.
                "degenerate_null": (p in (0.0, 1.0)) if p is not None else None,
            }
    return out


def driver(cells: dict) -> dict:
    """WHICH substitution carries the p-value across ALPHA. Threshold-free."""
    rr, pp = cells["rows_rows"], cells["packages_packages"]
    pr, rp = cells["packages_rows"], cells["rows_packages"]
    if any(c["significant"] is None for c in (rr, pp, pr, rp)):
        return {"state": "not_computable",
                "why": "at least one of the four cells has an empty arm"}
    base = rr["significant"]
    n_crosses = pr["significant"] != base
    p_crosses = rp["significant"] != base
    if pp["significant"] == base:
        if n_crosses or p_crosses:
            # NOT `no_crossing`. The two pure bases agreeing while a mixed cell
            # crosses means the substitutions cancel — a fact about fragility,
            # and the opposite of the reassurance `no_crossing` conveys.
            return {"state": "offsetting",
                    "why": (f"both pure bases read significant={base}, but the "
                            f"sample-size substitution alone crosses: {n_crosses} "
                            f"and the null-rate substitution alone crosses: "
                            f"{p_crosses} — the two terms move the p-value in "
                            f"OPPOSITE directions and cancel")}
        return {"state": "no_crossing",
                "why": (f"both the row basis and the package basis read "
                        f"significant={base} at alpha={ALPHA}, and neither "
                        f"substitution crosses alone")}
    if n_crosses and p_crosses:
        state = "either_alone"
    elif n_crosses:
        state = "n_alone"
    elif p_crosses:
        state = "p_alone"
    else:
        state = "both_needed"
    return {"state": state,
            "why": (f"significant {base} -> {pp['significant']}; "
                    f"sample-size substitution alone crosses: {n_crosses}; "
                    f"null-rate substitution alone crosses: {p_crosses}")}


#: Whether an arm's pre->post DELTA survives the change of denominator. A sign
#: flip is its own state because a reader takes the sign as the finding: a
#: control that reads "+0.8pp, flat" on rows and "-8.3pp, degrading" on packages
#: supports OPPOSITE conclusions about whether the break is e35-specific.
DELTA_STABILITY_STATES = ("stable", "sign_flip", "not_computable")


def _pct(cell: dict) -> float | None:
    """`None`, never 0.0, on an empty cell — 'no trades' is not 'no wins'."""
    return (100.0 * cell["wins"] / cell["n"]) if cell["n"] else None


def deltas(rows_basis: dict, pkg_basis: dict) -> dict:
    """Each arm's pre->post win-rate delta, computed BOTH ways.

    The p-value is the parent script's one inferential statement, but its
    RENDERED TABLE is what a reader actually quotes, and that table is deltas.
    Re-deriving the inference while leaving the description on the old
    denominator would answer the narrower half of the question.
    """
    out: dict[str, dict] = {}
    for arm in ARMS:
        row: dict = {}
        for basis, src in (("rows", rows_basis), ("packages", pkg_basis)):
            pre, post = _cell(src, arm, "pre"), _cell(src, arm, "post")
            a, b = _pct(pre), _pct(post)
            row[basis] = {
                "n_pre": pre["n"], "n_post": post["n"],
                "win_rate_pre": a, "win_rate_post": b,
                "delta_pp": (b - a) if (a is not None and b is not None) else None,
            }
        dr, dp = row["rows"]["delta_pp"], row["packages"]["delta_pp"]
        if dr is None or dp is None:
            row["stability"] = "not_computable"
        elif dr * dp < 0:
            row["stability"] = "sign_flip"
        else:
            row["stability"] = "stable"
        out[arm] = row
    return out


def report(rows: list[dict], account: str | None = None) -> dict:
    """The full re-derivation. `account_scope` is ALWAYS stated.

    U31 established that fan-out is CROSS-ACCOUNT, so a POOLED analysis is the
    exposed configuration and a single-account one is essentially immune. The
    parent script pools every bybit account, so its scope is stated here rather
    than left for a reader to infer from a filter three files away.
    """
    if account is not None:
        rows = [r for r in rows if str(r.get("account_id") or "") == account]
    pop, excluded = population(rows)
    rb, pb = row_cells(pop), package_cells(pop)
    cells = four_cells(rb, pb)
    n_pkg = pb["packages_seen"]
    return {
        "account_scope": account or "pooled:bybit_*",
        "alpha": ALPHA,
        "deploy": DEPLOY.isoformat(),
        "population_n_rows": len(pop),
        "population_n_packages": n_pkg,
        "inflation": round(len(pop) / n_pkg, 4) if n_pkg else None,
        "excluded": excluded,
        "rows_basis": rb,
        "packages_basis": pb,
        "cells": cells,
        "driver": driver(cells),
        "deltas": deltas(rb, pb),
    }


def render(v: dict) -> str:
    def fp(x):
        return "n/a" if x is None else format(x, ".4f")

    L = [f"e35-binomial-package-denominator: scope={v['account_scope']} "
         f"alpha={v['alpha']} split={v['deploy']}",
         f"  population: {v['population_n_rows']} rows over "
         f"{v['population_n_packages']} packages (inflation {v['inflation']}x)",
         f"  excluded:   {v['excluded']}",
         f"  placement:  {v['packages_basis']['placement']}",
         f"  rows with no package id: {v['packages_basis']['rows_without_package']}",
         ""]
    L.append(f"  {'n basis':>9} {'p basis':>9} | {'k':>4} {'n':>4} | "
             f"{'p_null':>7} | {'p-value':>8} | sig")
    for key in ("rows_rows", "packages_rows", "rows_packages", "packages_packages"):
        c = v["cells"][key]
        sig = "n/a" if c["significant"] is None else ("YES" if c["significant"] else "no")
        pn = "n/a" if c["p_null"] is None else format(c["p_null"], ".3f")
        flag = "  ⚠️ p_null is degenerate — the test is vacuous" \
            if c["degenerate_null"] else ""
        L.append(f"  {c['n_basis']:>9} {c['p_basis']:>9} | {c['k']:>4} {c['n']:>4} | "
                 f"{pn:>7} | {fp(c['p_value_one_sided']):>8} | {sig}{flag}")
    L += ["", f"  driver: {v['driver']['state']} — {v['driver']['why']}", ""]
    L.append("  the RENDERED table a reader quotes — each arm's delta, both ways:")
    L.append(f"  {'arm':22} {'rows':>18} {'packages':>18}   stability")
    for arm, d in v["deltas"].items():
        def g(b):
            x = d[b]["delta_pp"]
            return ("n/a" if x is None
                    else f"{x:+.1f}pp ({d[b]['n_pre']}->{d[b]['n_post']})")
        mark = "  ⚠️ SIGN FLIP" if d["stability"] == "sign_flip" else ""
        L.append(f"  {arm:22} {g('rows'):>18} {g('packages'):>18}   "
                 f"{d['stability']}{mark}")
    L.append("")
    dis = v["packages_basis"]["packages_disagreeing"]
    stra = v["packages_basis"]["packages_straddling_deploy"]
    L.append(f"  packages whose rows DISAGREE on win/loss: {len(dis)}"
             + (f" {dis[:6]}" if dis else " — none; reported, never arbitrated"))
    L.append(f"  packages STRADDLING the deploy boundary:  {len(stra)}"
             + (f" {stra[:6]}" if stra else " — none"))
    L += ["",
          "  ⚠️ THIS RE-DERIVES A DENOMINATOR, IT DOES NOT REVERSE A VERDICT. The",
          "     parent script's own caution stands: the e35 post arm is small and",
          "     the family-matched control's PRE arm is smaller. A package basis",
          "     makes the n honest; it does not make it large."]
    return "\n".join(L)


def _self_test() -> int:
    fails: list[str] = []

    def ok(c, label):
        if not c:
            fails.append(label)
        print(f"  {'ok ' if c else 'FAIL'} {label}")

    def row(leg, era, pnl, pid, acct="bybit_1", **kw):
        when = "2026-08-01T00:00:00Z" if era == "pre" else "2026-09-01T00:00:00Z"
        d = {"strategy_name": leg, "created_at": when, "pnl": pnl, "status": "closed",
             "is_backtest": 0, "account_id": acct, "order_package_id": pid}
        d.update(kw)
        return d

    E, C = "trend_donchian", "trend_donchian_eth"

    # --- _win: three states, and None is never a loss -----------------------
    ok(_win({"pnl": 1.0}) is True and _win({"pnl": -1.0}) is False,
       "_win reads a pnl sign")
    ok(_win({"pnl": 0.0}) is False, "a flat close is not a win (strict >0, as the parent)")
    ok(_win({"pnl": None}) is None and _win({"pnl": "x"}) is None,
       "_win returns None — 'we could not look' — never False")

    # --- package_win: the reduction, and what it refuses to do --------------
    ok(package_win([{"pnl": 1.0}, {"pnl": 2.0}]) == ("unanimous", True),
       "a package whose rows agree reduces to one observation")
    ok(package_win([{"pnl": 1.0}]) == ("unanimous", True),
       "…a one-row package is unanimous by construction, not a special case")
    ok(package_win([{"pnl": 1.0}, {"pnl": -1.0}]) == ("disagreement", None),
       "…and one that DISAGREES yields no outcome — never arbitrated")
    ok(package_win([{"pnl": None}, {"pnl": None}]) == ("ungradeable", None),
       "…and one with no usable pnl is ungradeable, distinct from disagreement")
    ok(package_win([{"pnl": None}, {"pnl": -1.0}]) == ("unanimous", False),
       "…an ungradeable row does not veto a package its gradeable rows agree on")

    # --- binomial_le: the arithmetic, and where it refuses ------------------
    ok(abs(binomial_le(0, 1, 0.5) - 0.5) < 1e-12, "binomial_le(0,1,.5) == 0.5")
    ok(abs(binomial_le(2, 2, 0.3) - 1.0) < 1e-12, "…and P(X<=n) == 1")
    ok(binomial_le(0, 0, 0.5) is None,
       "…and n=0 returns None, NOT 1.0 — no trials is not 'certainly fine'")
    ok(binomial_le(3, 2, 0.5) is None and binomial_le(1, 2, 1.4) is None,
       "…and an impossible k or p is refused rather than computed")

    # --- fan-out actually collapses -----------------------------------------
    v = report([row(E, "post", -1.0, "P1", "bybit_1"),
                row(E, "post", -1.0, "P1", "bybit_2"),
                row(E, "post", -1.0, "P1", "bybit_portfolio"),
                row(C, "post", 1.0, "P2", "bybit_1"),
                row(C, "post", -1.0, "P3", "bybit_1")])
    ok(v["population_n_rows"] == 5 and v["population_n_packages"] == 3,
       "5 fanned-out rows reduce to 3 packages")
    ok(v["cells"]["rows_rows"]["n"] == 3 and v["cells"]["packages_packages"]["n"] == 1,
       "…and the e35 arm's n falls 3 -> 1 while the control's does not")
    ok(v["inflation"] == round(5 / 3, 4),
       "…inflation is reported as rows/packages (rounded, so assert the rounding)")

    # --- a disagreeing package is dropped from n AND named ------------------
    v = report([row(E, "post", 1.0, "D1", "bybit_1"),
                row(E, "post", -1.0, "D1", "bybit_2"),
                row(C, "post", 1.0, "P2")])
    ok(v["cells"]["packages_packages"]["n"] == 0,
       "a disagreeing package contributes no observation")
    ok(v["packages_basis"]["packages_disagreeing"] == ["D1"],
       "…and is named, so a shrinking denominator is never silent")
    ok(v["packages_basis"]["placement"].get("win_disagreement") == 1,
       "…and censused by name")

    # --- a package straddling the deploy is REPORTED, never assigned --------
    v = report([row(E, "pre", 1.0, "S1"), row(E, "post", 1.0, "S1"),
                row(C, "post", 1.0, "P2")])
    ok(v["packages_basis"]["packages_straddling_deploy"] == ["S1"],
       "a package whose rows straddle the deploy is named")
    ok(v["packages_basis"]["placement"].get("mixed_era") == 1
       and v["cells"]["packages_packages"]["n"] == 0,
       "…and placed in no era rather than guessed into one")

    # --- a row with no package id is counted, never synthesised -------------
    v = report([row(E, "post", -1.0, None), row(E, "post", -1.0, "P1"),
                row(C, "post", 1.0, "P2")])
    ok(v["packages_basis"]["rows_without_package"] == 1
       and v["packages_basis"]["packages_seen"] == 2,
       "a row with no package id is counted apart, never given a synthetic one")

    # --- the ROW basis reproduces the parent script exactly -----------------
    fixture = ([row(E, "post", -1.0, f"E{i}") for i in range(19)]
               + [row(E, "post", 1.0, "E19")]
               + [row(C, "post", 1.0, f"C{i}") for i in range(5)]
               + [row(C, "post", -1.0, f"K{i}") for i in range(19)])
    parent = BA.grade(fixture)
    mine = report(fixture)
    ok(abs(parent["p_value_one_sided"] - mine["cells"]["rows_rows"]["p_value_one_sided"])
       < 1e-12,
       "the rows_rows cell reproduces the parent script's published p EXACTLY")
    ok(parent["arms"]["e35"]["n_post"] == mine["cells"]["rows_rows"]["n"],
       "…on the same n, so a difference can only come from the denominator")

    # --- the four cells are genuinely four, and the driver attributes -------
    #     n_alone: shrinking the e35 arm alone flips significance.
    v = report([row(E, "post", -1.0, "E1", a) for a in
                ("bybit_1", "bybit_2", "bybit_portfolio")]
               + [row(E, "post", -1.0, f"E{i}", "bybit_1") for i in range(2, 18)]
               # The CONTROL arm must fan out too, or its row rate and its
               # package rate coincide and the p-basis axis is untested — a
               # planted "tie p to the n basis" defect passed silently until
               # this fixture was widened.
               + [row(C, "post", 1.0, "CX", a) for a in
                  ("bybit_1", "bybit_2", "bybit_portfolio")]
               + [row(C, "post", -1.0, f"K{i}") for i in range(6)])
    c = v["cells"]
    ok(c["rows_rows"]["p_null"] != c["rows_packages"]["p_null"],
       "the control arm's row rate and package rate genuinely differ in this "
       "fixture — without that the p-basis axis is untestable")
    ok(c["rows_rows"]["n"] != c["packages_rows"]["n"]
       and c["rows_rows"]["p_null"] == c["packages_rows"]["p_null"],
       "the packages_rows cell moves n and holds p — it isolates the sample size")
    ok(c["rows_rows"]["n"] == c["rows_packages"]["n"]
       and c["packages_packages"]["n"] == c["packages_rows"]["n"],
       "…and rows_packages holds n, so the two mixed cells isolate opposite terms")
    ok(c["packages_packages"]["p_null"] == c["rows_packages"]["p_null"],
       "…and the p axis is carried by p_basis alone, never by n_basis")
    ok(v["driver"]["state"] in DRIVER_STATES, "the driver reports a declared state")

    #     offsetting: the pure bases agree while a MIXED cell crosses — the
    #     live pooled population does exactly this, so it is pinned here.
    #     Built to REPRODUCE THE LIVE POOLED SHAPE exactly — e35 1 win over 20
    #     rows / 14 packages, control 5 of 24 rows (0.208) but 4 of 16 packages
    #     (0.250) — so the assertion below is a literal state, not a
    #     re-implementation of the rule checking itself.
    e35 = ([row(E, "post", 1.0, "EW")]                      # the single win
           + [row(E, "post", -1.0, f"EP{i}", a)             # 6 packages x 2 rows
              for i in range(6) for a in ("bybit_1", "bybit_2")]
           + [row(E, "post", -1.0, f"ES{i}") for i in range(7)])   # 7 x 1 row
    ctl = ([row(C, "post", 1.0, "CW0", a) for a in ("bybit_1", "bybit_2")]
           + [row(C, "post", 1.0, f"CW{i}") for i in range(1, 4)]
           + [row(C, "post", -1.0, f"CP{i}", a)
              for i in range(7) for a in ("bybit_1", "bybit_2")]
           + [row(C, "post", -1.0, f"CS{i}") for i in range(5)])
    v = report(e35 + ctl)
    c = v["cells"]
    ok((c["rows_rows"]["k"], c["rows_rows"]["n"]) == (1, 20)
       and (c["packages_packages"]["k"], c["packages_packages"]["n"]) == (1, 14),
       "the offsetting fixture reproduces the live e35 arm: 1 of 20 rows / 1 of 14 pkgs")
    ok(abs(c["rows_rows"]["p_null"] - 5 / 24) < 1e-12
       and abs(c["packages_packages"]["p_null"] - 0.25) < 1e-12,
       "…and the live control rates: 0.208 on rows, 0.250 on packages")
    ok(c["rows_rows"]["significant"] is False
       and c["packages_packages"]["significant"] is False
       and c["rows_packages"]["significant"] is True,
       "…so both PURE bases read not-significant while a MIXED cell crosses alpha")
    ok(v["driver"]["state"] == "offsetting",
       "…and that is graded `offsetting`, NOT `no_crossing`: the substitutions "
       "cancel, so the published figure is stable by coincidence")

    #     degenerate_null: a control arm that won nothing makes the test vacuous.
    v = report([row(E, "post", -1.0, f"E{i}") for i in range(5)]
               + [row(C, "post", -1.0, f"K{i}") for i in range(4)])
    ok(v["cells"]["rows_rows"]["p_null"] == 0.0
       and v["cells"]["rows_rows"]["degenerate_null"] is True,
       "a control arm with zero wins is FLAGGED degenerate, not silently reported")
    ok(v["cells"]["rows_rows"]["p_value_one_sided"] == 1.0,
       "…and its p is 1.0, which is real arithmetic carrying no evidence")
    ok("vacuous" in render(v),
       "…and render says so on the line, where a reader cannot miss it")
    v2 = report([row(E, "post", -1.0, "E1"), row(C, "post", 1.0, "C1"),
                 row(C, "post", -1.0, "K1")])
    ok(v2["cells"]["rows_rows"]["degenerate_null"] is False,
       "…while an ordinary null rate is not flagged (the flag has a negative case)")
    ok(report([row(E, "post", -1.0, "E1")])["cells"]["rows_rows"]["degenerate_null"]
       is None,
       "…and an absent null rate is None — 'we could not look', never False")

    #     no_crossing: both bases land the same side of ALPHA.
    v = report([row(E, "post", 1.0, f"E{i}") for i in range(10)]
               + [row(C, "post", 1.0, f"C{i}") for i in range(5)]
               + [row(C, "post", -1.0, f"K{i}") for i in range(5)])
    ok(v["driver"]["state"] == "no_crossing",
       "…and reports no_crossing when the substitution changes no decision")

    #     not_computable: an empty control arm refuses rather than dividing.
    v = report([row(E, "post", -1.0, "E1")])
    ok(v["cells"]["rows_rows"]["p_value_one_sided"] is None
       and v["cells"]["rows_rows"]["significant"] is None,
       "an empty control arm yields p=None AND significant=None, never False")
    ok(v["driver"]["state"] == "not_computable",
       "…and the driver refuses rather than attributing a change it cannot see")

    # --- scope is always stated, and filtering works ------------------------
    both = [row(E, "post", -1.0, "P1", "bybit_1"), row(E, "post", -1.0, "P1", "bybit_2"),
            row(C, "post", 1.0, "P2", "bybit_1")]
    ok(report(both)["account_scope"] == "pooled:bybit_*",
       "an unscoped report NAMES itself pooled rather than leaving it implicit")
    v1 = report(both, account="bybit_1")
    ok(v1["account_scope"] == "bybit_1" and v1["population_n_rows"] == 2,
       "…and a scoped one filters and says so")
    ok(v1["inflation"] == 1.0,
       "…and a single-account scope has inflation 1.0 — fan-out is CROSS-account")

    # --- the parent's exclusions still apply --------------------------------
    v = report([row(E, "post", 1.0, "P1", exit_reason="netting_attributed"),
                row("pairs_sol_eth_a", "post", 1.0, "P2"),
                row(E, "post", 1.0, "P3", acct="ib_paper")])
    ok(v["population_n_rows"] == 0
       and set(v["excluded"]) == {"fabricated_close", "pairs_sleeve_exonerated",
                                  "off_venue"},
       "the parent's population filter is IMPORTED, not re-implemented")

    # --- deltas: the description, re-derived alongside the inference --------
    v = report(_offsetting := (
        [row(E, "pre", 1.0, "QP", a) for a in ("bybit_1", "bybit_2")]
        + [row(E, "post", -1.0, "QQ")]
        + [row(C, "pre", 1.0, "RP"), row(C, "post", -1.0, "RQ")]))
    d = v["deltas"]["e35"]
    ok(d["rows"]["n_pre"] == 2 and d["packages"]["n_pre"] == 1,
       "an arm's PRE side collapses under fan-out too, not just its POST side")
    ok(d["rows"]["delta_pp"] == -100.0 and d["packages"]["delta_pp"] == -100.0,
       "…and a delta unchanged by the collapse reads the same on both bases")
    ok(d["stability"] == "stable", "…and grades stable")

    #     sign_flip: the live family-matched control does this (+0.8 -> -8.3pp).
    #     Shaped like the live arm: the PRE side's LOSSES fan out, so collapsing
    #     raises the pre rate (20.0% -> 33.3%) while the post rate is unmoved.
    v = report([row(C, "pre", 1.0, "SW")]
               + [row(C, "pre", -1.0, f"SP{i}", a)
                  for i in range(2) for a in ("bybit_1", "bybit_2")]
               + [row(C, "post", 1.0, "TW")]
               + [row(C, "post", -1.0, f"TL{i}") for i in range(3)]
               + [row(E, "post", -1.0, "E1")])
    d = v["deltas"]["control_same_family"]
    ok(d["rows"]["delta_pp"] is not None and d["packages"]["delta_pp"] is not None
       and d["rows"]["delta_pp"] * d["packages"]["delta_pp"] < 0,
       "a fixture where the two bases' deltas have OPPOSITE signs")
    ok(d["stability"] == "sign_flip",
       "…is graded sign_flip, never pooled into stable — the SIGN is the finding")
    ok("SIGN FLIP" in render(v), "…and render marks it where a reader cannot miss it")

    v = report([row(E, "post", -1.0, "E1")])
    ok(v["deltas"]["e35"]["stability"] == "not_computable"
       and v["deltas"]["e35"]["rows"]["win_rate_pre"] is None,
       "an arm with an empty era grades not_computable and reports None, not 0.0")

    # --- render does not crash on the refusing case -------------------------
    ok("not_computable" in render(report([row(E, "post", -1.0, "E1")])),
       "render states the refusal instead of printing a bare n/a table")

    print(f"self-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", help="a saved /api/diag/journal?table=trades payload")
    ap.add_argument("--account", help="scope to ONE account_id (default: pooled bybit_*)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.journal:
        print("::error::--journal is required: save /api/diag/journal?table=trades&limit=1000 "
              "to a file and pass it. This script does NOT fetch, so it cannot report a "
              "verdict over a population it failed to read.", file=sys.stderr)
        return 2
    rows = json.loads(pathlib.Path(a.journal).read_text())
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("items") or []
    v = report(rows, account=a.account)
    print(json.dumps(v, indent=2) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
