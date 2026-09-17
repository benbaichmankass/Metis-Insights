#!/usr/bin/env python3
"""Which surfaces compute a PRICE-PATH rate over `trades` rows, and by how much is n inflated?

MI-278 U12, for `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`.

One live signal fans out to several accounts and EACH writes its own `trades` row
carrying the same symbol, direction, entry and stop. Those rows share ONE price path,
so for a question about WHAT PRICE DID they are not independent observations. For a
question about ACCOUNTS -- exposure, per-account PnL, routing, refusals -- the row
count is the CORRECT denominator.

WHY THIS IS A REGISTRY AND NOT A GUARD, in the row's own words: "no checker can read
which question is being asked". So the classification is DECLARED, per surface, and
this module's job is to (a) find every candidate mechanically, (b) report the ones
nobody has classified, and (c) supply the correction so a surface can state it. A
checker that guessed the question would be wrong in both directions and would train
people to silence it.

TWO INFLATIONS, NEVER POOLED -- and this is the part a naive fix gets wrong.
  * CROSS-ACCOUNT fan-out: one package, several accounts, one price path. This is the
    row's subject, and deduplicating by package is the right correction.
  * INTRA-ACCOUNT duplication: one package, ONE account, several rows (a partial
    close, a re-adoption, a rejected row beside a filled one). Deduplicating by
    package collapses these TOO, silently, and whether that is right depends on the
    question -- they are not extra views of one price path in the same way.
  Measured on the 2026-09-12 journal tail they differ by an order of magnitude
  (97 of 791 packages span >1 account; 16 of 979 (account, package) pairs carry >1
  row), so a single "inflation" number hides the second entirely.
"""

from __future__ import annotations

# wiring: manual-only - a survey answering one backlog row's "name the surfaces" clause. It reads the repo and one journal pull; there is no recurring question for a workflow to ask on a cadence, and the row itself says a CI guard is the wrong shape here because no checker can read which question a rate is asking.

import argparse
import collections
import json
import pathlib
import re

# --- what counts as a candidate --------------------------------------------

ROOTS = ("scripts/research", "scripts/ops", "scripts/reports", "scripts/ml",
         "src/web/api/routers")

READS_TRADES = re.compile(r"table=trades|FROM\s+trades|from\s+trades\b|[\"']trades[\"']")
COMPUTES_A_RATE = re.compile(
    r"/\s*(len\(|n_|total|count|denom)|\b(rate|pct|share|frac|proportion|percent)\w*\s*=|100\.0?\s*\*")
READS_LIVE_JOURNAL = re.compile(
    r"diag/journal|trade_journal_db_path|trade_journal\.db|TRADE_JOURNAL_DB|from src\.units\.db|Database\(")

CLASSES = (
    "price_path",
    "account_level",
    "mixed",
    "no_rate_over_journal_trades",
    "ungradeable",
)
"""`ungradeable` is *we could not determine*, deliberately not folded into either
substantive class -- guessing wrong in the `account_level` direction silently blesses
an inflated rate."""


def find_candidates(root_dir: pathlib.Path) -> dict[str, dict]:
    """Every .py under ROOTS that reads `trades` AND forms a rate.

    Deliberately over-collects: the mechanical half cannot tell a price-path rate
    from an account one, so it hands everything it finds to the declared registry
    and lets the UNDECLARED count be the finding.
    """
    out: dict[str, dict] = {}
    for r in ROOTS:
        base = root_dir / r
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if not (READS_TRADES.search(text) and COMPUTES_A_RATE.search(text)):
                continue
            rel = str(p.relative_to(root_dir))
            out[rel] = {
                "reads_live_journal": bool(READS_LIVE_JOURNAL.search(text)),
                "mentions_order_package_id": "order_package_id" in text,
                "rate_sites": len(COMPUTES_A_RATE.findall(text)),
            }
    return out


