#!/usr/bin/env python3
# wiring: manual-only — a one-shot re-derivation answering "does the inverted e35
#   dose result survive a PACKAGE denominator?". It is run BY A SESSION against a
#   journal pull; scheduling it would re-ask a settled question against a window
#   nobody chose.
"""MI-278 U19 — re-derive the e35 dose result per ORDER PACKAGE, and audit U18's own arms.

THE DEBT THIS PAYS, AND WHO NAMED IT
--------------------------------------
`BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`
has two clauses. MI-278 U12 discharged the second (name the surfaces) and left the
first — *a price-behaviour rate states its distinct package count beside its row
count, or is computed per package* — explicitly unpaid, naming the one place it
matters most:

    stop_width_counterfactual_2026_09_11.py's docstring states 'Every rate in this
    script is therefore reported per package' ... and dose_response() computes
    win_rate = wins/n and stop_rate with n = len(rs) (ROWS) while printing the
    distinct package count on the line immediately above. That is the function
    that produced the inverted dose result which
    BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS
    cites as evidence against a stop-width mechanism. ... a p<0.0001 on a row
    denominator inside a file promising package denominators should be re-derived
    before it is leaned on again.

Nobody re-derived it. This does.

WHY A PACKAGE IS THE RIGHT UNIT FOR *THIS* QUESTION AND NOT FOR EVERY QUESTION
-------------------------------------------------------------------------------
One signal fans out to several accounts; each writes its own `trades` row with the
same symbol, direction, entry and stop. Those rows share ONE price path, so for a
question about *what price did* they are not independent. They ARE independent for
questions about accounts — exposure, per-account PnL, routing — which is exactly
why the backlog row says a mechanical guard is the wrong shape and asks for the
denominator to be STATED instead.

⚠️ THE REDUCTION IS WHERE A NAIVE FIX GOES WRONG, so it is a first-class output
here rather than an implementation detail. Collapsing a package to one observation
means choosing among its rows, and MI-278 U15 measured that WHICH row you choose
swings the headline (4.3–28.2pp on identical data). So this module never picks:
it grades whether the package's rows AGREE, and a disagreement is its own state.
`disagreement` is reported, never resolved by taking the first row, the real-money
row, or the majority — any of those is a choice wearing the clothes of a fact.

WHAT IT DOES NOT DO
-------------------
It changes no published figure. Both denominators are reported side by side, which
is the clause the row actually asks for. It does not touch the three live dashboard
routes U12 named (`/api/bot/stats`, `/api/bot/performance`, `/api/bot/attribution`)
— those are `src/web/`, Tier-2, and changing a number the operator reads is a
decision rather than a side effect of a re-derivation.

Usage:
  python3 scripts/research/package_denominator_rederive.py --self-test
  python3 scripts/research/package_denominator_rederive.py --trades t.json
  python3 scripts/research/package_denominator_rederive.py --trades t.json --arms arms.json
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Import the SAME instruments, never a second copy — a second adjudicator is how
# two analyses of one event come to disagree about a row (CF's own words).
BA = _load("_bleed_attribution_u19", "bleed_attribution_2026_09_11.py")
CF = _load("_stop_width_cf_u19", "stop_width_counterfactual_2026_09_11.py")

adjudicate_exit = BA.adjudicate_exit
population = BA.population
group_of = BA.group_of
era_of = BA.era_of
wilson = BA.wilson
fisher_2x2 = BA.fisher_2x2
E35_LEGS = BA.E35_LEGS

#: The three outcomes CF's dose table counts as gradeable.
GRADEABLE_EXITS = ("reached_stop", "reached_target", "neither")

#: How a PACKAGE's rows resolve to one observation. Never collapsed, and
#: `disagreement` is deliberately NOT resolved — see the module docstring.
PACKAGE_STATES = (
    "unanimous",        # every gradeable row in the package adjudicated the same
    "disagreement",     # gradeable rows disagree — reported, never arbitrated
    "ungradeable",      # no row in the package is gradeable at all
)


def package_verdict(rows: list[dict], tol: float) -> tuple[str, str | None]:
    """(state, outcome). `outcome` is None unless the package is unanimous.

    A package with one gradeable row is unanimous by construction — that is not a
    special case, it is what unanimity means at n=1, and treating it as its own
    state would split the population on a distinction with no content.
    """
    verdicts = [adjudicate_exit(r, tol) for r in rows]
    gradeable = [v for v in verdicts if v in GRADEABLE_EXITS]
    if not gradeable:
        return "ungradeable", None
    distinct = set(gradeable)
    if len(distinct) == 1:
        return "unanimous", gradeable[0]
    return "disagreement", None


def by_package(rows: list[dict]) -> tuple[dict, int]:
    """Group rows on `order_package_id`. Returns (map, rows_with_no_package).

    A row with no package id is NOT silently dropped and NOT given a synthetic
    one: a synthetic id would make it look like its own package and quietly
    restore the row denominator for exactly the rows we cannot place.
    """
    out: dict[str, list[dict]] = collections.defaultdict(list)
    orphan = 0
    for r in rows:
        pid = r.get("order_package_id")
        if not pid:
            orphan += 1
            continue
        out[str(pid)].append(r)
    return dict(out), orphan


def inflation(rows: list[dict]) -> dict:
    """The row/package ratio, plus the cross-account share that causes it."""
    pkgs, orphan = by_package(rows)
    n_rows, n_pkg = len(rows), len(pkgs)
    cross = sum(1 for rs in pkgs.values() if len({r.get("account_id") for r in rs}) > 1)
    return {
        "rows": n_rows, "packages": n_pkg, "rows_without_package": orphan,
        "inflation": round(n_rows / n_pkg, 3) if n_pkg else None,
        "packages_spanning_multiple_accounts": cross,
        "size_distribution": dict(sorted(collections.Counter(
            len(rs) for rs in pkgs.values()).items())),
    }


def dose_cells(trades: list[dict], tol: float) -> dict:
    """CF's dose buckets, but each era counted BOTH ways.

    The bucketing is CF's own (`E35_LEGS`, `group_of`, `era_of`, `population`), so
    a difference between this table and CF's cannot come from a different
    population — only from the denominator, which is the whole question.
    """
    pop = population(trades)
    buckets: dict[str, dict[str, list[dict]]] = collections.defaultdict(
        lambda: {"pre": [], "post": []})
    for r in pop:
        leg = str(r.get("strategy_name") or "")
        if group_of(leg) != "e35":
            continue
        spec = E35_LEGS.get(leg) or {}
        pair = spec.get("atr_stop_mult")
        if not pair:
            dose = "no_stop_change(tp_r_only)"
        else:
            old, new = float(pair[0]), float(pair[1])
            verb = "tightened" if new < old else "WIDENED"
            dose = f"{verb}_to_{new:g}_from_{old:g} (ratio {new / old:.2f})"
        era = era_of(r)
        if era in ("pre", "post"):
            buckets[dose][era].append(r)

    out = {}
    for dose, eras in buckets.items():
        cell: dict = {}
        for era, rs in eras.items():
            if not rs:
                cell[era] = {"rows": 0, "packages": 0}
                continue
            row_adj = collections.Counter(adjudicate_exit(r, tol) for r in rs)
            row_gradeable = sum(row_adj[k] for k in GRADEABLE_EXITS)
            pkgs, orphan = by_package(rs)
            pv = {p: package_verdict(v, tol) for p, v in pkgs.items()}
            pkg_states = collections.Counter(s for s, _ in pv.values())
            pkg_gradeable = sum(1 for s, _ in pv.values() if s == "unanimous")
            pkg_stops = sum(1 for s, o in pv.values()
                            if s == "unanimous" and o == "reached_stop")
            cell[era] = {
                **inflation(rs),
                "row_gradeable": row_gradeable,
                "row_stop_outs": row_adj["reached_stop"],
                "row_stop_rate": (round(row_adj["reached_stop"] / row_gradeable, 3)
                                  if row_gradeable else None),
                "package_states": dict(pkg_states),
                "package_gradeable": pkg_gradeable,
                "package_stop_outs": pkg_stops,
                "package_stop_rate": (round(pkg_stops / pkg_gradeable, 3)
                                      if pkg_gradeable else None),
                "package_stop_rate_ci": (wilson(pkg_stops, pkg_gradeable)
                                         if pkg_gradeable else None),
                "rows_without_package": orphan,
            }
        a, b = cell.get("pre", {}), cell.get("post", {})
        if a.get("row_gradeable") and b.get("row_gradeable"):
            cell["row_fisher_p"] = fisher_2x2(
                a["row_stop_outs"], a["row_gradeable"] - a["row_stop_outs"],
                b["row_stop_outs"], b["row_gradeable"] - b["row_stop_outs"])
        if a.get("package_gradeable") and b.get("package_gradeable"):
            cell["package_fisher_p"] = fisher_2x2(
                a["package_stop_outs"], a["package_gradeable"] - a["package_stop_outs"],
                b["package_stop_outs"], b["package_gradeable"] - b["package_stop_outs"])
        out[dose] = cell
    return out


def arms_audit(arms: list[dict], tol: float) -> dict:
    """MI-278 U18's own two arms, re-derived per package.

    Included because U18 published `p = 0.0486` forty minutes before this unit
    began, on arms whose inflation this module measured at 1.421x treated against
    1.000x control — the maximally unequal case the backlog row describes. An
    instrument that audits everyone else's denominators and not its own author's
    is the shape this repo keeps paying for.
    """
    out = {}
    for arm in ("treated", "control"):
        rs = [r for r in arms if r.get("_arm") == arm]
        if not rs:
            out[arm] = {"rows": 0, "packages": 0}
            continue
        pkgs, orphan = by_package(rs)
        pv = {p: package_verdict(v, tol) for p, v in pkgs.items()}
        out[arm] = {
            **inflation(rs),
            "package_states": dict(collections.Counter(s for s, _ in pv.values())),
            "rows_without_package": orphan,
        }
    return out


def rederive_u18(verdicts: list[dict], pkg_of: dict, ) -> dict:
    """MI-278 U18's own headline, recomputed with a PACKAGE denominator.

    Uses U18's OWN adjudication (journal label first, venue second) rather than
    CF's `adjudicate_exit`: the two answer different questions, and swapping the
    adjudicator as well as the denominator would make the difference below
    unattributable to either.

    ⚠️ THE TWO CRITERIA DISAGREE ON THIS POPULATION AND THAT IS THE FINDING —
    see `criteria_agree`. The observed p WEAKENS (fewer, correlated observations)
    while the sensitivity band NARROWS (each unnameable package is one unit of
    uncertainty instead of up to three rows of it). U18's sign-survival test was
    chosen because it invents no alpha; this shows it is also INSENSITIVE TO n,
    so it can pass on a comparison with no power. Neither number is quotable
    alone.
    """
    from math import comb

    def _fisher(a: int, b: int, c: int, d: int) -> float:
        n = a + b + c + d
        if n == 0 or (a + b) == 0 or (c + d) == 0:
            return 1.0
        obs = comb(a + b, a) * comb(c + d, c) / comb(n, a + c)
        tot = 0.0
        for i in range(0, min(a + b, a + c) + 1):
            j = a + c - i
            if j < 0 or j > c + d:
                continue
            pr = comb(a + b, i) * comb(c + d, j) / comb(n, a + c)
            if pr <= obs * (1 + 1e-9):
                tot += pr
        return min(tot, 1.0)

    out: dict = {"arms": {}}
    for arm in ("treated", "control"):
        by: dict[str, list[dict]] = collections.defaultdict(list)
        orphan = 0
        for v in verdicts:
            if v.get("arm") != arm:
                continue
            pid = pkg_of.get(v.get("trade"))
            if not pid:
                orphan += 1
                continue
            by[str(pid)].append(v)
        states: collections.Counter = collections.Counter()
        stops = gradeable = unnameable = 0
        for vs in by.values():
            nameable = [v["is_stop"] for v in vs if v.get("is_stop") is not None]
            if not nameable:
                states["unnameable"] += 1
                unnameable += 1
            elif len(set(nameable)) == 1:
                states["unanimous"] += 1
                gradeable += 1
                stops += 1 if nameable[0] else 0
            else:
                # NEVER arbitrated — the same refusal package_verdict makes.
                states["disagreement"] += 1
        out["arms"][arm] = {
            "packages": len(by), "rows_without_package": orphan,
            "states": dict(states), "gradeable": gradeable, "stops": stops,
            "unnameable": unnameable,
            "rate": (stops / gradeable) if gradeable else None,
        }
    t, c = out["arms"]["treated"], out["arms"]["control"]
    if t["gradeable"] and c["gradeable"]:
        out["p"] = _fisher(t["stops"], t["gradeable"] - t["stops"],
                           c["stops"], c["gradeable"] - c["stops"])
        tb = [t["stops"] / (t["gradeable"] + t["unnameable"]),
              (t["stops"] + t["unnameable"]) / (t["gradeable"] + t["unnameable"])]
        cb = [c["stops"] / (c["gradeable"] + c["unnameable"]),
              (c["stops"] + c["unnameable"]) / (c["gradeable"] + c["unnameable"])]
        out["treated_band"], out["control_band"] = tb, cb
        out["sign_survives_worst_case"] = tb[0] > cb[1]
        out["sign_margin"] = tb[0] - cb[1]
        # The two criteria, side by side, so neither can be quoted alone.
        out["criteria_agree"] = (out["p"] < 0.05) == out["sign_survives_worst_case"]
    return out


def report(dose: dict, arms: dict | None, problems: list[str],
           u18: dict | None = None) -> int:
    print("PROBLEMS FIRST")
    print("=" * 78)
    for p in problems or ["  none"]:
        print(f"  {p}" if problems else p)
    print()

    print("THE e35 DOSE TABLE, COUNTED BOTH WAYS")
    print("=" * 78)
    print("  The bucketing, population, era split and adjudicator are CF's own and")
    print("  are imported unmodified, so a difference below is the DENOMINATOR and")
    print("  cannot be a different population.")
    print()
    for d, cell in sorted(dose.items()):
        print(f"  {d}")
        for era in ("pre", "post"):
            c = cell.get(era) or {}
            if not c.get("rows"):
                print(f"    {era:5s} — no rows")
                continue
            rr = c["row_stop_rate"]
            pr = c["package_stop_rate"]
            print(f"    {era:5s} rows {c['rows']:3d} / packages {c['packages']:3d} "
                  f"(inflation {c['inflation']}) · cross-account pkgs "
                  f"{c['packages_spanning_multiple_accounts']}")
            print(f"          stop rate  ROWS {c['row_stop_outs']}/{c['row_gradeable']}"
                  f"={rr if rr is not None else '—'}   "
                  f"PACKAGES {c['package_stop_outs']}/{c['package_gradeable']}"
                  f"={pr if pr is not None else '—'}")
            print(f"          package states {c['package_states']}")
        rp, pp = cell.get("row_fisher_p"), cell.get("package_fisher_p")
        if rp is not None or pp is not None:
            print(f"    Fisher p  ROWS {rp if rp is not None else '—'}   "
                  f"PACKAGES {pp if pp is not None else '—'}")
        print()

    if arms:
        print("MI-278 U18's OWN ARMS — this unit auditing its own author")
        print("=" * 78)
        for arm, c in arms.items():
            if not c.get("rows"):
                print(f"  {arm}: no rows")
                continue
            print(f"  {arm:8s} rows {c['rows']:3d} / packages {c['packages']:3d} "
                  f"(inflation {c['inflation']}) · cross-account pkgs "
                  f"{c['packages_spanning_multiple_accounts']} · "
                  f"sizes {c['size_distribution']}")
            print(f"           package states {c['package_states']}")
        t, ctl = arms.get("treated") or {}, arms.get("control") or {}
        if t.get("inflation") and ctl.get("inflation"):
            print(f"  ⚠️ the two arms are inflated UNEQUALLY: "
                  f"{t['inflation']}x treated vs {ctl['inflation']}x control")
        print()

    if u18 and u18.get("p") is not None:
        t, c = u18["arms"]["treated"], u18["arms"]["control"]
        print("MI-278 U18's HEADLINE, RE-DERIVED PER PACKAGE")
        print("=" * 78)
        print("    PUBLISHED (rows)     treated 11/14=0.786  control 4/11=0.364  "
              "p=0.0486  -> not_gradeable_missingness_dominates")
        print(f"    RE-DERIVED (packages) treated {t['stops']}/{t['gradeable']}"
              f"={t['rate']:.3f}  control {c['stops']}/{c['gradeable']}={c['rate']:.3f}  "
              f"p={u18['p']:.4f}")
        print(f"    band  treated [{u18['treated_band'][0]:.3f}, "
              f"{u18['treated_band'][1]:.3f}]  control "
              f"[{u18['control_band'][0]:.3f}, {u18['control_band'][1]:.3f}]  "
              f"sign survives: {u18['sign_survives_worst_case']} "
              f"(margin {u18['sign_margin']:+.4f})")
        print(f"    package states  treated {t['states']}  control {c['states']}")
        if not u18["criteria_agree"]:
            print("    !! THE TWO CRITERIA DISAGREE, AND THAT IS THE FINDING. The p "
                  "WEAKENS (fewer,")
            print("       correlated observations) while the band NARROWS (one "
                  "unnameable PACKAGE is one")
            print("       unit of uncertainty, not up to three rows of it). U18's "
                  "sign-survival test")
            print("       invents no alpha and is therefore ALSO INSENSITIVE TO n — "
                  "it can pass on a")
            print("       comparison with no power. NEITHER NUMBER IS QUOTABLE ALONE, "
                  "and the honest")
            print("       reading is that the comparison is still ungradeable, now "
                  "for a DIFFERENT")
            print("       reason than U18 gave.")
        print()

    print("WHAT THIS DOES NOT DO")
    print("=" * 78)
    print("  It changes no published figure — both denominators are reported, which")
    print("  is the clause the backlog row asks for. The three live dashboard routes")
    print("  are Tier-2 and are untouched. A `disagreement` package is REPORTED, never")
    print("  arbitrated: MI-278 U15 measured that choosing a row swings the headline.")
    return 1 if problems else 0


def self_test() -> int:
    """Plant each behaviour; the bar is a RETURN VALUE, never printed text."""
    fails: list[str] = []

    def ck(label, got, want):
        if got != want:
            fails.append(f"  FAIL - {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS - {label}")

    TOL = 0.0
    def L(pid, px, sl, tp, acct="a", d="long"):
        return {"order_package_id": pid, "exit_price": px, "stop_loss": sl,
                "take_profit_1": tp, "direction": d, "account_id": acct}

    # --- the adjudicator is the REAL one, not a local re-derivation ----
    ck("imported adjudicate_exit grades a stop", adjudicate_exit(L("p", 90, 90, 110), TOL),
       "reached_stop")
    ck("...and a target", adjudicate_exit(L("p", 110, 90, 110), TOL), "reached_target")
    ck("...and neither", adjudicate_exit(L("p", 100, 90, 110), TOL), "neither")

    # --- package_verdict: unanimity, disagreement, ungradeable ---------
    ck("a single gradeable row is unanimous (n=1 unanimity is not a special case)",
       package_verdict([L("p", 90, 90, 110)], TOL), ("unanimous", "reached_stop"))
    ck("two rows agreeing are unanimous",
       package_verdict([L("p", 90, 90, 110), L("p", 90, 90, 110, "b")], TOL),
       ("unanimous", "reached_stop"))
    # THE ONE THAT MATTERS: disagreement is a STATE, never arbitrated. Returning
    # the first row's verdict here is exactly the row-selection freedom U15
    # measured as worth 4.3–28.2pp.
    ck("rows disagreeing give `disagreement`, NOT the first row's verdict",
       package_verdict([L("p", 90, 90, 110), L("p", 110, 90, 110, "b")], TOL),
       ("disagreement", None))
    ck("...and the same pair in the OPPOSITE order gives the same answer",
       package_verdict([L("p", 110, 90, 110, "b"), L("p", 90, 90, 110)], TOL),
       ("disagreement", None))
    ck("no gradeable row at all is `ungradeable`, never a silent skip",
       package_verdict([L("p", None, 90, 110)], TOL), ("ungradeable", None))
    # An UNgradeable row beside a gradeable one must not create a disagreement:
    # "we could not look" is not a competing opinion.
    ck("an ungradeable row does not manufacture a disagreement",
       package_verdict([L("p", 90, 90, 110), L("p", None, 90, 110, "b")], TOL),
       ("unanimous", "reached_stop"))

    # --- by_package: an orphan row is counted, never invented into a package ---
    m, orphan = by_package([L("p1", 90, 90, 110), L(None, 90, 90, 110)])
    ck("a row with no package id is NOT given a synthetic one", len(m), 1)
    ck("...and is COUNTED as an orphan", orphan, 1)

    # --- inflation, and the cross-account discriminator ----------------
    i = inflation([L("p", 90, 90, 110, "a"), L("p", 90, 90, 110, "b")])
    ck("two rows in one package read 2.0x", i["inflation"], 2.0)
    ck("...and are recognised as cross-account",
       i["packages_spanning_multiple_accounts"], 1)
    i = inflation([L("p", 90, 90, 110, "a"), L("p", 90, 90, 110, "a")])
    ck("two rows in one package on ONE account still inflate",
       i["inflation"], 2.0)
    ck("...but are NOT cross-account — the two causes are never pooled",
       i["packages_spanning_multiple_accounts"], 0)
    i = inflation([L("p1", 90, 90, 110), L("p2", 90, 90, 110)])
    ck("distinct packages read 1.0x", i["inflation"], 1.0)
    ck("an empty population has inflation None, NOT 1.0",
       inflation([])["inflation"], None)

    # --- arms_audit ----------------------------------------------------
    a = arms_audit([{**L("p", 90, 90, 110, "a"), "_arm": "treated"},
                    {**L("p", 90, 90, 110, "b"), "_arm": "treated"},
                    {**L("q", 90, 90, 110, "a"), "_arm": "control"}], TOL)
    ck("the arms audit reports each arm's own inflation",
       (a["treated"]["inflation"], a["control"]["inflation"]), (2.0, 1.0))
    ck("...and an absent arm is 0 rows, not an error",
       arms_audit([], TOL)["treated"]["rows"], 0)

    # --- rederive_u18: the package reduction, and the criteria disagreement ---
    def V(t, arm, is_stop): return {"trade": t, "arm": arm, "is_stop": is_stop}
    # Two rows of one package that agree collapse to ONE observation.
    r = rederive_u18([V(1, "treated", True), V(2, "treated", True),
                      V(3, "control", False)], {1: "p", 2: "p", 3: "q"})
    ck("two agreeing rows of one package are ONE package observation",
       r["arms"]["treated"]["gradeable"], 1)
    ck("...and the stop is counted once, not twice",
       r["arms"]["treated"]["stops"], 1)
    # A disagreement inside a package is refused, exactly as package_verdict does.
    r = rederive_u18([V(1, "treated", True), V(2, "treated", False)],
                     {1: "p", 2: "p"})
    ck("a package whose rows disagree is NOT arbitrated",
       r["arms"]["treated"]["states"].get("disagreement"), 1)
    ck("...and contributes to neither the numerator nor the denominator",
       (r["arms"]["treated"]["gradeable"], r["arms"]["treated"]["stops"]), (0, 0))
    # An unnameable package is one unit of uncertainty, not one per row — which
    # is precisely why the band narrows while the p weakens.
    r = rederive_u18([V(1, "treated", None), V(2, "treated", None),
                      V(3, "treated", True), V(4, "control", False)],
                     {1: "p", 2: "p", 3: "q", 4: "r"})
    ck("three rows of one unnameable package are ONE unnameable unit",
       r["arms"]["treated"]["unnameable"], 1)
    # A row whose package is unknown is counted, never folded in.
    r = rederive_u18([V(1, "treated", True), V(2, "treated", True)],
                     {1: "p"})
    ck("a verdict with no package id is counted as an orphan, not dropped silently",
       r["arms"]["treated"]["rows_without_package"], 1)
    # THE CRITERIA-DISAGREEMENT FLAG, which is the unit's headline: it must be
    # able to read False, or it is decoration.
    r = rederive_u18([V(i, "treated", True) for i in range(1, 9)]
                     + [V(i, "control", False) for i in range(9, 17)],
                     {i: f"p{i}" for i in range(1, 17)})
    ck("a clean separation with nothing unnameable has the criteria AGREEING",
       r["criteria_agree"], True)
    ck("...and its sign survives", r["sign_survives_worst_case"], True)

    # --- the imported stats are the real ones --------------------------
    ck("imported fisher_2x2 on an unassociated table is 1.0",
       round(fisher_2x2(5, 5, 5, 5), 4), 1.0)
    ck("imported wilson returns an interval", len(wilson(5, 10)), 2)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", help="/api/diag/journal?table=trades response")
    ap.add_argument("--arms", help="MI-278 U18's arms.json, to audit its denominators")
    ap.add_argument("--u18-json", help="MI-278 U18's out.json, to re-derive its headline")
    ap.add_argument("--tol", type=float, default=0.0015)
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.trades:
        ap.error("--trades is required")

    problems: list[str] = []
    with open(args.trades) as fh:
        trades = json.load(fh)
    dose = dose_cells(trades, args.tol)
    arms = None
    if args.arms:
        with open(args.arms) as fh:
            arms = arms_audit(json.load(fh), args.tol)
    else:
        problems.append("no --arms given: MI-278 U18's own denominators were NOT "
                        "audited — that is 'we did not look', not 'they are fine'.")
    u18 = None
    if args.u18_json and args.arms:
        with open(args.arms) as fh:
            pkg_of = {r["id"]: r.get("order_package_id") for r in json.load(fh)}
        with open(args.u18_json) as fh:
            u18 = rederive_u18(json.load(fh).get("verdicts") or [], pkg_of)
    elif args.u18_json:
        problems.append("--u18-json needs --arms for the trade->package map; U18's "
                        "headline was NOT re-derived.")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"dose": dose, "arms": arms, "u18_rederived": u18,
                       "tol": args.tol, "problems": problems}, fh, indent=2)
    return report(dose, arms, problems, u18)


if __name__ == "__main__":
    raise SystemExit(main())
