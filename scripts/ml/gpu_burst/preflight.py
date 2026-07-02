#!/usr/bin/env python3
"""GPU-burst preflight — the hard spend-gate (M19 Tier-1).

Run BEFORE launching any spot pod. Reads the committed
``comms/gpu_spend_ledger.json`` (via ``src.runtime.gpu_spend``), computes
month-to-date spend, and **exits non-zero if this run's estimated cost would push
the month past the budget** — so the burst workflow refuses to launch. Prints a
human summary either way. Never spends anything; a read + a gate.

Usage:
    python -m scripts.ml.gpu_burst.preflight --est-cost 0.40 --experiment "T1.1 bake-off"
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.runtime import gpu_spend  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--est-cost", type=float, required=True, help="Estimated USD cost of this run.")
    ap.add_argument("--experiment", default="(unnamed)", help="What this run trains (for the log).")
    args = ap.parse_args(argv)

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    s = gpu_spend.summarize_spend(current_month=month)
    mtd = s["current_month_usd"]
    budget = s["budget_usd_per_month"]
    projected = mtd + max(0.0, args.est_cost)

    print(f"=== GPU burst preflight ({month}) ===")
    print(f"experiment      : {args.experiment}")
    print(f"month-to-date   : ${mtd:,.2f}")
    print(f"est. this run   : ${args.est_cost:,.2f}")
    print(f"projected total : ${projected:,.2f}")
    print(f"monthly budget  : ${budget:,.2f}")

    if gpu_spend.would_exceed_budget(args.est_cost, month):
        print(f"::error::GPU budget gate: ${projected:,.2f} would exceed the ${budget:,.2f} monthly cap — ABORTING (no pod launched).")
        return 1
    print(f"OK — within budget (${budget - projected:,.2f} would remain). Cleared to launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
