#!/usr/bin/env python3
"""Portfolio combiner (S-STRAT-IMPROVE-S7).

Merge per-trade net-R streams from multiple strategies into a combined
equity curve and compare drawdown / stability vs each standalone — the
North-Star payoff test: do regime-complementary strategies smooth the
curve (higher return-per-drawdown) than either alone?

Each --stream is `name=path.jsonl` where the JSONL rows carry at least
{entry_time, net_r} (as produced by `backtest_ict_scalp.py
--emit-decisions` and `backtest_trend.py --emit-trades`). Streams are
weighted (default equal, summing to 1) so the COMBINED risk budget ~=
a single strategy (fair drawdown comparison); trades are ordered by
entry_time and equity is the running sum of weighted net_r.

Read-only. No DB / live effects.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Tuple


def _load(path: str) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            t = str(d.get("entry_time") or d.get("exit_time") or "")
            out.append((t, float(d["net_r"])))
    return out


def _metrics(events: List[Tuple[str, float]]) -> Tuple[float, float, float]:
    """events sorted by time → (total_net_r, max_drawdown_r, return/DD)."""
    cum = peak = mdd = 0.0
    for _, r in events:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    rdd = (cum / mdd) if mdd > 0 else float("inf")
    return cum, mdd, rdd


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stream", action="append", required=True, metavar="NAME=PATH",
                    help="strategy stream, repeatable: name=path.jsonl")
    ap.add_argument("--weights", default=None,
                    help="comma weights matching --stream order; default equal")
    args = ap.parse_args(argv[1:])

    streams = []
    for spec in args.stream:
        name, path = spec.split("=", 1)
        streams.append((name, _load(path)))
    k = len(streams)
    w = ([float(x) for x in args.weights.split(",")] if args.weights
         else [1.0 / k] * k)
    if len(w) != k:
        print("weights count must match streams", file=sys.stderr)
        return 2

    print(f"streams={k} weights={[round(x, 3) for x in w]}")
    print(f"\n{'stream':>20} {'trades':>7} {'net_r':>9} {'maxDD_r':>9} {'ret/DD':>8}")
    weighted: List[Tuple[str, float]] = []
    full: List[Tuple[str, float]] = []
    for (name, rows), wi in zip(streams, w):
        tot, dd, rdd = _metrics(sorted(rows))
        print(f"{name:>20} {len(rows):>7} {tot:>+9.1f} {dd:>9.1f} {rdd:>8.2f}")
        weighted += [(t, r * wi) for t, r in rows]
        full += list(rows)
    wtot, wdd, wrdd = _metrics(sorted(weighted))
    ftot, fdd, frdd = _metrics(sorted(full))
    print(f"{'COMBINED (weighted)':>20} {len(weighted):>7} {wtot:>+9.1f} "
          f"{wdd:>9.1f} {wrdd:>8.2f}")
    print(f"{'COMBINED (full-sum)':>20} {len(full):>7} {ftot:>+9.1f} "
          f"{fdd:>9.1f} {frdd:>8.2f}")
    print("\nRead: if COMBINED(weighted) ret/DD beats each standalone ret/DD, "
          "the blend is smoother — the complementarity payoff. 'weighted' "
          "keeps total risk ~= one strategy; 'full-sum' is 2x risk (both at "
          "full size).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
