#!/usr/bin/env python3
"""Measure how two candle CSVs (same symbol/timeframe) agree on their overlap.

Prints per pair: rows, span, overlap bars, median/p99/max |close diff| in bps,
bars present in only one file inside the overlap window, and gap count (>1
interval steps) in each. Used to record the canonical-feed choice for
PI-20260929-FCJRWVAK-0002:

    python3 scripts/ops/compare_candle_feeds.py OLD.csv NEW.csv [--bybit SYMBOL:INTERVAL]
"""
import sys
import pandas as pd


def load(p):
    d = pd.read_csv(p)
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, format="mixed")
    return d.drop_duplicates("timestamp").set_index("timestamp").sort_index()


def gaps(idx, step):
    return int((idx.to_series().diff().dropna() > step).sum())


def compare(a, b, label):
    step = a.index.to_series().diff().mode().iloc[0]
    lo, hi = max(a.index.min(), b.index.min()), min(a.index.max(), b.index.max())
    if lo > hi:
        print(f"{label}: NO OVERLAP (a {a.index.min()}..{a.index.max()}, b {b.index.min()}..{b.index.max()})")
        return
    aw, bw = a.loc[lo:hi], b.loc[lo:hi]
    j = aw.join(bw, how="inner", lsuffix="_a", rsuffix="_b")
    bps = ((j["close_a"] - j["close_b"]).abs() / j["close_a"] * 1e4)
    vr = (j["volume_b"] / j["volume_a"]).median() if "volume_a" in j else float("nan")
    print(f"{label}: overlap {lo}..{hi} n_common={len(j)} only_a={len(aw)-len(j)} only_b={len(bw)-len(j)} "
          f"median_bps={bps.median():.3f} p99_bps={bps.quantile(.99):.2f} max_bps={bps.max():.1f} "
          f"median_vol_ratio_b/a={vr:.3f} gaps_a={gaps(aw.index, step)} gaps_b={gaps(bw.index, step)}")


if __name__ == "__main__":
    compare(load(sys.argv[1]), load(sys.argv[2]), f"{sys.argv[1]} vs {sys.argv[2]}")
