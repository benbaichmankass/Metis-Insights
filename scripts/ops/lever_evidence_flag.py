#!/usr/bin/env python3
"""Settled-evidence flag for `config/lever_reachability.json` — is a reach-share
verdict SETTLED, or is it a point estimate over a sample too small to grade?

THE DEFECT THIS EXISTS TO STOP
------------------------------
Every reachability verdict in the registry was a point estimate with no
interval, and several were carrying words far stronger than their denominator
supports. Measured 2026-09-22, the two that matter most:

  * `gld_pullback_1d` reads `verdict: inert` / `disposition: recorded_inert` --
    "no observed entry could reach the arm" -- on **0 of 8**. The 95% Wilson
    upper bound on 0/8 is **32.4%**. That sample cannot tell a dead arm from
    one firing on one entry in three.
  * `trend_donchian_sol_4h`'s AUTHORITATIVE live basis is **0 of 16**, upper
    bound **19.4%**. Only its backtest basis (0/127, upper 2.9%) is anywhere
    near settled -- and the registry's own `basis_note` says the backtest is
    the THIRD basis and not the authoritative one.

So a strip proposed on 0/8 is a proposal to delete a lever we cannot show is
dead. That is a different act from deleting one we can, and the decision
surface has to say which it is.

⚠️ **`UNSETTLED` IS A THIRD STATE AND IT NEVER COLLAPSES.** It means *we looked
and cannot tell*, which is distinct from `not_measured` (*we could not look* --
the registry's `unmeasured` verdict) and from `settled_inert` (*we looked and it
is dead*). Collapsing `unsettled` into either is exactly the class
`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" and
`scripts/ci/check_collapsed_states.py` exist for, and it is the collapse this
whole flag is built to prevent: today an `inert` on n=8 and an `inert` on n=127
are the same word.

WHAT IS COMPUTED, AND WHAT IS DECLARED
--------------------------------------
Computed here, never hand-written: the share, the interval and the flag. Read
from the registry, never hard-coded here: `k`, `n`, and the POLICY -- interval
method, confidence level and the two cut-points -- which live in the registry's
`evidence_flag_policy` block so a reader meets them next to the numbers they
grade rather than having to open this file.

⚠️ **THE CUT-POINTS ARE PROPOSED, NOT SETTLED.** `evidence_flag_policy.status`
says so in the file. What is NOT arbitrary is the shape: n=8 and n=16 cannot
settle this question at any defensible threshold, and that conclusion is
invariant to where the cut-points land.

Usage:
    python3 scripts/ops/lever_evidence_flag.py              # report
    python3 scripts/ops/lever_evidence_flag.py --check      # stored == computed
    python3 scripts/ops/lever_evidence_flag.py --write      # recompute in place
    python3 scripts/ops/lever_evidence_flag.py --self-test

Exit 0 clean, 1 on a finding, 2 when it could not look (registry missing or
unparseable) -- three outcomes, never collapsed, per the sibling guards.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "config" / "lever_reachability.json"

# The flag vocabulary. FOUR values, and the fourth is the point.
#
# These literals are emitted below rather than only named by constant, so a
# reader of the producer -- and `collapsed-state-guard`'s producer check -- can
# see every state this can return without chasing a name.
FLAG_SETTLED_INERT = "settled_inert"          # CI upper < inert cut-point
FLAG_SETTLED_REACHABLE = "settled_reachable"  # CI lower >= reachable cut-point
FLAG_UNSETTLED = "unsettled"                  # we looked and CANNOT TELL
FLAG_NOT_MEASURED = "not_measured"            # n == 0: we could not look
FLAGS = (FLAG_SETTLED_INERT, FLAG_SETTLED_REACHABLE,
         FLAG_UNSETTLED, FLAG_NOT_MEASURED)

#: Basis kinds, most authoritative first. The registry's `basis_note` settles
#: this ordering: `order_packages/risk_per_unit` is authoritative, the backtest
#: book is "a THIRD basis", and the candle screen "is NOT a bound in either
#: direction" -- so a screen may never be a leg's primary basis.
BASIS_RANK = {
    "live_entry_conditioned": 0,
    "backtest_entry_conditioned": 1,
    "candle_screen": 2,
}


class CouldNotLook(Exception):
    """Distinct from a finding: we were unable to read, not able to grade."""


def _log_binom_pmf(i: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def _binom_tail_ge(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.exp(_log_binom_pmf(i, n, p)) for i in range(k, n + 1))


def _binom_tail_le(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    return sum(math.exp(_log_binom_pmf(i, n, p)) for i in range(0, k + 1))


def _bisect(f, lo: float, hi: float, target: float) -> float:
    """Solve f(p) == target on [lo, hi] for an INCREASING f.

    Both callers below pass an increasing function deliberately. The obvious
    alternative -- negating a decreasing one and negating the target -- type-
    checks, runs, and silently returns the wrong endpoint; it did exactly that
    on the first draft and the self-test caught it as eight simultaneous
    failures.
    """
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson(k: int, n: int, confidence: float) -> Tuple[float, float]:
    """Two-sided exact (Clopper-Pearson) interval on k/n, as PERCENTAGES.

    ⚠️ **NOT Wilson, and the choice is not cosmetic.** The dispatch proposed
    Wilson and I started there; it is the wrong instrument for THIS job at the
    sample sizes this registry actually holds. Wilson is anti-conservative at
    the extremes with tiny n, and the failure is not hypothetical here:

        1 of 1 observation, 95% Wilson  ->  lower bound 20.6%

    which clears a 10% "settled_reachable" cut-point. A flag built to stop a
    verdict overclaiming its denominator would have declared a SINGLE
    observation settled, on `trend_donchian`, whose only entry-conditioned
    basis is exactly n=1. Clopper-Pearson gives 2.5% for the same cell, i.e.
    `unsettled`, which is the truth.

    Clopper-Pearson inverts the exact binomial test, so its coverage is
    guaranteed >= the nominal level at every n rather than on average. It is
    conservative -- intervals are WIDER than Wilson's -- and for a flag whose
    entire purpose is refusing to overclaim, erring wide is erring in the
    right direction. Wald is not a candidate at all: it has zero width at
    p=0 and p=1, so it would report 0/8 and 30/30 as infinitely precise.

    MEASURED AGREEMENT, so the swap is auditable rather than asserted: over the
    nine registry cells the dispatch tabulated with Wilson, Clopper-Pearson
    assigns the SAME flag to every one. The only cell where the two disagree is
    the n=1 case above, which the dispatch's table did not include.
    """
    if n <= 0:
        raise ValueError("clopper_pearson() needs n > 0; n == 0 is not_measured")
    alpha = (1.0 - confidence) / 2.0
    # lower: P(X >= k | p) is INCREASING in p; solve it == alpha.
    lo = 0.0 if k == 0 else _bisect(
        lambda p: _binom_tail_ge(k, n, p), 0.0, 1.0, alpha)
    # upper: P(X > k | p) is INCREASING in p; solve it == 1 - alpha, which is
    # the same root as P(X <= k | p) == alpha without inverting a decreasing f.
    hi = 1.0 if k == n else _bisect(
        lambda p: 1.0 - _binom_tail_le(k, n, p), 0.0, 1.0, 1.0 - alpha)
    # FULL PRECISION, as percentages. The caller compares on these and rounds
    # only for STORAGE: letting a 1-decimal round decide a verdict would put a
    # cell at 1.9921% and one at 2.0025% on opposite sides of a cut-point for a
    # reason no reader could see in the stored number.
    return lo * 100.0, hi * 100.0


#: The interval methods this file implements. A policy naming anything else
#: RAISES rather than silently falling back -- an interval quietly computed by a
#: different method than the one printed beside it is the same defect class this
#: whole file is about.
METHODS = {"clopper_pearson": clopper_pearson}


def grade(k: Optional[int], n: Optional[int], policy: Dict[str, Any]) -> Dict[str, Any]:
    """k/n + policy -> {share_pct, ci_low_pct, ci_high_pct, flag}."""
    inert_cut = float(policy["settled_inert_ci_high_below_pct"])
    reach_cut = float(policy["settled_reachable_ci_low_at_or_above_pct"])
    if n is None or int(n) <= 0:
        return {"k": k, "n": n, "share_pct": None, "ci_low_pct": None,
                "ci_high_pct": None, "flag": "not_measured"}
    k_i, n_i = int(k), int(n)
    if k_i < 0 or k_i > n_i:
        raise ValueError(f"k={k_i} is not in [0, n={n_i}]")
    method = policy["method"]
    if method not in METHODS:
        raise ValueError(
            f"interval method {method!r} is not implemented; declare one of "
            f"{sorted(METHODS)} in evidence_flag_policy.method")
    conf = float(policy["confidence"])
    if not 0.5 < conf < 1.0:
        raise ValueError(f"confidence {conf!r} must be strictly between 0.5 and 1.0")
    lo, hi = METHODS[method](k_i, n_i, conf)
    if hi < inert_cut:
        flag = "settled_inert"
    elif lo >= reach_cut:
        flag = "settled_reachable"
    else:
        flag = "unsettled"
    return {"k": k_i, "n": n_i, "share_pct": round(100.0 * k_i / n_i, 1),
            "ci_low_pct": round(lo, 1), "ci_high_pct": round(hi, 1),
            "flag": flag}


def pick_primary(bases: List[Dict[str, Any]]) -> Optional[str]:
    """The basis a leg's entry-level flag is taken from.

    Declared rule, applied in order: a basis measured on the CURRENT geometry
    beats a stale one; then the authority ordering in BASIS_RANK; then the
    larger n. A `candle_screen` is never primary -- the registry's own
    `basis_note` records it overstating reachability 90.5% vs 33.3% on xrp,
    because entries are filter-selected.
    """
    eligible = [b for b in bases
                if b.get("kind") != "candle_screen" and (b.get("n") or 0) > 0]
    if not eligible:
        # Every entry-conditioned basis is empty (or there are none). The
        # entry flag is `not_measured`, which is the honest answer — but note
        # the n == 0 exclusion is NOT cosmetic: without it `scha_trend_long_1d`
        # took its flag from a live probe that returned ZERO packages, purely
        # because live outranks backtest, and reported `not_measured` while a
        # 65-observation basis sat beside it unread.
        return None
    eligible.sort(key=lambda b: (
        0 if b.get("geometry") == "current" else 1,
        BASIS_RANK.get(b.get("kind"), 99),
        -(b.get("n") or 0),
    ))
    return eligible[0]["basis_id"]


def recompute(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Return the registry with every `evidence` block's derived fields refreshed."""
    policy = registry.get("evidence_flag_policy")
    if not isinstance(policy, dict):
        raise CouldNotLook("registry has no `evidence_flag_policy` block")
    for entry in registry.get("levers") or []:
        ev = entry.get("evidence")
        if not isinstance(ev, dict):
            continue
        for b in ev.get("bases") or []:
            b.update(grade(b.get("k"), b.get("n"), policy))
        primary = pick_primary(ev.get("bases") or [])
        ev["primary_basis"] = primary
        if primary is None:
            ev["flag"] = "not_measured"
        else:
            pb = next(b for b in ev["bases"] if b["basis_id"] == primary)
            ev["flag"] = pb["flag"]
    return registry


