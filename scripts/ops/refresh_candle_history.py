#!/usr/bin/env python3
# wiring: manual-only - the daily runner is deploy/trainer/ict-candle-refresh.timer, shipped as its own held Tier-2 PR (#14518) so it cannot ride this Tier-1 change.
"""Extend the trainer's multi-year candle CSVs forward, never truncating them.

OWNER of the refresh for ``data/<SYM>_<tf>.csv`` (PI-20260929-FCJRWVAK-0002):
before this, nothing fetched candles on the trainer and every study reading
those files ended on the day of one manual batch (2026-06-18).

Behaviour: read the file's last bar, fetch Bybit linear perp klines (the SAME venue as
the history: MEASURED trainer diag #14515, old SOL/ETH/XRP 15m files match Bybit at median
0.000 bps, volume ratio 1.000; Binance USD-M differs 0.5-1.4 bps median with 2.6-4.5x volume,
so it is deliberately NOT a fallback -- a venue swap changes the population) from the next bar to now, append bars strictly after the last
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch_backtest_candles as fbc  # noqa: E402
import candle_io  # noqa: E402  (the canonical loader: parses WITHOUT format='mixed')

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


def ts_formatter(sample: str):
    """Return f(datetime)->str reproducing the file's OWN timestamp spelling.

    The canonical loaders (candle_io.load_candles, backtest_pullback) parse
    without ``format='mixed'``: rows spelled differently from the first row
    become NaT and are silently DROPPED. So new rows must match the file.
    """
    sample = str(sample).strip()
    sep = "T" if "T" in sample else " "
    if sample.endswith("Z"):
        tz = "Z"
    elif len(sample) > 6 and sample[-6] in "+-" and sample[-3] == ":":
        tz = sample[-6:]
    elif len(sample) > 5 and sample[-5] in "+-" and sample[-5:-1].isdigit():
        tz = sample[-5:]
    else:
        tz = ""  # timezone-naive file (UTC by convention)
    return lambda t: t.strftime(f"%Y-%m-%d{sep}%H:%M:%S") + tz


def closed_end_ms(now: datetime, interval: str) -> int:
    """End of the last CLOSED bar: floor ``now`` to the current bar's start, so
    the still-forming bar is never fetched (fetch_klines drops start >= end).
    Same rule as src/runtime/closed_bars.py (FIX-CA-23)."""
    step = fbc._interval_ms(interval)
    return (int(now.timestamp() * 1000) // step) * step


def refresh_one(path: Path, symbol: str, interval: str, now: datetime) -> str:
    old = pd.read_csv(path)
    last = pd.to_datetime(old["timestamp"], utc=True, format="mixed").max()
    start_ms = int(last.timestamp() * 1000) + fbc._interval_ms(interval)
    end_ms = closed_end_ms(now, interval)
    if start_ms >= end_ms:
        return f"{path.name}: already current (last {last})"
    rows = fbc.fetch_klines(symbol, interval, start_ms, end_ms)  # Bybit only; raises, never falls back
    if not rows:
        return f"{path.name}: no new bars after {last}"
    new = pd.DataFrame(rows)
    fmt = ts_formatter(old["timestamp"].iloc[-1])
    new["timestamp"] = new["timestamp"].map(fmt)
    out = merge_extend(old, new)
    tmp = path.with_suffix(".csv.tmp")
    out.to_csv(tmp, index=False)
    # Read back through the CANONICAL loader: every row must survive.
    try:
        loaded = candle_io.load_candles(str(tmp))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if len(loaded) != len(out):
        tmp.unlink()
        raise RuntimeError(
            f"{path.name}: canonical loader reads {len(loaded)} of {len(out)} rows "
            f"after refresh (timestamp format mismatch?); original left untouched")
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
