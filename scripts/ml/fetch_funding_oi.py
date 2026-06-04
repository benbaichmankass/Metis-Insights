#!/usr/bin/env python3
"""Fetch a Bybit funding-rate + open-interest side-stream (S-MLOPT-S11).

Tier-1 trainer-side tooling: writes the `data.jsonl` (+ a small `metadata.json`)
that `market_features` joins as-of to compute the funding/OI feature columns
(funding z-score / extremes + open-interest change). Never touches a live-path
file; off-VM guarded (refuses unless `ICT_OFFVM_BUILD_HOST=1`).

Run on the trainer VM (or any build host that is NOT the live VM):

    export ICT_OFFVM_BUILD_HOST=1
    python -m scripts.ml.fetch_funding_oi \
      --symbol BTCUSDT --start 2021-06-01 --end 2026-06-04 \
      --oi-interval 1h --out datasets-out/funding_oi/BTCUSDT/v001

Then build market_features with `funding_oi_path=<out>`:

    python -m ml build-dataset market_features --output-dir datasets-out \
      --version v002 --source <market_raw_dir> --symbol-scope BTCUSDT \
      --timeframe 1h --overwrite market_raw_path=<market_raw_dir> \
      funding_oi_path=datasets-out/funding_oi/BTCUSDT/v001 funding_window_n=168 \
      vol_window_n=20 forward_window_m=5 vol_threshold=0.005 n_vol_buckets=3
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.adapters.bybit_funding_oi import fetch_funding_oi_rows  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", required=True, help="ISO date/datetime (UTC).")
    ap.add_argument("--end", required=True, help="ISO date/datetime (UTC).")
    ap.add_argument("--oi-interval", default="1h", help="5m/15m/30m/1h/4h/1d")
    ap.add_argument("--out", required=True, type=Path, help="Output dataset dir.")
    args = ap.parse_args(argv)

    rows = fetch_funding_oi_rows(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        oi_interval=args.oi_interval,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    data_path = args.out / "data.jsonl"
    with data_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    n_funding = sum(1 for r in rows if r.get("funding_rate") is not None)
    n_oi = sum(1 for r in rows if r.get("open_interest") is not None)
    meta = {
        "family": "funding_oi_raw",
        "symbol": args.symbol,
        "oi_interval": args.oi_interval,
        "start": args.start,
        "end": args.end,
        "row_count": len(rows),
        "funding_rows": n_funding,
        "open_interest_rows": n_oi,
        "source": "bybit_v5_offvm",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (args.out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
