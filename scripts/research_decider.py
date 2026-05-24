#!/usr/bin/env python3
"""Single-account decider simulator (S-STRAT-IMPROVE-S9, decider-v2 research).

The decider is SINGLE-account (operator architecture, 2026-05-24): one
fund, one position at a time, a layer that picks which strategy's trade to
be in. This quantifies two things:

  1. How much of the idealized multi-account blend (SUM — every member at
     full size, overlaps allowed) does ONE fund capture under realistic
     single-position selection?
  2. Does the selection ORDER matter (which member wins when signals
     overlap)?

Method: each member's trades carry [entry, exit, net_r]. SUM = sum of all
net_r (the upper bound; what portfolio_combine measured). GREEDY = walk
trades by entry time; when the account is flat, take a trade (ties at the
same timestamp resolved by the given priority order), then the account is
busy until that trade's exit — overlapping signals are skipped (you can
only be in one). Reports net / maxDD / return-per-drawdown for each.

Reads per-trade JSONL ({strategy, entry_time, exit_time, net_r}) from the
backtest_*.py --emit-trades. Research only.
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd


def _load(streams: list[str]) -> pd.DataFrame:
    rows = []
    for spec in streams:
        _, path = spec.split("=", 1)
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows.append({
                "strategy": d.get("strategy", "?"),
                "entry": pd.Timestamp(d["entry_time"]),
                "exit": pd.Timestamp(d.get("exit_time") or d["entry_time"]),
                "net_r": float(d["net_r"]),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if df["entry"].dt.tz is None:
        df["entry"] = df["entry"].dt.tz_localize("UTC")
    if df["exit"].dt.tz is None:
        df["exit"] = df["exit"].dt.tz_localize("UTC")
    return df.sort_values("entry").reset_index(drop=True)


def _metrics(net_list: list[float]):
    cum = peak = mdd = 0.0
    for r in net_list:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    rdd = (cum / mdd) if mdd > 0 else float("inf")
    return cum, mdd, rdd


def _greedy(df: pd.DataFrame, order: list[str]):
    """One position at a time. At each entry timestamp, if the account is
    flat, enter the highest-priority eligible trade; busy until its exit."""
    rank = {s: i for i, s in enumerate(order)}
    df = df.copy()
    df["rk"] = df["strategy"].map(lambda s: rank.get(s, 99))
    free = pd.Timestamp.min.tz_localize("UTC")
    taken: list[float] = []
    counts = {s: 0 for s in order}
    for _, grp in df.groupby("entry", sort=True):
        for _, row in grp.sort_values("rk").iterrows():
            if row["entry"] >= free:
                taken.append(row["net_r"])
                free = row["exit"]
                counts[row["strategy"]] = counts.get(row["strategy"], 0) + 1
                break
    return taken, counts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Single-account decider simulator")
    ap.add_argument("--stream", action="append", required=True, metavar="NAME=PATH",
                    help="member stream, repeatable")
    args = ap.parse_args(argv[1:])
    names = [s.split("=", 1)[0] for s in args.stream]
    df = _load(args.stream)
    if df.empty:
        print("no trades")
        return 1
    c, m, r = _metrics(list(df.sort_values("entry")["net_r"]))
    print(f"SUM (multi-acct blend, all at full size): net {c:+.1f}  maxDD {m:.1f}  "
          f"ret/DD {r:.2f}  trades {len(df)}")
    print("--- single-account (one position; selection on overlap) ---")
    orders = [names, list(reversed(names))]
    seen = set()
    for order in orders:
        key = tuple(order)
        if key in seen:
            continue
        seen.add(key)
        taken, counts = _greedy(df, order)
        c, m, r = _metrics(taken)
        print(f"GREEDY order={order}: net {c:+.1f}  maxDD {m:.1f}  ret/DD {r:.2f}  "
              f"taken {len(taken)}/{len(df)}  fills {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