def findings(registry: Dict[str, Any]) -> List[str]:
    """Stored derived fields must equal recomputed ones. Empty == pass."""
    before = json.loads(json.dumps(registry))
    after = recompute(json.loads(json.dumps(registry)))
    out: List[str] = []
    b_by = {e.get("strategy"): e for e in before.get("levers") or []}
    for entry in after.get("levers") or []:
        leg = entry.get("strategy")
        ev_a, ev_b = entry.get("evidence"), (b_by.get(leg) or {}).get("evidence")
        if ev_a is None:
            continue
        if ev_b is None:
            out.append(f"{leg}: evidence block appeared during recompute")
            continue
        if ev_a.get("flag") != ev_b.get("flag"):
            out.append(f"{leg}: stored flag {ev_b.get('flag')!r} but k/n grade to "
                       f"{ev_a.get('flag')!r} — the flag is COMPUTED, never declared")
        if ev_a.get("primary_basis") != ev_b.get("primary_basis"):
            out.append(f"{leg}: stored primary_basis {ev_b.get('primary_basis')!r} "
                       f"but the declared rule selects {ev_a.get('primary_basis')!r}")
        for ba, bb in zip(ev_a.get("bases") or [], ev_b.get("bases") or []):
            for f in ("share_pct", "ci_low_pct", "ci_high_pct", "flag"):
                if ba.get(f) != bb.get(f):
                    out.append(
                        f"{leg}/{ba.get('basis_id')}: stored {f}={bb.get(f)!r} but "
                        f"k={ba.get('k')} n={ba.get('n')} computes {ba.get(f)!r}")
    # Every entry that declares a verdict over observations must carry evidence.
    for entry in after.get("levers") or []:
        if entry.get("verdict") in ("reachable", "inert", "vol_conditional") \
                and not isinstance(entry.get("evidence"), dict):
            out.append(f"{entry.get('strategy')}: verdict "
                       f"{entry.get('verdict')!r} with NO `evidence` block — a "
                       f"reach-share verdict must carry k, n and an interval")
    return out


