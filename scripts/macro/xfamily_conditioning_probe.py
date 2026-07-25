#!/usr/bin/env python3
"""M34 — cross-family CONDITIONING probe (the two robust equity leads, conjoined).

The free signal-research program swept every free INPUT family (COT · crypto ·
value · implied-vol · credit/rates · microstructure · calendar) and found exactly
two *robust* leads — both equity-forward, both **non-deployable standalone**:

  - ``vix_term``   = VIX3M/VIX term ratio (VXVCLS/VIXCLS) → equity fwd return
  - ``hy_oas_pct`` = trailing-1y percentile of HY OAS (BAMLH0A0HYM2, credit stress)

Every within-family construction dimension (level / D1 transform / D2 conditioning /
D3 cross-section / D4 composite) has been exhausted. The one construction the
program never ran is the **cross-FAMILY conjunction**: does gating on BOTH leads
at once clear the cost bar where neither leg does alone? Two weak-but-real,
orthogonal (vol-term-structure vs credit-stress) equity signals *could* combine
into a deployable regime gate even though each is individually flat — that is a
genuinely un-foregone question, not another family scan.

Honest funnel (no lookahead, no seasonality/regime data-snoop):
  - Non-overlapping anchors (stride = horizon) → one (term, credit, fwd-return) row.
  - Each factor's FAVORABLE direction + threshold (median) fit on the IN-SAMPLE
    60% ONLY (the half with the higher in-sample mean forward return is "favorable").
  - The 2-factor gate goes long the target's forward H-day return only when BOTH
    factors are favorable; evaluated on the untouched OOS 40%, net of round-trip cost.
  - A conjunction only counts as an edge if its OOS net mean is positive AND
    t-significant AND beats BOTH single-factor gates AND beats buy-and-hold — so a
    conjunction that merely inherits one leg's tilt is not miscredited.
  - ``--mode wf`` re-confirms per contiguous era (robust needs sign-consistency +
    modern-era significance), the same gate that separated the vix_term/seasonality
    robust-vs-front-loaded calls.

Off-VM (``ICT_OFFVM_BUILD_HOST=1`` gates the keyless FRED fetch); network is
injected via ``urlopen`` in tests. Observe-only, Tier-1 research — writes nothing,
touches no ``src``/``config``/order path. Import-pure (no network at import).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Callable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import implied_vol_probe as iv  # noqa: E402  (reuse build_feature_series/log_return)

_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# The two robust leads' FRED series.
VIX_SID = "VIXCLS"        # VIX (1m implied vol)
VIX3M_SID = "VXVCLS"      # VIX3M (3m implied vol) — numerator of the term ratio
HY_OAS_SID = "BAMLH0A0HYM2"  # HY OAS (credit stress)

# Equity targets that carry deep FRED daily history (both leads are equity-forward).
DEFAULT_TARGETS = ("SP500", "NASDAQ100")
DEFAULT_HORIZONS = (21, 42)   # monthly→bimonthly — where both leads carry information
WF_CELLS = (("NASDAQ100", 21), ("NASDAQ100", 42), ("SP500", 21))


# ---------------------------------------------------------------------------
# alignment + anchors
# ---------------------------------------------------------------------------

def _align_many(dated_by_id: dict) -> tuple:
    """Align N dated series onto their common dates. Returns (dates, {id: values})."""
    ids = list(dated_by_id.keys())
    if not ids:
        return [], {}
    common = None
    maps = {}
    for sid in ids:
        m = {d: iv._to_float(v) for d, v in dated_by_id.get(sid, [])}
        maps[sid] = m
        keys = {d for d, v in m.items() if v is not None}
        common = keys if common is None else (common & keys)
    dates = sorted(common or [])
    out = {sid: [maps[sid][d] for d in dates] for sid in ids}
    return dates, out


def _series_range(dated_list) -> dict:
    """(first, last, n) of a raw dated series (non-None values only) — the
    per-series coverage diagnostic that explains a short 4-way overlap."""
    ds = [d for d, v in (dated_list or []) if iv._to_float(v) is not None]
    if not ds:
        return {"first": None, "last": None, "n": 0}
    return {"first": min(ds), "last": max(ds), "n": len(ds)}


def build_leads(target_vals: list, vix_vals: list, vix3m_vals: list,
                hy_vals: list) -> tuple:
    """Point-in-time (term_ratio, hy_oas_pct) feature series on the aligned grid."""
    term = iv.build_feature_series("term_ratio", vix_vals, target_vals,
                                   extra_vals=vix3m_vals)
    credit = iv.build_feature_series("level_pct", hy_vals, target_vals)
    return term, credit


def gate_anchors(term_series: list, credit_series: list, target_vals: list,
                 horizon: int) -> list:
    """Non-overlapping ``(term, credit, fwd_return)`` anchors in time order."""
    out = []
    n = min(len(term_series), len(credit_series), len(target_vals))
    for i in range(0, n - horizon, max(1, horizon)):
        t, c = term_series[i], credit_series[i]
        fwd = iv.log_return(target_vals[i], target_vals[i + horizon])
        if t is not None and c is not None and fwd is not None:
            out.append((t, c, fwd))
    return out


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def _mean_t(xs: list) -> tuple:
    """(mean, t, n). t is None for n < 8 or zero variance."""
    n = len(xs)
    if n == 0:
        return None, None, 0
    mean = sum(xs) / n
    if n < 8:
        return mean, None, n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    if var <= 1e-24:                       # numerically constant → t undefined
        return mean, None, n
    t = mean / math.sqrt(var / n)
    return mean, t, n


def _favorable(anchors: list, idx: int) -> tuple:
    """Fit factor ``idx``'s (threshold, sign) on ``anchors``: sign=+1 if the
    HIGH half (value >= median) has the higher mean fwd return, else -1. The high
    side uses ``>=`` (and the low side ``<``) so a median that lands on the max —
    e.g. a discrete/binary factor — still splits into two non-trivial groups
    instead of collapsing the "above" set to empty."""
    vals = sorted(a[idx] for a in anchors)
    if not vals:
        return 0.0, 1
    mid = vals[len(vals) // 2]
    hi = [a[2] for a in anchors if a[idx] >= mid]
    lo = [a[2] for a in anchors if a[idx] < mid]
    hi_m = sum(hi) / len(hi) if hi else 0.0
    lo_m = sum(lo) / len(lo) if lo else 0.0
    return mid, (1 if hi_m >= lo_m else -1)


def _is_favorable(value: float, threshold: float, sign: int) -> bool:
    return (value >= threshold) if sign > 0 else (value < threshold)


def grade_cell(term_series: list, credit_series: list, target_vals: list,
               horizon: int, *, split_frac: float = 0.6, cost_frac: float = 0.0001,
               t_flag: float = 2.0) -> dict:
    """One (target, horizon) cell: fit both factors' favorable direction IN-SAMPLE,
    then evaluate single-factor gates + the 2-factor conjunction on the OOS half."""
    anchors = gate_anchors(term_series, credit_series, target_vals, horizon)
    row = {"horizon": horizon, "n_anchors": len(anchors), "n_is": 0, "n_oos": 0,
           "buyhold_oos": None, "term_gate": None, "credit_gate": None,
           "conj_gate": None, "verdict": "no_data"}
    n = len(anchors)
    if n < 20:
        return row
    cut = int(n * split_frac)
    is_a, oos_a = anchors[:cut], anchors[cut:]
    if len(is_a) < 10 or len(oos_a) < 10:
        return row
    row["n_is"], row["n_oos"] = len(is_a), len(oos_a)

    t_thr, t_sign = _favorable(is_a, 0)
    c_thr, c_sign = _favorable(is_a, 1)

    def _gate(pred) -> dict:
        picked = [a[2] for a in oos_a if pred(a)]
        gross, t, k = _mean_t(picked)
        net = None if gross is None else gross - 2.0 * cost_frac
        return {"n": k, "coverage": (k / len(oos_a)) if oos_a else 0.0,
                "gross_mean": gross, "net_mean": net, "t": t}

    bh_mean, bh_t, bh_n = _mean_t([a[2] for a in oos_a])
    row["buyhold_oos"] = {"n": bh_n, "mean": bh_mean, "t": bh_t}
    row["term_gate"] = _gate(lambda a: _is_favorable(a[0], t_thr, t_sign))
    row["credit_gate"] = _gate(lambda a: _is_favorable(a[1], c_thr, c_sign))
    row["conj_gate"] = _gate(
        lambda a: _is_favorable(a[0], t_thr, t_sign) and _is_favorable(a[1], c_thr, c_sign))

    conj, term_g, credit_g = row["conj_gate"], row["term_gate"], row["credit_gate"]
    beats_legs = (conj["net_mean"] is not None
                  and term_g["net_mean"] is not None and credit_g["net_mean"] is not None
                  and conj["net_mean"] > term_g["net_mean"]
                  and conj["net_mean"] > credit_g["net_mean"])
    beats_bh = conj["net_mean"] is not None and bh_mean is not None and conj["net_mean"] > bh_mean
    significant = conj["t"] is not None and conj["net_mean"] is not None and \
        conj["net_mean"] > 0 and abs(conj["t"]) >= t_flag
    row["verdict"] = "conjunction_pays" if (significant and beats_legs and beats_bh) \
        else "no_conjunction_edge"
    return row


def walkforward_conjunction(term_series: list, credit_series: list, target_vals: list,
                            horizon: int, *, k_eras: int = 5, cost_frac: float = 0.0001,
                            t_flag: float = 2.0) -> dict:
    """Split the WHOLE anchor span into k contiguous eras; orient each factor on the
    first era only (no lookahead), then measure the conjunction gate's net mean per
    era. Robust needs sign-consistency >= 0.75 AND modern-era significance."""
    anchors = gate_anchors(term_series, credit_series, target_vals, horizon)
    row = {"horizon": horizon, "n_anchors": len(anchors), "k_eras": k_eras,
           "eras": [], "sign_consistency": None, "modern_significant": None,
           "verdict": "insufficient_sample"}
    n = len(anchors)
    if n < max(40, 8 * k_eras):
        return row
    sz = n // k_eras
    eras = [anchors[i * sz:(i + 1) * sz] for i in range(k_eras)]
    eras[-1] = anchors[(k_eras - 1) * sz:]           # sweep remainder into last era
    t_thr, t_sign = _favorable(eras[0], 0)           # orient on the FIRST era only
    c_thr, c_sign = _favorable(eras[0], 1)
    per = []
    for e in eras:
        picked = [a[2] for a in e
                  if _is_favorable(a[0], t_thr, t_sign) and _is_favorable(a[1], c_thr, c_sign)]
        gross, t, k = _mean_t(picked)
        net = None if gross is None else gross - 2.0 * cost_frac
        per.append({"n_era": len(e), "n_gated": k, "net_mean": net, "t": t})
    row["eras"] = per
    measured = [p for p in per if p["net_mean"] is not None]
    if len(measured) < 2:
        return row
    pos = sum(1 for p in measured if p["net_mean"] > 0)
    row["sign_consistency"] = pos / len(measured)
    modern = per[-1]
    row["modern_significant"] = bool(
        modern["net_mean"] is not None and modern["net_mean"] > 0
        and modern["t"] is not None and abs(modern["t"]) >= t_flag)
    if row["sign_consistency"] >= 0.75 and row["modern_significant"]:
        row["verdict"] = "robust"
    elif row["sign_consistency"] >= 0.75:
        row["verdict"] = "era_front_loaded"
    else:
        row["verdict"] = "not_robust"
    return row


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------

def _fetch(targets, urlopen):
    from src.units.strategies.macro_thesis.fred_adapter import (
        fetch_fred_series_history_dated,
    )
    sids = sorted({VIX_SID, VIX3M_SID, HY_OAS_SID, *targets})
    return fetch_fred_series_history_dated(sids, urlopen=urlopen)


def run_probe(targets=DEFAULT_TARGETS, *, urlopen: Optional[Callable] = None,
              horizons=DEFAULT_HORIZONS, split_frac: float = 0.6,
              cost_bps: float = 1.0, t_flag: float = 2.0) -> dict:
    dated = _fetch(targets, urlopen)
    cost_frac = cost_bps / 10000.0
    shared = {sid: _series_range(dated.get(sid, [])) for sid in (VIX_SID, VIX3M_SID, HY_OAS_SID)}
    results = []
    for tgt in targets:
        dates, vals = _align_many({k: dated.get(k, []) for k in
                                   (VIX_SID, VIX3M_SID, HY_OAS_SID, tgt)})
        overlap = {"first": dates[0] if dates else None,
                   "last": dates[-1] if dates else None, "n": len(dates),
                   "target_range": _series_range(dated.get(tgt, []))}
        if len(dates) < 60:
            results.append({"target": tgt, "verdict": "no_data", "rows": [], "overlap": overlap})
            continue
        term, credit = build_leads(vals[tgt], vals[VIX_SID], vals[VIX3M_SID], vals[HY_OAS_SID])
        rows = [grade_cell(term, credit, vals[tgt], h, split_frac=split_frac,
                           cost_frac=cost_frac, t_flag=t_flag) for h in horizons]
        any_pay = any(r["verdict"] == "conjunction_pays" for r in rows)
        computable = any(r["verdict"] != "no_data" for r in rows)
        verdict = "conjunction_pays" if any_pay else ("no_conjunction_edge" if computable else "no_data")
        results.append({"target": tgt, "n_dates": len(dates), "verdict": verdict,
                        "rows": rows, "overlap": overlap})
    return {"cost_bps": cost_bps, "split_frac": split_frac, "t_flag": t_flag,
            "horizons": list(horizons), "series_ranges": shared, "targets": results}


def run_walkforward(cells=WF_CELLS, *, urlopen: Optional[Callable] = None,
                    k_eras: int = 5, cost_bps: float = 1.0, t_flag: float = 2.0) -> dict:
    tgts = sorted({c[0] for c in cells})
    dated = _fetch(tgts, urlopen)
    cost_frac = cost_bps / 10000.0
    out = []
    cache = {}
    for tgt, h in cells:
        if tgt not in cache:
            dates, vals = _align_many({k: dated.get(k, []) for k in
                                       (VIX_SID, VIX3M_SID, HY_OAS_SID, tgt)})
            cache[tgt] = (dates, vals)
        dates, vals = cache[tgt]
        if len(dates) < 60:
            out.append({"target": tgt, "horizon": h, "verdict": "no_data"})
            continue
        term, credit = build_leads(vals[tgt], vals[VIX_SID], vals[VIX3M_SID], vals[HY_OAS_SID])
        wf = walkforward_conjunction(term, credit, vals[tgt], h, k_eras=k_eras,
                                     cost_frac=cost_frac, t_flag=t_flag)
        wf["target"] = tgt
        out.append(wf)
    return {"cost_bps": cost_bps, "k_eras": k_eras, "t_flag": t_flag, "cells": out}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _f(v, nd=6) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def _print_scan(out) -> None:
    print("M34 — cross-family CONDITIONING probe (vix_term × hy_oas_pct → equity fwd return)")
    print("=" * 78)
    print(f"cost={out['cost_bps']}bps  split={out['split_frac']}  t_flag={out['t_flag']}  "
          f"horizons={out['horizons']}  (factor directions fit IN-SAMPLE, evaluated OOS)\n")
    print("  Conjunction = long fwd return only when BOTH leads favorable; an edge must")
    print("  beat BOTH single-factor gates AND buy-and-hold, net of cost, t-significant.\n")
    sr = out.get("series_ranges", {})
    if sr:
        print("  data coverage (per-series first→last, n) — diagnoses a short 4-way overlap:")
        for sid, rng in sr.items():
            print(f"       {sid:<14} {rng['first']}→{rng['last']}  n={rng['n']}")
        print()
    for r in out["targets"]:
        ov = r.get("overlap") or {}
        tr = ov.get("target_range") or {}
        print(f"  {r['target']}: verdict={r['verdict']}  (n_dates={r.get('n_dates', 0)})")
        if ov:
            print(f"       target {r['target']:<10} {tr.get('first')}→{tr.get('last')} n={tr.get('n', 0)}"
                  f"  | 4-way overlap {ov.get('first')}→{ov.get('last')} n={ov.get('n', 0)}")
        for row in r.get("rows", []):
            if row["verdict"] == "no_data":
                print(f"       H={row['horizon']:>3}d  no_data (n_anchors={row['n_anchors']})")
                continue
            bh = row["buyhold_oos"]
            tg, cg, jg = row["term_gate"], row["credit_gate"], row["conj_gate"]
            print(f"       H={row['horizon']:>3}d  n_oos={row['n_oos']}  "
                  f"buyhold={_f(bh['mean'])}")
            print(f"            term_gate   net={_f(tg['net_mean'])} t={_f(tg['t'],2)} cov={_f(tg['coverage'],2)}")
            print(f"            credit_gate net={_f(cg['net_mean'])} t={_f(cg['t'],2)} cov={_f(cg['coverage'],2)}")
            print(f"            CONJUNCTION net={_f(jg['net_mean'])} t={_f(jg['t'],2)} cov={_f(jg['coverage'],2)}"
                  f"  → {row['verdict']}")
        print()
    print("Scan: both factor thresholds/directions fit on the in-sample half only; the "
          "conjunction gate is evaluated on the untouched OOS half, net of cost.")


def _print_wf(out) -> None:
    print("\nM34 — per-era WALK-FORWARD confirmation of the conjunction gate")
    print("=" * 78)
    print(f"cost={out['cost_bps']}bps  k_eras={out['k_eras']}  t_flag={out['t_flag']}  "
          f"(factors oriented on era 0; robust needs sign-consistency + MODERN-era significance)\n")
    for c in out["cells"]:
        if c.get("verdict") == "no_data":
            print(f"  {c['target']} · H={c['horizon']}d: no_data\n")
            continue
        print(f"  {c['target']} · H={c['horizon']}d: verdict={c['verdict']}  "
              f"(sign_consistency={_f(c['sign_consistency'],3)}, "
              f"modern_significant={c['modern_significant']})")
        for i, e in enumerate(c.get("eras", [])):
            print(f"      era {i}: n_gated={e['n_gated']:>4}/{e['n_era']:<4}  "
                  f"net={_f(e['net_mean'])} t={_f(e['t'],2)}")
        print()
    print("Walk-forward: the conjunction gate across contiguous eras — robust only if "
          "net-positive sign-consistent AND still significant in the modern era. Observe-only.")


def main() -> int:
    ap = argparse.ArgumentParser(description="M34 cross-family conditioning probe")
    ap.add_argument("--mode", choices=("scan", "wf", "both"), default="both")
    ap.add_argument("--cost-bps", type=float, default=1.0)
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--split-frac", type=float, default=0.6)
    ap.add_argument("--k-eras", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of cards")
    args = ap.parse_args()

    blob = {}
    if args.mode in ("scan", "both"):
        scan = run_probe(split_frac=args.split_frac, cost_bps=args.cost_bps, t_flag=args.t_flag)
        blob["scan"] = scan
        if not args.json:
            _print_scan(scan)
    if args.mode in ("wf", "both"):
        wf = run_walkforward(k_eras=args.k_eras, cost_bps=args.cost_bps, t_flag=args.t_flag)
        blob["walkforward"] = wf
        if not args.json:
            _print_wf(wf)
    if args.json:
        print(json.dumps(blob, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
