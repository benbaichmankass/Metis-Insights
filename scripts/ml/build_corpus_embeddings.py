#!/usr/bin/env python3
"""Build an SSL corpus-encoder embedding side-stream for market_features (M19 T1.2 P2).

Tier-1 trainer-side tooling: writes the `data.jsonl` (+ a small `metadata.json`)
that `market_features` joins as-of to populate the `corpus_emb_*` columns. The
sibling of `build_embeddings.py` (T0.1), but sourced from the in-house
masked-reconstruction **corpus encoder** instead of a frozen Chronos TSFM. It
reads the daily `corpus_panel` dataset already on the build host, runs the trained
encoder (its exported ONNX graph, loaded from a `--model-state` JSON) over each
day's trailing panel window, and re-keys the per-day embedding one-day-lagged onto
a `ts`-keyed side-stream `market_features` carries onto its bars.

**Optional trainer-side deps** (NOT on the live money-box): this script needs
`onnxruntime` + `numpy` (the encoder serve path). The `market_features` build
WITHOUT `corpus_embedding_path` never imports them — T1.2 heads stay at
`candidate` stage (offline only), so the dep is trainer-side only. The heavy
encoder import is lazy (via `predictor_embed_fn`), so this module imports cleanly
on an onnxruntime-/torch-free environment.

Run on the trainer VM:

    python -m scripts.ml.build_corpus_embeddings \
      --panel datasets-out/corpus_panel/all/daily/v001 \
      --model-state ml/experiments-runs/corpus-ssl-encoder-mae-v1/<run>/model_state.json \
      --out datasets-out/corpus_embeddings/all/daily/v001

Then build the target's market_features WITH `corpus_embedding_path=<out>`:

    python -m ml build-dataset market_features --output-dir datasets-out \
      --version v001 --source <btc_market_raw_dir> --symbol-scope BTCUSDT \
      --timeframe 15m --overwrite market_raw_path=<btc_market_raw_dir> \
      corpus_embedding_path=datasets-out/corpus_embeddings/all/daily/v001 \
      vol_window_n=20 forward_window_m=5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.corpus_embedding_features import (  # noqa: E402
    CORPUS_EMBEDDING_DIM,
    DEFAULT_LAG_DAYS,
    DEFAULT_SEQ_LEN,
    compute_corpus_embedding_rows,
    corpus_embedding_columns,
    corpus_embedding_sidestream,
    predictor_embed_fn,
)


def _load_corpus_panel(path: Path) -> list[dict]:
    data_path = path / "data.jsonl"
    if not data_path.is_file():
        raise FileNotFoundError(
            f"corpus_panel data.jsonl not found at {data_path}; "
            "build a corpus_panel dataset first via "
            "`python -m ml build-dataset corpus_panel ...`"
        )
    rows: list[dict] = []
    with data_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                rows.append(json.loads(line))
    return rows


def _load_model_state(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"model_state json not found at {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", required=True, type=Path,
                    help="Daily corpus_panel dataset dir (holds data.jsonl).")
    ap.add_argument("--model-state", required=True, type=Path,
                    help="Trained SSL corpus-encoder model_state.json "
                         "(the experiment-run model_state.json).")
    ap.add_argument("--out", required=True, type=Path, help="Output dataset dir.")
    ap.add_argument("--lag-days", type=int, default=DEFAULT_LAG_DAYS,
                    help="Calendar lag applied when re-keying each day-embedding "
                         "onto the bar grid (belt-and-suspenders on the panel's "
                         "own one-day-lag).")
    args = ap.parse_args(argv)

    model_state = _load_model_state(args.model_state)
    # The encoder's own window length + embedding width — read from the trained
    # state, never hardcoded (the market_features schema is fixed at
    # CORPUS_EMBEDDING_DIM columns, so guard a mismatched width here rather than
    # silently producing an unmergeable side-stream).
    seq_len = int(model_state.get("seq_len", DEFAULT_SEQ_LEN))
    out_dim = int(model_state.get("embedding_dim", CORPUS_EMBEDDING_DIM))
    if out_dim != CORPUS_EMBEDDING_DIM:
        ap.error(
            f"model_state embedding_dim must equal CORPUS_EMBEDDING_DIM "
            f"({CORPUS_EMBEDDING_DIM}) so the side-stream matches the "
            f"market_features schema; got {out_dim}."
        )

    panel_rows = _load_corpus_panel(args.panel)

    embed_fn = predictor_embed_fn(model_state)
    emb_rows = compute_corpus_embedding_rows(
        panel_rows,
        embed_fn=embed_fn,
        seq_len=seq_len,
        out_dim=out_dim,
    )
    sidestream = corpus_embedding_sidestream(emb_rows, lag_days=args.lag_days)

    args.out.mkdir(parents=True, exist_ok=True)
    data_path = args.out / "data.jsonl"
    with data_path.open("w", encoding="utf-8") as fh:
        for r in sidestream:
            fh.write(json.dumps(r) + "\n")

    meta = {
        "family": "corpus_embeddings",
        "model_state_path": str(args.model_state),
        "feature_columns": list(corpus_embedding_columns(out_dim)),
        "seq_len": seq_len,
        "embedding_dim": out_dim,
        "lag_days": args.lag_days,
        "panel_row_count": len(panel_rows),
        "row_count": len(sidestream),
        "source": "corpus_embeddings_from_corpus_panel",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (args.out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(
        {k: meta[k] for k in ("family", "seq_len", "embedding_dim", "row_count")},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
