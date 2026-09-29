#!/usr/bin/env python3
"""Grade an exit-head round's FINAL per-year fold against RQ-20260929-401's registered rule.

WHY THIS IS COMMITTED BEFORE THE RUN
------------------------------------
The rule is registered in `research/queue/RQ-20260929-401.yaml`; this file is the
same rule as code, committed in the SAME PR and merged before any dispatch, so the
partition (which legs count, what PASS / REFINE / FAIL mean) is not re-derived by
eye after the numbers are known. It reads what `scripts/ml/train_exit_head.py`
already wrote (`<round>/<leg>/e1_report.json`) and adds NO measurement of its own.

WHAT IT READS, PER LEG (a single-leg family dir, so the family block IS the leg)
  fold           the one whose `year` == --expect-year (the latest calendar year);
                 a leg with no such fold is `final_fold_missing`, never "the
                 previous year stands in" (the same rule as analyze_exit_head's
                 `_final_fold_entry`)
  n_oos          fold["n_trades"]
  baseline       fold["actual"]["net_r"]                      (held to the harness exit)
  head           fold["model_cond"]["below_half_r_tau_0.1"]["net_r"]
                 = the SHIPPED donchian shape {below_half_r, tau 0.10, below_r 0.5}
                 (src.runtime.exit_head_shadow.would_exit_for) — ONE arm, fixed a
                 priori. NOT max-over-arms, and NOT `selected_tau`.
  recovered_R_oos = head - baseline, on the SAME trades (asserted: trade counts equal)

THE PARTITION (RQ-20260928-005's rule with its gaps closed, thresholds unchanged)
  Q = legs with n_oos >= FLOOR (64).  P = legs in Q with recovered_R_oos > 0.
  |Q| < 2  -> indeterminate (underpowered). The floor is never lowered.
  |P| >= 2 -> pass          |P| == 1 -> indeterminate (refine: re-run 2nd split)
  |P| == 0 -> fail

Usage (on the trainer, after the round)::

    python scripts/research/exit_head_final_fold_grade.py \\
        --round-dir /home/ubuntu/rq20260929_401_round \\
        --legs ict_scalp_sol_15m,ict_scalp_xrp_15m,ict_scalp_eth_15m --expect-year 2026
"""
# wiring: manual-only - grades the one-off trainer round dispatched by
# research/queue/RQ-20260929-401.yaml (`run.note`); not called from CI.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

FLOOR = 64                       # RQ-20260928-005 / RQ-20260927-001 power floor; never lowered
ARM = "below_half_r_tau_0.1"     # key = f"{shape}_tau_{tau}" in train_exit_head.eval_split


def leg_stat(report: Dict[str, Any], leg: str, expect_year: int) -> Dict[str, Any]:
    """One leg's final-fold statistic, or an explicit non-computed state."""
    if report.get("fold_mode") != "years":
        return {"leg": leg, "state": "wrong_fold_mode", "fold_mode": report.get("fold_mode")}
    match = [f for f in (report.get("folds") or []) if int(f.get("year", -1)) == int(expect_year)]
    if not match:
        have = sorted(int(f.get("year", -1)) for f in (report.get("folds") or []))
        return {"leg": leg, "state": "final_fold_missing", "expect_year": expect_year,
                "folds_present": have}
    f = match[0]
    actual = (f.get("actual") or {})
    head = ((f.get("model_cond") or {}).get(ARM) or {})
    n = f.get("n_trades")
    if (not isinstance(n, int) or actual.get("trades") != n or head.get("trades") != n
            or actual.get("net_r") is None or head.get("net_r") is None):
        return {"leg": leg, "state": "malformed_fold", "n_trades": n,
                "actual_trades": actual.get("trades"), "head_trades": head.get("trades")}
    return {"leg": leg, "state": "ok", "fold_year": int(f["year"]), "n_oos": n,
            "baseline_net_r": actual["net_r"], "head_net_r": head["net_r"],
            "recovered_r_oos": round(head["net_r"] - actual["net_r"], 4),
            "train_rows": f.get("train_rows"), "auc": f.get("auc")}


def grade(stats: List[Dict[str, Any]], floor: int = FLOOR) -> Dict[str, Any]:
    """The registered partition. Pure function of the per-leg stats."""
    q = [s for s in stats if s.get("state") == "ok" and s["n_oos"] >= floor]
    p = [s for s in q if s["recovered_r_oos"] > 0]
    if len(q) < 2:
        verdict, why = "indeterminate", "underpowered: fewer than 2 legs reach n_oos >= %d" % floor
    elif len(p) >= 2:
        verdict, why = "pass", ">=2 legs with n_oos >= %d have recovered_R_oos > 0" % floor
    elif len(p) == 1:
        verdict, why = "indeterminate", "refine: exactly 1 leg positive; re-run on the fold before the last"
    else:
        verdict, why = "fail", "recovered_R_oos <= 0 on every leg with n_oos >= %d" % floor
    return {"verdict": verdict, "why": why, "floor": floor, "n_legs_graded": len(q),
            "n_legs_positive": len(p), "legs": stats}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round-dir", required=True)
    ap.add_argument("--legs", required=True)
    ap.add_argument("--expect-year", type=int, required=True)
    a = ap.parse_args(argv[1:])
    stats: List[Dict[str, Any]] = []
    for leg in a.legs.split(","):
        p = Path(a.round_dir) / leg / "e1_report.json"
        if not p.exists():
            stats.append({"leg": leg, "state": "no_report", "path": str(p)})
            continue
        stats.append(leg_stat(json.loads(p.read_text()), leg, a.expect_year))
    print(json.dumps(grade(stats), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
