#!/usr/bin/env python3
"""Build a pretrained-TSFM embedding side-stream for a target symbol (M19 T0.1).

Tier-1 trainer-side tooling: writes the `data.jsonl` (+ a small `metadata.json`)
that `market_features` joins as-of to populate the `tsfm_emb_*` columns. Unlike
`fetch_macro.py` it fetches nothing external — it reads the target's OHLCV from an
EXISTING `market_raw` dataset already on the build host, runs a frozen
`amazon/chronos-bolt-tiny` (9M, CPU) over each (strided) bar's trailing close
window, mean-pools the encoder embedding, and projects it to a fixed width.

**Optional trainer-side deps** (NOT on the live money-box): this script needs
`chronos-forecasting` + `torch` (`pip install -r requirements-backtest.txt`). The
`market_features` build WITHOUT `embedding_path` never imports them — T0.1 heads
stay at `candidate` stage (offline only), so the dep is trainer-side only.

Run on the trainer VM:

    python -m scripts.ml.build_embeddings \
      --target datasets-out/market_raw/BTCUSDT/15m/v002 \
      --out datasets-out/embeddings/BTCUSDT/15m/v001 \
      --context-len 64 --stride 4

Then build the target's market_features WITH `embedding_path=<out>`:

    python -m ml build-dataset market_features --output-dir datasets-out \
      --version v001 --source <btc_market_raw_dir> --symbol-scope BTCUSDT \
      --timeframe 15m --overwrite market_raw_path=<btc_market_raw_dir> \
      embedding_path=datasets-out/embeddings/BTCUSDT/15m/v001 \
      vol_window_n=20 forward_window_m=5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.embedding_features import (  # noqa: E402
    DEFAULT_CONTEXT_LEN,
    DEFAULT_MIN_CONTEXT,
    DEFAULT_SEED,
    DEFAULT_STRIDE,
    EMBEDDING_DIM,
    EMBEDDING_FEATURE_COLUMNS,
    EMBEDDING_MODEL_ID,
    chronos_embed_fn,
    compute_embedding_feature_rows,
    embed_available,
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
    parts = path.resolve().parts
    return parts[-3] if len(parts) >= 3 else path.name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, type=Path,
                    help="Target symbol's market_raw dataset dir.")
    ap.add_argument("--out", required=True, type=Path, help="Output dataset dir.")
    ap.add_argument("--model-id", default=EMBEDDING_MODEL_ID)
    ap.add_argument("--context-len", type=int, default=DEFAULT_CONTEXT_LEN)
    ap.add_argument("--stride", type=int, default=DEFAULT_STRIDE)
    ap.add_argument("--out-dim", type=int, default=EMBEDDING_DIM)
    ap.add_argument("--min-context", type=int, default=DEFAULT_MIN_CONTEXT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    if not embed_available():
        ap.error(
            "TSFM embedding deps not installed. On the trainer VM run "
            "`pip install -r requirements-backtest.txt` (chronos-forecasting + torch)."
        )
    if args.out_dim != EMBEDDING_DIM:
        # The market_features schema is fixed at EMBEDDING_DIM columns; a
        # different out-dim would not merge. Guard it here rather than silently
        # producing an unmergeable side-stream.
        ap.error(
            f"--out-dim must equal EMBEDDING_DIM ({EMBEDDING_DIM}) so the "
            f"side-stream matches the market_features schema; got {args.out_dim}."
        )

    target_rows = _load_market_raw(args.target)
    target_symbol = _symbol_of(args.target, target_rows)

    rows = compute_embedding_feature_rows(
        target_rows,
        context_len=args.context_len,
        stride=args.stride,
        out_dim=args.out_dim,
        seed=args.seed,
        min_context=args.min_context,
        embed_fn=chronos_embed_fn(args.model_id),
    )

    args.out.mkdir(parents=True, exist_ok=True)
    data_path = args.out / "data.jsonl"
    with data_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    meta = {
        "family": "tsfm_embeddings",
        "target_symbol": target_symbol,
        "model_id": args.model_id,
        "feature_columns": list(EMBEDDING_FEATURE_COLUMNS),
        "context_len": args.context_len,
        "stride": args.stride,
        "out_dim": args.out_dim,
        "min_context": args.min_context,
        "seed": args.seed,
        "row_count": len(rows),
        "source": "tsfm_embeddings_from_market_raw",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (args.out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(
        {k: meta[k] for k in ("family", "target_symbol", "model_id", "row_count")},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
