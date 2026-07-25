#!/usr/bin/env python3
"""M32 — credit / rates risk-premium sleeve (free FRED; the untested macro inputs).

**Why this exists (honest correction).** The M28 program declared "free macro
exhausted" after testing exactly THREE inputs — COT spec-positioning, crypto
funding, and the value sleeve (ERP / real-yield / GSR). But the free keyless FRED
library carries several *other* documented cross-asset risk-premium predictors the
program never ran: **credit spreads** (HY / IG OAS — credit famously leads equity),
the **yield-curve slope** (10Y-2Y / 10Y-3M — the canonical recession/risk lead), and
a **financial-conditions index** (Chicago Fed NFCI). "Free macro exhausted" was an
overclaim; this sleeve tests the inputs that were skipped, through the SAME honest
funnel Track A used (non-overlapping IC → OOS/cost → multi-fold walk-forward).

**Features (per credit/rates series → SP500 forward return):**
  - ``level_pct`` — trailing 1y percentile of the spread level (stress percentile;
    high = fear priced into credit → contrarian bounce hypothesis for equity).
  - ``mom``       — N-day CHANGE of the series (widening/steepening momentum).
  - ``level``     — the raw value (for the curve slope, where the level IS the
    signal: negative 10Y-2Y = inversion).

The grading is the drift-neutral **non-overlapping** IC + S3 + walk-forward machinery
from ``implied_vol_probe`` — imported, not re-implemented, so the honesty guarantees
(N≈n/H, OOS orientation-fit, cost-aware conviction, expanding-fold robustness) are
identical across the whole program. This module only adds the credit/rates FEATURE
builder + probe set; the verdict logic is shared.

Off-VM-guarded via ``fred_adapter`` (keyless fredgraph) + injectable ``urlopen`` for
tests. Import-pure: no order path, no DB write, no ``src.*`` beyond the FRED adapter.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.units.strategies.macro_thesis.fred_adapter import (  # noqa: E402
    fetch_fred_series_history_dated,
)
# Reuse the exact graders + helpers so the honesty guarantees are shared program-wide.
from implied_vol_probe import (  # noqa: E402
    _fmt,
    align_dated,
    pct_rank_last,
    scan_probe,
    scan_s3,
    scan_walkforward,
)

# name,             series_sid,       target_sid, feature
DEFAULT_PROBES = (
    ("hy_oas_pct",   "BAMLH0A0HYM2",   "SP500",    "level_pct"),   # HY OAS stress percentile
    ("hy_oas_mom",   "BAMLH0A0HYM2",   "SP500",    "mom"),         # HY OAS widening momentum
    ("ig_oas_pct",   "BAMLC0A0CM",     "SP500",    "level_pct"),   # IG OAS stress percentile
    ("curve_10y2y",  "T10Y2Y",         "SP500",    "level"),       # 2s10s slope (raw; <0 = inverted)
    ("curve_10y3m",  "T10Y3M",         "SP500",    "level"),       # 3m10y slope (raw)
    ("nfci",         "NFCI",           "SP500",    "level"),       # Chicago Fed financial conditions
)

DEFAULT_HORIZONS = (10, 21, 42, 63)   # trading days — credit/rates lead is monthly→quarterly
PCT_WINDOW = 252                      # 1y trailing percentile window
MOM_WINDOW = 21                       # ~1-month change for the momentum feature


def build_credit_feature(feature: str, vals: list, *,
                         pct_window: int = PCT_WINDOW, mom_window: int = MOM_WINDOW) -> list:
    """Point-in-time feature at each index (``None`` until warm). No lookahead."""
    n = len(vals)
    out: list = [None] * n
    if feature == "level_pct":
        for i in range(n):
            lo = max(0, i - pct_window + 1)
            out[i] = pct_rank_last(vals[lo:i + 1])
    elif feature == "mom":
        for i in range(mom_window, n):
            a, b = vals[i - mom_window], vals[i]
            if a is not None and b is not None:
                out[i] = b - a
    elif feature == "level":
        out = list(vals)
    else:
        raise ValueError(f"unknown feature {feature!r}")
    return out


def _grade_one(name, sid, tgt_sid, feature, dated, horizons, *, mode, t_flag,
               fee_frac, split_frac, k_folds):
    ser_d, tgt_d = dated.get(sid, []), dated.get(tgt_sid, [])
    if not ser_d or not tgt_d:
        return {"name": name, "feature": feature, "verdict": "no_data",
                "reason": f"empty series (feature={len(ser_d)}, target={len(tgt_d)})"}
    dates, ser_vals, tgt_vals = align_dated(ser_d, tgt_d)
    feat = build_credit_feature(feature, ser_vals)
    row = {"name": name, "feature": feature, "series_sid": sid,
           "target_sid": tgt_sid, "n_aligned": len(dates)}
    if mode == "s2":
        row.update(scan_probe(feat, tgt_vals, horizons, t_flag=t_flag))
    elif mode == "s3":
        s3 = scan_s3(feat, tgt_vals, horizons, split_frac=split_frac, fee_frac=fee_frac)
        row.update({"verdict": s3["verdict"], "rows": s3["rows"]})
    else:  # wf
        wf = scan_walkforward(feat, tgt_vals, horizons, k_folds=k_folds)
        row.update({"verdict": wf["verdict"], "best_horizon": wf["best_horizon"], "rows": wf["rows"]})
    return row


def run_probes(probes=DEFAULT_PROBES, horizons=DEFAULT_HORIZONS, *, urlopen=None,
               mode: str = "s2", t_flag: float = 2.0, fee_frac: float = 0.001,
               split_frac: float = 0.6, k_folds: int = 4) -> dict:
    """Fetch each probe's FRED series + SP500 and grade it in one of s2/s3/wf modes."""
    sids = set()
    for _, sid, tgt, _ in probes:
        sids.update([sid, tgt])
    dated = fetch_fred_series_history_dated(sorted(sids), urlopen=urlopen)
    results = [_grade_one(n, sid, tgt, feat, dated, horizons, mode=mode, t_flag=t_flag,
                          fee_frac=fee_frac, split_frac=split_frac, k_folds=k_folds)
               for n, sid, tgt, feat in probes]
    return {"mode": mode, "horizons": list(horizons), "probes": results}


