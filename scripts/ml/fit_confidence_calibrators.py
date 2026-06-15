#!/usr/bin/env python3
"""Fit per-strategy confidence calibrators from a backtest/live corpus.

P0 tool for the unified-confidence design (docs/unified-confidence-risk-DESIGN.md
§ 4a). Reads ``(strategy, confidence, net_r)`` rows emitted by the per-strategy
backtest harnesses (``scripts/backtest_*.py --emit-trades``) and/or a live-journal
corpus, labels ``won = net_r > 0``, fits one calibrator per strategy
(``ml.calibration.fit_calibrator``), and writes:

* ``--out-calibrators`` — JSON ``{strategy: calibrator.to_dict()}`` (the live path
  loads this read-only; predict is pure-Python, no sklearn needed).
* ``--out-report`` — JSON with per-strategy n / base_rate / method / Brier + ECE
  for RAW confidence vs CALIBRATED, plus the calibrated reliability curve. This is
  the evidence the operator signs the v1 weights/floor off of.

Usage:
    python3 scripts/ml/fit_confidence_calibrators.py \
        --emit-dir /tmp/cal --out-calibrators artifacts/calibration/calibrators.json \
        --out-report artifacts/calibration/report.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.calibration import (
    brier_score,
    expected_calibration_error,
    fit_calibrator,
    reliability_curve,
)


def _load_rows(emit_dir: str | None, corpus: str | None):
    """Yield (strategy, confidence, won) from emit-trades JSONL files."""
    paths: list[str] = []
    if emit_dir:
        paths += sorted(glob.glob(os.path.join(emit_dir, "*.jsonl")))
    if corpus:
        paths.append(corpus)
    by_strategy: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for p in paths:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                conf = r.get("confidence")
                # outcome: prefer realized net_r; fall back to pnl/won
                net_r = r.get("net_r")
                if net_r is None:
                    net_r = r.get("pnl")
                won = r.get("won")
                if conf is None:
                    continue
                if won is None:
                    if net_r is None:
                        continue
                    won = 1 if float(net_r) > 0 else 0
                strat = r.get("strategy") or r.get("strategy_name") or "unknown"
                by_strategy[strat].append((float(conf), int(bool(won))))
    return by_strategy


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-dir", help="dir of *.jsonl from backtest --emit-trades")
    ap.add_argument("--corpus", help="single combined corpus jsonl")
    ap.add_argument("--out-calibrators", required=True)
    ap.add_argument("--out-report", required=True)
    ap.add_argument("--method", default="auto",
                    help="auto|isotonic|platt|decile|constant")
    ap.add_argument("--min-rows", type=int, default=20,
                    help="skip strategies with fewer rows")
    args = ap.parse_args()

    by_strategy = _load_rows(args.emit_dir, args.corpus)
    if not by_strategy:
        print("no corpus rows found", flush=True)
        return 1

    calibrators: dict[str, dict] = {}
    report: dict[str, dict] = {}
    for strat, rows in sorted(by_strategy.items()):
        n = len(rows)
        xs = [c for c, _ in rows]
        ys = [w for _, w in rows]
        base_rate = sum(ys) / n if n else 0.0
        if n < args.min_rows:
            report[strat] = {"n": n, "skipped": "below_min_rows",
                             "base_rate": round(base_rate, 4)}
            continue
        cal = fit_calibrator(xs, ys, method=args.method)
        preds = cal.predict_many(xs)
        report[strat] = {
            "n": n,
            "base_rate": round(base_rate, 4),
            "method": cal.method,
            "brier_raw": round(brier_score(ys, xs), 4),
            "brier_calibrated": round(brier_score(ys, preds), 4),
            "ece_raw": round(expected_calibration_error(ys, xs), 4),
            "ece_calibrated": round(expected_calibration_error(ys, preds), 4),
            "reliability_calibrated": [
                {"mean_pred": round(b.mean_pred, 4),
                 "frac_pos": round(b.frac_pos, 4), "count": b.count}
                for b in reliability_curve(ys, preds)
            ],
        }
        calibrators[strat] = cal.to_dict()

    for path in (args.out_calibrators, args.out_report):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    with open(args.out_calibrators, "w") as fh:
        json.dump(calibrators, fh, indent=2)
    with open(args.out_report, "w") as fh:
        json.dump(report, fh, indent=2)

    print(f"fit {len(calibrators)} calibrator(s) over {len(by_strategy)} strategies")
    for strat, m in report.items():
        if "method" in m:
            print(f"  {strat}: n={m['n']} method={m['method']} "
                  f"brier {m['brier_raw']}->{m['brier_calibrated']} "
                  f"ece {m['ece_raw']}->{m['ece_calibrated']}")
        else:
            print(f"  {strat}: n={m['n']} SKIPPED ({m.get('skipped')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
