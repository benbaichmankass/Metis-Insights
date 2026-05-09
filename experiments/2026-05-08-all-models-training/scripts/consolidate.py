"""Consolidate qashdev/btc monthly 5m kline CSVs into a single normalized parquet.

Source: https://github.com/qashdev/btc (mirrors Binance Vision data.binance.vision).
Spot BTCUSDT 5m klines, monthly archive Jan 2024 - Feb 2026 (38 months, ~328k bars).

Quirk: open_time is in milliseconds for files up to mid-2024 (13-digit) and
microseconds (16-digit) for later files. We detect by magnitude and normalize
to UTC pandas timestamps.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

SRC = Path("/tmp/btc5m")
OUT = Path("experiments/2026-05-08-all-models-training/data/btc_5m.parquet")

COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trade_count", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]


def _normalize_ts(series: pd.Series) -> pd.Series:
    s = series.astype("int64")
    # Files have inconsistent units: ms (13 digit, ~1.7e12) vs us (16 digit, ~1.7e15)
    is_us = s > 10**14
    out = pd.Series(index=s.index, dtype="datetime64[ns, UTC]")
    out.loc[~is_us] = pd.to_datetime(s.loc[~is_us], unit="ms", utc=True)
    out.loc[is_us] = pd.to_datetime(s.loc[is_us], unit="us", utc=True)
    return out


def load() -> pd.DataFrame:
    frames = []
    for f in sorted(SRC.glob("*.csv")):
        if f.stat().st_size < 1000:
            continue
        df = pd.read_csv(f, header=None, names=COLS)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = _normalize_ts(df["open_time"])
    df = df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]].copy()
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def main() -> None:
    df = load()
    print(f"rows: {len(df):,}")
    print(f"range: {df['timestamp'].iloc[0]}  ->  {df['timestamp'].iloc[-1]}")
    diffs = df["timestamp"].diff().dropna().dt.total_seconds()
    print(f"median bar gap (s): {diffs.median()}  max gap (s): {diffs.max()}")
    print(f"price range: ${df['low'].min():.2f}  ->  ${df['high'].max():.2f}")
    print(f"missing-bar count (gap > 5min): {(diffs > 300).sum()}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
