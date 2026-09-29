#!/usr/bin/env python3
"""Grade an exit-head round's FINAL per-year fold against RQ-20260929-401's registered rule.

WHY THIS IS COMMITTED BEFORE THE RUN
------------------------------------
The rule is registered in `research/queue/RQ-20260929-401.yaml`; this file is the
same rule as code, committed in the SAME PR and merged before any dispatch, so the
partition (which legs count, what PASS / REFINE / FAIL mean) is not re-derived by
eye after the numbers are known.

WHAT IT READS, PER LEG: `<round>/<leg>/final_fold_net.json`, written by
`scripts/research/exit_head_final_fold_replay.py`. It does NOT read the trainer's
`e1_report.json` arms directly, because those are on different cost bases (baseline
= harness net_r; head = raw candle mark `open_r`, i.e. GROSS) — see that module.
  n_oos             fold trades that joined to the harness emit
  baseline_net_r    each trade held to the harness exit (harness net_r)
  head_net_r        the shipped {below_half_r, tau 0.10, below_r 0.5} head, each
                    truncated trade charged its round-trip cost + exit fee
  recovered_R_oos = head_net_r - baseline_net_r, UNROUNDED, on the SAME trades

THE PARTITION (RQ-20260928-005's rule with its gaps closed, thresholds unchanged)
  producer problem on ANY leg (no report, unreadable/corrupt report, replay failed /
  mismatched the trainer's own fold, cost policy differs from or could not be verified
  against the harness, incomplete join, wrong fold mode)
                              -> not_applicable  (read_state producer_failed);
                                 NEVER dropped from Q silently
  Q = legs with n_oos >= FLOOR (64); a leg whose final-year fold does not exist
  (fewer than 50 test trades) is `final_fold_missing` = underpowered = not in Q.
  P = legs in Q with recovered_R_oos > 0.
  |Q| < 2  -> indeterminate (underpowered).   The floor is never lowered.
  |P| >= 2 -> pass     |P| == 1 -> indeterminate (refine)     |P| == 0 -> fail

Usage (on the trainer, after the replay)::

    python scripts/research/exit_head_final_fold_grade.py \\
        --round-dir /home/ubuntu/rq20260929_401_round \\
        --legs ict_scalp_sol_15m,ict_scalp_xrp_15m,ict_scalp_eth_15m
"""
# wiring: manual-only - grades the one-off trainer round dispatched by
# research/queue/RQ-20260929-401.yaml (`run.note`); not called from CI.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

FLOOR = 64   # RQ-20260928-005 / RQ-20260927-001 power floor; never lowered

#: States that mean "the producer did not give us a measurement" — distinct from
#: "we measured and the leg is thin". Both used to be one silent exclusion.
PRODUCER_STATES = ("no_report", "replay_failed", "replay_mismatch", "cost_policy_mismatch",
                   "cost_policy_unverified", "join_incomplete", "wrong_fold_mode", "malformed")


def leg_stat(net: Dict[str, Any], leg: str) -> Dict[str, Any]:
    """One leg's statistic from its `final_fold_net.json`, or an explicit non-computed state."""
    state = net.get("state")
    if state == "final_fold_missing":
        return {"leg": leg, "state": "final_fold_missing", "n_test": net.get("n_test")}
    if state != "ok":
        return {"leg": leg, "state": state if state in PRODUCER_STATES else "malformed",
                "detail": {k: net.get(k) for k in ("why", "error", "missing", "fold_mode")
                           if net.get(k) is not None}}
    try:
        n = int(net["n_oos"])
        base, head = float(net["baseline_net_r"]), float(net["head_net_r"])
        rec = float(net["recovered_r_oos"])
    except (KeyError, TypeError, ValueError):
        return {"leg": leg, "state": "malformed"}
    if abs((head - base) - rec) > 1e-9:          # the stored delta must be head - baseline
        return {"leg": leg, "state": "malformed", "detail": {"why": "recovered != head - baseline"}}
    return {"leg": leg, "state": "ok", "n_oos": n, "baseline_net_r": base, "head_net_r": head,
            "recovered_r_oos": rec, "recovered_r_gross": net.get("recovered_r_gross"),
            "n_early_exits": net.get("n_early_exits"),
            "charged_roundtrip_cost_r": net.get("charged_roundtrip_cost_r"),
            "charged_exit_fee_r": net.get("charged_exit_fee_r")}


def read_leg(round_dir: Path, leg: str) -> Dict[str, Any]:
    """Load one leg's `final_fold_net.json` -> its stat. A missing file is `no_report`; a file that
    is unreadable, not JSON, or not an object is `malformed` — it must never crash the grader and so
    lose the other legs' results (both are producer problems -> not_applicable)."""
    p = Path(round_dir) / leg / "final_fold_net.json"
    if not p.exists():
        return {"leg": leg, "state": "no_report", "detail": {"missing": str(p)}}
    try:
        obj = json.loads(p.read_text())
    except (OSError, ValueError) as exc:          # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return {"leg": leg, "state": "malformed",
                "detail": {"why": f"unreadable final_fold_net.json: {type(exc).__name__}: {exc}"[:200]}}
    if not isinstance(obj, dict):
        return {"leg": leg, "state": "malformed",
                "detail": {"why": f"final_fold_net.json is a {type(obj).__name__}, not an object"}}
    return leg_stat(obj, leg)


def grade(stats: List[Dict[str, Any]], floor: int = FLOOR) -> Dict[str, Any]:
    """The registered partition. Pure function of the per-leg stats."""
    bad = [s for s in stats if s.get("state") in PRODUCER_STATES]
    if bad:
        return {"verdict": "not_applicable", "read_state": "producer_failed",
                "why": "producer problem on: " + ", ".join(f"{s['leg']}={s['state']}" for s in bad),
                "floor": floor, "legs": stats}
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
    return {"verdict": verdict, "read_state": "measured", "why": why, "floor": floor,
            "n_legs_graded": len(q), "n_legs_positive": len(p), "legs": stats}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round-dir", required=True)
    ap.add_argument("--legs", required=True)
    a = ap.parse_args(argv[1:])
    stats: List[Dict[str, Any]] = []
    for leg in a.legs.split(","):
        stats.append(read_leg(Path(a.round_dir), leg))
    print(json.dumps(grade(stats), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
