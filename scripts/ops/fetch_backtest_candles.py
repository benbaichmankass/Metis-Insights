#!/usr/bin/env python3
"""Fetch BTCUSDT 5m candles from Bybit public REST API.

Writes to BACKTEST_DATA_PATH (default: data/backtest_candles.csv under repo
root). No authentication required — Bybit V5 public klines endpoint.

Usage
-----
    # Last 365 days (default — wide enough for random-window sampling):
    python scripts/ops/fetch_backtest_candles.py

    # Explicit date range:
    python scripts/ops/fetch_backtest_candles.py \\
        --start-date 2026-02-01 --end-date 2026-05-13

    # Override output path:
    BACKTEST_DATA_PATH=/tmp/fresh.csv python scripts/ops/fetch_backtest_candles.py

Environment
-----------
BACKTEST_DATA_PATH   Override output CSV path.
REPO_ROOT            Override repo root (default: two levels above this file).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"
MAX_BARS_PER_REQUEST = 1000
_RETRY_LIMIT = 4
_RETRY_BACKOFF = [2, 4, 8, 16]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _interval_ms(interval: str) -> int:
    """Convert a Bybit interval string to milliseconds."""
    if interval.upper().endswith("D"):
        return int(interval[:-1]) * 86_400_000
    if interval.upper().endswith("W"):
        return int(interval[:-1]) * 7 * 86_400_000
    return int(interval) * 60_000


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Page through Bybit klines and return rows sorted oldest-first."""
    rows: list[dict] = []
    cursor_ms = start_ms
    interval_ms = _interval_ms(interval)

    while cursor_ms < end_ms:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "start": cursor_ms,
            "end": end_ms,
            "limit": MAX_BARS_PER_REQUEST,
        }
        resp = None
        for attempt in range(_RETRY_LIMIT):
            try:
                resp = requests.get(BYBIT_KLINES_URL, params=params, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt < _RETRY_LIMIT - 1:
                    wait = _RETRY_BACKOFF[attempt]
                    print(
                        f"  fetch retry {attempt + 1}/{_RETRY_LIMIT - 1} after {wait}s: {exc}",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                else:
                    raise

        data = resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit API error: {data.get('retMsg')} (retCode {data.get('retCode')})"
            )

        candles = data["result"]["list"]  # Bybit returns newest-first
        if not candles:
            break

        added = 0
        for c in reversed(candles):  # reverse to oldest-first
            ts_ms = int(c[0])
            if ts_ms < start_ms or ts_ms >= end_ms:
                continue
            rows.append({
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
            added += 1

        if added == 0:
            break

        # Next page starts after the oldest candle returned.
        oldest_ms = int(candles[-1][0])
        next_cursor = oldest_ms + interval_ms
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor

        print(
            f"  fetched up to {datetime.fromtimestamp(oldest_ms / 1000, tz=timezone.utc).date()}"
            f" ({len(rows)} bars so far)",
            file=sys.stderr,
        )

    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Bybit 5m candles for VWAP backtest"
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--interval",
        default="5",
        help="Bybit kline interval: 1/3/5/15/30/60/120/240/D/W (default: 5)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Trailing calendar days to fetch (default 365). Overridden by --start-date.",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Start date YYYY-MM-DD UTC (overrides --days)",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="End date YYYY-MM-DD UTC inclusive (default: today)",
    )
    parser.add_argument(
        "--output",
        default=os.environ.get(
            "BACKTEST_DATA_PATH",
            str(_REPO_ROOT / "data" / "backtest_candles.csv"),
        ),
        help="Output CSV path (default: data/backtest_candles.csv or BACKTEST_DATA_PATH)",
    )
    args = parser.parse_args(argv[1:])

    now_utc = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    if args.end_date:
        end_dt = datetime.fromisoformat(args.end_date).replace(
            tzinfo=timezone.utc
        ) + timedelta(days=1)
    else:
        end_dt = now_utc + timedelta(days=1)

    if args.start_date:
        start_dt = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
    else:
        start_dt = now_utc - timedelta(days=args.days)

    print(
        f"Fetching {args.symbol} {args.interval}m candles "
        f"{start_dt.date()} -> {(end_dt - timedelta(days=1)).date()} …",
        file=sys.stderr,
    )

    try:
        rows = fetch_klines(args.symbol, args.interval, _ms(start_dt), _ms(end_dt))
    except Exception as exc:
        sys.stderr.write(f"fetch failed: {exc}\n")
        return 1

    if not rows:
        sys.stderr.write("ERROR: no candles returned from Bybit.\n")
        return 1

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(
        f"Wrote {len(df)} rows "
        f"({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}) "
        f"to {output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
