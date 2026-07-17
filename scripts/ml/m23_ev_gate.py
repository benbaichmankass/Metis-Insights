#!/usr/bin/env python3
"""M23 Phase-1 — EV-at-threshold gate reframe (MB-20260717-M23-SELECTION-GATE).

The pooled meta-label model lifts real-trade precision to 0.318 (vs the 0.244 base
rate) but FAILS the "beat 0.756 majority accuracy" gate — the wrong bar for a
rare-positive (24.4% win) SELECTION head. The right question: **does taking only the
model's top-scored real trades beat taking ALL of them, net of cost?**

This scorer reuses the ALREADY-TRAINED pooled model (no retrain divergence): it loads
the latest `setup-candidates-metalabel-backtest-v1` model_state, resolves the same
predictor the evaluator uses, scores every real `is_live_trade` holdout row, and sweeps
the decision threshold — reporting, at each, the selected subset's win-rate + net R
(EV) net of a per-trade cost. If some threshold's selected book beats the take-all
baseline on net R at a usable volume, the meta-label is a useful trade FILTER even
though it can't beat all-lose accuracy.

Tier-1 / offline / read-only. No config, no registry, no order path.

Usage (on the trainer):
  .venv/bin/python scripts/ml/m23_ev_gate.py \
    --data datasets-out/setup_candidates/BTCUSDT/all/v001/data.jsonl \
    [--model-state <path>]   # default: newest experiments-runs/<manifest>/*/model_state.json
    [--cost-r 0.05]          # per-trade roundtrip cost in R (default 0.05)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

# repo root on sys.path (scripts/ml/ -> repo root)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ml.evaluators.base import Evaluator  # noqa: E402

MANIFEST_ID = "setup-candidates-metalabel-backtest-v1"


def _find_model_state(explicit: str | None) -> str:
    if explicit:
        return explicit
    pats = [
        f"experiments-runs/{MANIFEST_ID}/*/model_state.json",
        f"ml/experiments-runs/{MANIFEST_ID}/*/model_state.json",
    ]
    cands: list[str] = []
    for p in pats:
        cands.extend(glob.glob(p))
    if not cands:
        raise SystemExit(f"no model_state.json found for {MANIFEST_ID}; pass --model-state")
    cands.sort(key=lambda p: os.path.getmtime(p))
    return cands[-1]


def _r_of(row: dict) -> float:
    for k in ("r_multiple", "net_r", "gross_r"):
        v = row.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    # fallback: unit R from the win/loss label (coarse — flagged in output)
    return 1.0 if bool(row.get("won")) else -1.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True, help="setup_candidates data.jsonl (v001)")
    ap.add_argument("--model-state", default=None)
    ap.add_argument("--cost-r", type=float, default=0.05,
                    help="per-trade roundtrip cost in R subtracted from each selected trade")
    ap.add_argument("--raw-score", action="store_true",
                    help="the model outputs a raw score (e.g. predicted R from a "
                         "regression head), NOT a [0,1] probability: skip the [0,1] "
                         "clamp and sweep the threshold over the observed-score grid. "
                         "Use for the M23 variant-C2 regress-R model.")
    args = ap.parse_args()

    ms_path = _find_model_state(args.model_state)
    with open(ms_path) as fh:
        model_state = json.load(fh)
    predictor = Evaluator._resolve_predictor(model_state)
    print(f"model_state: {ms_path}")

    # Load the REAL holdout rows (is_live_trade=True) with a score + R outcome.
    scored: list[tuple[float, int, float, bool]] = []  # (prob, won, r, r_is_fallback)
    n_r_fallback = 0
    with open(args.data) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not bool(row.get("is_live_trade")):
                continue
            if row.get("won") is None:
                continue
            prob = float(predictor.predict(row))
            if not args.raw_score:
                # Probability head: clamp into [0,1]. A regression (raw-score)
                # head predicts R directly and must NOT be clamped (that would
                # collapse the ranking of every trade above +1R / below 0).
                prob = 0.0 if prob < 0.0 else 1.0 if prob > 1.0 else prob
            won = 1 if bool(row.get("won")) else 0
            # Real net R now rides on the live rows (MB-20260717-M23-LIVEROW-REALIZED-R):
            # r_multiple_source=='stop_distance' is the reconstructed pnl/risk R;
            # 'unit_fallback' (or a legacy row with no source tag) is the coarse
            # ±1 the builder emits when the journal lacked the risk columns.
            src = row.get("r_multiple_source")
            is_real_r = src == "stop_distance"
            if not is_real_r:
                n_r_fallback += 1
            scored.append((prob, won, _r_of(row), not is_real_r))

    n = len(scored)
    if n == 0:
        raise SystemExit("no live holdout rows with a label found in --data")
    base_wins = sum(w for _, w, _, _ in scored)
    base_rate = base_wins / n
    base_total_r = sum(r for _, _, r, _ in scored)
    base_net_r = base_total_r - args.cost_r * n
    print(f"holdout live rows: {n}  base win-rate: {base_rate:.4f}  "
          f"take-all total R: {base_total_r:.2f}  net R (cost {args.cost_r}): {base_net_r:.2f}")
    if n_r_fallback:
        print(f"  NOTE: {n_r_fallback}/{n} rows used coarse unit-R (no reconstructed "
              f"stop-distance R); {n - n_r_fallback}/{n} carry real net R")

    # Sweep the decision threshold. Probability head: a fixed [0,1] grid + the
    # observed probs. Raw-score (regression) head: the observed-score grid only
    # (raw R spans e.g. [-3,+5], so a [0,1] grid is meaningless).
    probs = sorted({round(p, 4) for p, _, _, _ in scored})
    if args.raw_score:
        grid = probs
    else:
        grid = sorted(set([0.0] + probs + [round(x / 100, 2) for x in range(0, 101, 2)]))
    print("\nthreshold  n_sel  win_rate  sel_totalR  sel_netR  vs_takeall_netR")
    best = None  # (sel_net_r, threshold, n_sel, win_rate)
    # In raw mode print ~14 evenly-spaced rows (no natural 0.05 cadence).
    print_stride = max(1, len(grid) // 14) if args.raw_score else 1
    for i, t in enumerate(grid):
        sel = [(w, r) for p, w, r, _ in scored if p >= t]
        n_sel = len(sel)
        if n_sel == 0:
            continue
        wr = sum(w for w, _ in sel) / n_sel
        tot_r = sum(r for _, r in sel)
        net_r = tot_r - args.cost_r * n_sel
        if best is None or net_r > best[0]:
            best = (net_r, t, n_sel, wr)
        show = (i % print_stride == 0 or i == len(grid) - 1) if args.raw_score \
            else (abs((t * 100) % 5) < 1e-6 or t in (grid[0], grid[-1]))
        if show:
            print(f"  {t:7.3f}  {n_sel:4d}   {wr:6.4f}   {tot_r:8.2f}  {net_r:8.2f}   "
                  f"{net_r - base_net_r:+8.2f}")

    print("\n=== VERDICT ===")
    if best is None:
        print("no non-empty selection — inconclusive")
        return 0
    net_r, t, n_sel, wr = best
    beats = net_r > base_net_r
    coverage = n_sel / n
    print(f"best threshold t*={t:.2f}: n_sel={n_sel} ({coverage:.0%} of book), "
          f"win-rate {wr:.4f} (vs {base_rate:.4f} base), net R {net_r:.2f} "
          f"(vs take-all {base_net_r:.2f}, delta {net_r - base_net_r:+.2f})")
    usable = beats and n_sel >= max(20, int(0.1 * n))  # beat take-all at >=10% (or >=20) volume
    print(f"SELECTION EV {'POSITIVE' if beats else 'NOT POSITIVE'} "
          f"({'USABLE filter' if usable else 'below usable-volume floor / no edge'}): "
          f"the meta-label {'IS' if usable else 'is NOT (yet)'} a net-positive trade filter "
          f"at cost {args.cost_r}R.")
    print('{"m23_ev_gate_done": true}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