# --- the declared registry: the row's "name the surfaces" clause ------------
#
# One entry per candidate the mechanical half finds. `class` is DECLARED by a human
# who read the code, because the row is explicit that no checker can read which
# question a rate is asking. Classified 2026-09-12 (MI-278 U12) over the 36
# live-journal candidates; a candidate absent from here grades `undeclared`, which is
# the finding, not a pass.
#
#   dedupes_by_package  -- the surface already reduces rows to one per package
#   states_package_count-- it prints a distinct package count beside its row count
#   account_filtered    -- it REQUIRES a single --account, which blunts fan-out to
#                          ~1.0x by accident; it does not NAME the issue, so removing
#                          the filter silently reintroduces it
#   live_spa_surface    -- a number a human reads on the dashboard
#   rederived_by        -- a COMPANION instrument re-derived this surface's statistic
#                          per package. ⚠️ IT DOES NOT MEAN THE SURFACE WAS FIXED: the
#                          surface still publishes the row-denominator rate, and this
#                          field is what makes the companion discoverable FROM the
#                          surface list instead of only from a memo nobody re-reads.
#
DECLARED: dict[str, dict] = {
    "scripts/ml/build_exit_head_dataset.py": {"class": "price_path",
     "rate": "holding_pays rate (share of bar-rows where future_r_delta >= +0.25R)"},
    "scripts/ml/fc_geometry_resolve.py": {"class": "price_path",
     "rate": "censoring rate over counterfactual barrier walks"},
    "scripts/ml/fc_sltp_geometry_backtest.py": {"class": "price_path",
     "rate": "win_rate over live closed journal trades per arm"},
    "scripts/ops/backfill_pnl_nulls.py": {"class": "no_rate_over_journal_trades"},
    "scripts/ops/bybit_account_audit.py": {"class": "no_rate_over_journal_trades"},
    "scripts/ops/bybit_bracket_audit.py": {"class": "account_level"},
    "scripts/ops/close_stranded_journal_row.py": {"class": "no_rate_over_journal_trades"},
    "scripts/ops/dead_leg_audit.py": {"class": "account_level", "states_package_count": True},
    "scripts/ops/exit_path_coverage.py": {"class": "mixed",
     "rate": "r_per_day / giveback_from_peak_r ranked per open trade row"},
    "scripts/ops/lever_reachability_audit.py": {"class": "price_path",
     "rate": "reach_share_pct (cap_R vs the lever's arm_r; signal-level geometry, identical across fanned-out rows)"},
    "scripts/ops/monitor_miss_analysis.py": {"class": "price_path", "account_filtered": True,
     "rate": "mean realized_R per exit class"},
    "scripts/ops/revert_backfill_monitor_closed_pnl.py": {"class": "no_rate_over_journal_trades"},
    "scripts/ops/strategy_performance_audit.py": {"class": "price_path", "account_filtered": True,
     "rate": "win_rate_pct sliced 6 ways, vs the R:R breakeven win rate"},
    "scripts/ops/system_invariants.py": {"class": "no_rate_over_journal_trades", "states_package_count": True},
    "scripts/research/backtest_fidelity_calibrate.py": {"class": "price_path",
     "rate": "live_win_rate and the KS statistic on the live realized-R distribution"},
    "scripts/research/bleed_attribution_2026_09_11.py": {"class": "price_path",
     "rate": "stop_rate_adjudicated, win rate with Wilson intervals, Fisher exact on pre/post 2x2, difference-in-differences",
     "rederived_by": "scripts/research/bleed_record_package_denominator.py",
     "rederived_note": "MI-278 U31 -- the e35 pre/post Fisher CROSSES ALPHA on packages (8e-05 -> 0.05703)"},
    "scripts/research/bracket_calibration_report.py": {"class": "price_path",
     "rate": "reach_rate (share that reached their declared target)"},
    "scripts/research/build_exit_panel.py": {"class": "no_rate_over_journal_trades"},
    "scripts/research/build_research_panel.py": {"class": "no_rate_over_journal_trades"},
    "scripts/research/component_edge_report.py": {"class": "price_path",
     "rate": "per-strategy and per-component-bucket win rate, AUC-of-win, mean-R"},
    "scripts/research/e35_break_attribution.py": {"class": "price_path",
     "rate": "pre/post win rate per arm AND the one-sided binomial p-value built on it",
     "rederived_by": "scripts/research/e35_binomial_package_denominator.py",
     "rederived_note": "MI-278 U32 -- the one-sided binomial takes TWO terms from rows, not one"},
    "scripts/research/exit_reconstruction_validator.py": {"class": "price_path",
     "rate": "exit-mechanism agreement rate (reconstructed sl/tp vs journalled exit_reason)"},
    "scripts/research/exit_reconstruction_validator_v2.py": {"class": "price_path",
     "rate": "exit-mechanism agreement rate plus <=10/<=50 bps hit shares per arm"},
    "scripts/research/fanout_denominator_survey.py": {"class": "account_level", "states_package_count": True,
     "rate": "the inflation ratios themselves -- a row count over a package count"},
    "scripts/research/ict_scalp_phase0/build_percell.py": {"class": "price_path", "dedupes_by_package": True, "states_package_count": True,
     "rate": "per-(trend,vol)-cell win rate over price-based R"},
    "scripts/research/m20_exit_analysis.py": {"class": "price_path",
     "rate": "roundtrippers_pct: share reaching >=1R MFE that still closed negative"},
    "scripts/research/m26_p0_conflict_bleed.py": {"class": "price_path",
     "rate": "held_worse_than_close_pct"},
    "scripts/research/net_r_regrade.py": {"class": "price_path",
     "rate": "mean_net_R / mean_gross_R per cell and the sign_flip flag derived from their sums"},
    "scripts/research/netting_close_venue_adjudication.py": {"class": "account_level"},
    "scripts/research/stop_width_counterfactual_2026_09_11.py": {"class": "price_path", "dedupes_by_package": True, "states_package_count": True,
     "rate": "winners_killed_by_e35_stop / winners -- BUT dose_response() computes win_rate and stop_rate over ROWS, against the file's own stated universal rule"},
    "scripts/research/tp_recovery_counterfactual.py": {"class": "price_path",
     "rate": "share of trades whose price reached the declared take-profit during the hold"},
    "scripts/research/venue_mechanism_recovery.py": {"class": "account_level"},
    "src/web/api/routers/attribution.py": {"class": "price_path", "live_spa_surface": True,
     "rate": "per-strategy lifetime win rate (real money)"},
    "src/web/api/routers/dashboard.py": {"class": "price_path", "live_spa_surface": True,
     "rate": "/api/bot/stats winRate -- the first number both apps render"},
    "src/web/api/routers/performance.py": {"class": "price_path", "live_spa_surface": True,
     "rate": "window winRate, expectancy, expectancyR, rCoverage, bracketOutcome.reachedRatio -- all per trades row"},
    "src/web/api/routers/trade_scores.py": {"class": "no_rate_over_journal_trades"},
}


