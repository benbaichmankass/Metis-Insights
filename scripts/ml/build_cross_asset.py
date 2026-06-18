#!/usr/bin/env python3
"""Build a cross-asset (peer-asset) side-stream for a target symbol.

Tier-1 trainer-side tooling (S-CROSS-ASSET-PROBE, 2026-06-18): writes the
`data.jsonl` (+ a small `metadata.json`) that `market_features` joins as-of to
compute the peer-asset conditioning columns (`xa_peer{1,2}_*` + `xa_breadth_up`)
for the target. Unlike `fetch_macro.py` it fetches nothing external — it reads
peer/target OHLCV from EXISTING `market_raw` datasets already on the build host,
so it needs no off-VM guard.

Run on the trainer VM (peers must be the SAME timeframe/grid as the target):

    python -m scripts.ml.build_cross_asset \
      --target datasets-out/market_raw/ETHUSDT/1h/v002 \
      --peer datasets-out/market_raw/BTCUSDT/1h/v002 \
      --peer datasets-out/market_raw/SOLUSDT/1h/v002 \
      --out datasets-out/cross_asset/ETHUSDT/1h/v001

Then build the target's market_features WITH `cross_asset_path=<out>`:

    python -m ml build-dataset market_features --output-dir datasets-out \
      --version v001 --source <eth_market_raw_dir> --symbol-scope ETHUSDT \
      --timeframe 1h --overwrite market_raw_path=<eth_market_raw_dir> \
      cross_asset_path=datasets-out/cross_asset/ETHUSDT/1h/v001 \
      vol_window_n=20 forward_window_m=5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.cross_asset_features import (  # noqa: E402
    CROSS_ASSET_FEATURE_COLUMNS,
    N_PEER_SLOTS,
    compute_cross_asset_feature_rows,
)


def _load_market_raw(path: Path) -> list[dict]:
    data_path = path / "data.jsonl"
    if not data_path.is_file():
        raise FileNotFoundError(f"market_raw data.jsonl not found at {data_path}")
    rows: list[dict] = []
    with data_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                rows.append(json.loads(line))
    return rows


def _symbol_of(path: Path, rows: list[dict]) -> str:
    for r in rows:
        if r.get("symbol"):
            return str(r["symbol"])
    # Fall back to the dir layout `.../market_raw/<SYMBOL>/<tf>/<ver>`.
    parts = path.resolve().parts
    return parts[-3] if len(parts) >= 3 else path.name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, type=Path,
                    help="Target symbol's market_raw dataset dir.")
    ap.add_argument("--peer", action="append", default=[], type=Path,
                    help=f"Peer market_raw dataset dir (up to {N_PEER_SLOTS}; repeatable).")
    ap.add_argument("--out", required=True, type=Path, help="Output dataset dir.")
    ap.add_argument("--vol-window-n", type=int, default=20)
    ap.add_argument("--beta-window-n", type=int, default=50)
    args = ap.parse_args(argv)

    if not args.peer:
        ap.error("at least one --peer is required")
    if len(args.peer) > N_PEER_SLOTS:
        print(f"warning: {len(args.peer)} peers given; only the first "
              f"{N_PEER_SLOTS} occupy slots, the rest are ignored", file=sys.stderr)

    target_rows = _load_market_raw(args.target)
    target_symbol = _symbol_of(args.target, target_rows)
    peer_rows_by_slot: list[list[dict]] = []
    slot_map: dict[str, str] = {}
    for slot, peer_path in enumerate(args.peer[:N_PEER_SLOTS], start=1):
        peer_rows = _load_market_raw(peer_path)
        peer_rows_by_slot.append(peer_rows)
        slot_map[f"peer{slot}"] = _symbol_of(peer_path, peer_rows)

    rows = compute_cross_asset_feature_rows(
        target_rows,
        peer_rows_by_slot,
        vol_window_n=args.vol_window_n,
        beta_window_n=args.beta_window_n,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    data_path = args.out / "data.jsonl"
    with data_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    meta = {
        "family": "cross_asset_raw",
        "target_symbol": target_symbol,
        "peer_slots": slot_map,
        "feature_columns": list(CROSS_ASSET_FEATURE_COLUMNS),
        "vol_window_n": args.vol_window_n,
        "beta_window_n": args.beta_window_n,
        "row_count": len(rows),
        "source": "cross_asset_from_market_raw",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (args.out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(
        {k: meta[k] for k in ("family", "target_symbol", "peer_slots", "row_count")},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
