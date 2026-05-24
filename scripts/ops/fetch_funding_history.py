#!/usr/bin/env python3
"""Fetch BTCUSDT perpetual funding-rate history from Bybit public REST.

The companion to ``fetch_backtest_candles.py`` for the funding-sentiment
R&D (S-STRAT-IMPROVE-S9, complementary-strategy hunt). Funding on Bybit
linear perps settles every 8h (00:00 / 08:00 / 16:00 UTC). Extreme
funding flags crowded positioning — a contrarian signal that is a
DIFFERENT return source than the price-channel strategies (trend /
fade), so it is a candidate uncorrelated portfolio member.

No authentication — Bybit V5 ``/v5/market/funding/history`` (limit 200,
newest-first, paginated BACKWARD via ``endTime``). Output CSV columns:
``timestamp`` (UTC), ``funding_rate`` (per-8h fraction, e.g. 0.0001 =
0.01%).

Usage
-----
    python scripts/ops/fetch_funding_history.py            # 2020-01-01 -> now
    python scripts/ops/fetch_funding_history.py --start-date 2021-01-01
    FUNDING_DATA_PATH=/tmp/f.csv python scripts/ops/fetch_funding_history.py
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
MAX_PER_REQUEST = 200
_RETRY_LIMIT = 4
_RETRY_BACKOFF = [2, 4, 8, 16]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Page Bybit funding history backward; return rows oldest-first."""
    rows: dict[int, float] = {}
    cursor_end = end_ms
    while True:
        params = {
            "category": "linear",
            "symbol": symbol,
            "endTime": cursor_end,
            "limit": MAX_PER_REQUEST,
        }
        resp = None
        for attempt in range(_RETRY_LIMIT):
            try:
                resp = requests.get(BYBIT_FUNDING_URL, params=params, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException:
                if attempt < _RETRY_LIMIT - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
                else:
                    raise
        data = resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit API error: {data.get('retMsg')} (retCode {data.get('retCode')})"
            )
        lst = data["result"]["list"]  # newest-first
        if not lst:
            break
        added = 0
        for r in lst:
            ts = int(r["fundingRateTimestamp"])
            if ts < start_ms or ts > end_ms:
                continue
            if ts not in rows:
                rows[ts] = float(r["fundingRate"])
                added += 1
        batch_oldest = min(int(r["fundingRateTimestamp"]) for r in lst)
        if batch_oldest <= start_ms or added == 0:
            break
        next_end = batch_oldest - 1
        if next_end >= cursor_end:
            break
        cursor_end = next_end
        print(
            f"  fetched back to "
            f"{datetime.fromtimestamp(batch_oldest / 1000, tz=timezone.utc).date()}"
            f" ({len(rows)} rows so far)",
            file=sys.stderr,
        )
    return [
        {"timestamp": datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
         "funding_rate": fr}
        for ts, fr in sorted(rows.items())
    ]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch Bybit perp funding-rate history")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-date", default="2020-01-01", help="UTC YYYY-MM-DD")
    p.add_argument("--end-date", default="", help="UTC YYYY-MM-DD (default: now)")
    p.add_argument(
        "--output",
        default=os.environ.get(
            "FUNDING_DATA_PATH", str(_REPO_ROOT / "data" / "funding_BTCUSDT.csv")
        ),
    )
    args = p.parse_args(argv[1:])
    start_dt = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
    end_dt = (
        datetime.fromisoformat(args.end_date).replace(tzinfo=timezone.utc)
        if args.end_date else datetime.now(tz=timezone.utc)
    )
    print(
        f"Fetching {args.symbol} funding {start_dt.date()} -> {end_dt.date()} …",
        file=sys.stderr,
    )
    try:
        rows = fetch_funding(
            args.symbol, int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"fetch failed: {exc}\n")
        return 1
    if not rows:
        sys.stderr.write("ERROR: no funding data returned from Bybit.\n")
        return 1
    df = pd.DataFrame(rows)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(
        f"Wrote {len(df)} funding rows "
        f"({df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}) to {out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
