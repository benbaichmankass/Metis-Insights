#!/usr/bin/env python3
"""Portfolio-robustness validation of the diversified alt book — "bank the win"
(docs/research/regime-conditional-strategy-weighting-DESIGN.md, Step-2 finding).

The Step-2 overlay matrix established that the un-weighted 10-cell book is
robustly net-positive out-of-sample (+140-198R) by diversification alone — no
regime weighting needed. Before that becomes a Tier-3 real-money portfolio
proposal it needs a rigorous, multi-angle robustness check that the positivity
is NOT an artifact of one cutoff, one year, one lucky cell, or a fee assumption.

Reads the same manifest the overlay tool uses (per-cell `emit` JSONL of
``{entry_time, net_r}``) and reports, on the AGGREGATE book:

  * headline: n, net_r, per-trade mean, per-trade Sharpe (scaled), max drawdown (R)
  * per-year net_r (does every calendar year stand positive?)
  * multi-cutoff holdout sweep (holdout net_r at many split dates — no cherry-pick)
  * leave-one-cell-out (worst-case single-cell dependence — is the book carried
    by one lucky cell?) and leave-one-family-out
  * added-cost-per-trade headroom (breakeven extra R/trade — the fee-robustness
    proxy: how much extra round-trip cost the book absorbs before going flat)
  * block bootstrap (monthly blocks): fraction of resamples net_r>0, 5th pct

Tier-1 research tooling — reads files, writes a report. No live path.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd


def _load_trades(manifest: list[dict]) -> pd.DataFrame:
    rows = []
    for cell in manifest:
        label = cell["label"]
        family = str(label).split("_")[0]
        for t in (json.loads(line) for line in open(cell["emit"]) if line.strip()):
            et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
            if pd.isna(et):
                continue
            rows.append({"cell": label, "family": family, "time": et,
                         "net_r": float(t.get("net_r", 0.0))})
    df = pd.DataFrame(rows)
    return df.sort_values("time").reset_index(drop=True) if not df.empty else df


def _metrics(net: pd.Series) -> dict:
    n = len(net)
    tot = float(net.sum())
    mean = float(net.mean()) if n else 0.0
    std = float(net.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mean / std * np.sqrt(n)) if std else 0.0
    cum = net.cumsum()
    dd = float((cum.cummax() - cum).max()) if n else 0.0
    return {"n": int(n), "net_r": round(tot, 1), "mean_r": round(mean, 4),
            "sharpe": round(float(sharpe), 2), "max_dd_r": round(dd, 1)}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--boot", type=int, default=2000, help="block-bootstrap resamples")
    a = p.parse_args(argv)

    manifest = json.load(open(a.manifest))
    T = _load_trades(manifest)
    if T.empty:
        print("no trades built")
        return 1

    out: dict = {"cells": len(manifest)}
    head = _metrics(T["net_r"])
    out["headline"] = head
    span = f"{T['time'].min().date()} .. {T['time'].max().date()}"
    out["span"] = span
    print(f"=== Diversified book robustness — {len(manifest)} cells, {head['n']} trades ({span}) ===")
    print(f"HEADLINE: net_r={head['net_r']}  mean={head['mean_r']:+.4f}R  "
          f"sharpe={head['sharpe']}  maxDD={head['max_dd_r']}R\n")

    # 1. per-year
    print("-- per-year net_r --")
    by_year = {}
    for yr, g in T.groupby(T["time"].dt.year):
        m = _metrics(g["net_r"])
        by_year[int(yr)] = m
        flag = "" if m["net_r"] > 0 else "  <== NEGATIVE YEAR"
        print(f"  {yr}: n={m['n']:5} net_r={m['net_r']:8.1f} mean={m['mean_r']:+.4f} sharpe={m['sharpe']:5.2f}{flag}")
    out["by_year"] = by_year
    out["all_years_positive"] = all(v["net_r"] > 0 for v in by_year.values())

    # 2. multi-cutoff holdout sweep — holdout (>= cutoff) net_r at many splits
    print("\n-- holdout net_r across cutoffs (train < cutoff <= holdout) --")
    cutoffs = ["2023-07-01", "2024-01-01", "2024-07-01", "2025-01-01", "2025-07-01"]
    sweep = {}
    for cut in cutoffs:
        c = pd.to_datetime(cut, utc=True)
        ho = T[T["time"] >= c]["net_r"]
        if ho.empty:
            continue
        m = _metrics(ho)
        sweep[cut] = m
        flag = "" if m["net_r"] > 0 else "  <== NEGATIVE HOLDOUT"
        print(f"  >= {cut}: n={m['n']:5} holdout net_r={m['net_r']:8.1f} sharpe={m['sharpe']:5.2f} maxDD={m['max_dd_r']:6.1f}{flag}")
    out["holdout_sweep"] = sweep
    out["all_holdouts_positive"] = all(v["net_r"] > 0 for v in sweep.values())

    # 3. leave-one-out (cell + family) — single-component dependence
    print("\n-- leave-one-cell-out (book net_r with each cell removed) --")
    loo_cell = {}
    for cell in sorted(T["cell"].unique()):
        rest = T[T["cell"] != cell]["net_r"].sum()
        loo_cell[cell] = round(float(rest), 1)
    worst_cell = min(loo_cell, key=loo_cell.get)
    print(f"  worst (most load-bearing) cell: {worst_cell} -> book without it = {loo_cell[worst_cell]}R "
          f"(full book {head['net_r']}R)")
    out["leave_one_cell_out"] = loo_cell
    out["loo_cell_min_net_r"] = loo_cell[worst_cell]
    out["loo_cell_all_positive"] = all(v > 0 for v in loo_cell.values())

    loo_fam = {}
    for fam in sorted(T["family"].unique()):
        loo_fam[fam] = round(float(T[T["family"] != fam]["net_r"].sum()), 1)
    print("  leave-one-family-out:", {k: f"{v}R" for k, v in loo_fam.items()})
    out["leave_one_family_out"] = loo_fam

    # 4. added-cost-per-trade headroom — fee robustness proxy (breakeven extra R/trade)
    print("\n-- added cost-per-trade headroom (extra R subtracted per trade) --")
    n = head["n"]
    breakeven = round(head["net_r"] / n, 4) if n else 0.0
    cost = {}
    for h in (0.0, 0.01, 0.02, 0.03, 0.05):
        cost[h] = round(float((T["net_r"] - h).sum()), 1)
        flag = "" if cost[h] > 0 else "  <== flat/negative"
        print(f"  +{h:.2f} R/trade: book net_r={cost[h]:8.1f}{flag}")
    print(f"  BREAKEVEN added cost = {breakeven:+.4f} R/trade "
          f"(the book stays +EV until per-trade cost rises this much)")
    out["added_cost_headroom"] = cost
    out["breakeven_added_cost_r_per_trade"] = breakeven

    # 5. block bootstrap (monthly blocks) — statistical positivity
    T = T.assign(ym=T["time"].dt.strftime("%Y-%m"))
    blocks = [g["net_r"].values for _, g in T.groupby("ym")]
    rng = np.random.default_rng(7)
    totals = np.empty(a.boot)
    nb = len(blocks)
    for i in range(a.boot):
        pick = rng.integers(0, nb, nb)
        totals[i] = float(np.concatenate([blocks[j] for j in pick]).sum())
    frac_pos = float((totals > 0).mean())
    p5 = float(np.percentile(totals, 5))
    print(f"\n-- block bootstrap ({a.boot} resamples, {nb} monthly blocks) --")
    print(f"  P(book net_r > 0) = {frac_pos:.3f} | 5th pct net_r = {p5:.1f} | median = {np.median(totals):.1f}")
    out["bootstrap"] = {"resamples": a.boot, "monthly_blocks": nb,
                        "frac_positive": round(frac_pos, 3),
                        "p5_net_r": round(p5, 1), "median_net_r": round(float(np.median(totals)), 1)}

    # overall verdict
    robust = (out["all_years_positive"] and out["all_holdouts_positive"]
              and out["loo_cell_all_positive"] and frac_pos >= 0.95 and p5 > 0)
    out["robust"] = bool(robust)
    print(f"\nVERDICT: {'ROBUST' if robust else 'NOT fully robust'} "
          f"(years+ {out['all_years_positive']}, holdouts+ {out['all_holdouts_positive']}, "
          f"LOO-cell+ {out['loo_cell_all_positive']}, boot P+ {frac_pos:.2f}, boot-p5 {p5:.0f})")

    if a.json_out:
        json.dump(out, open(a.json_out, "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
