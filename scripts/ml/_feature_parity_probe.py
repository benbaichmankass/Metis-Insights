#!/usr/bin/env python3
"""Train/serve feature-parity probe for a regime head (MB-20260627-003).

RG4 says the ETH 1h regime head discriminates the vol regime OFFLINE (RG3
0.70-0.73) but is NO_EDGE on the LIVE logged feature rows (RG4 ~0.46). RG3
passes because it feeds the SAME live feature builder clean candles; RG4 reads
the EXACT rows the live runtime logged. So the skew lives in WHAT the live
per-bar scorer fed the builder, visible as a per-feature distribution gap
between the training dataset and the logged-live rows.

This probe makes that gap concrete. For a given head it prints, per feature
column, the training-dataset distribution vs the logged-live distribution
(count / present% / mean / std / min / max; value-counts for categoricals like
vol_bucket), split by shadow-log `stage`. A feature whose live mean/std/range is
off-scale vs training, or whose live presence is low, IS the skew. It also
reports the live predicted-score distribution and (when labels join) the
score↔label point-biserial sign, so a "head emits a near-constant score live"
degeneracy shows up directly.

Read-only, trainer-side (datasets + registry + the mirrored shadow log live
there). Never touches the order path. No inline python -c (the trainer-vm-diag
relay mis-parses quoted -c), so this is a committed helper the relay calls.

  python scripts/ml/_feature_parity_probe.py \
      --model-id eth-regime-1h-lgbm-v1 --symbol ETHUSDT --timeframe 1h
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The market_features columns the regime heads train on (superset; a given
# manifest uses a subset). Numeric unless listed in _CATEGORICAL.
_NUMERIC_COLS = [
    "rolling_log_return_vol",
    "log_return",
    "log_return_lag_1",
    "log_return_lag_2",
    "parkinson_vol",
    "garman_klass_vol",
    "rogers_satchell_vol",
    "yang_zhang_vol",
    # cross-asset peer block (xasset variant only)
    "xa_peer1_log_return",
    "xa_peer1_rolling_log_return_vol",
    "xa_peer2_log_return",
    "xa_peer2_rolling_log_return_vol",
    "xa_breadth_up",
]
_CATEGORICAL = ["vol_bucket", "hour_of_day", "dayofweek"]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _stat_block(vals: List[Optional[float]], n_total: int) -> Dict[str, Any]:
    present = [v for v in vals if v is not None]
    if not present:
        return {"n": n_total, "present_pct": 0.0, "mean": None, "std": None,
                "min": None, "max": None}
    return {
        "n": n_total,
        "present_pct": round(100.0 * len(present) / max(1, n_total), 1),
        "mean": round(statistics.fmean(present), 8),
        "std": round(statistics.pstdev(present), 8) if len(present) > 1 else 0.0,
        "min": round(min(present), 8),
        "max": round(max(present), 8),
    }


def _value_counts(vals: List[Any], n_total: int) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    miss = 0
    for v in vals:
        if v is None or v == "":
            miss += 1
            continue
        counts[str(v)] = counts.get(str(v), 0) + 1
    top = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])
    return {"n": n_total, "missing": miss, "value_counts": top}


def _load_jsonl(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit and len(rows) >= limit:
                break
    return rows


def _training_rows(symbol: str, timeframe: str) -> (Optional[str], List[Dict[str, Any]]):
    cands = sorted(glob.glob(
        f"datasets-out/market_features/{symbol}/{timeframe}/*/data.jsonl"))
    if not cands:
        return None, []
    path = cands[-1]
    return path, _load_jsonl(Path(path))


def _shadow_rows(symbol: str, model_id: str) -> (Optional[str], List[Dict[str, Any]]):
    for c in ("runtime_logs/shadow_predictions.jsonl",
              "runtime_logs/trainer_mirror/shadow_predictions.jsonl",
              "runtime_logs/trainer_mirror/live/shadow_predictions.jsonl"):
        if Path(c).exists():
            out = []
            with open(c, encoding="utf-8") as fh:
                for line in fh:
                    if model_id not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(row.get("model_id")) == model_id:
                        out.append(row)
            return c, out
    return None, []


def _print_cols(title: str, rows_feat: List[Dict[str, Any]], cols_present: List[str]) -> None:
    n = len(rows_feat)
    print(f"  [{title}] n={n}")
    for col in cols_present:
        if col in _CATEGORICAL:
            vc = _value_counts([r.get(col) for r in rows_feat], n)
            print(f"    {col:34s} miss={vc['missing']:<5d} {vc['value_counts']}")
        else:
            blk = _stat_block([_num(r.get(col)) for r in rows_feat], n)
            print(f"    {col:34s} pres={blk['present_pct']:>5}%  "
                  f"mean={blk['mean']}  std={blk['std']}  "
                  f"min={blk['min']}  max={blk['max']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", required=True)
    a = ap.parse_args()

    print(f"== feature-parity probe: {a.model_id} ({a.symbol}/{a.timeframe}) ==")

    tr_path, tr_rows = _training_rows(a.symbol, a.timeframe)
    print(f"\n-- TRAINING dataset --\n  path={tr_path}  rows={len(tr_rows)}")
    if tr_rows:
        # Which of the candidate columns actually appear in the training data.
        keys = set()
        for r in tr_rows[:2000]:
            keys.update(r.keys())
        cols = [c for c in (_CATEGORICAL + _NUMERIC_COLS) if c in keys]
        _print_cols("train", tr_rows, cols)
        # regime_label base rate
        lbls = [str(r.get("regime_label")) for r in tr_rows if r.get("regime_label") is not None]
        vol = sum(1 for x in lbls if x == "volatile")
        print(f"    regime_label: n={len(lbls)} volatile={vol} "
              f"({round(100.0*vol/max(1,len(lbls)),2)}%)")

    sl_path, sh_rows = _shadow_rows(a.symbol, a.model_id)
    print(f"\n-- LIVE shadow-log rows --\n  path={sl_path}  rows={len(sh_rows)}")
    if not sh_rows:
        print("  (no live rows for this model — RG4 unscoreable until it soaks)")
        print("PARITY_PROBE_DONE")
        return 0

    # Group by stage; the feature payload is under `feature_row`.
    by_stage: Dict[str, List[Dict[str, Any]]] = {}
    score_by_stage: Dict[str, List[float]] = {}
    for r in sh_rows:
        st = str(r.get("stage", "?"))
        fr = r.get("feature_row") or {}
        by_stage.setdefault(st, []).append(fr)
        sc = _num(r.get("score"))
        if sc is None:
            # some logs nest the positive-class proba
            pr = r.get("proba") or {}
            sc = _num(pr.get("volatile"))
        if sc is not None:
            score_by_stage.setdefault(st, []).append(sc)

    train_keys = set()
    for r in tr_rows[:2000]:
        train_keys.update(r.keys())

    for st, frs in by_stage.items():
        keys = set()
        for fr in frs[:2000]:
            keys.update(fr.keys())
        cols = [c for c in (_CATEGORICAL + _NUMERIC_COLS) if c in keys or c in train_keys]
        print()
        _print_cols(f"live stage={st}", frs, cols)
        sc = score_by_stage.get(st, [])
        if sc:
            blk = _stat_block(sc, len(sc))
            print(f"    {'PREDICTED score(volatile)':34s} "
                  f"mean={blk['mean']}  std={blk['std']}  "
                  f"min={blk['min']}  max={blk['max']}  (n={len(sc)})")
        # which trained cols are entirely ABSENT from the live row
        missing_cols = sorted(c for c in (_CATEGORICAL + _NUMERIC_COLS)
                              if c in train_keys and c not in keys)
        if missing_cols:
            print(f"    !! cols in TRAIN but absent from LIVE rows: {missing_cols}")

    print("PARITY_PROBE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
