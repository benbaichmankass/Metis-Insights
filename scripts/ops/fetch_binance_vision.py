#!/usr/bin/env python3
"""Fetch monthly 5m klines for any symbol from Binance Vision into a parquet cache.

Generalises ``fetch_qashdev_btc_archive.py`` (which is BTCUSDT-only, off a GitHub
mirror) to ANY Binance-listed symbol straight from the canonical public archive
``data.binance.vision`` — verified reachable from the sandbox (Bybit's own REST
is 403-blocked here). Binance SPOT 5m matches Bybit linear-perp within the
spot-perp basis (~0-10 bps), so it is a sound proxy for a *relative* prop-eval
sweep across Bybit-tradeable coins.

Output: ``<DATA_ROOT>/<symbol_lower>_5m.parquet`` in the SAME schema the
backtest engine's ``_load_candles`` expects (timestamp[UTC], open, high, low,
close, volume). Monthly raw ZIPs are cached under ``<DATA_ROOT>/raw/`` so a
re-run is incremental.

Usage:
    python scripts/ops/fetch_binance_vision.py --symbol ETHUSDT --start 2023-01 --end 2026-02
    python scripts/ops/fetch_binance_vision.py --symbol SOLUSDT   # defaults 2023-01..2026-02

Env:
    ICT_TRADER_DATA_ROOT   default /home/user/ict-trader-data
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd
from dateutil.relativedelta import relativedelta

DATA_ROOT = Path(os.environ.get("ICT_TRADER_DATA_ROOT", "/home/user/ict-trader-data"))
BASE = "https://data.binance.vision/data/spot/monthly/klines"
_BINANCE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def _months(start: str, end: str) -> List[str]:
    s = datetime.strptime(start, "%Y-%m")
    e = datetime.strptime(end, "%Y-%m")
    out, cur = [], s
    while cur <= e:
        out.append(cur.strftime("%Y-%m"))
        cur += relativedelta(months=1)
    return out


def _fetch_month(symbol: str, ym: str, raw_dir: Path) -> Path | None:
    """Download + extract one monthly kline CSV; cached. Returns the CSV path or None."""
    csv_path = raw_dir / f"{symbol}-5m-{ym}.csv"
    if csv_path.exists() and csv_path.stat().st_size > 0:
        return csv_path
    url = f"{BASE}/{symbol}/5m/{symbol}-5m-{ym}.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            blob = resp.read()
    except Exception as exc:  # noqa: BLE001 — a missing month must not abort the rest
        print(f"  [skip] {ym}: {type(exc).__name__} {str(exc)[:80]}", file=sys.stderr)
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = zf.namelist()[0]
        csv_path.write_bytes(zf.read(name))
    return csv_path


def _load_csv(path: Path) -> pd.DataFrame:
    # Binance monthly CSVs may or may not carry a header row (post-2025 added one).
    head = path.read_text().splitlines()[:1]
    has_header = bool(head) and head[0].lower().startswith("open_time")
    df = pd.read_csv(path, header=0 if has_header else None,
                     names=None if has_header else _BINANCE_COLS)
    ot = pd.to_numeric(df["open_time"], errors="coerce")
    # epoch ms vs us (Binance switched some 2025+ files to microseconds)
    unit = "us" if ot.dropna().iloc[0] > 1e14 else "ms"
    out = pd.DataFrame({
        "timestamp": pd.to_datetime(ot, unit=unit, utc=True),
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    })
    return out.dropna(subset=["timestamp", "open", "high", "low", "close"])


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch Binance Vision 5m klines -> parquet.")
    p.add_argument("--symbol", required=True, help="e.g. ETHUSDT")
    p.add_argument("--start", default="2023-01", help="YYYY-MM (inclusive)")
    p.add_argument("--end", default="2026-02", help="YYYY-MM (inclusive)")
    p.add_argument("--force", action="store_true", help="rebuild parquet even if newer than raw")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    raw_dir = DATA_ROOT / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = DATA_ROOT / f"{symbol.lower()}_5m.parquet"

    frames: List[pd.DataFrame] = []
    for ym in _months(args.start, args.end):
        csv_path = _fetch_month(symbol, ym, raw_dir)
        if csv_path is not None:
            frames.append(_load_csv(csv_path))
    if not frames:
        print(f"ERROR: no months fetched for {symbol}", file=sys.stderr)
        return 1
    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True))
    df.to_parquet(out_path)
    print(f"wrote {out_path}  ({len(df):,} bars, "
          f"{df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
