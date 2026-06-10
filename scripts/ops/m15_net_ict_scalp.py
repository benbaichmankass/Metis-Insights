#!/usr/bin/env python3
"""Net-of-fee post-processor for backtest_ict_scalp --emit-trades output.

The ict_scalp harness has no fee model (gross_r == net_r; backlog
BL-20260610-M15-1). This computes the exact per-trade fee in R from the
enriched emit fields (entry, sl, risk): a roundtrip fee of F bps on
notional costs ``(F/1e4) * entry / |entry - sl|`` R, because 1R is the
price distance to the stop.

Usage:
    python scripts/ops/m15_net_ict_scalp.py --fee-bps-roundtrip 2.0 \
        results/m15_phase0/ict_scalp_*_trades.jsonl

Prints one summary line per file and writes ``<file>.net.json`` next to
each input. Rows without the enriched fields (old emits) are skipped and
counted, so partial reruns stay honest.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys


def process(path: str, fee_bps: float) -> dict:
    n = net = gross = wins = skipped = 0
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        entry, sl = row.get("entry"), row.get("sl")
        if entry is None or sl is None or not float(entry) or entry == sl:
            skipped += 1
            continue
        risk = abs(float(entry) - float(sl))
        fee_r = (fee_bps / 1e4) * float(entry) / risk
        g = float(row.get("gross_r", 0.0))
        nr = g - fee_r
        n += 1
        gross += g
        net += nr
        wins += 1 if nr > 0 else 0
    out = {
        "file": path,
        "fee_bps_roundtrip": fee_bps,
        "trades": n,
        "skipped_no_prices": skipped,
        "gross_r_total": round(gross, 4),
        "net_r_total": round(net, 4),
        "net_expectancy_r": round(net / n, 4) if n else None,
        "net_win_rate": round(100.0 * wins / n, 2) if n else None,
    }
    with open(path + ".net.json", "w") as fh:
        json.dump(out, fh, indent=2)
    return out


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--fee-bps-roundtrip", type=float, default=2.0)
    p.add_argument("globs", nargs="+")
    args = p.parse_args(argv)
    paths = sorted(set(sum((glob.glob(g) for g in args.globs), [])))
    if not paths:
        print("no trade files matched", file=sys.stderr)
        return 1
    for path in paths:
        o = process(path, args.fee_bps_roundtrip)
        print(
            f"{o['file']}: n={o['trades']} gross={o['gross_r_total']} "
            f"net={o['net_r_total']} exp={o['net_expectancy_r']} "
            f"(skipped {o['skipped_no_prices']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
