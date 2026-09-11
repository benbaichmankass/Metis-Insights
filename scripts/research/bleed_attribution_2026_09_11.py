#!/usr/bin/env python3
# wiring: manual-only - a one-off attribution analysis for a DATED event (the
# 2026-08-30 directional-leg break). It answers a question that was asked once,
# against a journal window that has already passed; a scheduled runner would
# re-answer it every day against a moving population and quietly turn a recorded
# verdict into a drifting one. The standing, cadenced version of this question is
# the sustained-losing-streak detector proposed in section 6 of the memo, which is
# a DIFFERENT deliverable and is deliberately not this file.
"""Attribute the 2026-08-30 directional-leg regime break (MI-271 / OI-20260911).

WHAT THIS ANSWERS, AND WHAT IT REFUSES TO
-----------------------------------------
`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-
UNATTRIBUTED` names ONE discriminating measurement: per-leg stop-out rate and
MFE-at-stop, **e35 legs vs NON-e35 legs, before vs after 2026-08-30**, with the
verdict and its population recorded. Two candidates -- the e35 bracket geometry
shipped that day, or a market regime change -- and neither established.

The control is the whole experiment. `ict_scalp_*` was never touched by e35 and
degraded too; if e35 were the whole story that should not have happened. So this
script reports the effect size for BOTH groups either way -- a null result here
is a real finding, not a failure.

THREE INSTRUMENT REPAIRS COME FIRST (the cycle priority's order: repair the
measurement before acting on what it says).

1. THE SPLIT IS ON `created_at`, NEVER `closed_at`. A leg carries the geometry
   it was OPENED under. A trade opened 2026-08-21 and closed 2026-09-02 carries
   the PRE-e35 bracket however late it closed, and grading it as "after" would
   attribute old geometry to the new regime. This is the exact trap
   `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS` had to sharpen its own clause
   (b) to close, on a real trade (4904) that satisfied the loose wording while
   carrying the old bracket.

2. `exit_reason` CANNOT BE USED FOR STOP-OUT RATE AS IT STANDS. The label is
   frozen at the one moment the answer could not be known: `order_monitor`
   pins `reconciler_filled` when `exit_price` is NULL, and when a price later
   arrives NO writer re-runs `_classify_broker_exit`
   (`BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE`; measured there at
   91 of 155 = 58.7% of `reconciler_filled` rows having actually reached a
   declared bracket level). `reconciler_filled` is the single largest exit label
   in the window, so a stop-out rate read off `exit_reason` is not a
   measurement of stop-outs. This script therefore ADJUDICATES exit location
   from the row's own declared levels and its exit price, and it PROVES the
   adjudicator works with a positive control before using it (see
   `positive_control`): on rows the venue-side classifier DID label `sl`, an
   adjudicator that cannot recover `reached_stop` is broken, and reporting its
   verdict on the frozen rows would be the unprovenanced-diagnostic class this
   repo has a guard for.

3. STATES ARE NEVER COLLAPSED. `ungradeable_no_price` and `ungradeable_no_levels`
   are *we could not look*; `neither` is *we looked and it exited away from both
   brackets*. Folding the first two into the third manufactures a clean negative
   -- sub-class C of UNPROVENANCED DIAGNOSTIC OUTPUT.

POPULATION (stated, per the top-level binding rule -- a headline figure in this
repo once flipped its own SIGN on a filter choice):
  closed, non-backtest, `pnl NOT NULL`, PAIRS SLEEVE EXCLUDED.
The pairs sleeve is a separate 2-leg order path (`pairs_executor`, not
`multi_account_execute`) and is already EXONERATED by OI-20260911 (+$29.69 over
120 post closes); leaving it in would dilute every directional rate with 198
rows from a mechanism that is not under investigation.

Source data: `/api/diag/journal?table=trades&limit=1000` -- a 1000-row TAIL, not
the lifetime. Its truncation bias is measured and reported (`window`), not
assumed away: the window is bounded by trade id, so a trade OPENED before the
oldest id but CLOSED inside the window is absent. That biases toward
short-duration trades at the old end of the window, which is stated in the
memo rather than corrected here (correcting it needs the full DB).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.runtime import provenance as P  # noqa: E402

# ---------------------------------------------------------------------------
# The e35 change, read off the COMMIT, not off prose.
#
# `git show 892c9a2c -- config/strategies.yaml`, merged 2026-08-30T08:53:19Z.
# 10 field edits across 9 legs; a 10th leg was HELD.
#
# NOTE THE SHAPE, because the one-line summary in circulation
# ("atr_stop_mult 2.5 -> 2.0, 9 legs") is an over-simplification that would
# destroy this analysis if taken at face value: the shipped change is a
# FIVE-LEVEL LADDER. Two legs were tightened to 1.5, one was WIDENED to 3.0,
# and one leg's stop was not touched at all (only its tp_r). That ladder is
# what makes a dose-response test possible -- if tighter stops are the cause,
# the 1.5 legs must degrade MORE than the 2.0 legs and the 3.0 leg must degrade
# LESS or not at all.
# ---------------------------------------------------------------------------
E35_DEPLOY_UTC = "2026-08-30T08:53:19+00:00"

E35_LEGS: dict[str, dict[str, Any]] = {
    "trend_donchian":         {"atr_stop_mult": (2.5, 2.0)},
    "htf_pullback_trend_2h":  {"atr_stop_mult": (2.5, 3.0)},   # WIDER
    "trend_donchian_eth_4h":  {"atr_stop_mult": (2.5, 2.0)},
    "trend_donchian_sol_4h":  {"atr_stop_mult": (2.5, 1.5)},   # much tighter
    "trend_donchian_xrp_4h":  {"atr_stop_mult": (2.5, 2.0), "tp_r": (50.0, 3.0)},
    "trend_donchian_ada_4h":  {"atr_stop_mult": (2.5, 2.0)},
    "trend_donchian_avax_4h": {"atr_stop_mult": (2.5, 1.5)},   # much tighter
    "ada_pullback_2h":        {"tp_r": (50.0, 4.0)},           # stop UNCHANGED
    "avax_pullback_2h":       {"atr_stop_mult": (2.5, 2.0)},
}
# Held deliberately at the base leg's 2.5 -- the WITHIN-FAMILY control, same
# strategy family and same symbol (ETHUSDT) as trend_donchian_eth_4h.
E35_HELD = {"trend_donchian_eth_prop"}

# ---------------------------------------------------------------------------
# THE CONTROL WAS CONTAMINATED AND THE CHANGE CENSUS IS WHAT CAUGHT IT.
#
# e35 is NOT the only bracket-geometry change in this window. `91de68b9c`
# ("M20 B4: declare validated bracket geometry on 8 legs"), merged
# 2026-08-29T17:08:30Z -- THE DAY BEFORE e35 -- changed atr_stop_mult and/or
# tp_r on eight FURTHER legs. Every one of them would otherwise have been
# graded as part of the untouched `non_e35` control, which is precisely the
# group whose stability the e35 verdict rests on. An analysis that split only
# on e35 would have been measuring "changed legs vs partly-changed legs" and
# calling the second one a control.
#
# This is why the dispatch required the change census BEFORE attributing:
# 2026-08-30 does not carry one change, it carries at least three (e35, B4 the
# day before, and BYBIT_HEDGE_MODE_SYMBOLS arming). "It changed on the day e35
# shipped" is a coincidence until the others are enumerated and held apart.
# ---------------------------------------------------------------------------
B4_DEPLOY_UTC = "2026-08-29T17:08:30+00:00"
B4_LEGS = {
    "mgc_pullback_1d", "spy_trend_long_1d", "qqq_trend_long_1d",
    "iwm_trend_long_1d", "slv_trend_1h", "tlt_pullback_1h",
    "uso_trend_1h", "scha_trend_long_1d",
}

# The GOLD-STANDARD control: legs that declare NEITHER `atr_stop_mult` NOR
# `tp_r` in config/strategies.yaml at all, so no bracket-geometry change could
# have reached them even in principle -- they are structurally immune rather
# than merely un-edited. Verified against the live config, not assumed: every
# `ict_scalp_*` leg reads `atr_stop_mult: None, tp_r: None`.
#
# This is a STRONGER control than "a leg nobody happened to edit", because it
# cannot be silently contaminated by a future geometry commit.
UNTOUCHED_PREFIXES = ("ict_scalp_",)

# The pairs sleeve: a separate order path (`pairs_executor.run_pairs_tick`, NOT
# `multi_account_execute`), already exonerated by OI-20260911.
#
# IT IS EXCLUDED ON *BOTH* KEYS, AND THE SECOND ONE IS NOT REDUNDANT. Filtering
# on the `pairs_*` exit vocabulary alone leaks every pairs row whose exit label
# was frozen at `reconciler_filled` by the defect in repair (2) above -- measured
# here at 42 rows across three pairs legs, which then land in the `non_e35`
# control and dilute exactly the comparison the whole analysis turns on. A
# filter that is correct for the healthy rows and silently wrong for the broken
# ones is worse than no filter.
PAIRS_EXIT_PREFIX = "pairs_"
PAIRS_STRATEGY_PREFIX = "pairs_"

# exit_reason values that DECLARE a stop. Kept only to measure how far the
# declared label is from the adjudicated one -- never used as the stop-out rate.
DECLARED_STOP = {"sl", "sl_cross", "stale_stop", "giveback_stop"}
DECLARED_TARGET = {"tp", "tp_cross"}
# The frozen label. Not a stop and not a target -- it is "we did not classify".
FROZEN_LABEL = "reconciler_filled"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion.

    Reported on every rate in this analysis because the per-LEG cells are tiny
    (2-10 trades) and a bare "win rate fell from 100% to 0%" over n=4 and n=3
    reads as a catastrophe while being entirely consistent with noise. The
    interval is what stops a reader -- including a later session -- from acting
    on a cell that says nothing.
    """
    if n <= 0:
        return None
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def fisher_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a,b],[c,d]]. Stdlib only (no scipy here)."""
    from math import comb
    n = a + b + c + d
    r1, c1 = a + b, a + c
    def pr(x: int) -> float:
        return comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)
    obs = pr(a)
    lo = max(0, c1 - (n - r1))
    hi = min(r1, c1)
    return round(sum(pr(x) for x in range(lo, hi + 1) if pr(x) <= obs * (1 + 1e-9)), 5)


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # NaN out
    except (TypeError, ValueError):
        return None


def adjudicate_exit(row: dict, tol: float) -> str:
    """Where did this trade actually exit, relative to its OWN declared bracket?

    Five states, never collapsed. `tol` is a fractional band absorbing slippage
    and the fact that a stop fills THROUGH its level, not at it.
    """
    px = _f(row.get("exit_price"))
    sl = _f(row.get("stop_loss"))
    tp = _f(row.get("take_profit_1"))
    direction = (row.get("direction") or "").lower()
    if px is None or px <= 0:
        return "ungradeable_no_price"
    if direction not in ("long", "short"):
        return "ungradeable_no_direction"
    has_sl = sl is not None and sl > 0
    has_tp = tp is not None and tp > 0
    if not has_sl and not has_tp:
        return "ungradeable_no_levels"

    if direction == "long":
        hit_sl = has_sl and px <= sl * (1.0 + tol)
        hit_tp = has_tp and px >= tp * (1.0 - tol)
    else:
        hit_sl = has_sl and px >= sl * (1.0 - tol)
        hit_tp = has_tp and px <= tp * (1.0 + tol)

    if hit_sl and hit_tp:
        # A declared bracket whose legs overlap inside the tolerance band. Not a
        # stop-out and not a target -- it is a bracket we cannot adjudicate.
        return "ungradeable_bracket_degenerate"
    if hit_sl:
        return "reached_stop"
    if hit_tp:
        return "reached_target"
    return "neither"


def load_rows(path: str) -> list[dict]:
    with open(path) as fh:
        return json.load(fh)


def population(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("status") != "closed":
            continue
        if r.get("is_backtest"):
            continue
        if r.get("pnl") is None:
            continue
        if str(r.get("exit_reason") or "").startswith(PAIRS_EXIT_PREFIX):
            continue
        if str(r.get("strategy_name") or "").startswith(PAIRS_STRATEGY_PREFIX):
            continue
        out.append(r)
    return out


def group_of(name: str) -> str:
    if name in E35_LEGS:
        return "e35"
    if name in E35_HELD:
        return "e35_held_control"
    if name in B4_LEGS:
        return "b4_geometry"
    if name.startswith(UNTOUCHED_PREFIXES):
        return "untouched_control"
    return "other"


def era_of(row: dict) -> str:
    """PRE/POST by OPEN time -- a leg carries the geometry it was opened under."""
    ca = row.get("created_at")
    if not ca:
        return "unknown"
    return "post" if str(ca) >= E35_DEPLOY_UTC else "pre"


def summarise(rows: list[dict], tol: float) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    pnls = [float(r["pnl"]) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    adj = collections.Counter(adjudicate_exit(r, tol) for r in rows)
    gradeable = adj["reached_stop"] + adj["reached_target"] + adj["neither"]
    prov = collections.Counter()
    measured_pnls = []
    for r in rows:
        bucket, _ = P.classify_pnl(r)
        prov[bucket] += 1
        if bucket == P.MEASURED:
            measured_pnls.append(float(r["pnl"]))
    return {
        "n": n,
        "win_rate": round(wins / n, 4),
        "win_rate_ci95": wilson(wins, n),
        "wins": wins,
        "pnl_sum": round(sum(pnls), 2),
        "pnl_mean": round(statistics.fmean(pnls), 2),
        "pnl_median": round(statistics.median(pnls), 2),
        "loss_mean": round(statistics.fmean([p for p in pnls if p <= 0]), 2) if any(p <= 0 for p in pnls) else None,
        "win_mean": round(statistics.fmean([p for p in pnls if p > 0]), 2) if wins else None,
        # The adjudicated rate, over the GRADEABLE denominator only. `None`
        # when nothing could be graded -- never 0.0, which would read as
        # "no stop-outs" when it means "we could not look".
        "stop_rate_adjudicated": round(adj["reached_stop"] / gradeable, 4) if gradeable else None,
        "target_rate_adjudicated": round(adj["reached_target"] / gradeable, 4) if gradeable else None,
        "stop_rate_ci95": wilson(adj["reached_stop"], gradeable) if gradeable else None,
        "stops_adjudicated": adj["reached_stop"],
        "gradeable_n": gradeable,
        "ungradeable_n": n - gradeable,
        "adjudication": dict(adj),
        "pnl_provenance": dict(prov),
        "pnl_coverage": P.coverage({**prov, "total": n}),
        "pnl_sum_measured_only": round(sum(measured_pnls), 2) if measured_pnls else None,
        "measured_n": len(measured_pnls),
    }


def positive_control(rows: list[dict], tol: float) -> dict:
    """Does the adjudicator recover a stop the VENUE-SIDE classifier already found?

    This is the denominator the whole exit-location half rests on. If the
    adjudicator cannot reproduce `reached_stop` on rows independently labelled
    `sl`/`sl_cross`, then its verdict on the frozen `reconciler_filled` rows is
    worthless and must not be reported. A low recall here is a finding about
    THIS SCRIPT, not about the book.
    """
    ctrl = [r for r in rows if (r.get("exit_reason") or "") in DECLARED_STOP]
    tgt = [r for r in rows if (r.get("exit_reason") or "") in DECLARED_TARGET]
    c = collections.Counter(adjudicate_exit(r, tol) for r in ctrl)
    t = collections.Counter(adjudicate_exit(r, tol) for r in tgt)
    c_grade = c["reached_stop"] + c["reached_target"] + c["neither"]
    t_grade = t["reached_stop"] + t["reached_target"] + t["neither"]
    return {
        "declared_stop_n": len(ctrl),
        "declared_stop_adjudication": dict(c),
        "recall_on_declared_stops": round(c["reached_stop"] / c_grade, 4) if c_grade else None,
        "declared_target_n": len(tgt),
        "declared_target_adjudication": dict(t),
        "recall_on_declared_targets": round(t["reached_target"] / t_grade, 4) if t_grade else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="JSON array from /api/diag/journal?table=trades")
    ap.add_argument("--tol", type=float, default=0.0015, help="bracket-touch tolerance, fractional")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    raw = load_rows(args.trades)
    pop = population(raw)

    ids = [r["id"] for r in raw]
    cas = sorted(str(r.get("created_at")) for r in raw if r.get("created_at"))
    window = {
        "source_rows": len(raw),
        "id_min": min(ids), "id_max": max(ids),
        "created_at_min": cas[0], "created_at_max": cas[-1],
        "truncation_bias": (
            "1000-row tail ordered by id DESC. A trade OPENED before id_min but "
            "CLOSED inside the window is ABSENT. Long-held pre-e35 trades are "
            "therefore under-counted in the PRE era."
        ),
        "population_rule": "status=closed AND NOT is_backtest AND pnl IS NOT NULL AND exit_reason NOT LIKE 'pairs_%'",
        "population_n": len(pop),
        "pairs_excluded_n": sum(
            1 for r in raw
            if r.get("status") == "closed" and not r.get("is_backtest") and r.get("pnl") is not None
            and (str(r.get("exit_reason") or "").startswith(PAIRS_EXIT_PREFIX)
                 or str(r.get("strategy_name") or "").startswith(PAIRS_STRATEGY_PREFIX))
        ),
        "pairs_excluded_by_strategy_only_n": sum(
            1 for r in raw
            if r.get("status") == "closed" and not r.get("is_backtest") and r.get("pnl") is not None
            and not str(r.get("exit_reason") or "").startswith(PAIRS_EXIT_PREFIX)
            and str(r.get("strategy_name") or "").startswith(PAIRS_STRATEGY_PREFIX)
        ),
        "split_basis": "created_at (OPEN time) vs e35 deploy " + E35_DEPLOY_UTC,
    }

    out: dict[str, Any] = {
        "window": window,
        "positive_control": positive_control(pop, args.tol),
        "tolerance": args.tol,
        "groups": {},
        "legs": {},
        "accounts": {},
    }

    # Frozen-label exposure: how much of the population carries the label that
    # cannot be trusted, and what the adjudicator says about it.
    frozen = [r for r in pop if (r.get("exit_reason") or "") == FROZEN_LABEL]
    fa = collections.Counter(adjudicate_exit(r, args.tol) for r in frozen)
    out["frozen_label"] = {
        "n": len(frozen),
        "share_of_population": round(len(frozen) / len(pop), 4) if pop else None,
        "adjudication": dict(fa),
    }

    for g in ("e35", "b4_geometry", "untouched_control", "other", "e35_held_control"):
        out["groups"][g] = {}
        for era in ("pre", "post"):
            sel = [r for r in pop if group_of(r.get("strategy_name") or "") == g and era_of(r) == era]
            out["groups"][g][era] = summarise(sel, args.tol)

    # ------------------------------------------------------------------
    # THE DISCRIMINATING TEST, stated as a hypothesis rather than eyeballed.
    #
    # Both groups degrade -- that much is visible without arithmetic and is
    # what makes "the market chopped" a live candidate. The question the row
    # actually asks is whether e35 legs degraded MORE than the legs e35 never
    # touched. So the statistic is a DIFFERENCE-IN-DIFFERENCES, and the
    # significance test is on the POST cross-section (did e35 legs win less
    # than non-e35 legs AFTER the change), which is the comparison that has
    # usable n. A non-significant result here is a REAL FINDING -- it means
    # the data cannot separate the two candidates -- not a failed test.
    # ------------------------------------------------------------------
    def cell(g: str, era: str) -> tuple[int, int]:
        sel = [r for r in pop if group_of(r.get("strategy_name") or "") == g and era_of(r) == era]
        return sum(1 for r in sel if float(r["pnl"]) > 0), len(sel)

    e_pre_w, e_pre_n = cell("e35", "pre")
    e_post_w, e_post_n = cell("e35", "post")
    n_pre_w, n_pre_n = cell("untouched_control", "pre")
    n_post_w, n_post_n = cell("untouched_control", "post")
    d_e35 = (e_post_w / e_post_n - e_pre_w / e_pre_n) if e_pre_n and e_post_n else None
    d_non = (n_post_w / n_post_n - n_pre_w / n_pre_n) if n_pre_n and n_post_n else None
    out["hypothesis_tests"] = {
        "win_rate_cells": {
            "e35_pre": [e_pre_w, e_pre_n], "e35_post": [e_post_w, e_post_n],
            "untouched_control_pre": [n_pre_w, n_pre_n], "untouched_control_post": [n_post_w, n_post_n],
        },
        "delta_win_e35": round(d_e35, 4) if d_e35 is not None else None,
        "delta_win_untouched_control": round(d_non, 4) if d_non is not None else None,
        "difference_in_differences": round(d_e35 - d_non, 4) if (d_e35 is not None and d_non is not None) else None,
        "fisher_p_post_cross_section": fisher_2x2(e_post_w, e_post_n - e_post_w, n_post_w, n_post_n - n_post_w),
        "fisher_p_pre_cross_section": fisher_2x2(e_pre_w, e_pre_n - e_pre_w, n_pre_w, n_pre_n - n_pre_w),
        "fisher_p_e35_pre_vs_post": fisher_2x2(e_pre_w, e_pre_n - e_pre_w, e_post_w, e_post_n - e_post_w),
        "fisher_p_untouched_control_pre_vs_post": fisher_2x2(n_pre_w, n_pre_n - n_pre_w, n_post_w, n_post_n - n_post_w),
        "reading": (
            "fisher_p_e35_pre_vs_post and fisher_p_untouched_control_pre_vs_post both small => BOTH "
            "groups really degraded => a common cause (regime) is established. "
            "fisher_p_post_cross_section small => e35 legs are ALSO worse than the untouched "
            "legs after the change => a second, e35-specific effect on top. "
            "fisher_p_pre_cross_section is the FALSIFIER: if the two groups already differed "
            "BEFORE the change, the post-period gap is not attributable to e35 at all."
        ),
    }

    names = sorted({r.get("strategy_name") or "(none)" for r in pop})
    for nm in names:
        legrows = [r for r in pop if (r.get("strategy_name") or "(none)") == nm]
        entry = {"group": group_of(nm), "e35_change": E35_LEGS.get(nm)}
        for era in ("pre", "post"):
            entry[era] = summarise([r for r in legrows if era_of(r) == era], args.tol)
        out["legs"][nm] = entry

    for acct in sorted({r.get("account_id") or "(none)" for r in pop}):
        arows = [r for r in pop if (r.get("account_id") or "(none)") == acct]
        entry = {"account_class": next((r.get("account_class") for r in arows), None)}
        for era in ("pre", "post"):
            entry[era] = summarise([r for r in arows if era_of(r) == era], args.tol)
        out["accounts"][acct] = entry

    txt = json.dumps(out, indent=2, sort_keys=False)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