def _print(out) -> None:
    labels = {"s2": "HONEST non-overlapping directional IC (S2)",
              "s3": "cost-aware OOS conviction test (S3)",
              "wf": "multi-fold walk-forward robustness (S4-prep)"}
    print(f"M32 credit/rates sleeve — {labels[out['mode']]}")
    print("=" * 74)
    print(f"horizons={out['horizons']} trading-days")
    for r in out["probes"]:
        print(f"\n  {r['name']}  ({r.get('series_sid','?')}→{r.get('target_sid','?')}, "
              f"{r['feature']}): verdict={r['verdict']}")
        if r.get("reason"):
            print(f"      {r['reason']}")
        for row in r.get("rows", []):
            h = row["horizon"]
            if out["mode"] == "s2":
                print(f"      H={h:>3}d  n={_fmt(row['n_nonoverlap'])}  "
                      f"ic={_fmt(row['ic'])} (t={_fmt(row['ic_t'])})")
            elif out["mode"] == "s3":
                print(f"      H={h:>3}d  is_ic={_fmt(row['is_ic'])} oos_ic={_fmt(row['oos_ic'])} "
                      f"(t={_fmt(row['oos_ic_t'])})  net={_fmt(row['net_spread'])}  "
                      f"pays_oos={row['pays_oos']}")
            else:
                print(f"      H={h:>3}d  sign_consistency={_fmt(row['sign_consistency'])}  "
                      f"pooled_oos_ic={_fmt(row['pooled_oos_ic'])} "
                      f"(t={_fmt(row['pooled_oos_ic_t'])})  → {row['verdict']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="M32 — credit/rates risk-premium IC probe (free FRED)")
    ap.add_argument("--mode", choices=["s2", "s3", "wf", "all"], default="all")
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    modes = ["s2", "s3", "wf"] if args.mode == "all" else [args.mode]
    bundle = {m: run_probes(horizons=horizons, mode=m, t_flag=args.t_flag) for m in modes}
    if args.json:
        print(json.dumps(bundle, indent=2))
        return 0
    for m in modes:
        _print(bundle[m])
        print()
    print("ic = drift-neutral fwd-return Spearman IC on NON-OVERLAPPING anchors (honest t, N≈n/H).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
