#!/usr/bin/env python3
"""Fetch S&P 500 index 1m OHLCV from Dukascopy (free, deep intraday).

The clean intraday data source for MES/ES cross-symbol research
(S-STRAT-IMPROVE-S9). yfinance caps intraday hard (1m=8d, 1h=730d);
Dukascopy serves 1-minute history back years for the S&P 500 index
(`INSTRUMENT_IDX_AMERICA_E_SANDP_500`) — a clean cash-index proxy for the
MES/ES futures the bot trades on IBKR.

Output: OHLCV CSV/parquet (timestamp, open, high, low, close, volume) in
the same shape as scripts/backtest_*.py expect. Cached under data/ so we
never re-fetch (run with later --start to backfill earlier years).

Usage
-----
    python scripts/ops/fetch_dukascopy_index.py --start 2023-01-01
    python scripts/ops/fetch_dukascopy_index.py --start 2018-01-01 --end 2020-01-01 \
        --output data/SPX500_1m_2018_2020.parquet
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def fetch(start: datetime, end: datetime, interval: str = "1m"):
    import dukascopy_python as dk
    import dukascopy_python.instruments as I
    inst = I.INSTRUMENT_IDX_AMERICA_E_SANDP_500
    iv = {
        "1m": dk.INTERVAL_MIN_1, "5m": dk.INTERVAL_MIN_5,
        "15m": dk.INTERVAL_MIN_15, "1h": dk.INTERVAL_HOUR_1,
        "1d": dk.INTERVAL_DAY_1,
    }[interval]
    df = dk.fetch(inst, iv, dk.OFFER_SIDE_BID, start, end)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.reset_index()
    # index col is the timestamp; normalise name + columns
    tcol = df.columns[0]
    df = df.rename(columns={tcol: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    keep = ["timestamp", "open", "high", "low", "close"]
    if "volume" in df.columns:
        keep.append("volume")
    return df[keep].dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch S&P500 index OHLCV from Dukascopy")
    p.add_argument("--start", default="2023-01-01", help="UTC YYYY-MM-DD")
    p.add_argument("--end", default="", help="UTC YYYY-MM-DD (default: now)")
    p.add_argument("--interval", default="1m", choices=["1m", "5m", "15m", "1h", "1d"])
    p.add_argument("--output", default=os.environ.get(
        "SPX_DATA_PATH", str(_REPO_ROOT / "data" / "SPX500_1m.parquet")))
    p.add_argument("--append", action="store_true",
                   help="Merge into an existing --output cache (dedup on timestamp).")
    a = p.parse_args(argv[1:])
    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    end = (datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc)
           if a.end else datetime.now(tz=timezone.utc))
    print(f"Fetching SPX500 {a.interval} {start.date()} -> {end.date()} (Dukascopy) …",
          file=sys.stderr)
    try:
        df = fetch(start, end, a.interval)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"fetch failed: {exc}\n")
        return 1
    if df.empty:
        sys.stderr.write("ERROR: no data returned.\n")
        return 1
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.append and out.exists():
        prev = pd.read_parquet(out) if str(out).endswith(".parquet") else pd.read_csv(out)
        prev["timestamp"] = pd.to_datetime(prev["timestamp"], utc=True, errors="coerce")
        df = (pd.concat([prev, df])
              .drop_duplicates(subset="timestamp")
              .sort_values("timestamp").reset_index(drop=True))
    if str(out).endswith(".parquet"):
        df.to_parquet(out, index=False)
    else:
        df.to_csv(out, index=False)
    print(f"Wrote {len(df)} rows ({df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}) "
          f"to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
