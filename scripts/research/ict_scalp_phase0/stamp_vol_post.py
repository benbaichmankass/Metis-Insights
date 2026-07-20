#!/usr/bin/env python3
"""Post-stamp vol_regime onto a backtest --emit-trades JSONL.

For emit files produced before the frozen vol spec was available (the
harness run stamps only the ADX trend axis without --vol-spec-json), this
fills meta.vol_regime / meta.rolling_log_return_vol per trade using the
SAME `vol_regime_from_spec` the live stamp calls, over the closes ending
at the trade's entry bar (the live builder's 200-bar fetch window).
Never recomputes edges from backtest data — the spec is the frozen
registry spec passed in via --vol-spec-json.

Usage:
  python scripts/research/ict_scalp_phase0/stamp_vol_post.py \
      --emit runA.jsonl --data btc_5m.csv --vol-spec-json spec.json --out runA_v.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.regime.vol_detector import vol_regime_from_spec  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--vol-spec-json", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv[1:])

    spec = json.loads(Path(args.vol_spec_json).read_text())
    df = pd.read_csv(args.data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    ts_index = {t: i for i, t in enumerate(df["timestamp"])}
    closes = df["close"].astype(float).tolist()

    n_stamped = 0
    out_lines = []
    for line in Path(args.emit).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        meta = row.get("meta") or {}
        t = pd.to_datetime(row["entry_time"], utc=True)
        i = ts_index.get(t)
        if i is not None:
            window = closes[max(0, i + 1 - 200): i + 1]
            vol_regime, vol = vol_regime_from_spec(spec, window)
            meta["vol_regime"] = vol_regime
            meta["rolling_log_return_vol"] = round(vol, 8) if vol is not None else None
            meta["vol_regime_source"] = f"vol-bucket-edges:{spec.get('model_id')}(post-stamp)"
            n_stamped += 1
        row["meta"] = meta
        out_lines.append(json.dumps(row, default=str))
    Path(args.out).write_text("\n".join(out_lines) + "\n")
    print(f"stamped {n_stamped}/{len(out_lines)} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
