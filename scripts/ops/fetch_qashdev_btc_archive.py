#!/usr/bin/env python3
"""Fetch the qashdev/btc 5m monthly BTCUSDT archive into a parquet cache.

Source: https://github.com/qashdev/btc — verbatim mirror of Binance
Vision's `data.binance.vision` public S3 bucket. The 2026-05-08
experiment (`experiments/2026-05-08-all-models-training/`) established
this as the canonical multi-year dataset for backtests: 38 monthly
files Jan 2023 → Feb 2026, ~328k bars, price range $16.5k → $126k.

Bybit / Coinbase / yfinance are typically firewalled from sandbox
environments; this archive is reachable from the trainer VM and
matches live Bybit Linear BTCUSDT-PERP within the spot-perp basis
(~0–10 bps).

Why this exists separately from `fetch_backtest_candles.py`:
`fetch_backtest_candles.py` pulls a 365-day window from Bybit's V5
REST. That's right for the GHA workflows (vwap-backtest.yml, ict-
scalp-backtest.yml) and the smaller fixture at
`data/backtest_candles.csv`. For multi-year backtest sweeps we need
the full 3-year archive, hence this fetcher.

Output layout (defaults are overridable via env vars):
    ICT_TRADER_DATA_ROOT/raw/btc_5m_<YYYY-MM>.csv   — one per month
    ICT_TRADER_DATA_ROOT/btc_5m.parquet             — consolidated cache

Both live OUTSIDE the repo (under ``/home/ubuntu/ict-trader-data/``
by default) so a `git clean -fdx` or a fresh `git pull --rebase`
never blows them away. The parquet is what
`experiments/2026-05-17-post-incident-validation/scripts/run.py`
consumes.

Idempotency:
    Months already present in raw/ are not re-downloaded. The parquet
    is rebuilt whenever its mtime is older than the newest raw CSV.
    Passing `--force-refetch` bypasses both caches.

Usage:
    python scripts/ops/fetch_qashdev_btc_archive.py
    python scripts/ops/fetch_qashdev_btc_archive.py --start 2024-01 --end 2026-04
    python scripts/ops/fetch_qashdev_btc_archive.py --force-refetch

Environment:
    ICT_TRADER_DATA_ROOT   Default /home/ubuntu/ict-trader-data
    QASHDEV_BASE_URL       Default https://raw.githubusercontent.com/qashdev/btc/main/data/spot/monthly/klines/BTCUSDT/5m
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

DATA_ROOT = Path(os.environ.get(
    "ICT_TRADER_DATA_ROOT", "/home/ubuntu/ict-trader-data"
))
RAW_DIR = DATA_ROOT / "raw"
PARQUET_PATH = DATA_ROOT / "btc_5m.parquet"

BASE_URL = os.environ.get(
    "QASHDEV_BASE_URL",
    "https://raw.githubusercontent.com/qashdev/btc/main/data/spot/monthly/klines/BTCUSDT/5m",
)

# Earliest month qashdev/btc has on record per the 2026-05-08 experiment
# notes. The fetcher will still try a month outside this range if the
# user explicitly passes --start; the range is just the default scope.
DEFAULT_START = "2023-01"

REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 4
RETRY_BACKOFF_S = (2, 4, 8, 16)

# Binance Vision CSV columns (12-col klines schema). Same as
# experiments/2026-05-08-all-models-training/scripts/consolidate.py.
CSV_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trade_count", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]


def _months_to_fetch(start_yyyy_mm: str, end_yyyy_mm: Optional[str]) -> List[str]:
    """Inclusive list of YYYY-MM strings between start and end (default: today)."""
    start = datetime.strptime(start_yyyy_mm, "%Y-%m")
    if end_yyyy_mm:
        end = datetime.strptime(end_yyyy_mm, "%Y-%m")
    else:
        end = datetime.utcnow().replace(day=1)
    months = []
    cur = start
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        cur = cur + relativedelta(months=1)
    return months


def _fetch_one_month(yyyy_mm: str) -> Optional[bytes]:
    """Download one month's CSV bytes from qashdev/btc. Return None if 404."""
    url = f"{BASE_URL}/BTCUSDT-5m-{yyyy_mm}.csv"
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_BACKOFF_S[attempt])
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code == 200:
            return resp.content
        if attempt == MAX_RETRIES - 1:
            resp.raise_for_status()
        time.sleep(RETRY_BACKOFF_S[attempt])
    return None


