#!/usr/bin/env python3
"""Anchored k-fold OOS validation of the ict_scalp_5m Phase-0 gate proposals.

Validates, on the regime-stamped --emit-trades JSONL of a full harness walk
(Run B, live-exit-faithful), the M8 walk-forward discipline for:

  1. min_confidence — per fold: select the net-total-R-optimal threshold on
     the anchored TRAIN prefix (grid, min 20 train trades above floor),
     evaluate that selected threshold OOS on the TEST window.
  2. Fixed rules (no fitting, stability check per test window):
       - calm-only under the 5m frozen edges (the attribution label)
       - calm-only under the 15m frozen edges (offline proxy for the ML
         15m vol verdict the live 2-D gate actually fires on)
       - OFF-cells rule: drop chop/volatile + trending/volatile (5m label)
       - OFF-cells + conf>=0.7 combined
  3. The unfiltered baseline per fold, for lift comparison.

Net R charges --fee-bps-roundtrip (default 7.5) against each trade's own
risk geometry: fee_r = bps/1e4 * entry / risk.

Post-hoc filtering of a single walk — ignores cooldown re-entries a true
per-fold re-walk would free up (the harness's own documented conservative
approximation, adequate for threshold/gate selection).

Usage:
  python scripts/research/ict_scalp_phase0/kfold_oos.py \
      --emit runB_v.jsonl --data btc_5m.csv \
      --volspec-15m spec15.json --folds 4 --out result.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.regime.vol_detector import vol_regime_from_spec  # noqa: E402

CONF_GRID = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85]
MIN_TRAIN_TRADES = 20


def net_stats(trades):
    if not trades:
        return {"n": 0, "tot_r_net": None, "exp_r_net": None, "win_rate": None, "max_dd_net": None}
    net = [t["net_fee_r"] for t in trades]
    cum = peak = mdd = 0.0
    for x in net:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "n": len(net),
        "tot_r_net": round(sum(net), 2),
        "exp_r_net": round(statistics.mean(net), 4),
        "win_rate": round(sum(1 for x in net if x > 0) / len(net), 3),
        "max_dd_net": round(mdd, 2),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--volspec-15m", required=True)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--fee-bps-roundtrip", type=float, default=7.5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv[1:])

    fee = args.fee_bps_roundtrip / 10000.0
    rows = [json.loads(line) for line in Path(args.emit).read_text().splitlines() if line.strip()]

    # 15m-label stamp: resample the 5m closes to 15m, classify each trade's
    # entry bar against the frozen 15m edges (offline proxy for the ML 15m
    # vol verdict the live gate uses for BTC).
    spec15 = json.loads(Path(args.volspec_15m).read_text())
    df = pd.read_csv(args.data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    c15 = (df.set_index("timestamp")["close"].astype(float)
             .resample("15min").last().dropna())
    c15_times = c15.index
    c15_vals = c15.tolist()

    trades = []
    for r in rows:
        m = r.get("meta") or {}
        t = pd.to_datetime(r["entry_time"], utc=True)
        # closes up to the last CLOSED 15m bar at-or-before the entry bar
        i = c15_times.searchsorted(t, side="right")
        window = c15_vals[max(0, i - 200): i]
        vol15, _ = vol_regime_from_spec(spec15, window)
        trades.append({
            "t": t,
            "conf": float(r.get("confidence") or 0.0),
            "trend": m.get("regime") or "unknown",
            "vol5": m.get("vol_regime") or "unknown",
            "vol15": vol15,
            "net_fee_r": float(r["net_r"]) - fee * float(r["entry"]) / float(r["risk"]),
        })
    trades.sort(key=lambda x: x["t"])

    t0, t1 = trades[0]["t"], trades[-1]["t"]
    span = (t1 - t0) / (args.folds + 1)  # fold 0 span is the minimum train warmup

    def off_cells_rule(t):
        return not ((t["trend"] == "chop" and t["vol5"] == "volatile")
                    or (t["trend"] == "trending" and t["vol5"] == "volatile"))

    rules = {
        "baseline": lambda t: True,
        "calm_only_5m": lambda t: t["vol5"] == "calm",
        "calm_only_15m": lambda t: t["vol15"] == "calm",
        "off_cells_5m": off_cells_rule,
        "off_cells_5m_and_conf070": lambda t: off_cells_rule(t) and t["conf"] >= 0.7,
        "conf070_fixed": lambda t: t["conf"] >= 0.7,
    }

    folds = []
    for k in range(1, args.folds + 1):
        lo = t0 + span * k
        hi = t0 + span * (k + 1) if k < args.folds else t1 + pd.Timedelta(seconds=1)
        train = [t for t in trades if t["t"] < lo]
        test = [t for t in trades if lo <= t["t"] < hi]
        # fitted min_confidence: net-total-R-optimal on train (with a floor
        # of MIN_TRAIN_TRADES surviving train trades so a starved threshold
        # can't win on noise)
        best_thr, best_tot = None, None
        for thr in CONF_GRID:
            sub = [t for t in train if t["conf"] >= thr]
            if len(sub) < MIN_TRAIN_TRADES:
                continue
            tot = sum(t["net_fee_r"] for t in sub)
            if best_tot is None or tot > best_tot:
                best_thr, best_tot = thr, tot
        fold = {
            "fold": k,
            "test_start": str(lo), "test_end": str(hi),
            "n_train": len(train), "n_test": len(test),
            "selected_min_confidence": best_thr,
            "fitted_conf_oos": net_stats([t for t in test if best_thr is not None
                                          and t["conf"] >= best_thr]),
        }
        for name, rule in rules.items():
            fold[name] = net_stats([t for t in test if rule(t)])
        folds.append(fold)

    # aggregate OOS per rule
    agg = {}
    for name in list(rules) + ["fitted_conf_oos"]:
        oos = []
        for f in folds:
            s = f[name]
            if s["n"]:
                oos.append(s)
        tot = round(sum(s["tot_r_net"] for s in oos), 2) if oos else None
        n = sum(s["n"] for s in oos)
        agg[name] = {
            "folds_positive": sum(1 for s in oos if s["tot_r_net"] > 0),
            "folds_with_trades": len(oos),
            "n": n,
            "tot_r_net": tot,
            "exp_r_net": round(tot / n, 4) if n else None,
        }

    result = {
        "emit": args.emit, "fee_bps_roundtrip": args.fee_bps_roundtrip,
        "folds": folds, "aggregate_oos": agg,
        "note": "post-hoc filter on one walk (ignores cooldown re-entries); "
                "vol15 is the frozen-15m-edge proxy for the live ML vol verdict",
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, default=str))
    print(f"{'rule':>26} {'folds+':>7} {'n_oos':>6} {'totRnet':>8} {'expRnet':>8}")
    for name, s in agg.items():
        print(f"{name:>26} {s['folds_positive']}/{s['folds_with_trades']:<5} "
              f"{s['n']:>6} {s['tot_r_net']!s:>8} {s['exp_r_net']!s:>8}")
    for f in folds:
        print(f"fold {f['fold']} test {f['test_start'][:10]}..{f['test_end'][:10]} "
              f"sel_conf={f['selected_min_confidence']} "
              f"base={f['baseline']['tot_r_net']} fitted={f['fitted_conf_oos']['tot_r_net']} "
              f"calm5={f['calm_only_5m']['tot_r_net']} calm15={f['calm_only_15m']['tot_r_net']} "
              f"offcells={f['off_cells_5m']['tot_r_net']} off+conf={f['off_cells_5m_and_conf070']['tot_r_net']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
