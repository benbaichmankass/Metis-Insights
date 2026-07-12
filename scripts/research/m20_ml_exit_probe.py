#!/usr/bin/env python3
"""M20 phase-2 — ML-supplemented exit feasibility probe (Tier-1, read-only).

Question: could the existing ML fleet supply an EXIT signal — not another
hard rule? Concretely: during a live hold, when the symbol's 15m regime head
reads high P(volatile) (or flips upward), is the REMAINING part of the trade
(exit R minus the mark at that moment) systematically worse? If yes, "exit
(or tighten) when the regime head flips against you" is a learnable trigger
worth a real experiment; if no, the hard rules stand alone.

Method (honest by construction — same truncation logic as the M20 memo § 3):
for every closed BTC/ETH/SOL trade with a resolvable 15m path, join the
symbol's regime-head shadow predictions (runtime_logs/shadow_predictions.jsonl,
synced trainer-side) that fired DURING the hold. At each prediction time t:
  future_dR(t) = real_exit_R − mark_R(t)
i.e. what the trade went on to earn/lose after t. Bucket by the head's score
at t. A materially negative mean future_dR in the high-P(volatile) bucket
(vs the low bucket) = signal. No model is trained; no simulation is run —
every quantity is observed.

Run on the trainer:
    python3 scripts/research/m20_ml_exit_probe.py \
        --db data/trade_journal.db --datasets-root datasets-out \
        --shadow-log runtime_logs/shadow_predictions.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

# Reuse the M20 analyzer's loaders/metrics — same data contracts.
from scripts.research.m20_exit_analysis import (  # noqa: E402
    _epoch,
    _f,
    load_candles,
    trade_path,
)

BAR_S = 900.0


def _load_shadow_scores(path: Path) -> dict:
    """{symbol: sorted [(ts_epoch, model_id, score)]} for regime-ish heads."""
    out: dict = {}
    if not path.exists():
        return out
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = str(r.get("model_id") or "")
        if "regime" not in mid:
            continue
        t = _epoch(r.get("ts") or r.get("timestamp"))
        s = _f(r.get("score"))
        sym = str(r.get("symbol") or "")
        if t is None or s is None or not sym:
            continue
        out.setdefault(sym, []).append((t, mid, s))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trade_journal.db")
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--shadow-log", default="runtime_logs/shadow_predictions.jsonl")
    ap.add_argument("--since-days", type=float, default=90.0)
    a = ap.parse_args()

    shadow = _load_shadow_scores(Path(a.shadow_log))
    print("shadow regime records per symbol:",
          {k: len(v) for k, v in shadow.items()})

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).timestamp()
    rows = con.execute(
        "SELECT timestamp, closed_at, symbol, direction, entry_price, "
        "stop_loss, position_size, pnl, strategy_name, account_class, is_demo "
        "FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
        "AND pnl IS NOT NULL "
        "AND COALESCE(setup_type,'') NOT IN ('intent_reduce','adopted_orphan') "
        "AND COALESCE(notes,'') NOT LIKE '%\"intent_reduce\": true%' "
        "AND COALESCE(reconcile_status,'') != 'superseded'").fetchall()

    ds_root = Path(a.datasets_root)
    candles, cand_ts = {}, {}
    buckets = {"hi": [], "mid": [], "lo": []}
    per_model: dict = {}
    n_joined = 0
    for r in rows:
        t_open, t_close = _epoch(r["timestamp"]), _epoch(r["closed_at"])
        if t_open is None or (now - t_open) > a.since_days * 86400:
            continue
        sym = str(r["symbol"])
        if sym not in shadow:
            continue
        entry, sl, qty = _f(r["entry_price"]), _f(r["stop_loss"]), _f(r["position_size"])
        if not entry or not sl or not qty:
            continue
        if sym not in candles:
            candles[sym] = load_candles(sym, ds_root)
            cand_ts[sym] = [c["t"] for c in candles[sym]]
        if not candles[sym]:
            continue
        is_long = str(r["direction"] or "").lower() in ("buy", "long")
        pm = trade_path(candles[sym], cand_ts[sym], t_open,
                        t_close or t_open, entry, sl, is_long)
        if pm is None or pm["bars"] < 2:
            continue
        real_exit_r = pm["marks"][-1]  # mark at close bar ≈ exit R basis
        # predictions during the hold
        preds = shadow[sym]
        i0 = bisect_right([p[0] for p in preds], t_open)
        joined = False
        for t, mid, score in preds[i0:]:
            if t >= (t_close or t_open):
                break
            bar_i = min(int((t - t_open) // BAR_S), pm["bars"] - 1)
            mark = pm["marks"][bar_i]
            fut = real_exit_r - mark
            b = "hi" if score >= 0.6 else ("lo" if score <= 0.4 else "mid")
            buckets[b].append(fut)
            slot = per_model.setdefault(mid, {"hi": [], "mid": [], "lo": []})
            slot[b].append(fut)
            joined = True
        if joined:
            n_joined += 1

    print(f"trades with in-hold regime predictions: {n_joined}")
    print("\n=== future_dR by P(volatile) bucket (all regime heads) ===")
    for b in ("lo", "mid", "hi"):
        v = buckets[b]
        print(f"{b:4s} n={len(v):5d} mean_future_dR="
              f"{mean(v):.3f}" if v else f"{b:4s} n=0")
    print("\n=== per model ===")
    for mid, slot in sorted(per_model.items()):
        parts = []
        for b in ("lo", "mid", "hi"):
            v = slot[b]
            parts.append(f"{b}: n={len(v)} mean={mean(v):.3f}" if v
                         else f"{b}: n=0")
        print(f"{mid}: " + " | ".join(parts))
    print("\nInterpretation: a materially MORE NEGATIVE mean future_dR in the "
          "'hi' bucket than 'lo' = the regime head carries exit information "
          "(candidate for an ML exit-trigger experiment); similar buckets = "
          "no exit signal in the current heads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
