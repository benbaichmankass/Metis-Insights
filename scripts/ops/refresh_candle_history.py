#!/usr/bin/env python3
"""Extend the trainer's multi-year candle CSVs forward, never truncating them.

OWNER of the refresh for ``data/<SYM>_<tf>.csv`` (PI-20260929-FCJRWVAK-0002):
before this, nothing fetched candles on the trainer and every study reading
those files ended on the day of one manual batch (2026-06-18).

Behaviour: read the file's last bar, fetch Binance USD-M (same feed the corpus
fetcher uses) from the next bar to now, append bars strictly after the last
existing timestamp (dedupe on timestamp), write atomically. Existing rows are
never modified or dropped; the write is refused if the result would be shorter.

    python3 scripts/ops/refresh_candle_history.py --data-dir data \
        SOLUSDT:15 XRPUSDT:15 ETHUSDT:15
    (pair = SYMBOL:bybit-interval-code; 15 -> data/SYMBOL_15m.csv)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_backtest_candles as fbc  # noqa: E402

_TF_LABEL = {"5": "5m", "15": "15m", "60": "1h", "120": "2h", "240": "4h"}


def merge_extend(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Append rows of ``new`` strictly after ``old``'s last timestamp."""
    ots = pd.to_datetime(old["timestamp"], utc=True, format="mixed")
    nts = pd.to_datetime(new["timestamp"], utc=True, format="mixed")
    add = new[nts > ots.max()].copy()
    add = add.drop_duplicates("timestamp")
    add = add.reindex(columns=[c for c in old.columns if c in add.columns])
    out = pd.concat([old, add], ignore_index=True)
    assert len(out) >= len(old), "refresh would shrink the file"
    return out


def refresh_one(path: Path, symbol: str, interval: str, now: datetime) -> str:
    old = pd.read_csv(path)
    last = pd.to_datetime(old["timestamp"], utc=True, format="mixed").max()
    start_ms = int(last.timestamp() * 1000) + fbc._interval_ms(interval)
    end_ms = int(now.timestamp() * 1000)
    rows = fbc.fetch_klines_binance_vision(symbol, interval, start_ms, end_ms)
    if not rows:
        return f"{path.name}: no new bars after {last}"
    new = pd.DataFrame(rows)
    new["timestamp"] = new["timestamp"].map(lambda t: t.isoformat(sep=" "))
    out = merge_extend(old, new)
    tmp = path.with_suffix(".csv.tmp")
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return f"{path.name}: {len(old)} -> {len(out)} rows, last {out['timestamp'].iloc[-1]}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("pairs", nargs="+")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    rc = 0
    for pair in args.pairs:
        sym, iv = pair.split(":")
        path = Path(args.data_dir) / f"{sym}_{_TF_LABEL[iv]}.csv"
        if not path.exists():
            print(f"{path}: MISSING (refresh extends, never creates)", file=sys.stderr)
            rc = 1
            continue
        try:
            print(refresh_one(path, sym, iv, now))
        except Exception as exc:  # one bad symbol must not block the others
            print(f"{path.name}: FAILED {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
