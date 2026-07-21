#!/usr/bin/env python3
"""M27 Batch-2 — convert an IBKR market_raw shard (data.jsonl) to the CSV
shape ``scripts/backtest_ict_scalp.py::_load_candles`` reads.

Shard rows are the ibkr_offvm adapter's ``{"ts": ISO, "open": .., "high": ..,
"low": .., "close": .., "volume": ..}`` JSONL (deduped canonical stream).
Output columns: timestamp,open,high,low,close,volume with an explicit
``+00:00`` offset (the tz-aware contract PR #7199 established — the HTF path
parses ``utc=True`` and merge_asof dies on aware-vs-naive).

Usage (trainer):
  .venv/bin/python scripts/research/m27/ibkr_jsonl_to_csv.py \
      --jsonl /home/ubuntu/trainer-data/ibkr_datasets/market_raw/MGC/5m/v002/data.jsonl \
      --out /home/ubuntu/m27_data/MGC_5m.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def _iso_utc(raw: str) -> str:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows: dict[str, tuple] = {}
    bad = 0
    with open(args.jsonl) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                b = json.loads(line)
                ts = _iso_utc(b["ts"])
                rows[ts] = (ts, float(b["open"]), float(b["high"]),
                            float(b["low"]), float(b["close"]),
                            float(b.get("volume") or 0.0))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                bad += 1

    ordered = [rows[k] for k in sorted(rows)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(ordered)

    first = ordered[0][0] if ordered else "-"
    last = ordered[-1][0] if ordered else "-"
    print(f"{args.jsonl}: {len(ordered)} bars ({bad} unparseable skipped)  "
          f"{first} .. {last} -> {out}")
    return 0 if ordered else 1


if __name__ == "__main__":
    raise SystemExit(main())
