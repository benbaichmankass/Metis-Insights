#!/usr/bin/env python3
"""M29 — off-VM historical backfill of crypto funding/OI/basis snapshots (Bybit).

The crypto analogue of the value + CFTC-COT backfills: reconstruct point-in-time
BTC/ETH/SOL derivatives-positioning snapshots (valuation-snapshot schema) from
Bybit's keyless public v5 API, so the M28 P4 gate + horizon-IC scan grade the
short-horizon crypto signals **unchanged**.

    Bybit v5 (keyless): funding/history + open-interest + perp & spot kline
        → per-symbol daily funding / OI / (perp−spot)/spot basis series
        → rolling percentile per day (trailing lookback, leakage-safe)
        → point-in-time snapshot rows  → comms/macro/crypto_snapshots.jsonl
        → spot daily-close candle CSVs → data/macro_candles/<SYMBOL>.csv
          (the same fetch serves both the basis signal AND the P4 forward returns)

    python scripts/macro/thesis_backtest_run.py \
        --snapshots comms/macro/crypto_snapshots.jsonl \
        --candles-dir data/macro_candles --rebalance-every 3 --horizon-days 7
    python scripts/macro/horizon_ic_scan.py \
        --snapshots comms/macro/crypto_snapshots.jsonl --candles-dir data/macro_candles \
        --rebalance-every 3 --horizons 1,3,7,14,30

Off-VM-guarded (needs ICT_OFFVM_BUILD_HOST or injected fetchers). Full regen each
run — idempotent. No order path, no DB write.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crypto_signals_data import (  # noqa: E402
    CRYPTO_SYMBOLS,
    DEFAULT_LOOKBACK_DAYS,
    build_crypto_snapshots,
    compute_basis,
    fetch_funding_history,
    fetch_kline_close,
    fetch_open_interest,
    resample_daily_last,
)

DEFAULT_SNAPSHOT_PATH = os.path.join("comms", "macro", "crypto_snapshots.jsonl")
DEFAULT_CANDLES_DIR = os.path.join("data", "macro_candles")


def build_symbol(
    symbol: str,
    *,
    fetched=None,
    urlopen=None,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    min_history: int = 30,
    timeout: float = 30.0,
):
    """Return ``(snapshots, spot_daily)`` for one symbol. ``fetched`` (a dict with
    ``funding``/``oi``/``perp_close``/``spot_close`` as dated-ms lists) injects data
    for tests; absent, fetch off-VM from Bybit."""
    if fetched is not None:
        funding_ms = fetched.get("funding", [])
        oi_ms = fetched.get("oi", [])
        perp_ms = fetched.get("perp_close", [])
        spot_ms = fetched.get("spot_close", [])
    else:
        funding_ms = fetch_funding_history(symbol, urlopen=urlopen, timeout=timeout)
        oi_ms = fetch_open_interest(symbol, urlopen=urlopen, timeout=timeout)
        perp_ms = fetch_kline_close(symbol, category="linear", urlopen=urlopen, timeout=timeout)
        spot_ms = fetch_kline_close(symbol, category="spot", urlopen=urlopen, timeout=timeout)

    funding_daily = resample_daily_last(funding_ms)
    oi_daily = resample_daily_last(oi_ms)
    perp_daily = resample_daily_last(perp_ms)
    spot_daily = resample_daily_last(spot_ms)
    basis_daily = compute_basis(perp_daily, spot_daily)

    snaps = build_crypto_snapshots(
        symbol, funding_daily=funding_daily, basis_daily=basis_daily, oi_daily=oi_daily,
        lookback=lookback, min_history=min_history,
    )
    return snaps, spot_daily


def backfill(
    *,
    symbols=CRYPTO_SYMBOLS,
    fetched_by_symbol=None,
    urlopen=None,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    min_history: int = 30,
    timeout: float = 30.0,
) -> dict:
    """Build snapshots for all symbols + collect their spot candle series. Never raises."""
    all_rows: list[dict] = []
    by_symbol: dict[str, int] = {}
    candles: dict[str, list] = {}
    for sym in symbols:
        fetched = (fetched_by_symbol or {}).get(sym)
        snaps, spot_daily = build_symbol(
            sym, fetched=fetched, urlopen=urlopen, lookback=lookback,
            min_history=min_history, timeout=timeout,
        )
        by_symbol[sym] = len(snaps)
        all_rows.extend(snaps)
        candles[sym] = spot_daily
    all_rows.sort(key=lambda r: (str(r.get("observed_at")), str(r.get("symbol")), str(r.get("metric"))))
    return {
        "rows": all_rows,
        "by_symbol": by_symbol,
        "candles": candles,
        "symbols_ok": sum(1 for n in by_symbol.values() if n > 0),
        "symbols_total": len(symbols),
    }


def write_snapshots_fresh(rows, path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in rows or []:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows or [])


def write_candles(candles: dict, out_dir) -> dict:
    """Write ``<out_dir>/<SYMBOL>.csv`` (date,close) from each symbol's spot series."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for sym, series in (candles or {}).items():
        p = d / f"{sym}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "close"])
            for day, close in series or []:
                w.writerow([day, close])
        written[sym] = len(series or [])
    return written


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M29 off-VM crypto funding/OI/basis snapshot backfill (Bybit)")
    ap.add_argument("--path", default=DEFAULT_SNAPSHOT_PATH, help=f"snapshot JSONL out (default {DEFAULT_SNAPSHOT_PATH})")
    ap.add_argument("--candles-dir", default=DEFAULT_CANDLES_DIR, help="write spot candle CSVs here for the P4/horizon scans")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS, help="rolling percentile window (days)")
    ap.add_argument("--min-history", type=int, default=30, help="min trailing days before a row is emitted")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    args = ap.parse_args(argv)

    result = backfill(lookback=args.lookback, min_history=args.min_history)

    print("M29 crypto funding/OI/basis snapshot backfill (Bybit)")
    print("=" * 52)
    for sym in CRYPTO_SYMBOLS:
        n = result["by_symbol"].get(sym, 0)
        nc = len(result["candles"].get(sym, []))
        print(f"  {sym:>8}: {n} snapshot(s), {nc} spot candles" + ("" if n else "  (EMPTY — check Bybit reachability)"))
    print(f"{result['symbols_ok']}/{result['symbols_total']} symbols with history; {len(result['rows'])} total rows")

    if not args.dry_run:
        n = write_snapshots_fresh(result["rows"], args.path)
        wc = write_candles(result["candles"], args.candles_dir)
        print(f"wrote {n} rows → {args.path}")
        print(f"wrote candles → {args.candles_dir}: {wc}")
    return 0 if result["symbols_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
