#!/usr/bin/env python3
"""M15 WS-B — anchored k-fold walk-forward report over an --emit-trades log.

Buckets the per-trade rows of a FIXED-PARAM harness run into the M8-style
anchored k-fold OOS windows (scripts/ml/strategy_tune_sweep.py::KFold: the
first ``train_frac`` of [wf_start, wf_end] is the burn-in/train span, the
remainder splits into ``folds`` equal OOS segments) and evaluates the
promotion gate:

    PASS  =  net R > 0 in EVERY OOS fold at the base fee
             AND total OOS net R > 0 at 2x the base fee (fee headroom).

Because the strategy params are fixed (no per-fold fitting), running the
harness once over the full series and bucketing trades by entry_time is
equivalent to per-fold reruns — and avoids per-fold indicator warm-up
artifacts at the boundaries.

Two emit-row modes:
  --mode ict   rows carry {entry, sl, gross_r}: the exact per-trade fee in R
               is (bps/1e4) * entry / |entry - sl| (the ict_scalp harness has
               no fee model — BL-20260610-M15-1), so both fee levels come
               from one emit file.
  --mode net   rows carry net_r already net of the run's fee; pass the 2x-fee
               rerun via --emit-2x (e.g. backtest_fvg_range at 2.0 and 4.0).

Usage:
    python3 scripts/ops/m15_ws_b_fold_report.py --mode ict \
        --emit results/m15_ws_b/ict_scalp_SPY_5m_full_trades.jsonl \
        --fee-bps 2.0 --wf-start 2019-01-01 --wf-end 2026-06-11 \
        --folds 5 --train-frac 0.4 --json results/m15_ws_b/fold_ict_SPY.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone


def _parse_ts(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.fromisoformat(s + "T00:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fold_windows(wf_start: str, wf_end: str, folds: int, train_frac: float):
    """Mirror strategy_tune_sweep.fold_windows for the KFold case."""
    start, end = _parse_ts(wf_start), _parse_ts(wf_end)
    total = (end - start).days
    train_days = total * train_frac
    seg = (total - train_days) / folds
    out = []
    for k in range(folds):
        oos_s = start + timedelta(days=round(train_days + seg * k))
        oos_e = start + timedelta(days=round(train_days + seg * (k + 1)))
        out.append((oos_s, oos_e))
    return out


def load_rows(path: str):
    rows = []
    for line in open(path):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def ict_net(row: dict, fee_bps: float):
    entry, sl = row.get("entry"), row.get("sl")
    if entry is None or sl is None or not float(entry) or entry == sl:
        return None
    fee_r = (fee_bps / 1e4) * float(entry) / abs(float(entry) - float(sl))
    return float(row.get("gross_r", 0.0)) - fee_r


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["ict", "net"], required=True)
    p.add_argument("--emit", required=True)
    p.add_argument("--emit-2x", default=None,
                   help="2x-fee rerun emit (required for --mode net)")
    p.add_argument("--fee-bps", type=float, default=2.0)
    p.add_argument("--wf-start", default="2019-01-01")
    p.add_argument("--wf-end", default="2026-06-11")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--train-frac", type=float, default=0.4)
    p.add_argument("--label", default=None)
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv)

    windows = fold_windows(a.wf_start, a.wf_end, a.folds, a.train_frac)
    rows = load_rows(a.emit)
    rows2x = load_rows(a.emit_2x) if a.emit_2x else None
    if a.mode == "net" and rows2x is None:
        p.error("--mode net requires --emit-2x")

    def nets(rs, fee_mult):
        out = []
        skipped = 0
        for r in rs:
            t = _parse_ts(str(r["entry_time"]))
            if a.mode == "ict":
                nr = ict_net(r, a.fee_bps * fee_mult)
                if nr is None:
                    skipped += 1
                    continue
            else:
                nr = float(r["net_r"])
            out.append((t, nr))
        return out, skipped

    base, skipped = nets(rows, 1.0)
    if a.mode == "ict":
        dbl, _ = nets(rows, 2.0)
    else:
        dbl, _ = nets(rows2x, 1.0)

    def bucket(pairs):
        per = []
        for (s, e) in windows:
            seg = [nr for (t, nr) in pairs if s <= t < e]
            per.append({
                "oos_start": s.date().isoformat(),
                "oos_end": e.date().isoformat(),
                "trades": len(seg),
                "net_r": round(sum(seg), 4),
            })
        return per

    folds_base = bucket(base)
    folds_dbl = bucket(dbl)
    total_base = round(sum(f["net_r"] for f in folds_base), 4)
    total_dbl = round(sum(f["net_r"] for f in folds_dbl), 4)
    all_pos = all(f["net_r"] > 0 for f in folds_base)
    headroom_ok = total_dbl > 0
    out = {
        "label": a.label or a.emit,
        "mode": a.mode,
        "fee_bps_base": a.fee_bps,
        "wf_start": a.wf_start, "wf_end": a.wf_end,
        "folds": a.folds, "train_frac": a.train_frac,
        "skipped_no_prices": skipped,
        "folds_base_fee": folds_base,
        "folds_double_fee": folds_dbl,
        "total_oos_net_r_base": total_base,
        "total_oos_net_r_double": total_dbl,
        "gate_all_folds_positive": all_pos,
        "gate_2x_fee_headroom": headroom_ok,
        "verdict": "PASS" if (all_pos and headroom_ok) else "FAIL",
    }
    # Readiness tier (reject / paper_ready / live_money_ready) so the gate stops
    # discarding genuine-but-not-yet-robust edges — docs/strategy-readiness-ladder.md.
    try:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from classify_strategy_tier import classify_tier as _classify_tier
        out["tier"] = _classify_tier(out)["tier"]
    except Exception:  # never let tiering break the report
        pass
    text = json.dumps(out, indent=2)
    print(text)
    if a.json_out:
        with open(a.json_out, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
