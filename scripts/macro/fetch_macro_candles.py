#!/usr/bin/env python3
"""M28 P1 — off-VM fetch of historical daily closes for the seed-universe symbols.

The M28 P4 value-thesis gate scores each thesis's forward return against real
prices, so it needs a per-symbol daily-close CSV (``--candles-dir <SYMBOL>.csv``).
This pulls those for the seed universe declared in ``config/macro_valuation.yaml``
(so a roster change needs no edit here), from **two free, keyless sources** for
resilience:

  1. **Yahoo Finance** (yfinance) — primary.
  2. **Stooq** daily CSV — fallback when yfinance returns short/empty for a symbol
     (yfinance is flaky from datacenter IPs, and a single-symbol download's
     ``df["Close"]`` is a 1-column *DataFrame* whose columns must be squeezed to a
     Series — the bug that made the first backfill run write 1 garbage row/symbol).

Pairs with ``valuation_snapshot_backfill.py``: together they let the P4 gate run on
real history immediately. Off-VM-guarded (needs ICT_OFFVM_BUILD_HOST) so the live
trading VM never opens a market-data socket. Best-effort per symbol: a failure is
logged + skipped, never fatal. No order path, no DB write.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.strategies.macro_thesis.valuation_feed import load_valuation_config  # noqa: E402

_TRUTHY = {"1", "true", "yes", "on"}
_STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}.us&i=d"


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def seed_symbols(config) -> list[str]:
    """Seed-universe symbols to price = the ``instruments`` keys (tradable ETFs).
    The ``context`` group is macro reads, not tradable, so it's excluded."""
    return sorted((config.get("instruments", {}) or {}).keys())


def yf_close_pairs(df) -> list[tuple[str, float]]:
    """Extract ``[(date, close), ...]`` from a yfinance download frame.

    A single-symbol ``yf.download`` returns **MultiIndex columns**, so ``df["Close"]``
    is a 1-column DataFrame, not a Series — squeeze it to a Series first (the bug
    that wrote one garbage row per symbol). Best-effort; a non-finite value is skipped."""
    close = df["Close"]
    if hasattr(close, "columns"):        # 1-col DataFrame → its only column's Series
        close = close.iloc[:, 0]
    out: list[tuple[str, float]] = []
    for idx, val in close.items():
        try:
            v = float(val.iloc[0]) if hasattr(val, "iloc") else float(val)
        except (TypeError, ValueError):
            continue
        day = str(idx)[:10]
        if len(day) == 10:
            out.append((day, v))
    return out


def stooq_close_pairs(text: str) -> list[tuple[str, float]]:
    """Parse a Stooq ``/q/d/l`` CSV body (``Date,Open,High,Low,Close,Volume``) →
    ``[(date, close), ...]`` ascending. Skips the header + unparseable rows."""
    out: list[tuple[str, float]] = []
    lines = [ln for ln in (text or "").strip().splitlines() if ln]
    if len(lines) < 2:
        return out
    header = [h.strip().lower() for h in lines[0].split(",")]
    try:
        di, ci = header.index("date"), header.index("close")
    except ValueError:
        return out
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= max(di, ci):
            continue
        day = parts[di].strip()[:10]
        try:
            if len(day) == 10:
                out.append((day, float(parts[ci])))
        except ValueError:
            continue
    return out


def _write_pairs(path: Path, pairs) -> int:
    if not pairs:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "close"])
        for d, v in pairs:
            w.writerow([d, v])
    return len(pairs)


def fetch_candles(
    symbols, out_dir, *, start: str = "2005-01-01",
    download=None, stooq_urlopen=None, min_rows: int = 30, timeout: float = 25.0,
) -> dict:
    """Fetch daily closes per symbol → ``<out_dir>/<SYMBOL>.csv``, yfinance-then-Stooq.

    Both fetchers are injectable for tests (``download`` = yfinance, ``stooq_urlopen``
    = a fake urlopen). Real fetches are off-VM-only; a symbol that yfinance returns
    with fewer than ``min_rows`` rows falls back to Stooq."""
    real = download is None and stooq_urlopen is None
    if real and not _offvm_enabled():
        raise RuntimeError(
            "fetch_macro_candles: network fetch is off-VM only "
            "(set ICT_OFFVM_BUILD_HOST=1) or inject download/stooq_urlopen"
        )
    if download is None and _offvm_enabled():
        import yfinance as yf
        download = lambda s: yf.download(s, start=start, progress=False, auto_adjust=True)  # noqa: E731
    if stooq_urlopen is None and _offvm_enabled():
        stooq_urlopen = urllib.request.urlopen

    out = Path(out_dir)
    result: dict[str, int] = {}
    for s in symbols:
        pairs: list[tuple[str, float]] = []
        if download is not None:
            try:
                df = download(s)
                if df is not None and not getattr(df, "empty", True):
                    pairs = yf_close_pairs(df)
            except Exception as exc:  # noqa: BLE001
                print(f"{s}: yfinance failed ({exc})")
        if len(pairs) < min_rows and stooq_urlopen is not None:
            try:
                with stooq_urlopen(_STOOQ_URL.format(sym=s.lower()), timeout=timeout) as resp:
                    sp = stooq_close_pairs(resp.read().decode())
                if len(sp) > len(pairs):
                    pairs = sp
                    print(f"{s}: yfinance short → stooq ({len(sp)} rows)")
            except Exception as exc:  # noqa: BLE001
                print(f"{s}: stooq fallback failed ({exc})")
        result[s] = _write_pairs(out / f"{s}.csv", pairs)
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
    ok = sum(1 for n in result.values() if n >= 30)
    print(f"{ok}/{len(result)} symbols with usable history → {args.out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
