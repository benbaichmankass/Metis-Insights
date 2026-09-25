#!/usr/bin/env python3
"""Build the confidence-calibration corpus by running the per-strategy backtest
harnesses with ``--emit-trades`` (design doc § 4a, step 1 of the P0 experiments).

Each harness calls (or mirrors) the live strategy's signal builder and emits one
JSONL row per trade carrying ``confidence`` + realized ``net_r`` — exactly the
``(confidence, won)`` pairs the calibrators fit on. Output JSONL files land in
``--out-dir`` (one per strategy), ready for ``fit_confidence_calibrators.py``.

This is the reproducible corpus step. Point ``--data`` at the full validated
history (the sample ``data/backtest_candles.csv`` is only enough to smoke-test
the pipeline; production calibration needs the long history per instrument).

Usage:
    python3 scripts/ml/build_calibration_corpus.py --data data/backtest_candles.csv \
        --out-dir artifacts/calibration/corpus
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# (slug, script) — each script already supports --data/--emit-trades.
# ict_scalp calls the LIVE order_package() (best fidelity); the others mirror it.
HARNESSES: list[tuple[str, str]] = [
    ("trend_donchian", "scripts/backtest_trend.py"),
    ("fade_breakout_4h", "scripts/backtest_fade.py"),
    ("squeeze_breakout_4h", "scripts/backtest_squeeze.py"),
    ("htf_pullback_trend_2h", "scripts/backtest_pullback.py"),
    ("fvg_range_15m", "scripts/backtest_fvg_range.py"),
    ("ict_scalp_5m", "scripts/backtest_ict_scalp.py"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/backtest_candles.csv")
    ap.add_argument("--out-dir", default="artifacts/calibration/corpus")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--extra", default="",
                    help="extra args passed verbatim to every harness")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    extra = args.extra.split() if args.extra else []

    rc = 0
    for slug, script in HARNESSES:
        emit = out / f"{slug}.jsonl"
        cmd = [sys.executable, str(_REPO / script),
               "--data", args.data, "--emit-trades", str(emit), *extra]
        # ict_scalp/symbol-aware harnesses accept --symbol; pass best-effort.
        proc = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
        n = sum(1 for _ in open(emit)) if emit.exists() else 0
        status = "ok" if proc.returncode == 0 else f"rc={proc.returncode}"
        print(f"  {slug:24s} {status:8s} trades={n}")
        if proc.returncode != 0:
            rc = 1
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            for ln in tail:
                print(f"      {ln}")
    print(f"corpus written to {out}/")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
