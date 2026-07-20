#!/usr/bin/env python3
"""M27 P0 Batch-1 data puller — multi-year Bybit linear 5m klines to CSV.

Runs ON the trainer VM (the sandbox proxy blocks exchange endpoints); uses
only stdlib + requests so it works in any venv. Paginates
/v5/market/kline backwards from now to --start, writes one CSV per symbol
(columns: timestamp,open,high,low,close,volume — the exact shape
scripts/backtest_ict_scalp.py::_load_candles reads), and prints a per-symbol
row count + date range so the relay comment is the audit record.

Usage (trainer):
  .venv/bin/python scripts/research/m27/fetch_bybit_5m.py \
      --out-dir /home/ubuntu/m27_data --start 2023-01-01 \
      --symbols ETHUSDT SOLUSDT XRPUSDT ADAUSDT AVAXUSDT
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import time
from pathlib import Path

import requests

API = "https://api.bybit.com/v5/market/kline"
INTERVAL = "5"
LIMIT = 1000
BAR_MS = 5 * 60 * 1000


def fetch_symbol(symbol: str, start_ms: int, out_path: Path) -> None:
    rows: list[tuple] = []
    end_ms = int(time.time() * 1000)
    seen_oldest = None
    while True:
        resp = requests.get(API, params={
            "category": "linear", "symbol": symbol, "interval": INTERVAL,
            "limit": LIMIT, "end": end_ms,
        }, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"{symbol}: retCode={payload.get('retCode')} "
                               f"retMsg={payload.get('retMsg')}")
        batch = ((payload.get("result") or {}).get("list") or [])
        if not batch:
            break
        # Bybit returns newest-first: [startTime, open, high, low, close, volume, turnover]
        for row in batch:
            ts = int(row[0])
            rows.append((ts, row[1], row[2], row[3], row[4], row[5]))
        oldest = int(batch[-1][0])
        if seen_oldest is not None and oldest >= seen_oldest:
            break  # no progress — venue history exhausted
        seen_oldest = oldest
        if oldest <= start_ms:
            break
        end_ms = oldest - BAR_MS
        time.sleep(0.15)  # gentle on the public endpoint

    rows = sorted({r[0]: r for r in rows}.values())
    rows = [r for r in rows if r[0] >= start_ms]
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts, o, h, lo, c, v in rows:
            iso = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc
                                            ).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow([iso, o, h, lo, c, v])

    def fmt(ms):
        if not ms:
            return "-"
        return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()

    first = rows[0][0] if rows else None
    last = rows[-1][0] if rows else None
    print(f"{symbol}: {len(rows)} bars  {fmt(first)} .. {fmt(last)}  -> {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    start_ms = int(dt.datetime.strptime(args.start, "%Y-%m-%d")
                   .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym in args.symbols:
        fetch_symbol(sym, start_ms, out_dir / f"{sym}_5m.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
