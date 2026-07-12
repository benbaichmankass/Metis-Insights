#!/usr/bin/env python3
"""Convert a datasets-out/market_raw/<SYM>/<interval>/*/data.jsonl candle
side-stream into the OHLCV CSV shape the standalone backtest harnesses read
(timestamp,open,high,low,close,volume). Stdlib-only; trainer-side utility
(M20). Usage: market_raw_to_csv.py SYMBOL DATASETS_ROOT OUT_CSV [INTERVAL]"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    sym, root, out = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    interval = sys.argv[4] if len(sys.argv) > 4 else "15m"
    d = root / "market_raw" / sym / interval
    cands = sorted(d.glob("*/data.jsonl"))
    if not cands:
        print(f"no data.jsonl under {d}", file=sys.stderr)
        return 1
    rows = []
    for line in cands[-1].open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = r.get("ts") or r.get("time") or r.get("timestamp")
        try:
            if str(ts).replace(".", "").isdigit():
                ts = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            o, h, lo, c = (float(r.get("open", r.get("close"))), float(r["high"]),
                           float(r["low"]), float(r["close"]))
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((ts, o, h, lo, c, float(r.get("volume") or 0.0)))
    rows.sort(key=lambda x: x[0])
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"{sym}: wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
