#!/usr/bin/env python3
"""Fetch a cross-asset / macro side-stream for MES (S-MLOPT-S12, Phase 2.4).

Tier-1 trainer-side tooling: writes the `data.jsonl` (+ a small `metadata.json`)
that `market_features` joins as-of to compute the macro conditioning columns for
MES (VIX level/z + term-structure slope, DXY z/return, 10y level + 3m-10y slope).
Never touches a live-path file; off-VM guarded (refuses unless
`ICT_OFFVM_BUILD_HOST=1`).

Run on the trainer VM (or any build host that is NOT the live VM):

    export ICT_OFFVM_BUILD_HOST=1
    python -m scripts.ml.fetch_macro \
      --start 2015-01-01 --end 2026-06-04 \
      --out datasets-out/macro/MES/v001

Then build MES market_features with `macro_path=<out>`:

    python -m ml build-dataset market_features --output-dir datasets-out \
      --version v002 --source <mes_market_raw_dir> --symbol-scope MES \
      --timeframe 5m --overwrite market_raw_path=<mes_market_raw_dir> \
      macro_path=datasets-out/macro/MES/v001 vol_window_n=20 forward_window_m=5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.adapters.yfinance_macro import (  # noqa: E402
    DEFAULT_TICKERS,
    fetch_macro_rows,
)
from ml.datasets.macro_features import MACRO_FEATURE_COLUMNS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="ISO date/datetime (UTC).")
    ap.add_argument("--end", required=True, help="ISO date/datetime (UTC).")
    ap.add_argument("--out", required=True, type=Path, help="Output dataset dir.")
    ap.add_argument("--zscore-window-n", type=int, default=20)
    ap.add_argument("--return-window-n", type=int, default=5)
    args = ap.parse_args(argv)

    rows = fetch_macro_rows(
        start=args.start,
        end=args.end,
        zscore_window_n=args.zscore_window_n,
        return_window_n=args.return_window_n,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    data_path = args.out / "data.jsonl"
    with data_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    meta = {
        "family": "macro_raw",
        "tickers": dict(DEFAULT_TICKERS),
        "feature_columns": list(MACRO_FEATURE_COLUMNS),
        "start": args.start,
        "end": args.end,
        "zscore_window_n": args.zscore_window_n,
        "return_window_n": args.return_window_n,
        "row_count": len(rows),
        "source": "yfinance_macro_offvm",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (args.out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("family", "row_count", "start", "end")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