def _normalize_timestamps(s: pd.Series) -> pd.Series:
    """qashdev/btc's open_time is ms (13 digits) before mid-2024 and us (16
    digits) after. Detect by magnitude and normalize to UTC."""
    s = s.astype("int64")
    is_us = s > 10**14
    out = pd.Series(index=s.index, dtype="datetime64[ns, UTC]")
    out.loc[~is_us] = pd.to_datetime(s.loc[~is_us], unit="ms", utc=True)
    out.loc[is_us] = pd.to_datetime(s.loc[is_us], unit="us", utc=True)
    return out


def fetch(args: argparse.Namespace) -> int:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    months = _months_to_fetch(args.start, args.end)
    print(f"target months: {len(months)} ({months[0]} -> {months[-1]})")

    fetched = 0
    skipped = 0
    missing = []
    for m in months:
        local = RAW_DIR / f"BTCUSDT-5m-{m}.csv"
        if local.exists() and local.stat().st_size > 1000 and not args.force_refetch:
            skipped += 1
            continue
        print(f"  fetching {m} ...", end="", flush=True)
        body = _fetch_one_month(m)
        if body is None:
            print(" 404 (not yet published)")
            missing.append(m)
            continue
        local.write_bytes(body)
        print(f" {len(body)/1024:.0f} KB")
        fetched += 1
    print(f"raw cache: {fetched} fetched, {skipped} skipped, {len(missing)} missing")
    if missing:
        print(f"  missing months: {missing}")

    # Consolidate. Rebuild the parquet whenever any raw CSV is newer
    # than the parquet, OR when --force-refetch is set, OR when the
    # parquet doesn't exist yet.
    raw_csvs = sorted(RAW_DIR.glob("BTCUSDT-5m-*.csv"))
    if not raw_csvs:
        print("FATAL: no raw CSVs available — fetch failed.", file=sys.stderr)
        return 1

    rebuild = (
        args.force_refetch
        or not PARQUET_PATH.exists()
        or max(p.stat().st_mtime for p in raw_csvs) > PARQUET_PATH.stat().st_mtime
    )
    if not rebuild:
        print(f"parquet up to date at {PARQUET_PATH} ({PARQUET_PATH.stat().st_size/1e6:.1f} MB)")
        return 0

    print(f"+ consolidating {len(raw_csvs)} monthly CSVs into parquet")
    frames = []
    for f in raw_csvs:
        if f.stat().st_size < 1000:
            continue
        df = pd.read_csv(f, header=None, names=CSV_COLS)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = _normalize_timestamps(df["open_time"])
    df = df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]].copy()
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

    diffs = df["timestamp"].diff().dropna().dt.total_seconds()
    print(f"  rows:             {len(df):,}")
    print(f"  range:            {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    print(f"  median gap (s):   {diffs.median()}")
    print(f"  max gap (s):      {diffs.max()}")
    print(f"  missing bars:     {(diffs > 300).sum()}")
    print(f"  price range:      ${df['low'].min():.2f} -> ${df['high'].max():.2f}")

    df.to_parquet(PARQUET_PATH)
    print(f"+ wrote {PARQUET_PATH} ({PARQUET_PATH.stat().st_size/1e6:.1f} MB)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--start", default=DEFAULT_START,
        help=f"First month (YYYY-MM). Default {DEFAULT_START}.",
    )
    p.add_argument(
        "--end", default=None,
        help="Last month (YYYY-MM). Default = current month.",
    )
    p.add_argument(
        "--force-refetch", action="store_true",
        help="Re-download every month even if cached, rebuild parquet.",
    )
    args = p.parse_args()
    return fetch(args)


if __name__ == "__main__":
    sys.exit(main())