def load() -> Dict[str, Any]:
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except FileNotFoundError as exc:
        raise CouldNotLook(f"{REGISTRY_PATH} is missing") from exc
    except json.JSONDecodeError as exc:
        raise CouldNotLook(f"{REGISTRY_PATH} is not valid JSON: {exc}") from exc


def report(registry: Dict[str, Any]) -> None:
    policy = registry["evidence_flag_policy"]
    conf = int(round(float(policy["confidence"]) * 100))
    print(f"settled-evidence flag — {policy['method']} {conf}% interval; "
          f"settled_inert when CI high < {policy['settled_inert_ci_high_below_pct']}%, "
          f"settled_reachable when CI low >= "
          f"{policy['settled_reachable_ci_low_at_or_above_pct']}%")
    print(f"policy status: {policy['status']}\n")
    hdr = f"{'leg':24} {'basis':34} {'k/n':>8} {'share':>7} {'CI':>16}  flag"
    print(hdr)
    print("-" * len(hdr))
    for entry in registry.get("levers") or []:
        ev = entry.get("evidence")
        if not isinstance(ev, dict):
            print(f"{entry.get('strategy'):24} {'(no evidence block)':34}")
            continue
        for b in ev.get("bases") or []:
            star = "*" if b["basis_id"] == ev.get("primary_basis") else " "
            kn = "—" if b.get("n") in (None, 0) else f"{b['k']}/{b['n']}"
            sh = "—" if b.get("share_pct") is None else f"{b['share_pct']:.1f}%"
            ci = ("—" if b.get("ci_low_pct") is None
                  else f"[{b['ci_low_pct']:.1f}%, {b['ci_high_pct']:.1f}%]")
            print(f"{entry['strategy']:24} {star}{b['basis_id']:33} {kn:>8} "
                  f"{sh:>7} {ci:>16}  {b['flag']}")
        print(f"{'':24} {'=> entry flag':34} {'':8} {'':7} {'':16}  {ev['flag']}"
              f"   (verdict: {entry.get('verdict')})")
    print("\n* = primary basis (the one the entry flag is taken from)")