#: A surface can carry a package notion through EITHER the `order_package_id` COLUMN
#: or the `order_packages` TABLE, and keying on the column alone is UNSOUND.
#:
#: ⚠️ MEASURED 2026-09-17 (MI-278 U50) and it is not hypothetical: a narrow probe on
#: `order_package_id` grades `scripts/ops/dead_leg_audit.py` as a FALSE DECLARATION --
#: it declares `states_package_count: True` and never names that column -- while the
#: file plainly counts packages, by querying `FROM order_packages` directly. The
#: declaration is right and the probe was wrong. A guard shipped on the narrow probe
#: would have reported a correct surface as lying, which is the direction that gets a
#: guard disabled rather than fixed. Widening it takes that false positive to ZERO.
_PACKAGE_NOTION = re.compile(
    r"order_package_id|order_packages|per_package|n_packages|distinct_packages"
    r"|package[_ ]count")

#: The four payment states, never collapsed.
#:
#: ⚠️ THERE IS DELIBERATELY NO `paid_in_place` HERE, AND AN EARLIER DRAFT OF THIS
#: MODULE HAD ONE. It graded 6 of the 21 exposed surfaces `paid_in_place` off a
#: WHOLE-FILE token match -- contradicting this module's own docstring two functions
#: down, which says a positive is necessary and NOT sufficient. A file can name a
#: package in a comment, in an unrelated query, or beside the rate rather than in it.
#: Naming that state "paid" would have published exactly the overclaim the asymmetry
#: was written to prevent, so the positive is named for what it is: unverified.
NOTION_UNVERIFIED = "package_notion_present_rate_unverified"
REDERIVED_ELSEWHERE = "rederived_elsewhere"
NO_PACKAGE_NOTION = "no_package_notion"
PAYMENT_UNREADABLE = "unreadable"


