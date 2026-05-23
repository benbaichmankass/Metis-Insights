#!/usr/bin/env python3
"""Single-feature gate analysis (S-STRAT-IMPROVE-S6).

Question: does gating a strategy's entries on a single signal-time
feature lift net-of-fee OUT-OF-SAMPLE? Reads the JSONL produced by
`backtest_ict_scalp.py --emit-decisions` (each row = signal-time
feature_row + realized net_r/won/entry_time).

Method (walk-forward, deliberately parsimonious):
  - Sort by entry_time; split into train (first --train-frac) / test.
  - For each candidate feature, find the train-optimal threshold +
    direction (>= / <=) that maximizes kept-trade net on TRAIN, requiring
    a meaningful kept fraction (guards against degenerate 1-trade gates).
  - Apply that fixed rule to TEST and report TEST (OOS) net.
  - Report ALL features (not just the best) so multiple-testing is
    visible — picking the best train feature is itself an overfit risk.

Why single-feature: the emitted samples are O(hundreds). A complex model
would memorize. If no simple feature gate lifts OOS net, a complex one
won't either — and we avoid fooling ourselves (the research's DSR/PBO
spirit). A positive, consistent OOS lift from a robust feature is the
only thing worth promoting to a real gate model.

Read-only. No DB / live effects.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

NUMERIC_FEATURES = [
    "confidence", "atr", "body_to_range", "sweep_depth_atr",
    "fvg_size_norm", "displacement_idx_from_end",
]


def _load(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _net(rows: List[Dict[str, Any]]) -> float:
    return sum(float(r["net_r"]) for r in rows)


def _best_threshold(
    train: List[Dict[str, Any]], feat: str, min_keep_frac: float,
) -> Optional[Tuple[float, float, str]]:
    """Return (train_net, threshold, direction) maximizing kept net on
    train, or None. direction 'ge' keeps rows with feat>=thr, 'le' <=thr."""
    vals = sorted({float(r[feat]) for r in train if feat in r})
    if len(vals) < 2:
        return None
    min_keep = max(10, int(len(train) * min_keep_frac))
    best: Optional[Tuple[float, float, str]] = None
    for thr in vals:
        for direction in ("ge", "le"):
            kept = [r for r in train
                    if (float(r[feat]) >= thr if direction == "ge"
                        else float(r[feat]) <= thr)]
            if len(kept) < min_keep:
                continue
            ns = _net(kept)
            if best is None or ns > best[0]:
                best = (ns, thr, direction)
    return best


def _apply(rows, feat, thr, direction):
    return [r for r in rows
            if (float(r[feat]) >= thr if direction == "ge"
                else float(r[feat]) <= thr)]


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True, help="decisions JSONL path")
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--min-keep-frac", type=float, default=0.25,
                    help="min fraction of train trades a gate must keep")
    args = ap.parse_args(argv[1:])

    rows = _load(args.data)
    rows.sort(key=lambda r: str(r.get("entry_time", "")))
    n = len(rows)
    if n < 30:
        print(f"too few rows ({n}) for a walk-forward gate test", file=sys.stderr)
        return 1
    k = int(n * args.train_frac)
    train, test = rows[:k], rows[k:]
    base_test = _net(test)
    print(f"rows={n} train={len(train)} test={len(test)} "
          f"span={rows[0].get('entry_time','')[:10]}..{rows[-1].get('entry_time','')[:10]}")
    print(f"BASELINE (no gate): train_net={_net(train):+.1f} "
          f"test_net={base_test:+.1f} (test_n={len(test)})")
    print(f"{'feature':>26} {'dir':>3} {'thr':>11} {'train_net':>10} "
          f"{'TEST_net':>9} {'test_kept':>9} {'vs_base':>8}")
    for feat in NUMERIC_FEATURES:
        bt = _best_threshold(train, feat, args.min_keep_frac)
        if not bt:
            print(f"{feat:>26}   (no valid threshold)")
            continue
        tn, thr, direction = bt
        kept = _apply(test, feat, thr, direction)
        tnet = _net(kept)
        print(f"{feat:>26} {direction:>3} {thr:>11.4f} {tn:>+10.1f} "
              f"{tnet:>+9.1f} {len(kept):>9} {tnet - base_test:>+8.1f}")
    print("\nRead: a gate is promising only if its TEST_net beats BASELINE "
          "test_net (vs_base > 0) AND keeps a healthy fraction. Multiple "
          "features shown = multiple testing; treat a lone winner skeptically.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
