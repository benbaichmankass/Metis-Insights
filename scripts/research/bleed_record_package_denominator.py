#!/usr/bin/env python3
# wiring: manual-only — a one-shot re-derivation answering "do the bleed record's
#   own rates survive a PACKAGE denominator?". It is run BY A SESSION against a
#   journal pull; scheduling it would re-ask a settled question against a window
#   nobody chose. Same disposition as package_denominator_rederive.py.
"""MI-278 U31 — re-derive the BLEED RECORD's own rates per ORDER PACKAGE.

WHAT THIS PAYS, AND WHY THIS SCRIPT
-------------------------------------
`BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`
clause 1. MI-278 U12 named 21 price-path surfaces computing a rate over `trades`
rows; U19 paid two of them. `bleed_attribution_2026_09_11.py` is the most
load-bearing of the 19 left, because it is the script behind the -$38,851.81
figure that `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
(`loud: true`) rests on -- adjudicated stop rate, Wilson intervals, Fisher exact
on a pre/post 2x2, pooled across accounts.

MEASURED with a positive control: `order_package_id` appears ZERO times in that
file and `account_id` appears twice. It reads the journal and never the package
id, while the backlog row measured the fan-out inflating the two arms UNEQUALLY
(e35 1.49x against the untouched control's 1.27x, `b4_geometry` 1.64x). A Fisher
test between differentially inflated denominators is anti-conservative and
unequally so.

U19 imported this script's helpers to re-derive the e35 DOSE result. It never
re-derived this script's OWN published figures. That is the gap.

⚠️ THE HEADLINE IS NOT A RATE AND MUST NOT BE RE-DERIVED HERE
---------------------------------------------------------------
**-$38,851.81 IS AN ACCOUNT FACT AND IS CORRECTLY ROW-DENOMINATED.** Each
fanned-out row is a real position on a real account that really lost that money;
collapsing the siblings to one package would UNDERSTATE money actually lost. A
session that read "the bleed record was re-derived per package" and revised the
headline DOWNWARD would be making the same error in the opposite direction, and
that is the likelier mistake here because a package denominator sounds
uniformly more careful.

The backlog row states the rule exactly, and it is a rule about the QUESTION and
not about the table:

    the row count is the right denominator for questions about ACCOUNTS
    (exposure, per-account PnL, routing) and the wrong one only for questions
    about the PRICE PATH ... What is owed is that the distinction is STATED
    where rates are produced.

So this module's deliverable is the PARTITION, per key, plus a re-derivation of
the price-path side only. `KEY_BASIS` below is that partition, and it is
COMPLETENESS-CHECKED BY CALLING `summarise`, never by reading its source -- a
key added tomorrow shows up as `undeclared`, which is the finding and never a
pass.

WHAT IT REUSES RATHER THAN REBUILDS
-------------------------------------
`population`, `adjudicate_exit`, `group_of`, `era_of`, `wilson`, `fisher_2x2`
and `summarise` are imported from `bleed_attribution_2026_09_11` UNMODIFIED. A
second copy of the adjudication would be free to drift from the record it is
auditing, which would make a disagreement between them uninterpretable. The
package reduction is `package_denominator_rederive`'s, for the same reason.

⚠️ `disagreement` IS A STATE AND IS NEVER ARBITRATED. Collapsing a package to one
observation means choosing among its rows, and MI-278 U15 measured that WHICH
row you choose swings a headline 4.3-28.2pp. This module never picks.

WHAT IT DOES NOT DO
---------------------
* It re-grades NO live figure. The three dashboard routes in U12's list
  (`/api/bot/stats` winRate, `/api/bot/performance`, `/api/bot/attribution`) are
  `src/web/` -- Tier-2 -- and changing a number the operator reads is a
  decision, not a side effect of a re-derivation. Untouched.
* It does not EDIT `bleed_attribution_2026_09_11.py`. Its published memo is a
  dated record; the correction belongs beside it, not written over it.
* It proposes no parameter change. That is Tier-3.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO / relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BA = _load("_bleed_attribution_u31", "scripts/research/bleed_attribution_2026_09_11.py")
U19 = _load("_pkg_denominator_u31", "scripts/research/package_denominator_rederive.py")

population = BA.population
group_of = BA.group_of
era_of = BA.era_of
wilson = BA.wilson
fisher_2x2 = BA.fisher_2x2
summarise = BA.summarise
by_package = U19.by_package
package_verdict = U19.package_verdict

#: FOUR BASIS STATES, never collapsed. `undeclared` is the finding.
BASIS_STATES = ("price_path", "account_fact", "both_stated", "undeclared")

#: WHICH QUESTION EACH `summarise` KEY ANSWERS.
#:
#: Declared, not inferred, because no checker can read which question is being
#: asked -- the backlog row says so and it is right. What a checker CAN do is
#: notice a key nobody declared, which is why `undeclared` exists and why the
#: completeness test CALLS `summarise` rather than reading it.
#:
#: `account_fact` is not a lesser status. A PnL sum over account rows is the
#: money that was actually lost; a package denominator would be WRONG for it.
KEY_BASIS: dict[str, str] = {
    # --- price-path: what did PRICE do? A package is one price path. ---------
    "win_rate": "price_path",
    "win_rate_ci95": "price_path",
    "wins": "price_path",
    "stop_rate_adjudicated": "price_path",
    "target_rate_adjudicated": "price_path",
    "stop_rate_ci95": "price_path",
    "stops_adjudicated": "price_path",
    "gradeable_n": "price_path",
    "ungradeable_n": "price_path",
    "adjudication": "price_path",
    # --- account facts: real money on real accounts. ROWS ARE CORRECT. -------
    "pnl_sum": "account_fact",
    "pnl_sum_measured_only": "account_fact",
    "pnl_mean": "account_fact",
    "pnl_median": "account_fact",
    "loss_mean": "account_fact",
    "win_mean": "account_fact",
    "pnl_provenance": "account_fact",
    "pnl_coverage": "account_fact",
    "measured_n": "account_fact",
    # --- the denominator itself: both, and both are printed. -----------------
    "n": "both_stated",
}


def declare_basis(key: str) -> str:
    """`undeclared` for anything the partition does not cover -- never a guess."""
    return KEY_BASIS.get(key, "undeclared")


def basis_census(summary: dict[str, Any]) -> dict[str, Any]:
    """Grade a real `summarise()` output, so a new key cannot slip through.

    Called against the LIVE function rather than its source: a key added
    tomorrow appears here as `undeclared` the next time anyone runs this.
    """
    counts = {s: 0 for s in BASIS_STATES}
    undeclared: list[str] = []
    for k in summary:
        b = declare_basis(k)
        counts[b] += 1
        if b == "undeclared":
            undeclared.append(k)
    return {"counts": counts, "undeclared": sorted(undeclared),
            "complete": not undeclared}


def package_rates(rows: list[dict], tol: float) -> dict[str, Any]:
    """The price-path half, per PACKAGE, with the reduction's states reported.

    ⚠️ Returns `None` for a rate whose denominator is zero -- never 0.0, which
    would read as "no stop-outs" when it means "we could not look". That is
    `summarise`'s own discipline and is copied deliberately.
    """
    groups, no_pkg = by_package(rows)
    states = {s: 0 for s in U19.PACKAGE_STATES}
    wins = stops = targets = gradeable = 0
    for _pkg, legs in groups.items():
        verdict, exit_state = package_verdict(legs, tol)
        states[verdict] = states.get(verdict, 0) + 1
        if verdict == "disagreement":
            # REPORTED, NEVER ARBITRATED. Counting it toward either arm would be
            # a choice wearing the clothes of a fact (MI-278 U15: which row you
            # believe swings a headline 4.3-28.2pp).
            continue
        if verdict == "ungradeable":
            continue
        gradeable += 1
        if exit_state == "reached_stop":
            stops += 1
        elif exit_state == "reached_target":
            targets += 1
        # A package wins when its rows agree that it did. Unanimity is what
        # `package_verdict` already established, so no second rule is invented.
        if all(float(r.get("pnl") or 0.0) > 0 for r in legs):
            wins += 1
    n_pkg = len(groups)
    return {
        "packages": n_pkg,
        "rows": len(rows),
        "rows_without_package": no_pkg,
        "inflation": round(len(rows) / n_pkg, 4) if n_pkg else None,
        "package_states": states,
        "wins": wins,
        "win_rate": round(wins / n_pkg, 4) if n_pkg else None,
        "win_rate_ci95": wilson(wins, n_pkg) if n_pkg else None,
        "gradeable_packages": gradeable,
        "stops_adjudicated": stops,
        "stop_rate_adjudicated": round(stops / gradeable, 4) if gradeable else None,
        "stop_rate_ci95": wilson(stops, gradeable) if gradeable else None,
        "target_rate_adjudicated": round(targets / gradeable, 4) if gradeable else None,
    }


def both_denominators(rows: list[dict], tol: float) -> dict[str, Any]:
    """`summarise`'s own output beside the package re-derivation of its rates."""
    per_row = summarise(rows, tol)
    return {
        "per_row": per_row,
        "per_package": package_rates(rows, tol) if per_row.get("n") else None,
        "basis": basis_census(per_row),
    }


