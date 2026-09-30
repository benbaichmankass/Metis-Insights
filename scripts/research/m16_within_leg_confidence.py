#!/usr/bin/env python3
# wiring: research/queue/RQ-20260930-601.yaml (run.command) via research-script-run.yml
"""M16 re-scope, Stage 0 — does `confidence` rank trades WITHIN ONE LEG?

WHY THIS EXISTS
---------------
M18 measured `confidence` at OOS AUC 0.522 (pre-registered gate 0.55, n_eval=1,236) as a
CROSS-candidate win predictor. That cannot be the question for M16 any more. Each
strategy builds `confidence` from its own geometry (breakout depth, pierce depth,
pullback quality, ...), so its scale is not comparable across strategies, and a pooled
AUC would sit near 0.5 even if the score ranked trades well inside every leg. This
script asks the question M18 did not: **inside one leg, do the trades that enter with
relatively higher confidence earn more R?** (docs/research/m16-rescope-2026-09-30.md)

POPULATION
----------
Every leg whose summary `comms/strategy_evidence/<leg>.json` reads
`coverage_state == "measured"` and whose `source_run` ledger exists. The ledger is the
ONE canonical per-trade file for that leg (never a scan of `runs/`, which holds
superseded and variant runs). Per-trade `net_r` is the FULL cost stack (fee + venue
slippage + funding); `confidence` is the value the strategy emitted at entry.
Legs with fewer than MIN_LEG_TRADES usable trades are excluded and counted.

STATISTIC (registered before the run — see the unit's decision_rule)
--------------------------------------------------------------------
  pct_i  = past-only percentile of trade i's confidence among the EARLIER trades of
           the same leg (mid-rank for ties), defined only once MIN_PAST earlier
           trades exist. It reads confidences only, never an outcome, so it cannot
           leak; "past-only" is there so the tilt below is deployable.
  rho    = pooled Spearman(pct, net_r), both centred within (leg, entry-month) strata.
           Centring removes the main false-positive route: a month in which every
           leg's confidence and R both happen to be high.
  p      = one-sided permutation p: net_r permuted WITHIN the same strata,
           PERM_N shuffles, seed PERM_SEED.
  lofo   = rho recomputed with each strategy family removed in turn.
  delta  = sum((0.5 + pct_i) - 1) * net_r_i over eval trades: the net R a budget-matched
           tilt (size x(0.5 + pct)) gains or loses against flat sizing, on this ledger.
  informational, NOT gating: bottom-decile vs rest, per-family rho, per-leg rho.

RULE (mirrors the unit; change one and the other must change in the same PR)
  n_eval < N_FLOOR                                 -> indeterminate (UNDERPOWERED)
  no usable leg / no ledger                        -> not_applicable
  else PASS iff rho >= RHO_BAR and p <= P_BAR and min(lofo) >= LOFO_BAR and delta > 0
  else FAIL

Tier-1 research tooling: reads committed files, writes `<out>/verdict.json` only.
Does not touch any config, sizing or risk code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = REPO / "comms" / "strategy_evidence"

MIN_LEG_TRADES = 40   # a leg needs this many usable trades to enter at all
MIN_PAST = 20         # earlier trades needed before a trade gets a percentile
N_FLOOR = 1500        # pooled eval trades below this -> UNDERPOWERED
RHO_BAR = 0.08
P_BAR = 0.05
LOFO_BAR = 0.04
PERM_N = 10000
PERM_SEED = 20260930
RULE_ID = "RULE-RQ0930-601-M16-WITHIN-LEG-CONFIDENCE"


def family_of(leg: str) -> str:
    if leg.startswith("ict_scalp"):
        return "ict_scalp"
    if leg.startswith("trend_donchian"):
        return "trend_donchian"
    if "pullback" in leg:
        return "pullback"
    return "other"


def load_legs(evidence: Path) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """leg -> usable trades (entry-ordered) plus a census of what was excluded."""
    legs: Dict[str, List[dict]] = {}
    census = {"summaries": 0, "not_measured": 0, "no_ledger": 0, "unreadable": 0,
              "rows_dropped_non_numeric": 0}
    for summ in sorted(evidence.glob("*.json")):
        census["summaries"] += 1
        try:
            d = json.loads(summ.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            census["unreadable"] += 1
            continue
        if d.get("coverage_state") != "measured":
            census["not_measured"] += 1
            continue
        src = d.get("source_run")
        ledger = None
        if src:
            for cand in (REPO / src, evidence / src, evidence.parent.parent / src):
                if cand.exists():
                    ledger = cand
                    break
        if ledger is None:
            census["no_ledger"] += 1
            continue
        rows: List[dict] = []
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                census["unreadable"] += 1
                continue
            c, n = r.get("confidence"), r.get("net_r")
            if (isinstance(c, (int, float)) and isinstance(n, (int, float))
                    and r.get("entry_time")):
                rows.append({"c": float(c), "r": float(n), "t": str(r["entry_time"])})
            else:
                census["rows_dropped_non_numeric"] += 1
        rows.sort(key=lambda x: x["t"])
        legs[summ.stem] = rows
    return legs, census


def past_percentile(conf: np.ndarray) -> np.ndarray:
    """Mid-rank percentile of each element among the strictly earlier ones; NaN < MIN_PAST."""
    out = np.full(len(conf), np.nan)
    for i in range(MIN_PAST, len(conf)):
        past = conf[:i]
        out[i] = ((past < conf[i]).sum() + 0.5 * (past == conf[i]).sum()) / i
    return out


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a))
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _centre(v: np.ndarray, strata: np.ndarray) -> np.ndarray:
    out = v.copy()
    for s in np.unique(strata):
        m = strata == s
        out[m] = v[m] - v[m].mean()
    return out


def _corr(xc: np.ndarray, yc: np.ndarray) -> float:
    den = float(np.sqrt((xc * xc).sum() * (yc * yc).sum()))
    return float((xc * yc).sum() / den) if den > 0 else 0.0


def stratified_rho(pct: np.ndarray, r: np.ndarray, strata: np.ndarray) -> float:
    return _corr(_centre(_rank(pct), strata), _centre(_rank(r), strata))


def permutation_p(pct, r, strata, observed, n, seed) -> float:
    """One-sided P(rho_null >= observed); r permuted within strata. Data pre-sorted by stratum."""
    rng = np.random.default_rng(seed)
    xc = _centre(_rank(pct), strata)
    ry = _rank(r)
    # block means of the rank vector are permutation-invariant, so centre once per draw
    # cheaply: permute ranks within blocks, then centre with the same block means.
    _, inv = np.unique(strata, return_inverse=True)
    counts = np.bincount(inv)
    block_mean = (np.bincount(inv, weights=ry) / counts)[inv]
    sx2 = float((xc * xc).sum())
    hits = 0
    for _ in range(n):
        order = np.lexsort((rng.random(len(ry)), inv))
        yc = ry[order] - block_mean
        den = np.sqrt(sx2 * float((yc * yc).sum()))
        if den > 0 and float((xc * yc).sum() / den) >= observed:
            hits += 1
    return (hits + 1) / (n + 1)


def grade(legs: Dict[str, List[dict]], census: Dict[str, Any]) -> Dict[str, Any]:
    kept = {k: v for k, v in legs.items() if len(v) >= MIN_LEG_TRADES}
    base = {"rule_id": RULE_ID, "census": census,
            "legs_excluded_thin": sorted(k for k, v in legs.items() if len(v) < MIN_LEG_TRADES)}
    if not kept:
        return dict(base, verdict="not_applicable", read_state="no_data", n=0,
                    population="no measured leg with a ledger and >= "
                               f"{MIN_LEG_TRADES} usable trades")
    leg_l, fam_l, pct_l, r_l, strat_l = [], [], [], [], []
    for leg in sorted(kept):
        rows = kept[leg]
        pct = past_percentile(np.array([x["c"] for x in rows]))
        for x, p in zip(rows, pct):
            if np.isnan(p):
                continue
            leg_l.append(leg)
            fam_l.append(family_of(leg))
            pct_l.append(p)
            r_l.append(x["r"])
            strat_l.append(f"{leg}|{x['t'][:7]}")
    n = len(pct_l)
    pop = (f"{len(kept)} measured legs with >= {MIN_LEG_TRADES} usable trades "
           f"({len(legs) - len(kept)} thinner legs excluded); {n} trades after the "
           f"{MIN_PAST}-trade per-leg warm-up; full-cost net_r from each leg's committed "
           f"source_run ledger")
    if n == 0:
        return dict(base, verdict="not_applicable", read_state="no_data", n=0, population=pop)
    pct, r = np.array(pct_l), np.array(r_l)
    fam, leg_a = np.array(fam_l), np.array(leg_l)
    # sort by stratum so permutation blocks are contiguous
    strata = np.array(strat_l)
    o = np.argsort(strata, kind="mergesort")
    pct, r, fam, leg_a, strata = pct[o], r[o], fam[o], leg_a[o], strata[o]

    rho = stratified_rho(pct, r, strata)
    p = permutation_p(pct, r, strata, rho, PERM_N, PERM_SEED)
    lofo = {}
    for f in sorted(set(fam)):
        m = fam != f
        lofo[f] = stratified_rho(pct[m], r[m], strata[m]) if m.sum() > 2 else None
    per_family = {}
    for f in sorted(set(fam)):
        m = fam == f
        per_family[f] = {"n": int(m.sum()), "rho": stratified_rho(pct[m], r[m], strata[m])}
    per_leg = {}
    for lg in sorted(set(leg_a)):
        m = leg_a == lg
        per_leg[lg] = {"n": int(m.sum()), "rho": stratified_rho(pct[m], r[m], strata[m])}
    w = 0.5 + pct
    delta = float(((w - 1.0) * r).sum())
    low = pct <= 0.10
    info = {
        "mean_tilt_weight": float(w.mean()),
        "delta_net_r_total": delta, "delta_net_r_per_trade": delta / n,
        "bottom_decile_n": int(low.sum()),
        "bottom_decile_mean_net_r": float(r[low].mean()) if low.any() else None,
        "rest_mean_net_r": float(r[~low].mean()) if (~low).any() else None,
        "per_family": per_family, "per_leg": per_leg,
    }
    lofo_vals = [v for v in lofo.values() if v is not None]
    conds = {
        "A_n_eval>=N_FLOOR": n >= N_FLOOR,
        "B_rho>=RHO_BAR": rho >= RHO_BAR,
        "C_p<=P_BAR": p <= P_BAR,
        "D_min_lofo>=LOFO_BAR": bool(lofo_vals) and min(lofo_vals) >= LOFO_BAR,
        "E_delta>0": delta > 0,
    }
    if n < N_FLOOR:
        verdict = "indeterminate"
    elif all(conds.values()):
        verdict = "pass"
    else:
        verdict = "fail"
    return dict(base, verdict=verdict, read_state="measured", n=n, population=pop,
                measurement={"rho": rho, "perm_p_one_sided": p, "lofo": lofo,
                             "conditions": conds, "bars": {
                                 "N_FLOOR": N_FLOOR, "RHO_BAR": RHO_BAR, "P_BAR": P_BAR,
                                 "LOFO_BAR": LOFO_BAR, "PERM_N": PERM_N}},
                informational=info,
                note=("UNDERPOWERED: n_eval below N_FLOOR; rho is reported, not graded."
                      if n < N_FLOOR else
                      "Stage-0 mechanism test on committed full-cost ledgers. A PASS "
                      "authorises a budget-matched path A/B, never a config change."))


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    legs, census = load_legs(Path(a.evidence_dir))
    v = grade(legs, census)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdict.json").write_text(json.dumps(v, indent=1, default=float) + "\n")
    print(json.dumps({k: v[k] for k in ("verdict", "read_state", "n", "population")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
