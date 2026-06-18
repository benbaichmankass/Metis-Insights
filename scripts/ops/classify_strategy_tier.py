#!/usr/bin/env python3
"""Classify a strategy/backtest cell into a readiness tier.

Canonical ladder + criteria: ``docs/strategy-readiness-ladder.md``. This module
turns a k-fold walk-forward fold-report (the JSON written by
``scripts/ops/m15_ws_b_fold_report.py``) into one of four tiers, so the gate is
no longer a binary PASS/FAIL that throws away genuine-but-not-yet-robust edges:

    reject  <  paper_ready  <  live_ready          (backtest_only is pre-gate)

- **live_ready**  — every OOS fold positive (strict) AND survives 2x fees.
  Eligible for real money (Tier-3, operator-gated; demo soak + account_compat
  still required before the flip).
- **paper_ready** — net-of-fee positive overall (7.5 bps) AND survives 2x fees
  (15 bps) AND no single fold *catastrophically* negative. A real edge that is
  not yet fold-robust: wire to DEMO for decision/ML soak and enrol in the
  refinement queue (``docs/claude/strategy-refinement-queue.json``).
- **reject** — not net-of-fee viable (net-negative at 7.5 bps, or fee-bleed:
  positive gross but negative once 2x fees bite — the vwap failure mode), OR
  net-positive overall but with a catastrophic single fold (too fragile even
  for demo).

The "moderate" paper bar (operator decision 2026-06-18): a fold is
*catastrophic* when its net R is worse than ``-max(catastrophe_floor_r,
abs(total_net_r))`` — i.e. a single fold may not lose more than the whole
strategy's net OOS edge (with a small absolute floor so a barely-positive total
isn't disqualified by ordinary fold noise).

Pure-stdlib so it imports cleanly into the trainer tooling AND runs in CI.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from typing import Any, Mapping

# Default absolute floor (R) below which a single losing fold is "catastrophic"
# regardless of the total — keeps a small-but-positive total from being
# disqualified by an ordinary-sized losing fold. Operator-tunable.
DEFAULT_CATASTROPHE_FLOOR_R = 3.0

TIERS = ("backtest_only", "reject", "paper_ready", "live_ready")


def classify_tier(
    report: Mapping[str, Any],
    *,
    catastrophe_floor_r: float = DEFAULT_CATASTROPHE_FLOOR_R,
) -> dict[str, Any]:
    """Return ``{"tier": <tier>, "reasons": [...], "metrics": {...}}``.

    ``report`` is a fold-report dict (``m15_ws_b_fold_report.py`` output):
    requires ``total_oos_net_r_base`` (net R at 7.5 bps) and either
    ``gate_2x_fee_headroom`` / ``total_oos_net_r_double`` (net R at 15 bps);
    ``gate_all_folds_positive`` and a ``folds: [{net_r, ...}]`` list refine the
    verdict but are optional (their absence degrades gracefully).
    """
    reasons: list[str] = []
    total = report.get("total_oos_net_r_base")
    double = report.get("total_oos_net_r_double")
    # 2x-fee headroom: explicit gate if present, else derive from the double-fee total.
    headroom = report.get("gate_2x_fee_headroom")
    if headroom is None:
        headroom = double is not None and double > 0
    all_folds_pos = report.get("gate_all_folds_positive")
    # Resolve the per-fold list. m15_ws_b_fold_report.py names it `folds_base_fee`
    # AND additionally emits a scalar `folds` (the fold *count*); a plain `folds`
    # list is the hand-built / other-harness shape. Prefer `folds_base_fee`, then
    # a list-valued `folds`, and guard against the scalar `folds` shadowing the
    # list — that collision made this function iterate an int and raise
    # TypeError, which the fold-report's bare `except` swallowed, silently
    # voiding the tier stamp on every real report until the 2026-06-18 fix.
    folds = report.get("folds_base_fee")
    if not isinstance(folds, list):
        folds = report.get("folds")
    if not isinstance(folds, list):
        folds = []
    fold_nets = [f.get("net_r") for f in folds if isinstance(f, Mapping) and f.get("net_r") is not None]
    worst_fold = min(fold_nets) if fold_nets else None

    metrics = {
        "total_oos_net_r_base": total,
        "total_oos_net_r_double": double,
        "gate_2x_fee_headroom": bool(headroom),
        "gate_all_folds_positive": all_folds_pos,
        "n_folds": len(fold_nets),
        "worst_fold_net_r": worst_fold,
    }

    # --- reject: not net-of-fee viable -------------------------------------
    if total is None:
        reasons.append("no total_oos_net_r_base in report")
        return {"tier": "reject", "reasons": reasons, "metrics": metrics}
    if total <= 0:
        reasons.append(f"net-negative at 7.5 bps (total_oos_net_r={total:.2f})")
        return {"tier": "reject", "reasons": reasons, "metrics": metrics}
    if not headroom:
        reasons.append("fails 2x-fee headroom (net-negative at 15 bps — fee-bleed)")
        return {"tier": "reject", "reasons": reasons, "metrics": metrics}

    # --- live_ready: strict every-fold + headroom --------------------------
    if all_folds_pos:
        reasons.append("every OOS fold positive at 7.5 bps + 2x-fee headroom")
        return {"tier": "live_ready", "reasons": reasons, "metrics": metrics}

    # --- paper_ready vs catastrophic-fold reject ---------------------------
    floor = -max(catastrophe_floor_r, abs(total))
    if worst_fold is not None and worst_fold < floor:
        reasons.append(
            f"net-positive overall (+{total:.2f}R) but a catastrophic fold "
            f"({worst_fold:.2f}R < floor {floor:.2f}R) — too fragile for demo"
        )
        return {"tier": "reject", "reasons": reasons, "metrics": metrics}

    why_not_live = (
        "not every fold positive" if all_folds_pos is False
        else "fold-positivity unknown"
    )
    reasons.append(
        f"net-of-fee positive (+{total:.2f}R) + 2x-fee headroom, {why_not_live} "
        f"(worst fold {worst_fold if worst_fold is None else round(worst_fold, 2)}R) "
        "— real edge, not yet fold-robust"
    )
    return {"tier": "paper_ready", "reasons": reasons, "metrics": metrics}


def _label_of(report: Mapping[str, Any], path: str) -> str:
    return str(report.get("label") or report.get("name") or path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "paths", nargs="*",
        help="fold-report JSON files or globs (default: results/m15_ws_c_kfold/fold_*.json)",
    )
    ap.add_argument("--catastrophe-floor-r", type=float, default=DEFAULT_CATASTROPHE_FLOOR_R)
    ap.add_argument("--json", action="store_true", help="emit a JSON array instead of a table")
    args = ap.parse_args(argv)

    patterns = args.paths or ["results/m15_ws_c_kfold/fold_*.json"]
    files: list[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(pat)) or ([pat] if pat.endswith(".json") else []))
    if not files:
        print("no fold-report files matched", file=sys.stderr)
        return 2

    out = []
    for path in files:
        try:
            with open(path) as fh:
                report = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"SKIP {path}: {exc}", file=sys.stderr)
            continue
        res = classify_tier(report, catastrophe_floor_r=args.catastrophe_floor_r)
        out.append({"label": _label_of(report, path), **res})

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        order = {t: i for i, t in enumerate(TIERS)}
        for row in sorted(out, key=lambda r: (-order[r["tier"]], r["label"])):
            m = row["metrics"]
            print(
                f"{row['tier']:<13} {row['label']:<26} "
                f"net={m['total_oos_net_r_base']} 2x={m['total_oos_net_r_double']} "
                f"folds_pos={m['gate_all_folds_positive']} worst={m['worst_fold_net_r']}"
            )
            print(f"  └─ {row['reasons'][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
