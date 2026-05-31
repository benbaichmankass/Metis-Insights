#!/usr/bin/env python3
"""Compare two harness JSON outputs (sim Phase-5 vs backtest_system) on the
metrics that decide money, within a tolerance band.

The two harnesses are NOT expected to be byte-identical end-to-end — they
differ in warmup handling, signal caching, and (for a multi-TF roster)
per-strategy resampling. Unit-level equivalence (the sizing formula and
R-metric identity when account=None) is already proven in
tests/test_sim_phase5_account.py. This is the e2e SANITY band: it confirms the
consolidated harness lands in the same neighbourhood as the harness it
replaces, so backtest_system.py can be retired with evidence.

Usage:
    reproduce_diff.py <sim.json> <backtest_system.json> [--tol-pct 5] [--json-out F]

Exit code: 0 if every compared metric is within tolerance (or both sides agree
it's absent), 2 if any metric diverges beyond the band. Never raises on a
missing field — a missing field is reported as a divergence, not a crash.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# (label, dotted-path-in-sim, dotted-path-in-backtest_system)
_FIELDS = [
    ("net_pnl",        "account.net_pnl",          "net_pnl"),
    ("final_balance",  "account.final_balance",    "final_balance"),
    ("return_pct",     "account.return_pct",       "return_pct"),
    ("max_drawdown",   "account.max_drawdown_usd",  "max_drawdown_usd"),
    ("return_dd",      "account.return_over_dd",    "return_dd_ratio"),
    ("total_trades",   "total_trades",              "total_trades"),
    ("win_rate_pct",   "win_rate_pct",              "win_rate_pct"),
]


def _dig(d: Any, dotted: str) -> Optional[float]:
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if isinstance(cur, bool):  # guard: bool is an int subclass
        return None
    return cur if isinstance(cur, (int, float)) else None


def _within(a: Optional[float], b: Optional[float], tol_pct: float) -> bool:
    if a is None or b is None:
        return a is None and b is None  # both-absent agrees; one-sided diverges
    scale = max(abs(a), abs(b), 1.0)  # absolute floor so near-zero doesn't blow up
    return abs(a - b) <= (tol_pct / 100.0) * scale


def compare(sim: dict, bts: dict, tol_pct: float) -> dict:
    rows = []
    ok = True
    for label, sp, bp in _FIELDS:
        a, b = _dig(sim, sp), _dig(bts, bp)
        within = _within(a, b, tol_pct)
        ok = ok and within
        rows.append({"metric": label, "sim": a, "backtest_system": b,
                     "within_tol": within})
    return {"tolerance_pct": tol_pct, "match": ok, "fields": rows}


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sim_json")
    p.add_argument("bts_json")
    p.add_argument("--tol-pct", type=float, default=5.0)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    sim = json.loads(Path(args.sim_json).read_text())
    bts = json.loads(Path(args.bts_json).read_text())
    report = compare(sim, bts, args.tol_pct)

    out = json.dumps(report, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(out)
    print(out)
    verdict = "MATCH" if report["match"] else "DIVERGENCE"
    print(f"\nreproduce-check: {verdict} (tol={args.tol_pct}%)", file=sys.stderr)
    return 0 if report["match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