def arm_table(rows: list[dict], tol: float) -> dict[str, Any]:
    """Every (group, era) arm, both denominators, plus the pre/post Fisher on each.

    ⚠️ THE FISHER IS RECOMPUTED ON PACKAGES BECAUSE THAT IS THE TEST THE BACKLOG
    ROW IMPEACHES: it treats each observation as an independent Bernoulli draw,
    which is exactly the assumption fan-out breaks, and the arms are inflated
    unequally so the error does not cancel.
    """
    arms: dict[str, dict] = {}
    for r in rows:
        arms.setdefault(group_of(str(r.get("strategy_name") or "")), {}) \
            .setdefault(era_of(r), []).append(r)
    out: dict[str, Any] = {}
    for grp, eras in sorted(arms.items()):
        cell: dict[str, Any] = {}
        for era in ("pre", "post", "unknown"):
            if era in eras:
                cell[era] = both_denominators(eras[era], tol)
        pre, post = eras.get("pre"), eras.get("post")
        if pre and post:
            cell["prepost_fisher"] = _prepost(pre, post, tol)
        out[grp] = cell
    return out


def _prepost(pre: list[dict], post: list[dict], tol: float) -> dict[str, Any]:
    """Pre/post stop-rate Fisher, on ROWS and on PACKAGES, side by side."""
    def _row_arm(rs):
        s = summarise(rs, tol)
        return s.get("stops_adjudicated"), s.get("gradeable_n")

    def _pkg_arm(rs):
        p = package_rates(rs, tol)
        return p["stops_adjudicated"], p["gradeable_packages"]

    out: dict[str, Any] = {}
    for label, fn in (("per_row", _row_arm), ("per_package", _pkg_arm)):
        a, na = fn(pre)
        c, nc = fn(post)
        if not na or not nc:
            # NO TEST EXISTS on an empty arm. `None`, never p=1.0, which would
            # read as "measured and not significant".
            out[label] = {"p": None, "state": "no_denominator",
                          "pre": [a, na], "post": [c, nc]}
            continue
        out[label] = {
            "p": round(fisher_2x2(a, na - a, c, nc - c), 6),
            "state": "computed",
            "pre_rate": round(a / na, 4), "post_rate": round(c / nc, 4),
            "pre": [a, na], "post": [c, nc],
        }
    rp, pp = out["per_row"].get("p"), out["per_package"].get("p")
    out["verdict"] = _verdict(rp, pp)
    return out


