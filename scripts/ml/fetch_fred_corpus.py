#!/usr/bin/env python3
"""Fetch the keyless FRED wide-corpus panel into the standing corpus store (M19 C2).

Tier-1 trainer-side tooling: pulls the multi-group daily context series
(`ml.datasets.adapters.fred_corpus.CORPUS_SERIES` — equity / commodity / credit /
rates) from FRED's keyless endpoint and registers each into the corpus store
(`ml.datasets.corpus_store`) — the panel the label-free encoder reads. The second
free adapter after `fetch_fred_macro`, writing into the SAME store.

Never touches a live-path file; off-VM guarded (`ICT_OFFVM_BUILD_HOST=1`);
read-mostly, never `trade_journal.db`; no API key, no spend.

Run on the trainer VM (or any build host that is NOT the live VM):

    export ICT_OFFVM_BUILD_HOST=1
    python -m scripts.ml.fetch_fred_corpus \
      --start 2010-01-01 --end 2026-07-02 \
      --corpus-root runtime_logs/trainer_mirror/corpus

Design: docs/research/T0-data-corpus-DESIGN.md § C2.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.adapters.fred_corpus import (  # noqa: E402
    CORPUS_SERIES,
    fetch_fred_corpus_series,
)
from ml.datasets.corpus_store import write_series  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="ISO date/datetime (UTC).")
    ap.add_argument("--end", required=True, help="ISO date/datetime (UTC).")
    ap.add_argument(
        "--corpus-root", type=Path, default=None,
        help="Corpus store root (defaults to $CORPUS_ROOT → the trainer-mirror corpus dir).",
    )
    args = ap.parse_args(argv)

    refreshed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    panel = fetch_fred_corpus_series(start=args.start, end=args.end)
    skipped = panel.pop("_skipped", {})  # discontinued/failed ids — not series

    registered: list[dict] = []
    for fred_id, block in panel.items():
        entry = write_series(
            series_id=f"fred_{block['name']}",
            group=block["group"],
            source="fred",
            source_ref=fred_id,
            rows=block["rows"],
            refreshed_at=refreshed_at,
            root=args.corpus_root,
        )
        registered.append({"series": f"fred_{block['name']}", "group": block["group"], "rows": entry["row_count"]})

    summary = {
        "source": "fred_corpus_offvm",
        "series_registered": len(registered),
        "series_skipped": len(skipped),
        "total_rows": sum(r["rows"] for r in registered),
        "start": args.start,
        "end": args.end,
        "registered": registered,
        "skipped": skipped,
        "default_series": {v[0]: k for k, v in CORPUS_SERIES.items()},
    }
    print(json.dumps(
        {k: summary[k] for k in ("source", "series_registered", "series_skipped", "total_rows", "start", "end", "skipped")},
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