def package_notion(path: str, root: pathlib.Path | None = None) -> str | None:
    """Does this file carry ANY package notion? `None` means we could not read it.

    ⚠️ THE ANSWER IS ASYMMETRIC AND THAT ASYMMETRY IS THE WHOLE POINT.

    A ``False`` here is a SOUND, strong negative: a file that never names a package in
    any form cannot be stating a package denominator, so ``no_package_notion`` is
    mechanically checkable and needs no human.

    A ``True`` is NECESSARY AND NOT SUFFICIENT. It says the token appears somewhere --
    in a comment, in an unrelated query, beside the rate rather than in it. It does NOT
    say the RATE states its denominator, and nothing textual can: that is the judgement
    the backlog row explicitly says no checker can make ("no checker can read which
    question is being asked"). So this never upgrades a declaration; it only ever
    CONTRADICTS one.
    """
    fp = (root or pathlib.Path(".")) / path
    try:
        text = fp.read_text(errors="replace")
    except OSError:
        return None
    return bool(_PACKAGE_NOTION.search(text))


def payment_state(path: str, root: pathlib.Path | None = None) -> str:
    """How was this exposed surface's denominator debt settled, if at all?

    ``package_notion_present_rate_unverified``
                             the file names a package SOMEWHERE. Necessary, NOT
                             sufficient, and never reported as paid -- whether the RATE
                             uses it is a human judgement this cannot make.
    ``rederived_elsewhere``  a COMPANION instrument re-derived the number and the
                             surface is UNCHANGED -- so a reader who runs the surface
                             still gets the row-denominator rate, with no pointer.
    ``no_package_notion``    the SOUND negative: no package appears in the file in any
                             form, so it cannot be stating a package denominator.
    ``unreadable``           we could not read the file.

    ⚠️ ``rederived_elsewhere`` IS NOT A WEAKER ``paid``, IT IS A DIFFERENT FACT, and
    conflating them is what this function exists to stop. MI-278 U31 and U32 both
    re-derived a statistic per package and published the result in a memo; neither
    touched the producing surface. The prose in the backlog row says those surfaces
    were "paid", the registry correctly still lists them as exposed, and a session
    reading the prose would not re-check. Field beats comment.
    """
    notion = package_notion(path, root)
    if notion is None:
        return PAYMENT_UNREADABLE
    if notion:
        return NOTION_UNVERIFIED
    if DECLARED.get(path, {}).get("rederived_by"):
        return REDERIVED_ELSEWHERE
    return NO_PACKAGE_NOTION


def declaration_contradictions(root: pathlib.Path | None = None) -> list[dict]:
    """Declarations the code CONTRADICTS: a claimed package count with no notion at all.

    One direction only, deliberately -- see :func:`package_notion`. An empty list means
    no declaration is refuted; it does NOT mean every declaration is correct.

    ⚠️ It reads ``DECLARED``, never a discovered candidate set. An earlier signature took
    a ``candidates`` argument it never used -- the inert parameter of
    ``diagnostic-provenance`` class D, which reads as *"graded over what you passed in"*
    while grading something else. The same defect was removed from
    ``risk_dispersion_attribution.py`` one unit earlier and re-introduced here; the guard
    caught it, this note is why it is not re-introduced again.
    """
    out = []
    for path, d in sorted(DECLARED.items()):
        if not (d.get("states_package_count") or d.get("dedupes_by_package")):
            continue
        notion = package_notion(path, root)
        if notion is False:
            out.append({"path": path, "declared": d, "package_notion": False})
    return out


def grade_registry(candidates: dict[str, dict]) -> dict:
    """Join the mechanically-found candidates to the declared registry.

    `undeclared` is the finding: a surface nobody has classified is not a surface
    that is fine, and it is deliberately not defaulted to either substantive class.
    """
    live = {k: v for k, v in candidates.items() if v["reads_live_journal"]}
    undeclared = sorted(set(live) - set(DECLARED))
    stale = sorted(set(DECLARED) - set(live))
    by_class: dict[str, list[str]] = {}
    for path in sorted(live):
        d = DECLARED.get(path)
        by_class.setdefault(d["class"] if d else "undeclared", []).append(path)
    exposed = [
        p for p in sorted(live)
        if (DECLARED.get(p, {}).get("class") in ("price_path", "mixed")
            and not DECLARED.get(p, {}).get("dedupes_by_package"))
    ]
    return {
        "live_candidates": len(live),
        "undeclared": undeclared,
        "declared_but_no_longer_a_candidate": stale,
        "by_class": by_class,
        "price_path_not_deduped": exposed,
        "price_path_not_deduped_live_spa": [
            p for p in exposed if DECLARED.get(p, {}).get("live_spa_surface")],
        "price_path_blunted_by_account_filter_only": [
            p for p in exposed if DECLARED.get(p, {}).get("account_filtered")],
    }


