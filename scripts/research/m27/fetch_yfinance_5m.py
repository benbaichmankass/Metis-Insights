#!/usr/bin/env python3
"""M27 P0 Batch-3 data puller — equities/ETF intraday bars to CSV (yfinance).

Runs ON the trainer VM (the sandbox proxy blocks exchange/data endpoints).
Uses yfinance (keyless, free) rather than Alpaca's data API — Batch-3 is a
RESEARCH signal-detection pass (no live equities scalp leg exists yet to be
"config-exact" against), so a free, credential-free source is preferred over
provisioning any Alpaca key onto the trainer (the live/paper Alpaca key pairs
are production secrets and CLAUDE.md's VM-authority split forbids copying
production secrets to the trainer regardless of read-only intent).

HONEST LIMITATION (record this in the findings doc, don't silently omit it):
yfinance's intraday granularity is capped by Yahoo's own API — 5m/15m bars
are only available for the trailing ~60 days, nowhere near Bybit's multi-year
or IBKR's ~1-year pulls used in Batch-1/Batch-2. Batch-3's trade counts will
be lower for that reason alone; if a symbol looks underpowered, check the
window length before concluding "setup rarity" (mirrors the Batch-2 low-
signal diagnostic caveat).

Writes one CSV per symbol (columns: timestamp,open,high,low,close,volume —
the exact shape scripts/backtest_ict_scalp.py::_load_candles reads), tz-aware
+00:00 timestamps (the #7199 contract), RTH-only (yfinance's intraday bars
are exchange-session bars already, no synthetic overnight fill).

Usage (trainer):
  .venv/bin/python scripts/research/m27/fetch_yfinance_5m.py \
      --out-dir /home/ubuntu/m27_data_eq --interval 5m --period 60d \
      --symbols SPY QQQ IWM TLT GLD SLV GDX USO IEF

INTERVAL CAP DIFFERS BY GRANULARITY (PB-20260721-M27-EQUITIES-DATACAP,
verified 2026-07-21 via a direct trainer-side probe against SPY, not assumed):
5m/15m/30m/90m are ALL hard-capped by Yahoo at ~60 days server-side regardless
of the requested --period. 60m/1h has no such cap and returns ~2.9 years of
history (5,082 SPY bars, 2023-08-21..2026-07-20 observed) — pass
``--interval 60m --period max`` for a statistically-powered run at that
granularity; ``--period`` is otherwise ignored by Yahoo above 60d for the
finer intervals so leave it at the default there.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def fetch_symbol(symbol: str, interval: str, period: str, out_path: Path) -> None:
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        print(f"{symbol}: 0 bars (empty response) -> {out_path}")
        with out_path.open("w") as fh:
            fh.write("timestamp,open,high,low,close,volume\n")
        return

    df = df.reset_index()
    ts_col = "Datetime" if "Datetime" in df.columns else df.columns[0]

    with out_path.open("w", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for _, row in df.iterrows():
            ts = row[ts_col]
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            iso = ts.strftime("%Y-%m-%d %H:%M:%S+00:00")
            fh.write(f"{iso},{row['Open']},{row['High']},{row['Low']},{row['Close']},{row['Volume']}\n")

    first = df[ts_col].iloc[0]
    last = df[ts_col].iloc[-1]
    print(f"{symbol}: {len(df)} bars  {first.date()} .. {last.date()}  -> {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--interval", default="5m", choices=["5m", "15m", "60m", "1h"])
    ap.add_argument("--period", default="60d",
                     help="yfinance lookback window (max ~60d for 5m/15m/30m/90m; "
                          "60m/1h is uncapped by period, use e.g. 'max' or '730d')")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = 0
    for sym in args.symbols:
        try:
            fetch_symbol(sym, args.interval, args.period, out_dir / f"{sym}_{args.interval}.csv")
        except Exception as exc:  # noqa: BLE001 — this is a best-effort research puller
            print(f"{sym}: FAILED — {exc}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