def _self_test() -> int:
    """Prove each boundary AND that the flag can say 'I don't know'.

    The non-vacuity control is the one that matters: a flag that can only ever
    return the two confident values is a rubber stamp, and its output would be
    indistinguishable from the point estimates it replaces.
    """
    pol = {"method": "clopper_pearson", "confidence": 0.95,
           "settled_inert_ci_high_below_pct": 2.0,
           "settled_reachable_ci_low_at_or_above_pct": 10.0}
    cases = [
        ("n=0 is not_measured, NOT settled_inert", 0, 0, "not_measured"),
        ("0/8 (the gld cell) is UNSETTLED — the non-vacuity control",
         0, 8, "unsettled"),
        ("0/16 (the sol_4h live cell) is UNSETTLED", 0, 16, "unsettled"),
        # ⚠️ The sol_4h backtest cell. The dispatch called it "anywhere near
        # settled" and it is NOT settled under the dispatch's own proposed
        # cut-point. Recorded as a case because the near-miss is the
        # interesting one.
        ("0/127 (the sol_4h backtest cell) is UNSETTLED", 0, 127, "unsettled"),
        ("0/300 (large n at zero) is settled_inert", 0, 300, "settled_inert"),
        # ⚠️ THE CASE THAT CHANGED THE METHOD. 95% Wilson puts the lower bound
        # on 1/1 at 20.6%, which clears a 10% bar — a flag built to stop
        # overclaiming would have called ONE observation settled.
        ("1/1 is UNSETTLED under Clopper-Pearson (Wilson said reachable)",
         1, 1, "unsettled"),
        ("30/30 is settled_reachable", 30, 30, "settled_reachable"),
        ("54/65 is settled_reachable", 54, 65, "settled_reachable"),
        ("2/37 straddles both cut-points -> UNSETTLED", 2, 37, "unsettled"),
    ]
    fails = 0
    for label, k, n, want in cases:
        got = grade(k, n, pol)["flag"]
        if got != want:
            print(f"  SELF-TEST FAIL [{label}]: want {want}, got {got}")
            fails += 1
        else:
            print(f"  ok  [{label}]")

    # Every flag must be REACHABLE — a vocabulary value nothing can produce is
    # already collapsed (the `provenance-consumer-guard` insight).
    produced = {grade(k, n, pol)["flag"] for _, k, n, _ in cases}
    if set(FLAGS) - produced:
        print(f"  SELF-TEST FAIL [every flag reachable]: never produced "
              f"{sorted(set(FLAGS) - produced)}")
        fails += 1
    else:
        print("  ok  [every flag in the vocabulary is reachable]")

    # An interval must WIDEN as n shrinks, or it is not measuring what we think.
    if not (grade(0, 8, pol)["ci_high_pct"] > grade(0, 127, pol)["ci_high_pct"]):
        print("  SELF-TEST FAIL [interval widens at small n]")
        fails += 1
    else:
        print("  ok  [interval widens at small n]")

    # An unimplemented interval method must RAISE, never silently fall back.
    try:
        grade(1, 10, dict(pol, method="wilson"))
    except ValueError:
        print("  ok  [an unimplemented method raises rather than defaulting]")
    else:
        print("  SELF-TEST FAIL [unimplemented method silently defaulted]")
        fails += 1

    # The stored-vs-computed check must FAIL on a planted wrong flag.
    planted = {"evidence_flag_policy": pol, "levers": [
        {"strategy": "planted", "lever": "trail_decay_arm_r", "verdict": "inert",
         "evidence": {"bases": [{"basis_id": "b", "kind": "live_entry_conditioned",
                                 "geometry": "current", "k": 0, "n": 8,
                                 "share_pct": 0.0, "ci_low_pct": 0.0,
                                 "ci_high_pct": 32.4, "flag": "settled_inert"}],
                      "primary_basis": "b", "flag": "settled_inert"}}]}
    if not any("COMPUTED, never declared" in f for f in findings(planted)):
        print("  SELF-TEST FAIL [planted wrong flag not caught]")
        fails += 1
    else:
        print("  ok  [planted wrong flag is caught]")

    # A verdict over observations with NO evidence block must be a finding.
    bare = {"evidence_flag_policy": pol, "levers": [
        {"strategy": "bare", "lever": "trail_decay_arm_r", "verdict": "inert"}]}
    if not any("NO `evidence` block" in f for f in findings(bare)):
        print("  SELF-TEST FAIL [verdict without evidence not caught]")
        fails += 1
    else:
        print("  ok  [a verdict with no evidence block is caught]")

    # A candle screen must never become the primary basis.
    if pick_primary([{"basis_id": "screen", "kind": "candle_screen", "n": 500},
                     {"basis_id": "live", "kind": "live_entry_conditioned",
                      "geometry": "stale", "n": 8}]) != "live":
        print("  SELF-TEST FAIL [candle screen chosen as primary]")
        fails += 1
    else:
        print("  ok  [a candle screen is never the primary basis]")

    # An n == 0 basis must never win primary over one that has observations,
    # however much more authoritative its KIND is.
    if pick_primary([{"basis_id": "empty_live", "kind": "live_entry_conditioned",
                      "geometry": "current", "n": 0},
                     {"basis_id": "bt", "kind": "backtest_entry_conditioned",
                      "geometry": "stale", "n": 65}]) != "bt":
        print("  SELF-TEST FAIL [an empty basis won primary over a populated one]")
        fails += 1
    else:
        print("  ok  [an n=0 basis never wins primary over a populated one]")

    total = len(cases) + 7
    print(f"self-test: {total - fails}/{total} passed")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="fail when a stored derived field disagrees with k/n")
    ap.add_argument("--write", action="store_true",
                    help="recompute the derived fields in place")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    try:
        registry = load()
        if a.write:
            REGISTRY_PATH.write_text(
                json.dumps(recompute(registry), indent=2, ensure_ascii=False) + "\n")
            print(f"rewrote {REGISTRY_PATH.relative_to(REPO)}")
            return 0
        problems = findings(registry)
    except CouldNotLook as exc:
        print(f"::error::lever-evidence-flag: COULD NOT LOOK — {exc}. This is "
              f"not a pass and not a finding.")
        return 2

    if a.check:
        if problems:
            for p in problems:
                print(f"::error::lever-evidence-flag: {p}")
            return 1
        print("OK — every stored share, interval and flag reproduces from its "
              "own k/n under the registry's declared policy.")
        return 0

    report(registry)
    if problems:
        print()
        for p in problems:
            print(f"::error::lever-evidence-flag: {p}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