# --- the two inflations -----------------------------------------------------


def inflation(rows: list[dict]) -> dict:
    """Decompose n-inflation into its cross-account and intra-account terms.

    `rows_without_package` is reported, never dropped and never counted as its own
    package: a row we cannot group is *we could not look*, and silently treating each
    as a distinct package would understate the inflation exactly where the data is
    worst.
    """
    with_pkg = [r for r in rows if r.get("order_package_id")]
    without = len(rows) - len(with_pkg)
    if not with_pkg:
        return {
            "rows": len(rows),
            "rows_without_package": without,
            "packages": 0,
            "overall": None,
            "cross_account": None,
            "intra_account": None,
            "packages_spanning_multiple_accounts": None,
            "state": "no_gradeable_rows",
        }

    packages = {r["order_package_id"] for r in with_pkg}
    pairs = collections.Counter(
        (r.get("account_id"), r["order_package_id"]) for r in with_pkg)
    spanning = collections.defaultdict(set)
    for r in with_pkg:
        spanning[r["order_package_id"]].add(r.get("account_id"))

    return {
        "rows": len(rows),
        "rows_without_package": without,
        "packages": len(packages),
        # every row against distinct packages -- what a naive dedupe would remove
        "overall": len(with_pkg) / len(packages),
        # one row per (account, package): the ACCOUNT-SPANNING term alone
        "cross_account": len(pairs) / len(packages),
        # rows per (account, package): the term a package-dedupe ALSO removes
        "intra_account": len(with_pkg) / len(pairs),
        "packages_spanning_multiple_accounts": sum(1 for v in spanning.values() if len(v) > 1),
        "state": "measured",
    }


def unequal_across(rows: list[dict], key) -> list[tuple]:
    """Inflation per cut. The row's finding is that it is UNEQUAL between arms, so a
    single overall figure is not enough to decide whether a comparison is distorted."""
    g = collections.defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    out = []
    for k, v in g.items():
        i = inflation(v)
        out.append((k, i["rows"], i["packages"], i["overall"]))
    return sorted(out, key=lambda t: -t[1])


# --- self-test --------------------------------------------------------------


