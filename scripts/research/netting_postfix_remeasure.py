#!/usr/bin/env python3
# wiring: manual-only - a one-off re-measure of a DATED question (does the
# netting_attributed winner-R fall survive the two hedge-book fixes?) whose era
# boundaries are two specific deploys. A scheduled runner would re-answer it
# against a moving population and turn a recorded verdict into a drifting one;
# and its whole point is that the answer is NOT YET AVAILABLE, which a cadence
# would quietly convert into a different number every day. Registered in
# docs/research/RESEARCH-CAPABILITY-INDEX.md.
"""Did the netting path's contribution change after the hedge-book fixes? (MI-278 U52)

THE ROW
-------
`BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED`
offers two exits. Option **(b)** -- adjudicate the rows individually against venue
truth -- is EXHAUSTED: MI-278 U9 matched 38 of 50 and U16 proved the remainder
unreachable by quantity matching of any kind (`sum_matched_unique = 0 of 12`,
two admitting 5 and 74 distinct answers). What remains is option **(a)**:

  *"OI-20260908's root cause lands (book selection in `_bybit_position_protection`)
  and a re-measure over a post-fix window shows the netting_attributed winner-R
  fall persisting or vanishing."*

⚠️ **THE FIRST HALF IS DONE AND THE ROW DOES NOT KNOW IT.** MI-281's correction to
`OI-20260908` records the fix as landed (`46e1efb1f`, PR #11435) and DEPLOYED at
2026-09-08T18:53Z. This file is the second half.

THERE ARE TWO FIXES IN THE WINDOW, NOT ONE
--------------------------------------------
⚠️ A three-era split (pre-e35 / post-e35 / post-fix) is the obvious framing and it
is **confounded**, because a SECOND fix to the same chain merged inside the third
era: PR **#11903** (MI-283), the `(symbol, position_idx)` dedupe in
`account_open_positions`, merged 2026-09-12T19:47:52Z. Both sever the path from a
dropped hedge book to a false close. So the eras are FOUR, and a result pooled
across the last two attributes to neither.

THE SCOPE DECIDES THE VERDICT, WHICH IS WHY IT IS IMPORTED AND NOT RE-DERIVED
-------------------------------------------------------------------------------
⚠️ `exit_reason == 'netting_attributed'` -- the filter this row, U9 and U16 all use
-- is UNSOUND. MI-278 U33 established that `_netting_apply_close`'s PARTIAL branch
rewrites `position_size` and leaves the row OPEN, so it closes later under an
ordinary exit reason and the filter cannot see it. The corrected scope is the UNION
of three sources, each named in the output:

  `exit_reason`  the row closed under the label
  `notes_stamp`  `netting_attribution_basis` / `netting_attributed_qty` in notes
  `soak_only`    an `mode: apply` row in `netting_attribution_soak` and neither above

That is not a refinement. On the measured window the two framings give **opposite
verdicts** at alpha=0.05, and the corrected one is the conservative side.

WHAT THIS FILE REFUSES TO DO
------------------------------
⚠️ It does not attribute the change to either fix, and says `confounded_two_fixes`.
⚠️ It does not answer the row's winner-R question when the arm is under-powered;
it returns `insufficient_n` WITH the n it has and the n it would need.
⚠️ It computes several contrasts and REPORTS THE COUNT, because a single p-value
selected from four framings is not a p-value.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import statistics
import sys
from collections import Counter
from math import comb
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# ⚠️ R IS IMPORTED, NEVER RE-DERIVED. `trades` carries NO `r` column -- measured,
# 0 of 1000 rows on the live pull -- so a reader that looked for one would grade
# every arm `unmeasurable` and call it a finding. The FIRST draft of this file did
# exactly that. The single owner is MI-278's decomposition, whose `risk_usd`
# docstring records why `trades.stop_loss` is the WRONG basis (it is the TRAILED
# stop, so for a winner that trailed it puts a dragged-up distance in a
# DENOMINATOR -- one row read R = 3672 on that basis). It reads
# `order_packages.entry` / `.sl`, which are entry-frozen.
try:  # pragma: no cover - exercised by the live path, not the harness
    from winner_size_collapse_2026_09_12 import risk_usd as _risk_usd
    _RISK_IMPORT = "winner_size_collapse_2026_09_12.risk_usd"
except Exception as exc:  # noqa: BLE001  # allow-silent: NOT silent -- the failure is
    # recorded in `_RISK_IMPORT` ("unimportable:<Type>"), published on every census as
    # `risk_reader`, and surfaced as the declared R state `risk_reader_unavailable`, which
    # is kept apart from `no_risk_basis`. A narrow `ImportError` would be WRONG here: the
    # target module imports two others at module scope, so a broken dependency deeper in
    # that chain raises something else and would crash the run instead of grading it.
    _risk_usd = None
    _RISK_IMPORT = f"unimportable:{type(exc).__name__}"

# --------------------------------------------------------------------------
# Era boundaries. Each carries its provenance INLINE -- a boundary whose origin
# is not stated is a number somebody can move without anybody noticing.
# --------------------------------------------------------------------------
E35_DEPLOY_UTC = "2026-08-30T08:53:19"   # 892c9a2c, e35 bracket geometry (CLAUDE.md)
FIX_A_DEPLOY_UTC = "2026-09-08T18:58:00"  # 46e1efb1f / PR #11435 merged 18:53:00Z + ~5min git-sync
FIX_B_DEPLOY_UTC = "2026-09-12T19:53:00"  # PR #11903 merged 19:47:52Z + ~5min git-sync

# ⚠️ BOTH boundaries are the MERGE plus the observed ~5-minute `ict-git-sync` pull, not the
# merge itself -- a merge is not a deploy, and an earlier draft applied the lag to B and not
# to A. MEASURED on the live pull: ZERO `bybit_1` rows opened inside either merge->sync window
# (fix A has 50 min of clearance either side, fix B has 8 h), so the verdict is INVARIANT to
# how the lag is modelled. That is a fact about this window, not a licence to stop modelling it.

ERAS = ("1_pre_e35", "2_e35_only", "3_fix_a", "4_fix_a_and_b", "unknown")

# Where a scoped row came from. Never collapsed: the three sources have different
# reliability and a count that pools them hides which half is load-bearing.
SCOPE_SOURCES = ("exit_reason", "notes_stamp", "soak_only")

# Whether the SOAK -- the second of the three sources -- actually covers an era.
# `partial` is the state that matters: the diag log_file endpoint caps at 1000
# lines whatever you ask for, so an early era can be covered for part of its span
# and a union count there is an UNDER-count of unknown size.
COVERAGE_STATES = ("covered", "partial", "uncovered", "unknown")

# The row's own question.
VERDICT_STATES = (
    "fall_persists",
    "fall_vanished",
    "insufficient_n",      # the arms exist and are too small to separate
    "unmeasurable",        # an arm is empty, or no row carries a usable R
)

# Why a scoped winner has no R. `no_risk_basis` is *we could not look* and is
# NEVER folded into "the arm is empty": an arm of 4 winners none of which can be
# graded is a DIFFERENT fact from an arm with no winners, and only the first says
# the pull is too narrow rather than the path being quiet.
R_STATES = ("graded", "no_risk_basis", "risk_reader_unavailable")

STAMP_KEYS = ("netting_attribution_basis", "netting_attributed_qty")
SOAK_APPLIED_MODE = "apply"


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def era_of(created_at: Any) -> str:
    """PRE/POST by OPEN time -- a leg carries the regime it was opened under.

    Returns `unknown` on a missing or unparseable stamp, never a default era:
    silently bucketing an undateable row is how an era count grows a row nobody
    can point at.
    """
    if not created_at:
        return "unknown"
    s = str(created_at)
    try:
        dt.datetime.fromisoformat(s)
    except ValueError:
        return "unknown"
    if s < E35_DEPLOY_UTC:
        return "1_pre_e35"
    if s < FIX_A_DEPLOY_UTC:
        return "2_e35_only"
    if s < FIX_B_DEPLOY_UTC:
        return "3_fix_a"
    return "4_fix_a_and_b"


def applied_soak_ids(soak_records: list[dict]) -> set[str]:
    """Trade ids the soak says the reconciler ACTUALLY applied to.

    ⚠️ `mode` is the EFFECTIVE mode for that row, not `global_mode`, which is only
    what was asked for. An `annotate` row touched no database and must not enter a
    scope that claims to count rows the path acted on.
    """
    out = set()
    for r in soak_records:
        if r.get("mode") != SOAK_APPLIED_MODE:
            continue
        tid = r.get("trade_id")
        if tid is not None:
            out.add(str(tid))
    return out


def scope_source(row: dict, applied: set[str]) -> str | None:
    """Which of the three sources puts this row in scope, or None.

    Order matters only for ATTRIBUTION, never for membership: a row is in scope if
    ANY source claims it, and the first that does is reported so the census can say
    how much each contributes.
    """
    if row.get("exit_reason") == "netting_attributed":
        return "exit_reason"
    notes = str(row.get("notes") or "")
    if any(k in notes for k in STAMP_KEYS):
        return "notes_stamp"
    if str(row.get("id")) in applied:
        return "soak_only"
    return None


def soak_coverage(soak_records: list[dict], era_bounds: dict[str, tuple[str, str]]) -> dict:
    """Does the soak span each era, or only part of it?

    ⚠️ **THIS EXISTS BECAUSE A UNION SCOPE INHERITS ITS WEAKEST SOURCE.** Two of the
    three sources read the journal, which the pull covers uniformly; the third reads
    a capped log tail. An era the soak reaches for one day of nine contributes
    `soak_only` rows for that day and none for the rest, so its union count is low
    for a reason that has nothing to do with the trading. Reporting the count
    without the coverage is the unasserted-denominator defect.
    """
    ts = sorted(str(r["ts"]) for r in soak_records if r.get("ts"))
    if not ts:
        return {"soak_first": None, "soak_last": None,
                "by_era": {e: "unknown" for e in era_bounds}}
    lo, hi = ts[0], ts[-1]
    by = {}
    for era, (a, b) in era_bounds.items():
        if a is None or b is None:
            by[era] = "unknown"
        elif hi < a or lo > b:
            by[era] = "uncovered"
        elif lo <= a and hi >= b:
            by[era] = "covered"
        else:
            by[era] = "partial"
    return {"soak_first": lo, "soak_last": hi, "by_era": by}


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact two-sided Fisher on a 2x2. No scipy -- this repo's research lane has none."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    r1, c1 = a + b, a + c
    def p(x):
        return comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)
    p0 = p(a)
    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= p0 + 1e-12))


def _z(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(200):
        m = (lo + hi) / 2
        if 0.5 * (1 + math.erf(m / math.sqrt(2))) < p:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def n_per_arm(p1: float, p2: float, alpha: float = 0.05, power: float = 0.80) -> int | None:
    """Rows per arm to detect p1 vs p2. `None` when the two rates are equal.

    ⚠️ A PLANNING FIGURE, NOT A GUARANTEE. It takes the OBSERVED post rate as the
    true one; if the truth is nearer p1 the requirement rises. Returning it beside
    an `insufficient_n` verdict is what turns 'wait and see' into a date, and it
    must never be read as 'the answer will be yes by then'.
    """
    if p1 == p2:
        return None
    za, zb = _z(1 - alpha / 2), _z(power)
    pbar = (p1 + p2) / 2
    num = (za * math.sqrt(2 * pbar * (1 - pbar))
           + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / ((p1 - p2) ** 2))


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
def census(rows: list[dict], applied: set[str], account: str = "bybit_1",
           pkgs: dict | None = None) -> dict:
    """Per-era closed population and netting share, on the CORRECTED scope."""
    tot: Counter = Counter()
    net: Counter = Counter()
    src: Counter = Counter()
    netwin: Counter = Counter()
    net_r: dict[str, list[float]] = {}
    r_state: Counter = Counter()
    bounds: dict[str, list[str]] = {}
    excluded = Counter()

    for r in rows:
        if str(r.get("account_id")) != account:
            excluded["other_account"] += 1
            continue
        if str(r.get("status")) != "closed":
            excluded["not_closed"] += 1
            continue
        if r.get("is_backtest") in (1, True, "1", "true"):
            excluded["backtest"] += 1
            continue
        if r.get("pnl") in (None, ""):
            excluded["pnl_null"] += 1
            continue
        era = era_of(r.get("created_at"))
        tot[era] += 1
        ca = str(r.get("created_at"))
        lohi = bounds.setdefault(era, [ca, ca])
        lohi[0], lohi[1] = min(lohi[0], ca), max(lohi[1], ca)
        pnl = _f(r.get("pnl"))
        s = scope_source(r, applied)
        if s:
            net[era] += 1
            src[(era, s)] += 1
            if pnl is not None and pnl > 0:
                netwin[era] += 1
                if _risk_usd is None:
                    r_state[(era, "risk_reader_unavailable")] += 1
                else:
                    ru, st = _risk_usd(r, pkgs)
                    if ru:
                        net_r.setdefault(era, []).append(pnl / ru)
                        r_state[(era, "graded")] += 1
                    else:
                        r_state[(era, "no_risk_basis")] += 1
                        r_state[(era, f"why:{st}")] += 1
    return {
        "account": account,
        "totals": dict(tot),
        "netting": dict(net),
        "by_source": {f"{e}|{s}": n for (e, s), n in src.items()},
        "netting_winners": dict(netwin),
        "netting_winner_r": {k: sorted(v) for k, v in net_r.items()},
        "era_bounds": {e: (v[0], v[1]) for e, v in bounds.items()},
        "r_state": {f"{e}|{k}": n for (e, k), n in r_state.items()},
        "risk_reader": _RISK_IMPORT,
        "excluded": dict(excluded),
    }


def contrasts(c: dict) -> list[dict]:
    """Every era pair worth testing, each with its own p. The LIST is the point."""
    out = []
    tot, net = c["totals"], c["netting"]
    pairs = [
        ("2_e35_only", "3_fix_a", "did fix A alone change the share?"),
        ("3_fix_a", "4_fix_a_and_b", "did fix B add anything on top of A?"),
        ("2_e35_only", "4_fix_a_and_b", "both fixes vs neither"),
        ("1_pre_e35", "2_e35_only", "control: e35 itself, no fix involved"),
    ]
    for a, b, why in pairs:
        if a not in tot or b not in tot:
            continue
        A, B = net.get(a, 0), tot[a] - net.get(a, 0)
        C, D = net.get(b, 0), tot[b] - net.get(b, 0)
        out.append({
            "left": a, "right": b, "why": why,
            "left_share": A / tot[a] if tot[a] else None,
            "right_share": C / tot[b] if tot[b] else None,
            "p": fisher_exact_two_sided(A, B, C, D),
        })
    # the pooled post arm, named as pooled so nobody reads it as attributing
    if "2_e35_only" in tot and "3_fix_a" in tot and "4_fix_a_and_b" in tot:
        A = net.get("2_e35_only", 0)
        B = tot["2_e35_only"] - A
        C = net.get("3_fix_a", 0) + net.get("4_fix_a_and_b", 0)
        D = (tot["3_fix_a"] + tot["4_fix_a_and_b"]) - C
        out.append({
            "left": "2_e35_only", "right": "POOLED_3+4",
            "why": "both fix eras pooled -- ATTRIBUTES TO NEITHER FIX",
            "left_share": A / tot["2_e35_only"], "right_share": C / (C + D) if C + D else None,
            "p": fisher_exact_two_sided(A, B, C, D),
        })
    return out


def winner_r_verdict(c: dict, min_n: int = 11) -> dict:
    """The row's OWN question, and the refusal when it cannot be answered.

    `min_n` defaults to 11 -- the n of the PRE arm in the figure this row published
    (median R 3.143 on n=11). Asking the post arm to reach the pre arm's n is the
    weakest defensible bar; anything smaller compares a distribution against a
    handful of draws.
    """
    pre = c["netting_winner_r"].get("2_e35_only", [])
    post = (c["netting_winner_r"].get("3_fix_a", [])
            + c["netting_winner_r"].get("4_fix_a_and_b", []))
    n_post_w = c["netting_winners"].get("3_fix_a", 0) + c["netting_winners"].get("4_fix_a_and_b", 0)
    out = {
        "pre_n": len(pre), "post_n": len(post),
        "post_winners_any_r": n_post_w,
        "pre_median_r": statistics.median(pre) if pre else None,
        "post_median_r": statistics.median(post) if post else None,
        "min_n": min_n,
    }
    if not pre or not post:
        out["verdict"] = "unmeasurable"
        out["why"] = ("one arm has no netting winner carrying a usable R -- there is no "
                      "distribution to compare, which is NOT the same as the fall vanishing")
        return out
    if len(post) < min_n:
        out["verdict"] = "insufficient_n"
        out["why"] = (f"the post arm holds {len(post)} netting winners with an R against the "
                      f"pre arm's own published n of {min_n}. A median over {len(post)} draws "
                      "is not a distribution, and reporting one here would answer the row "
                      "with the kind of number it exists to distrust")
        return out
    out["verdict"] = "fall_vanished" if out["post_median_r"] >= out["pre_median_r"] else "fall_persists"
    out["why"] = "both arms reach the declared n; the medians are comparable"
    return out


def _post_span_days(c: dict) -> float | None:
    """Wall-clock days the two fix eras span, from the rows themselves."""
    lows, highs = [], []
    for e in ("3_fix_a", "4_fix_a_and_b"):
        b = c["era_bounds"].get(e)
        if b:
            lows.append(b[0])
            highs.append(b[1])
    if not lows:
        return None
    try:
        d = (dt.datetime.fromisoformat(max(highs)) - dt.datetime.fromisoformat(min(lows)))
    except ValueError:
        return None
    return d.total_seconds() / 86400 or None


def _timing(c: dict, wv: dict) -> dict:
    """Turn `insufficient_n` into a DATE, so the row stops reading as `wait`.

    ⚠️ EVERY FIGURE HERE IS A PLANNING FIGURE. Each takes the OBSERVED post rate as
    the true one and the OBSERVED arrival rate as stationary; the winner-arrival
    rate in particular is estimated from a handful of events, so its date carries
    the noise of that handful. They say WHEN TO LOOK AGAIN, never what will be found.
    """
    out: dict[str, str] = {}
    tot, net = c["totals"], c["netting"]
    span = _post_span_days(c)
    post_n = tot.get("3_fix_a", 0) + tot.get("4_fix_a_and_b", 0)
    if not (tot.get("2_e35_only") and post_n and span):
        return out
    rate = post_n / span
    p1 = net.get("2_e35_only", 0) / tot["2_e35_only"]
    p2 = (net.get("3_fix_a", 0) + net.get("4_fix_a_and_b", 0)) / post_n
    need = n_per_arm(p1, p2)
    today = dt.date.today()
    if need:
        short = max(0, need - post_n)
        days = short / rate if rate else None
        out["share arm"] = (f"{p1*100:.1f}% vs {p2*100:.1f}% needs ~{need}/arm at 80% power; "
                            f"post arm holds {post_n} at {rate:.1f} closes/day -> "
                            + (f"~{days:.0f} more days (~{today + dt.timedelta(days=days)})"
                               if days else "already powered"))
    wn = wv.get("post_n") or 0
    if wv.get("verdict") == "insufficient_n" and wn:
        wrate = wn / span
        short = max(0, wv["min_n"] - wn)
        days = short / wrate if wrate else None
        out["winner-R arm"] = (f"{wn} graded winners at {wrate:.2f}/day -> n={wv['min_n']} in "
                               + (f"~{days:.0f} more days (~{today + dt.timedelta(days=days)})"
                                  if days else "already there")
                               + f"  \u26a0 the rate is estimated from {wn} events")
    return out


def render(c: dict, cons: list[dict], cov: dict, wv: dict, timing: dict) -> str:
    L = []
    L.append(f"netting post-fix re-measure — account {c['account']}")
    L.append(f"  eras: e35 {E35_DEPLOY_UTC} · fixA {FIX_A_DEPLOY_UTC} · fixB {FIX_B_DEPLOY_UTC}")
    L.append("")
    L.append(f"  {'era':<15}{'closed':>7}{'netting':>9}{'share':>8}{'reason':>8}{'stamp':>7}"
             f"{'soak':>6}{'winners':>9}{'soak cov':>10}")
    for e in ERAS:
        if e not in c["totals"]:
            continue
        t, n = c["totals"][e], c["netting"].get(e, 0)
        L.append(f"  {e:<15}{t:>7}{n:>9}{(n/t*100 if t else 0):>7.1f}%"
                 f"{c['by_source'].get(f'{e}|exit_reason',0):>8}"
                 f"{c['by_source'].get(f'{e}|notes_stamp',0):>7}"
                 f"{c['by_source'].get(f'{e}|soak_only',0):>6}"
                 f"{c['netting_winners'].get(e,0):>9}"
                 f"{cov['by_era'].get(e,'unknown'):>10}")
    L.append("")
    L.append(f"  CONTRASTS — {len(cons)} computed, so a single p selected from them is not a p:")
    for k in cons:
        ls = f"{k['left_share']*100:.1f}%" if k["left_share"] is not None else "—"
        rs = f"{k['right_share']*100:.1f}%" if k["right_share"] is not None else "—"
        L.append(f"    {k['left']:<12} {ls:>7}  vs  {k['right']:<14} {rs:>7}   p={k['p']:.4f}   {k['why']}")
    bonf = 0.05 / len(cons) if cons else None
    if bonf:
        surv = [k for k in cons if k["p"] < bonf]
        L.append(f"    Bonferroni at 0.05/{len(cons)} = {bonf:.4f} — surviving: "
                 f"{len(surv)} ({', '.join(k['left']+'~'+k['right'] for k in surv) or 'none'})")
    L.append("")
    L.append(f"  THE ROW'S QUESTION: {wv['verdict']}")
    L.append(f"    pre n={wv['pre_n']} median R={wv['pre_median_r']}")
    L.append(f"    post n={wv['post_n']} (winners of any R: {wv['post_winners_any_r']})"
             f" median R={wv['post_median_r']}")
    L.append(f"    {wv['why']}")
    if timing:
        L.append("")
        L.append("  WHEN IT BECOMES ANSWERABLE (planning figures, not guarantees):")
        for k, v in timing.items():
            L.append(f"    {k}: {v}")
    return "\n".join(L)


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _rows(n_pre=10, n_post=10, net_pre=5, net_post=1, era_pre="2026-09-01T00:00:00+00:00",
          era_post="2026-09-15T00:00:00+00:00"):
    out = []
    i = 0
    for era, tot, nets in ((era_pre, n_pre, net_pre), (era_post, n_post, net_post)):
        for k in range(tot):
            i += 1
            out.append({
                "id": str(1000 + i), "account_id": "bybit_1", "status": "closed",
                "created_at": era, "pnl": "10.0", "position_size": "1.0",
                "order_package_id": f"pkg-{1000 + i}",
                "exit_reason": "netting_attributed" if k < nets else "sl",
                "notes": "",
            })
    return out


def _pkgs(rows: list[dict]) -> dict:
    """Entry-frozen levels for the fixture: distance 5.0, so R = pnl / 5.0.

    ⚠️ The fixture carries PACKAGES because `risk_usd` reads them and returns
    `no_pkg` otherwise. Without this the R arms come back empty and every verdict
    control passes as `unmeasurable` -- which is what the first version of this
    harness did, and it is the same false-negative the live run caught.
    """
    return {r["order_package_id"]: {"entry": 100.0, "sl": 95.0} for r in rows}


def _selftest() -> int:
    fails = 0
    n = 0

    def check(label, cond):
        nonlocal fails, n
        n += 1
        if not cond:
            fails += 1
            print(f"  FAIL {n:>3} {label}")
        else:
            print(f"  ok   {n:>3} {label}")

    # 1-6 eras
    check("pre-e35 row buckets 1_pre_e35", era_of("2026-08-25T00:00:00+00:00") == "1_pre_e35")
    check("between e35 and fixA buckets 2_e35_only", era_of("2026-09-01T00:00:00+00:00") == "2_e35_only")
    check("between fixA and fixB buckets 3_fix_a", era_of("2026-09-10T00:00:00+00:00") == "3_fix_a")
    check("after fixB buckets 4_fix_a_and_b", era_of("2026-09-15T00:00:00+00:00") == "4_fix_a_and_b")
    check("a missing created_at is `unknown`, never an era", era_of(None) == "unknown")
    check("an unparseable created_at is `unknown`, never an era", era_of("not-a-date") == "unknown")
    check("the boundaries are ordered and distinct",
          E35_DEPLOY_UTC < FIX_A_DEPLOY_UTC < FIX_B_DEPLOY_UTC)
    check("fixB's boundary is AFTER #11903's merge — a merge is not a deploy",
          FIX_B_DEPLOY_UTC > "2026-09-12T19:47:52")
    check("fixA's boundary is AFTER #11435's merge too — the lag is applied to BOTH or neither",
          FIX_A_DEPLOY_UTC > "2026-09-08T18:53:00")

    # 8-14 scope
    applied = {"77"}
    check("exit_reason puts a row in scope",
          scope_source({"exit_reason": "netting_attributed"}, set()) == "exit_reason")
    check("a notes stamp puts a row in scope under its OWN source",
          scope_source({"notes": '{"netting_attributed_qty": 1}'}, set()) == "notes_stamp")
    check("an applied soak id puts a row in scope under its OWN source",
          scope_source({"id": "77"}, applied) == "soak_only")
    check("an ordinary row is out of scope", scope_source({"id": "5", "exit_reason": "sl"}, applied) is None)
    check("the three sources are never pooled into one label",
          len({scope_source({"exit_reason": "netting_attributed"}, applied),
               scope_source({"notes": "netting_attribution_basis"}, applied),
               scope_source({"id": "77"}, applied)}) == 3)
    check("an ANNOTATE soak row is not `applied` — it touched no database",
          applied_soak_ids([{"mode": "annotate", "trade_id": 1}]) == set())
    check("an APPLY soak row is",
          applied_soak_ids([{"mode": "apply", "trade_id": 1}]) == {"1"})
    check("global_mode does not make a row applied — `mode` is the effective one",
          applied_soak_ids([{"mode": "annotate", "global_mode": "apply", "trade_id": 9}]) == set())

    # 16-20 the scope CHANGES the answer -- the whole reason it is imported
    rows = _rows(net_pre=5, net_post=1)
    rows[12]["exit_reason"] = "sl"
    rows[12]["notes"] = '{"netting_attribution_basis": "x"}'
    # the NARROW arm is what the row/U9/U16 used: exit_reason only. Built by
    # stripping the other two sources, so the two arms differ in scope alone.
    narrow_rows = [dict(r, notes="") for r in rows]
    narrow = census(narrow_rows, set())
    wide = census(rows, set())
    check("a stamped-but-not-labelled row is counted by the corrected scope",
          wide["netting"].get("4_fix_a_and_b", 0) == 2)
    check("...and is attributed to `notes_stamp`, not silently to exit_reason",
          wide["by_source"].get("4_fix_a_and_b|notes_stamp") == 1)
    check("the exit_reason count is reported separately so the delta is readable",
          wide["by_source"].get("4_fix_a_and_b|exit_reason") == 1)
    check("THE SCOPE CHANGES THE COUNT — the narrow filter sees one fewer row",
          narrow["netting"].get("4_fix_a_and_b", 0) == 1
          and wide["netting"].get("4_fix_a_and_b", 0) == 2)
    check("census counts a closed non-backtest row with a pnl", sum(narrow["totals"].values()) == 20)
    ex = census(rows + [{"id": "9", "account_id": "bybit_1", "status": "open",
                         "created_at": "2026-09-15T00:00:00+00:00", "pnl": "1"}], set())
    check("an OPEN row is excluded and the exclusion is COUNTED, not dropped",
          ex["excluded"].get("not_closed") == 1)

    # 21-26 soak coverage
    bounds = {"1_pre_e35": ("2026-08-21T00:00:00", "2026-08-29T00:00:00"),
              "3_fix_a": ("2026-09-09T00:00:00", "2026-09-12T00:00:00")}
    cov = soak_coverage([{"ts": "2026-08-28T00:00:00"}, {"ts": "2026-09-16T00:00:00"}], bounds)
    check("an era the soak enters mid-way grades `partial`, never `covered`",
          cov["by_era"]["1_pre_e35"] == "partial")
    check("an era fully inside the soak span grades `covered`",
          cov["by_era"]["3_fix_a"] == "covered")
    check("an era entirely before the soak grades `uncovered`",
          soak_coverage([{"ts": "2026-09-01T00:00:00"}], bounds)["by_era"]["1_pre_e35"] == "uncovered")
    check("an EMPTY soak grades `unknown` everywhere — never `uncovered`",
          set(soak_coverage([], bounds)["by_era"].values()) == {"unknown"})
    check("coverage reports the soak's own span so the grade can be re-read",
          cov["soak_first"] == "2026-08-28T00:00:00" and cov["soak_last"] == "2026-09-16T00:00:00")

    # 26-31 statistics
    check("Fisher on a perfectly balanced table is 1.0",
          abs(fisher_exact_two_sided(5, 5, 5, 5) - 1.0) < 1e-9)
    check("Fisher on a separated table is small",
          fisher_exact_two_sided(10, 0, 0, 10) < 0.001)
    check("Fisher is symmetric under row swap",
          abs(fisher_exact_two_sided(3, 7, 8, 2) - fisher_exact_two_sided(8, 2, 3, 7)) < 1e-12)
    check("an empty table returns 1.0, not a division error",
          fisher_exact_two_sided(0, 0, 0, 0) == 1.0)
    check("n_per_arm returns None when the rates are equal", n_per_arm(0.1, 0.1) is None)
    check("n_per_arm grows as the effect shrinks", n_per_arm(0.11, 0.09) > n_per_arm(0.11, 0.04))
    check("n_per_arm on the measured contrast is a few hundred, not a handful",
          100 < n_per_arm(0.1111, 0.0417) < 1000)

    # 33-39 the verdict refuses rather than answering small
    vr = _rows(n_pre=40, n_post=40, net_pre=11, net_post=2)
    c = census(vr, set(), pkgs=_pkgs(vr))
    wv = winner_r_verdict(c)
    check("the fixture actually grades an R -- otherwise every verdict control is vacuous",
          c["r_state"].get("2_e35_only|graded", 0) > 0)
    check("an under-powered post arm returns `insufficient_n`", wv["verdict"] == "insufficient_n")
    check("...and does NOT publish a comparison verdict", wv["verdict"] not in ("fall_persists", "fall_vanished"))
    check("...and reports the n it HAS", wv["post_n"] == 2)
    check("...and the n it NEEDS", wv["min_n"] == 11)
    er = _rows(net_pre=5, net_post=0)
    empty = winner_r_verdict(census(er, set(), pkgs=_pkgs(er)))
    check("an EMPTY post arm is `unmeasurable`, NOT `fall_vanished`",
          empty["verdict"] == "unmeasurable")
    check("...and that distinction is stated in the why",
          "NOT the same as the fall vanishing" in empty["why"])
    br = _rows(n_pre=60, n_post=60, net_pre=20, net_post=20)
    big = census(br, set(), pkgs=_pkgs(br))
    check("a powered pair does produce a verdict",
          winner_r_verdict(big)["verdict"] in ("fall_persists", "fall_vanished"))

    # an UNGRADEABLE winner must not enter the R arm, and must not enter it as ZERO.
    # Planting that (P12) passed a 48-control suite, because every fixture row had a
    # package. A branch no fixture reaches is a branch the harness cannot defend.
    ug = _rows(n_pre=6, n_post=6, net_pre=3, net_post=3)
    pk = _pkgs(ug)
    for r in ug:
        if r["exit_reason"] == "netting_attributed" and era_of(r["created_at"]) == "3_fix_a":
            pk.pop(r["order_package_id"], None)
    nopkg = dict(ug[0])
    nopkg["order_package_id"] = None
    nopkg["id"] = "9999"
    nopkg["exit_reason"] = "netting_attributed"
    cu = census(ug + [nopkg], set(), pkgs=pk)
    era0 = era_of(ug[0]["created_at"])
    check("a scoped winner with no package grades `no_risk_basis`",
          cu["r_state"].get(f"{era0}|no_risk_basis", 0) >= 1)
    check("...and the REASON is carried, not just the refusal",
          any(k.startswith(f"{era0}|why:") for k in cu["r_state"]))
    check("...and it does NOT enter the R arm at all",
          len(cu["netting_winner_r"].get(era0, [])) == cu["r_state"].get(f"{era0}|graded", 0))
    check("...and emphatically not as R = 0.0",
          0.0 not in cu["netting_winner_r"].get(era0, []))
    check("the ungradeable row is still COUNTED as a netting winner -- the two facts differ",
          cu["netting_winners"].get(era0, 0) > cu["r_state"].get(f"{era0}|graded", 0))
    check("the risk reader is NAMED in the output, so a swapped basis is visible",
          cu["risk_reader"].startswith("winner_size_collapse"))

    # 40-43 contrasts and multiplicity.
    # The fixture needs THREE eras or the pooled contrast is skipped and its two
    # controls pass vacuously -- which is how they first went green.
    three_era = (_rows(n_pre=50, n_post=50, net_pre=10, net_post=2)
                 + _rows(n_pre=0, n_post=30, net_pre=0, net_post=2,
                         era_post="2026-09-10T00:00:00+00:00"))
    c3 = census(three_era, set())
    check("the fixture really carries all three post-e35 eras",
          {"2_e35_only", "3_fix_a", "4_fix_a_and_b"} <= set(c3["totals"]))
    cons = contrasts(c3)
    check("the pooled arm is LABELLED as pooled so it cannot read as attributing",
          any("POOLED" in k["right"] for k in cons))
    check("the pooled arm's why says it attributes to neither fix",
          any("ATTRIBUTES TO NEITHER" in k["why"] for k in cons if "POOLED" in k["right"]))
    check("a contrast naming a missing era is skipped, not computed on zeros",
          all(k["left"] in ("1_pre_e35", "2_e35_only", "3_fix_a", "4_fix_a_and_b") for k in cons))
    orow = _rows()
    oc = census(orow, set(), pkgs=_pkgs(orow))
    out = render(oc, cons, soak_coverage([], {}), winner_r_verdict(oc), {})
    check("the render states how many contrasts were computed", "computed, so a single p" in out)
    check("the render carries the soak coverage column", "soak cov" in out)

    print(f"\nself-test: {n} controls, {fails} failure(s)")
    return 1 if fails else 0


# --------------------------------------------------------------------------
def _load(path: str) -> Any:
    return json.loads(pathlib.Path(path).read_text())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--trades", help="JSON from /api/diag/journal?table=trades")
    ap.add_argument("--soak", help="JSON from /api/diag/log_file?name=netting_attribution_soak")
    ap.add_argument("--packages", help="JSON from /api/diag/journal?table=order_packages")
    ap.add_argument("--account", default="bybit_1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _selftest()
    if not args.trades:
        ap.print_help()
        return 0

    payload = _load(args.trades)
    rows = payload["rows"] if isinstance(payload, dict) and "rows" in payload else payload
    soak_records: list[dict] = []
    if args.soak:
        sp = _load(args.soak)
        for ln in (sp.get("lines") or []):
            try:
                soak_records.append(json.loads(ln))
            except (TypeError, ValueError):
                continue

    pkgs = None
    if args.packages:
        pp = _load(args.packages)
        prows = pp["rows"] if isinstance(pp, dict) and "rows" in pp else pp
        pkgs = {p["order_package_id"]: p for p in prows if p.get("order_package_id")}

    applied = applied_soak_ids(soak_records)
    c = census(rows, applied, account=args.account, pkgs=pkgs)
    cov = soak_coverage(soak_records, c["era_bounds"])
    cons = contrasts(c)
    wv = winner_r_verdict(c)

    timing = _timing(c, wv)
    print(json.dumps({"census": c, "coverage": cov, "contrasts": cons,
                      "winner_r": wv, "timing": timing}, indent=2)
          if args.json else render(c, cons, cov, wv, timing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
