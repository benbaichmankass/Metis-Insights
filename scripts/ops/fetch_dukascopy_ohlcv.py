#!/usr/bin/env python3
"""Fetch OHLCV for any Dukascopy instrument (free, deep intraday history).

M15 Phase 0 generalization of the SPX-only
``fetch_dukascopy_index.py`` (strategy-improvement program branch):
same output contract — a candle CSV/parquet with columns
``timestamp, open, high, low, close[, volume]`` in the exact shape
``scripts/backtest_*.py --data`` expects — but the instrument is a CLI
argument instead of a constant, so one script covers the whole Phase 0
universe (FX majors, XAU/USD, and US ETF CFDs like QQQ/SPY/GLD).

Dukascopy serves years of 1-minute history for FX/metals/index/ETF CFDs
where yfinance caps intraday hard (1m=8d, 5m=60d, 1h=730d).

Instrument names are the attribute names in
``dukascopy_python.instruments`` (e.g. ``INSTRUMENT_FX_MAJORS_EUR_USD``).
Use ``--list <substring>`` to discover them.

Equities/ETF CFDs trade nearly around the clock on Dukascopy's book but
the strategies under test are RTH objects — pass ``--rth-only`` to keep
only the US cash session (14:30–21:00 UTC; approximation that ignores
the DST hour shift — fine for research sweeps, NOT a trading calendar).

Usage
-----
    python scripts/ops/fetch_dukascopy_ohlcv.py --list QQQ
    python scripts/ops/fetch_dukascopy_ohlcv.py \
        --instrument INSTRUMENT_FX_MAJORS_EUR_USD --interval 15m \
        --start 2020-01-01 --output data/EURUSD_15m.csv
    python scripts/ops/fetch_dukascopy_ohlcv.py \
        --instrument INSTRUMENT_ETF_CFD_US_QQQ_US --interval 5m \
        --start 2023-01-01 --rth-only --output data/QQQ_5m_rth.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timezone

import pandas as pd

INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")

RTH_START_UTC = time(14, 30)
RTH_END_UTC = time(21, 0)


def _interval_map():
    import dukascopy_python as dk

    return {
        "1m": dk.INTERVAL_MIN_1,
        "5m": dk.INTERVAL_MIN_5,
        "15m": dk.INTERVAL_MIN_15,
        "1h": dk.INTERVAL_HOUR_1,
        "4h": dk.INTERVAL_HOUR_4,
        "1d": dk.INTERVAL_DAY_1,
    }


def fetch(instrument_name: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame:
    import dukascopy_python as dk
    import dukascopy_python.instruments as I

    try:
        inst = getattr(I, instrument_name)
    except AttributeError:
        raise SystemExit(
            f"unknown instrument {instrument_name!r} — discover names with --list <substring>"
        )
    df = dk.fetch(inst, _interval_map()[interval], dk.OFFER_SIDE_BID, start, end)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.reset_index()
    tcol = df.columns[0]
    df = df.rename(columns={tcol: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    keep = ["timestamp", "open", "high", "low", "close"]
    if "volume" in df.columns:
        keep.append("volume")
    return df[keep].dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def rth_filter(df: pd.DataFrame) -> pd.DataFrame:
    t = df["timestamp"].dt.time
    return df[(t >= RTH_START_UTC) & (t < RTH_END_UTC)].reset_index(drop=True)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch OHLCV for any Dukascopy instrument")
    p.add_argument("--instrument", help="attribute name in dukascopy_python.instruments")
    p.add_argument("--list", metavar="SUBSTRING", help="list instrument names matching SUBSTRING and exit")
    p.add_argument("--start", default="2023-01-01", help="UTC YYYY-MM-DD")
    p.add_argument("--end", default="", help="UTC YYYY-MM-DD (default: now)")
    p.add_argument("--interval", default="1m", choices=INTERVALS)
    p.add_argument("--rth-only", action="store_true", help="keep only US cash-session bars (14:30-21:00 UTC)")
    p.add_argument("--output", default="", help="output path (.csv or .parquet); default data/<instrument>_<interval>.csv")
    args = p.parse_args(argv)

    if args.list is not None:
        import dukascopy_python.instruments as I

        names = sorted(n for n in dir(I) if args.list.upper() in n.upper())
        print("\n".join(names) or f"no instrument matches {args.list!r}")
        return 0
    if not args.instrument:
        p.error("--instrument is required (or use --list)")

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (
        datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    df = fetch(args.instrument, start, end, args.interval)
    if df.empty:
        print(f"no data returned for {args.instrument} {args.interval} {args.start}..{args.end or 'now'}")
        return 1
    if args.rth_only:
        df = rth_filter(df)

    out = args.output or f"data/{args.instrument}_{args.interval}.csv"
    if out.endswith(".parquet"):
        df.to_parquet(out, index=False)
    else:
        df.to_csv(out, index=False)
    print(
        f"wrote {len(df)} rows to {out} "
        f"({df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