# TWO ORTHOGONAL FACTS, NEVER COLLAPSED INTO ONE VERDICT.
#
# ⚠️ A SINGLE VERDICT STRING GOT THIS WRONG ON REAL DATA AND THE MISLABEL IS
# WORTH RECORDING. The first version returned `loses_significance` whenever the
# package p was larger and did not clear 0.05 -- which labelled `b4_geometry`
# (row p=0.3043 -> package p=0.4643) as having LOST significance it never had,
# and labelled two arms that stayed comfortably non-significant as
# `strengthens`. That is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A: the label
# names a quantity the code did not compute, and a reader trusting it reaches a
# confident wrong conclusion. Caught by running it on the live journal, not by
# reading it.
#
# So the two questions are answered separately, because they are separate:
#   * DID THE CONCLUSION CHANGE?  -> `significance`
#   * DID THE EVIDENCE WEAKEN?    -> `strength`
# An arm can weaken a lot and change nothing (it was never significant), or move
# barely and cross the line. Only the join of the two says what happened.
SIGNIFICANCE_STATES = ("both_significant", "lost", "gained",
                       "neither_significant", "not_computable")
STRENGTH_STATES = ("weaker", "stronger", "unchanged", "not_computable")

ALPHA = 0.05  # the conventional threshold, named so it is arguable rather than buried


def _significance(row_p: float | None, pkg_p: float | None) -> str:
    if row_p is None or pkg_p is None:
        return "not_computable"
    row_sig, pkg_sig = row_p < ALPHA, pkg_p < ALPHA
    if row_sig and pkg_sig:
        return "both_significant"
    if row_sig and not pkg_sig:
        return "lost"
    if pkg_sig and not row_sig:
        return "gained"
    return "neither_significant"


