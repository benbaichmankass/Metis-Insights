#!/usr/bin/env python3
"""M27 P0 per-symbol pipeline — derive frozen vol specs, run the config-exact
ict_scalp backtest with decision-time regime stamps, then k-fold OOS.

Runs ON the trainer VM from the repo root. For each symbol:

1. Derive per-symbol FROZEN vol specs from the 2023 calendar year ONLY
   (the first fold's train territory — never the full period, which would
   leak resolution-time information into the edges):
   - 5m spec: tercile edges of ``rolling_log_return_vol`` (pstdev of the
     last 20 log returns — the exact live function) over 2023 5m closes.
   - 15m spec: same on the 15m resample (the kfold ML-verdict-proxy label).
   Written to the artifact dir with provenance (window, source range).
2. Run ``scripts/backtest_ict_scalp.py`` config-exact (live YAML params,
   live-exit-faithful ``--sim-breakeven``) with ``--stamp-regime`` against
   the derived 5m spec; emit per-trade rows.
3. Run ``scripts/research/ict_scalp_phase0/kfold_oos.py`` (anchored 4-fold,
   7.5 bps round-trip fees) against the emit + 15m spec.

Usage (trainer):
  .venv/bin/python scripts/research/m27/run_symbol_p0.py \
      --csv /home/ubuntu/m27_data/ETHUSDT_5m.csv --symbol ETHUSDT \
      --out-dir /home/ubuntu/m27_out
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

VOL_WINDOW = 20
DERIVE_YEAR = "2023"


def derive_spec(closes: pd.Series, *, symbol: str, timeframe: str,
                source_range: str, window_desc: str) -> dict:
    rv = np.log(closes.astype(float)).diff().rolling(VOL_WINDOW).std(ddof=0)
    rv = rv.dropna()
    q33, q67 = float(rv.quantile(1 / 3)), float(rv.quantile(2 / 3))
    return {
        "model_id": f"m27-derived-{symbol.lower()}-{timeframe}-frozen",
        "vol_bucket_labels": ["low", "mid", "high"],
        "vol_bucket_edges": [q33, q67],
        "vol_window_n": VOL_WINDOW,
        "derived_from": f"{window_desc} ({source_range}); "
                        "tercile edges of rolling_log_return_vol (pstdev, w=20). "
                        "Frozen BEFORE any fold boundary — no resolution-time leak.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--fee-bps-roundtrip", type=float, default=7.5)
    # Futures cost mode (Batch-2): flat USD round-trip per contract charged
    # against dollar risk — passed through to kfold_oos.py. When set,
    # --contract-value-usd is required and the bps mode is not used.
    ap.add_argument("--fee-usd-roundtrip", type=float, default=None)
    ap.add_argument("--contract-value-usd", type=float, default=None)
    # Vol-spec derivation window. The crypto batch froze edges on the 2023
    # calendar year (pre-fold-boundary). The IBKR pulls only reach ~1y back,
    # so futures derive from the earliest PREFIX of the data instead — still
    # strictly inside the first fold's train territory for a 4-fold walk.
    ap.add_argument("--derive-window", default=f"year:{DERIVE_YEAR}",
                    help="year:<YYYY> (default) or prefix:<fraction 0..0.25+>")
    args = ap.parse_args()

    if args.fee_usd_roundtrip is not None and args.contract_value_usd is None:
        ap.error("--fee-usd-roundtrip requires --contract-value-usd")

    out = Path(args.out_dir) / args.symbol
    out.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.csv)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    mode, _, val = args.derive_window.partition(":")
    if mode == "year":
        d23 = df[df["timestamp"].dt.year == int(val)]
        window_desc = f"{val} calendar year only"
    elif mode == "prefix":
        frac = float(val)
        if not 0.0 < frac <= 0.25:
            print(f"FAIL: prefix fraction {frac} outside (0, 0.25] — must stay "
                  "inside the first fold's train territory")
            return 1
        d23 = df.iloc[: max(1, int(len(df) * frac))]
        window_desc = f"earliest {frac:.0%} prefix of the data only"
    else:
        print(f"FAIL: unknown --derive-window {args.derive_window!r}")
        return 1
    if len(d23) < 10_000:
        print(f"FAIL: only {len(d23)} derivation bars ({args.derive_window}) "
              f"in {args.csv}")
        return 1
    rng = f"{d23['timestamp'].iloc[0]} .. {d23['timestamp'].iloc[-1]}"

    spec5 = derive_spec(d23["close"], symbol=args.symbol, timeframe="5m",
                        source_range=rng, window_desc=window_desc)
    c15 = (d23.set_index("timestamp")["close"].resample("15min").last().dropna())
    spec15 = derive_spec(c15, symbol=args.symbol, timeframe="15m",
                         source_range=rng, window_desc=window_desc)
    p5 = out / "volspec_5m.json"
    p15 = out / "volspec_15m.json"
    p5.write_text(json.dumps(spec5, indent=2))
    p15.write_text(json.dumps(spec15, indent=2))
    print(f"{args.symbol}: 5m edges={spec5['vol_bucket_edges']} "
          f"15m edges={spec15['vol_bucket_edges']}")

    py = sys.executable
    emit = out / "emit.json"
    summary = out / "backtest.json"
    cmd_bt = [
        py, str(_REPO_ROOT / "scripts/backtest_ict_scalp.py"),
        "--data", args.csv, "--symbol", args.symbol,
        "--stamp-regime", "--vol-spec-json", str(p5),
        "--sim-breakeven",
        "--emit-trades", str(emit), "--json", str(summary),
    ]
    print("RUN:", " ".join(cmd_bt), flush=True)
    subprocess.run(cmd_bt, check=True, cwd=_REPO_ROOT)

    kfold_out = out / "kfold.json"
    cmd_kf = [
        py, str(_REPO_ROOT / "scripts/research/ict_scalp_phase0/kfold_oos.py"),
        "--emit", str(emit), "--data", args.csv,
        "--volspec-15m", str(p15),
        "--folds", str(args.folds),
        "--out", str(kfold_out),
    ]
    if args.fee_usd_roundtrip is not None:
        cmd_kf += ["--fee-usd-roundtrip", str(args.fee_usd_roundtrip),
                   "--contract-value-usd", str(args.contract_value_usd)]
    else:
        cmd_kf += ["--fee-bps-roundtrip", str(args.fee_bps_roundtrip)]
    print("RUN:", " ".join(cmd_kf), flush=True)
    subprocess.run(cmd_kf, check=True, cwd=_REPO_ROOT)

    print(f"{args.symbol}: DONE -> {summary} + {kfold_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
