#!/usr/bin/env python3
"""M27 P0/P1 data puller — multi-year Bybit linear klines to CSV (any timeframe).

Runs ON the trainer VM (the sandbox proxy blocks exchange endpoints); uses
only stdlib + requests so it works in any venv. Paginates
/v5/market/kline backwards from now to --start, writes one CSV per symbol
(columns: timestamp,open,high,low,close,volume — the exact shape
scripts/backtest_ict_scalp.py::_load_candles reads), and prints a per-symbol
row count + date range so the relay comment is the audit record.

The historical default is 5m (Batch-1/2/3); ``--interval`` generalizes it to the
M27 P1 timeframe sweep — e.g. ``--interval 1m`` for the native 1m leg or
``--interval 15m`` for the 15m leg. The friendly label matches
``run_symbol_p0.py --timeframe`` and names the output CSV (``<SYM>_<interval>.csv``)
so the two scripts compose (fetch → analyze) without a rename step. A native pull
at the target resolution (not a resample of 5m) is what keeps the P1 findings
faithful to what the live scalp would see.

Usage (trainer):
  .venv/bin/python scripts/research/m27/fetch_bybit_5m.py \
      --out-dir /home/ubuntu/m27_data --start 2023-01-01 --interval 1m \
      --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import time
from pathlib import Path

import requests

API = "https://api.bybit.com/v5/market/kline"
LIMIT = 1000

# Bybit v5 kline `interval` codes keyed by the friendly timeframe label
# (matches run_symbol_p0.py --timeframe); minutes per bar drives the pagination
# step + the bar_ms. Sub-hour codes are the raw minute count; hourly+ use the
# Bybit minute-equivalent ("60"/"240").
_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
                   "1h": "60", "2h": "120", "4h": "240"}
_BAR_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                "1h": 60, "2h": 120, "4h": 240}


def resolve_interval(label: str) -> tuple[str, int]:
    """Map a friendly timeframe label → (Bybit interval code, bar milliseconds).

    Raises ``ValueError`` on an unsupported label (never a silent wrong pull)."""
    key = str(label).strip().lower()
    if key not in _BYBIT_INTERVAL:
        raise ValueError(
            f"unsupported --interval {label!r}; choose one of "
            f"{', '.join(sorted(_BYBIT_INTERVAL, key=lambda k: _BAR_MINUTES[k]))}"
        )
    return _BYBIT_INTERVAL[key], _BAR_MINUTES[key] * 60 * 1000


def fetch_symbol(symbol: str, start_ms: int, out_path: Path, *,
                 interval_code: str, bar_ms: int) -> None:
    rows: list[tuple] = []
    end_ms = int(time.time() * 1000)
    seen_oldest = None
    while True:
        resp = requests.get(API, params={
            "category": "linear", "symbol": symbol, "interval": interval_code,
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
        end_ms = oldest - bar_ms
        time.sleep(0.15)  # gentle on the public endpoint

    rows = sorted({r[0]: r for r in rows}.values())
    rows = [r for r in rows if r[0] >= start_ms]
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts, o, h, lo, c, v in rows:
            # Explicit +00:00 offset: backtest_ict_scalp's HTF path parses
            # with utc=True (tz-aware) while _load_candles keeps the main
            # frame as-parsed — an offset-less timestamp goes naive and the
            # merge_asof dies on aware-vs-naive (M27 attempt-2 failure).
            iso = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc
                                            ).strftime("%Y-%m-%d %H:%M:%S+00:00")
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
    ap.add_argument("--interval", default="5m",
                    help="Friendly timeframe label (1m/3m/5m/15m/30m/1h/2h/4h; "
                         "default 5m). Names the output CSV <SYM>_<interval>.csv.")
    args = ap.parse_args()

    interval_code, bar_ms = resolve_interval(args.interval)
    label = str(args.interval).strip().lower()
    start_ms = int(dt.datetime.strptime(args.start, "%Y-%m-%d")
                   .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym in args.symbols:
        fetch_symbol(sym, start_ms, out_dir / f"{sym}_{label}.csv",
                     interval_code=interval_code, bar_ms=bar_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