def self_test() -> int:
    ok = 0

    def chk(c, m):
        nonlocal ok
        print(("  ok  " if c else "  FAIL ") + m)
        ok += 0 if c else 1

    def R(a, p):
        return {"account_id": a, "order_package_id": p}

    i = inflation([R("a", "p1"), R("b", "p1"), R("c", "p1")])
    chk(i["overall"] == 3.0, "three accounts on one package inflate 3x")
    chk(i["cross_account"] == 3.0, "  and it is entirely the CROSS-account term")
    chk(i["intra_account"] == 1.0, "  with no intra-account duplication")
    chk(i["packages_spanning_multiple_accounts"] == 1, "  the spanning package is counted")

    i = inflation([R("a", "p1"), R("a", "p1"), R("a", "p1")])
    chk(i["overall"] == 3.0, "three rows on ONE account also read 3x overall")
    chk(i["cross_account"] == 1.0, "  but the cross-account term is 1.0 -- it is NOT fan-out")
    chk(i["intra_account"] == 3.0, "  and the intra-account term carries all of it")
    chk(i["packages_spanning_multiple_accounts"] == 0,
        "  so a package-dedupe here would remove rows fan-out never duplicated")

    i = inflation([R("a", "p1"), R("b", "p2")])
    chk(i["overall"] == 1.0, "one row per package is no inflation at all")

    i = inflation([{"account_id": "a"}, R("a", "p1")])
    chk(i["rows_without_package"] == 1 and i["packages"] == 1,
        "a row with NO package is reported, never counted as its own package")
    i = inflation([{"account_id": "a"}])
    chk(i["state"] == "no_gradeable_rows" and i["overall"] is None,
        "no gradeable row returns None and its own state, never 1.0")
    chk(inflation([])["overall"] is None, "an empty population cannot be graded")

    cuts = unequal_across([R("a", "p1"), R("b", "p1"), R("c", "p2")],
                          lambda r: "x" if r["account_id"] in ("a", "b") else "y")
    chk(dict((c[0], c[3]) for c in cuts) == {"x": 2.0, "y": 1.0},
        "inflation is reported PER CUT, because it is unequal between arms")

    chk(set(CLASSES) >= {"ungradeable"}, "`ungradeable` is a declarable class")

    g = grade_registry({"a.py": {"reads_live_journal": True},
                        "b.py": {"reads_live_journal": False}})
    chk(g["undeclared"] == ["a.py"],
        "a live candidate nobody classified grades `undeclared`, never a pass")
    chk("b.py" not in str(g["by_class"]),
        "a backtest-only candidate is not held against the registry")
    g2 = grade_registry({p: {"reads_live_journal": True} for p in DECLARED})
    chk(g2["undeclared"] == [], "the shipped registry covers every candidate it declares")
    chk(all(DECLARED[p]["class"] in CLASSES for p in DECLARED),
        "every declared class is in the closed vocabulary")
    chk(all(not DECLARED[p].get("dedupes_by_package")
            for p in g2["price_path_not_deduped"]),
        "the exposed list never contains a surface that already dedupes")
    # --- MI-278 U50: the payment axis, and the probe that must not come back ----
    root = pathlib.Path(".")

    # THE NAMED REGRESSION CONTROL. A narrow probe on the `order_package_id` COLUMN
    # grades dead_leg_audit.py a false declaration; it counts packages by querying the
    # order_packages TABLE. If this fails, someone narrowed _PACKAGE_NOTION back.
    chk(package_notion("scripts/ops/dead_leg_audit.py", root) is True,
        "dead_leg_audit counts packages via the TABLE, not the column — and is seen")
    chk("order_package_id" not in
        pathlib.Path("scripts/ops/dead_leg_audit.py").read_text(errors="replace"),
        "...and it genuinely never names the column, so the control is live not vacuous")

    chk(declaration_contradictions(root) == [],
        "no declaration is contradicted by the code today")

    chk(package_notion("scripts/research/bleed_attribution_2026_09_11.py", root) is False,
        "the bleed-attribution surface still names no package at all")
    chk(payment_state("scripts/research/bleed_attribution_2026_09_11.py", root)
        == REDERIVED_ELSEWHERE,
        "...and grades rederived_elsewhere, NOT paid — the surface is unchanged")
    chk(payment_state("scripts/research/e35_break_attribution.py", root)
        == REDERIVED_ELSEWHERE,
        "the e35 break-attribution surface likewise")
    chk(payment_state("scripts/research/m20_exit_analysis.py", root) == NO_PACKAGE_NOTION,
        "a surface with no companion and no notion grades no_package_notion")
    chk(package_notion("scripts/research/does_not_exist_xyz.py", root) is None,
        "an unreadable file is None — we could not look, never False")
    chk(payment_state("scripts/research/does_not_exist_xyz.py", root) == PAYMENT_UNREADABLE,
        "...and grades unreadable, never no_package_notion")

    # The vocabulary must not regain a state that asserts what it cannot establish.
    chk("paid_in_place" not in globals().values() and
        NOTION_UNVERIFIED == "package_notion_present_rate_unverified",
        "the positive state is named unverified, never paid")
    chk(len({NOTION_UNVERIFIED, REDERIVED_ELSEWHERE, NO_PACKAGE_NOTION,
             PAYMENT_UNREADABLE}) == 4,
        "the four payment states are distinct")

    print("self-test: OK" if ok == 0 else f"self-test: {ok} FAILURE(S)")
    return 1 if ok else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--journal", help="a saved /api/diag/journal?table=trades pull")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    root = pathlib.Path(a.root).resolve()
    cands = find_candidates(root)
    live = {k: v for k, v in cands.items() if v["reads_live_journal"]}
    payload: dict = {
        "candidates": len(cands),
        "read_live_journal": len(live),
        "backtest_only": len(cands) - len(live),
        "live_mentioning_order_package_id": sum(
            1 for v in live.values() if v["mentions_order_package_id"]),
    }

    if a.journal:
        rows = json.loads(pathlib.Path(a.journal).read_text())
        if isinstance(rows, dict):
            rows = rows.get("rows") or []
        rows = [r for r in rows if not r.get("is_backtest")]
        payload["inflation"] = inflation(rows)
        payload["by_account"] = unequal_across(rows, lambda r: r.get("account_id"))
        payload["by_account_class"] = unequal_across(rows, lambda r: r.get("account_class"))

    if a.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    g = grade_registry(cands)
    print(f"CANDIDATE SURFACES (read `trades` AND form a rate): {payload['candidates']}")
    print(f"  read the LIVE JOURNAL          {payload['read_live_journal']}")
    print(f"  backtest/synthetic corpus only {payload['backtest_only']}  "
          "(no accounts, therefore no fan-out)")
    print(f"  of the live ones, mentioning order_package_id at all: "
          f"{payload['live_mentioning_order_package_id']} of {payload['read_live_journal']}")
    print()
    print("DECLARED CLASSIFICATION of the live-journal surfaces "
          f"({g['live_candidates']} candidates):")
    for k in list(CLASSES) + ["undeclared"]:
        v = g["by_class"].get(k)
        if v:
            print(f"  {k:28s} {len(v)}")
    if g["undeclared"]:
        print(f"  ^^ UNDECLARED ({len(g['undeclared'])}) -- nobody has read these; "
              "that is the finding, not a pass:")
        for p in g["undeclared"]:
            print(f"       {p}")
    if g["declared_but_no_longer_a_candidate"]:
        print("  stale registry entries (declared, no longer a candidate): "
              + ", ".join(g["declared_but_no_longer_a_candidate"]))
    print()
    print(f"PRICE-PATH RATES ON A ROW DENOMINATOR: {len(g['price_path_not_deduped'])} surface(s)")
    for p in g["price_path_not_deduped"]:
        d = DECLARED.get(p, {})
        tag = " [LIVE SPA]" if d.get("live_spa_surface") else (
            " [blunted by a required --account, not by design]" if d.get("account_filtered") else "")
        state = payment_state(p, root)
        print(f"    {p}{tag}  [{state}]")
        if d.get("rate"):
            print(f"        {d['rate']}")
        if d.get("rederived_by"):
            print(f"        ^ re-derived per package by {d['rederived_by']}")
            print(f"          {d.get('rederived_note', '')}")
            print("          \u26a0\ufe0f THE SURFACE ITSELF IS UNCHANGED -- running it still "
                  "gives the row-denominator rate.")

    states = collections.Counter(
        payment_state(p, root) for p in g["price_path_not_deduped"])
    print()
    print("  PAYMENT STATE over those surfaces -- four different facts, never pooled:")
    for k in (NOTION_UNVERIFIED, REDERIVED_ELSEWHERE, NO_PACKAGE_NOTION,
              PAYMENT_UNREADABLE):
        if states.get(k):
            print(f"    {k:22s} {states[k]}")
    print("    \u26a0\ufe0f NONE OF THESE IS `paid`, AND THAT IS DELIBERATE. "
          "`..._rate_unverified` means\n       the file names a package somewhere -- necessary, "
          "not sufficient. "
          "`rederived_elsewhere`\n       means a companion re-derived the number and THE SURFACE "
          "IS UNCHANGED, so a reader\n       who runs it still gets the un-annotated row rate. "
          "Prose calling those 'paid' is what\n       goes stale.")

    contra = declaration_contradictions(root)
    print()
    if contra:
        print(f"  \u26a0\ufe0f DECLARATIONS THE CODE CONTRADICTS: {len(contra)}")
        for c in contra:
            print(f"    {c['path']} declares a package count and names no package anywhere")
    else:
        print("  declarations contradicted by the code: 0 \u2014 and that is ONE DIRECTION ONLY. "
              "A\n    mechanical probe can refute a package claim; it can never confirm that a "
              "RATE\n    states its denominator, which is the judgement no checker can make.")

    if "inflation" in payload:
        i = payload["inflation"]
        print()
        print(f"INFLATION over {i['rows']} non-backtest rows / {i['packages']} packages "
              f"({i['rows_without_package']} rows carry no package id)")
        print(f"  overall        {i['overall']:.3f}x   <- what a naive package-dedupe removes")
        print(f"  cross-account  {i['cross_account']:.3f}x   <- THE FAN-OUT: "
              f"{i['packages_spanning_multiple_accounts']} package(s) span >1 account")
        print(f"  intra-account  {i['intra_account']:.3f}x   <- a DIFFERENT thing "
              "(partial close, re-adoption); a package-dedupe removes this too")
        print()
        print("  PER ACCOUNT -- an analysis restricted to ONE account has almost no fan-out,")
        print("  because fan-out is BY DEFINITION across accounts:")
        for k, n, p, f in payload["by_account"][:8]:
            print(f"    {str(k):24s} {n:5d} rows {p:5d} pkgs  {f:.3f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