def _strength(row_p: float | None, pkg_p: float | None) -> str:
    if row_p is None or pkg_p is None:
        return "not_computable"
    if pkg_p > row_p:
        return "weaker"
    if pkg_p < row_p:
        return "stronger"
    return "unchanged"


def _verdict(row_p: float | None, pkg_p: float | None) -> dict:
    """Both facts, plus the one join that is genuinely a headline.

    `headline` is True ONLY for `lost` -- a conclusion that the row denominator
    supported and the package denominator does not. Everything else, including a
    large weakening of something that was never significant, is context.
    """
    sig = _significance(row_p, pkg_p)
    return {"significance": sig, "strength": _strength(row_p, pkg_p),
            "conclusion_changed": sig in ("lost", "gained")}


def report(rows: list[dict], tol: float,
           account: str | None = None) -> dict[str, Any]:
    """⚠️ STATE THE POPULATION. `bleed_attribution`'s published headline is
    `bybit_1`-scoped; `population()` is not, so running this unscoped grades the
    whole fleet and its `pnl_sum` is NOT the bleed record. `account` is
    therefore reported on every run, `None` included, so a reader can never take
    a fleet number for the memo's."""
    pop = population(rows)
    if account:
        pop = [r for r in pop if str(r.get("account_id") or "") == account]
    return {
        "population": {"rows_in": len(rows), "in_population": len(pop),
                       "account_scope": account or "ALL_ACCOUNTS"},
        "overall": both_denominators(pop, tol),
        "arms": arm_table(pop, tol),
        "headline_caveat": (
            "pnl_sum IS AN ACCOUNT FACT AND IS NOT RE-DERIVED HERE. Each fanned-out "
            "row is a real position on a real account that really lost that money; a "
            "package denominator would UNDERSTATE money actually lost. Only the "
            "price-path RATES beside it are re-derived."),
    }


