#!/usr/bin/env python3
"""M28 P1 — off-VM fetch of historical daily closes for the seed-universe symbols.

The M28 P4 value-thesis gate scores each thesis's forward return against real
prices, so it needs a per-symbol daily-close CSV (``--candles-dir <SYMBOL>.csv``).
This pulls those from **Yahoo Finance** (free, keyless — the same fallback the
dashboard's `_fetch_candles` uses), for the seed universe declared in
``config/macro_valuation.yaml`` (so a roster change needs no edit here).

Pairs with ``valuation_snapshot_backfill.py``: together they let the P4 gate run
on real history immediately instead of waiting weeks for a live soak.

Off-VM-guarded (needs ICT_OFFVM_BUILD_HOST) so the live trading VM never opens a
Yahoo socket — this is a research/backtest tool run on a GitHub runner / trainer.
Best-effort per symbol: a failed fetch is logged + skipped, never fatal. No order
path, no DB write.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.strategies.macro_thesis.valuation_feed import load_valuation_config  # noqa: E402

_TRUTHY = {"1", "true", "yes", "on"}


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def seed_symbols(config) -> list[str]:
    """Seed-universe symbols to price = the ``instruments`` keys (the tradable
    ETFs). The ``context`` group is macro reads, not tradable, so it's excluded."""
    return sorted((config.get("instruments", {}) or {}).keys())


def _write_csv(path: Path, closes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "close"])
        for idx, val in closes.items():
            v = float(val.iloc[0]) if hasattr(val, "iloc") else float(val)
            w.writerow([str(idx)[:10], v])
            n += 1
    return n


def fetch_candles(symbols, out_dir, *, start: str = "2005-01-01", download=None) -> dict:
    """Fetch daily closes for each symbol → ``<out_dir>/<SYMBOL>.csv`` (date,close).

    ``download`` is injectable for tests (defaults to ``yfinance.download``, which
    is only imported — and only reachable — off-VM)."""
    if download is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_macro_candles: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject download"
            )
        import yfinance as yf
        download = lambda s: yf.download(s, start=start, progress=False, auto_adjust=True)  # noqa: E731

    out = Path(out_dir)
    result: dict[str, int] = {}
    for s in symbols:
        try:
            df = download(s)
            if df is None or getattr(df, "empty", True):
                result[s] = 0
                continue
            result[s] = _write_csv(out / f"{s}.csv", df["Close"])
        except Exception as exc:  # noqa: BLE001
            print(f"{s}: fetch failed ({exc})")
            result[s] = 0
    return result


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M28 fetch historical daily closes for the seed universe")
    ap.add_argument("--config", default=None, help="config/macro_valuation.yaml override")
    ap.add_argument("--out-dir", default=os.path.join("data", "macro_candles"),
                    help="output dir for <SYMBOL>.csv (default data/macro_candles)")
    ap.add_argument("--start", default="2005-01-01", help="history start (YYYY-MM-DD)")
    ap.add_argument("--symbols", default=None, help="comma-separated override (default: config instruments)")
    args = ap.parse_args(argv)

    config = load_valuation_config(args.config)
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else seed_symbols(config)
    if not symbols:
        print("no seed symbols resolved (empty config?)")
        return 1

    result = fetch_candles(symbols, args.out_dir, start=args.start)
    print("M28 macro-candle fetch")
    print("=" * 24)
    for s, n in result.items():
        print(f"  {s:>6}: {n} daily closes" + ("" if n else "  (EMPTY / failed)"))
    ok = sum(1 for n in result.values() if n)
    print(f"{ok}/{len(result)} symbols fetched → {args.out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
