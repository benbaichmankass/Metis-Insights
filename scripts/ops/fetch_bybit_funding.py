#!/usr/bin/env python3
"""Fetch Bybit LINEAR-PERP funding-rate history for the prop EV re-validation.

Companion to ``fetch_backtest_candles.py`` (which pulls the 5m klines). The
cost-aware prop EV re-validation (PB-20260616-004) must factor in perp funding,
which the original Binance-*spot* research could not see. This pulls the real
8-hourly funding rates so ``src.prop.funding.apply_funding_to_ledger`` can
charge each backtested trade the exact funding it would have bled.

Bybit V5 public endpoint, no auth: ``/v5/market/funding/history``
(``category=linear``). Returns newest-first; we page backwards by ``endTime``
and write oldest-first ``timestamp,funding_rate`` rows (``funding_rate`` is a
per-8h fraction, e.g. 0.0001 = 0.01%).

Usage
-----
    python scripts/ops/fetch_bybit_funding.py --symbol SOLUSDT \\
        --start-date 2023-01-01 --end-date 2026-02-28 \\
        --output ~/ict-trader-data/solusdt_funding.csv

Tier-1 research tooling — no auth, no live path.
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

BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
MAX_ROWS_PER_REQUEST = 200
_RETRY_LIMIT = 4
_RETRY_BACKOFF = [2, 4, 8, 16]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Page backwards through Bybit funding history; return rows oldest-first."""
    rows: list[dict] = []
    cursor_end = end_ms
    seen: set[int] = set()

    while cursor_end > start_ms:
        params = {
            "category": "linear",
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": cursor_end,
            "limit": MAX_ROWS_PER_REQUEST,
        }
        resp = None
        for attempt in range(_RETRY_LIMIT):
            try:
                resp = requests.get(BYBIT_FUNDING_URL, params=params, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt < _RETRY_LIMIT - 1:
                    wait = _RETRY_BACKOFF[attempt]
                    print(f"  retry {attempt + 1} after {wait}s: {exc}", file=sys.stderr)
                    time.sleep(wait)
                else:
                    raise

        data = resp.json()
        if data.get("retCode") == 10006:
            print("  rate-limited (10006) — sleeping 30s", file=sys.stderr)
            time.sleep(30)
            continue
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit API error: {data.get('retMsg')} (retCode {data.get('retCode')})"
            )

        items = (data.get("result") or {}).get("list") or []  # newest-first
        if not items:
            break

        oldest_ms = end_ms
        added = 0
        for it in items:
            ts_ms = int(it["fundingRateTimestamp"])
            if ts_ms in seen or ts_ms < start_ms or ts_ms > end_ms:
                oldest_ms = min(oldest_ms, ts_ms)
                continue
            seen.add(ts_ms)
            rows.append({
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "funding_rate": float(it["fundingRate"]),
            })
            oldest_ms = min(oldest_ms, ts_ms)
            added += 1

        if added == 0 and oldest_ms >= cursor_end:
            break
        next_end = oldest_ms - 1
        if next_end >= cursor_end:
            break
        cursor_end = next_end

        print(
            f"  funding up to {datetime.fromtimestamp(oldest_ms / 1000, tz=timezone.utc).date()}"
            f" ({len(rows)} rows so far)",
            file=sys.stderr,
        )
        time.sleep(0.25)

    rows.sort(key=lambda r: r["timestamp"])
    return rows


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch Bybit linear-perp funding history.")
    p.add_argument("--symbol", default="SOLUSDT")
    p.add_argument("--days", type=int, default=1200,
                   help="Trailing days (default 1200). Overridden by --start-date.")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--output", default=os.environ.get(
        "FUNDING_DATA_PATH", str(_REPO_ROOT / "data" / "funding_history.csv")))
    args = p.parse_args(argv[1:])

    now_utc = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = (datetime.fromisoformat(args.end_date).replace(tzinfo=timezone.utc) + timedelta(days=1)
              if args.end_date else now_utc + timedelta(days=1))
    start_dt = (datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
                if args.start_date else now_utc - timedelta(days=args.days))

    print(f"Fetching {args.symbol} funding {start_dt.date()} -> {(end_dt - timedelta(days=1)).date()} …",
          file=sys.stderr)
    try:
        rows = fetch_funding(args.symbol, _ms(start_dt), _ms(end_dt))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"fetch failed: {exc}\n")
        return 1
    if not rows:
        sys.stderr.write("ERROR: no funding rows returned from Bybit.\n")
        return 1

    df = pd.DataFrame(rows)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    mean_8h = df["funding_rate"].mean()
    print(
        f"Wrote {len(df)} funding rows "
        f"({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}) to {out}; "
        f"mean 8h rate {mean_8h:.6f} (~{mean_8h * 3 * 365 * 100:.1f}%/yr to a long)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