def self_test() -> int:
    fails: list[str] = []

    def ck(label: str, cond: bool) -> None:
        ck.n += 1
        print(f"  self-test {'ok  ' if cond else 'FAIL'}: {label}")
        if not cond:
            fails.append(label)
    ck.n = 0

    # --- the partition ----------------------------------------------------
    ck("every declared basis is one of the four states",
       set(KEY_BASIS.values()) <= set(BASIS_STATES))
    ck("`undeclared` is NOT used as a declaration (it is only ever derived)",
       "undeclared" not in KEY_BASIS.values())
    ck("an unknown key derives `undeclared`, never a guess",
       declare_basis("some_key_nobody_declared") == "undeclared")
    ck("the money keys are account_fact, not price_path",
       all(declare_basis(k) == "account_fact"
           for k in ("pnl_sum", "pnl_mean", "pnl_sum_measured_only")))
    ck("the rate keys are price_path",
       all(declare_basis(k) == "price_path"
           for k in ("win_rate", "stop_rate_adjudicated", "stop_rate_ci95")))

    # --- completeness is checked against the LIVE function ----------------
    def _row(pnl, era_post, sl=None, tp=None, px=None, pkg="p1", strat="trend_donchian_eth"):
        # `direction` is REQUIRED by the imported adjudicator -- omitting it
        # grades every row `ungradeable_no_direction`, which silently empties
        # the gradeable denominator and makes the reduction tests vacuous. Found
        # by running them, not by reading the fixture.
        return {"id": id(object()), "status": "closed", "is_backtest": 0, "pnl": pnl,
                "exit_reason": "sl_cross", "strategy_name": strat,
                "direction": "long",
                "order_package_id": pkg, "account_id": "bybit_1",
                "created_at": "2026-09-01T00:00:00Z" if era_post else "2026-08-01T00:00:00Z",
                "stop_loss": sl, "take_profit_1": tp, "exit_price": px,
                "pnl_source": "local_compute", "exit_price_source": "exchange_fill"}

    live = summarise([_row(-1.0, True)], 0.001)
    cen = basis_census(live)
    ck("the census CALLS summarise and covers every key it really emits",
       cen["complete"] or print(f"      undeclared: {cen['undeclared']}") is None
       and not cen["undeclared"])
    ck("a key the partition does not cover grades `undeclared`, not a pass",
       basis_census({**live, "invented_key": 1})["undeclared"] == ["invented_key"])

    # --- the reduction ----------------------------------------------------
    two_legs = [_row(-1.0, True, pkg="pA"), _row(-2.0, True, pkg="pA")]
    pr = package_rates(two_legs, 0.001)
    ck("two fan-out legs of one package are ONE package, not two",
       pr["packages"] == 1 and pr["rows"] == 2)
    ck("inflation is reported beside both counts", pr["inflation"] == 2.0)

    disagreeing = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg="pB"),
                   _row(+1.0, True, sl=10.0, tp=99.0, px=99.0, pkg="pB")]
    pd_ = package_rates(disagreeing, 0.001)
    ck("a package whose rows disagree is REPORTED, never arbitrated into an arm",
       pd_["package_states"].get("disagreement", 0) == 1)

    empty = package_rates([], 0.001)
    ck("an empty population yields None rates, never 0.0",
       empty["win_rate"] is None and empty["stop_rate_adjudicated"] is None)

    # --- the survival verdict --------------------------------------------
    ck("a p that crosses 0.05 upward LOSES the conclusion",
       _verdict(0.001, 0.20)["significance"] == "lost")
    ck("a p that weakens but stays under 0.05 changes NO conclusion",
       _verdict(0.0001, 0.03) == {"significance": "both_significant",
                                  "strength": "weaker",
                                  "conclusion_changed": False})
    ck("THE MISLABEL THAT REACHED LIVE DATA: an arm that was never significant "
       "cannot LOSE significance (b4_geometry, row p=0.3043 -> pkg p=0.4643)",
       _verdict(0.3043, 0.4643) == {"significance": "neither_significant",
                                    "strength": "weaker",
                                    "conclusion_changed": False})
    ck("AND ITS TWIN: a p that improves without crossing has not `strengthened` "
       "a conclusion (untouched_control, 0.22025 -> 0.10528)",
       _verdict(0.22025, 0.10528)["conclusion_changed"] is False)
    ck("crossing DOWNWARD is a gained conclusion, not merely stronger",
       _verdict(0.20, 0.01)["significance"] == "gained")
    ck("an identical p is unchanged", _verdict(0.03, 0.03)["strength"] == "unchanged")
    ck("a missing arm is not_computable on BOTH axes, never a pass",
       _verdict(None, 0.01) == {"significance": "not_computable",
                                "strength": "not_computable",
                                "conclusion_changed": False})
    ck("every significance state is reachable",
       {_verdict(a, b)["significance"] for a, b in
        ((0.001, 0.01), (0.001, 0.20), (0.20, 0.01), (0.20, 0.30), (None, 0.01))}
       == set(SIGNIFICANCE_STATES))
    ck("every strength state is reachable",
       {_verdict(a, b)["strength"] for a, b in
        ((0.01, 0.20), (0.20, 0.01), (0.03, 0.03), (None, 0.01))}
       == set(STRENGTH_STATES))

    # --- the Fisher, both denominators ------------------------------------
    pre = [_row(-1.0, False, sl=10.0, tp=99.0, px=10.0, pkg=f"q{i}") for i in range(4)]
    post = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg=f"r{i}") for i in range(4)]
    fp = _prepost(pre, post, 0.001)
    ck("both denominators are computed for the pre/post test",
       fp["per_row"]["state"] == "computed" and fp["per_package"]["state"] == "computed")
    ck("with no fan-out the two denominators AGREE (the null control)",
       fp["per_row"]["pre"] == fp["per_package"]["pre"])
    fanned = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg="one") for _ in range(4)]
    fq = _prepost(pre, fanned, 0.001)
    ck("with fan-out the package arm has a SMALLER denominator than the row arm",
       fq["per_package"]["post"][1] < fq["per_row"]["post"][1])
    ck("an empty arm yields p=None, never p=1.0",
       _prepost([], post, 0.001)["per_row"]["p"] is None)

    # --- the headline caveat is not optional ------------------------------
    rep = report([_row(-1.0, True)], 0.001)
    ck("the report always carries the account-fact caveat",
       "ACCOUNT FACT" in rep["headline_caveat"])

    print(f"\n{'FAIL' if fails else 'PASS'}: {len(fails)} failure(s) "
          f"of {ck.n} checks")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journal", help="path to a journal pull (JSON rows or {rows:[...]})")
    ap.add_argument("--tol", type=float, default=0.001)
    ap.add_argument("--account", default=None,
                    help="scope to one account_id. The bleed memo's population "
                         "is bybit_1; unscoped grades the whole fleet.")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.journal:
        ap.error("--journal is required (or --self-test)")
    raw = json.loads(pathlib.Path(a.journal).read_text())
    rows = raw.get("rows", raw) if isinstance(raw, dict) else raw
    rep = report(rows, a.tol, a.account)
    print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
