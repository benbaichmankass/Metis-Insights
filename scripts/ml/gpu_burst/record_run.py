#!/usr/bin/env python3
"""GPU-burst cost recorder (M19 Tier-1).

Run AFTER a burst finishes + the pod is torn down: appends one entry to the
committed ``comms/gpu_spend_ledger.json`` so the dashboard's GPU-spend panel shows
this session's actual cost. The workflow commits + pushes the ledger afterward.

``--cost`` is the authoritative billed figure (actual GPU-hours × the pod's rate,
read from the provider at teardown); if omitted, it's derived from
``--gpu-hours × --rate``.

Usage:
    python -m scripts.ml.gpu_burst.record_run \
      --run-id gpu-20260702-t11 --experiment "T1.1 deep-head bake-off" \
      --gpu-type "RTX 4090" --gpu-hours 0.9 --rate 0.34 --cost 0.31 \
      --started 2026-07-02T10:00:00Z --ended 2026-07-02T10:54:00Z --status completed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.runtime import gpu_spend  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--gpu-type", default=None)
    ap.add_argument("--gpu-hours", type=float, default=None)
    ap.add_argument("--rate", type=float, default=None, help="USD per GPU-hour.")
    ap.add_argument("--cost", type=float, default=None, help="Authoritative billed USD (else gpu-hours × rate).")
    ap.add_argument("--started", default=None)
    ap.add_argument("--ended", default=None)
    ap.add_argument("--status", default="completed")
    args = ap.parse_args(argv)

    run = {
        "run_id": args.run_id,
        "experiment": args.experiment,
        "gpu_type": args.gpu_type,
        "gpu_hours": args.gpu_hours,
        "rate_usd_per_hour": args.rate,
        "started_at": args.started,
        "ended_at": args.ended,
        "status": args.status,
    }
    if args.cost is not None:
        run["cost_usd"] = args.cost

    gpu_spend.record_run(run)
    ledger = gpu_spend.load_ledger()
    entry = ledger["runs"][-1]
    print(json.dumps({"recorded": entry.get("run_id"), "cost_usd": entry.get("cost_usd"),
                      "month_runs": len(ledger["runs"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
