#!/usr/bin/env python3
"""Build the committed-corpus MANIFEST by fetching real candles for every
(symbol, timeframe) a rostered leg trades, through the ONE existing fetcher
(``scripts/ops/fetch_backtest_candles.py`` -- Bybit/Binance-vision for crypto,
yfinance/Dukascopy for equities/ETFs/futures proxies). This script does not
re-derive fetch logic; it is the orchestration layer + the coverage record.

WHY THIS EXISTS (docs/claude/work/MANAGER-CHECKLIST.json row E4)
------------------------------------------------------------------
The default candle fixture (``data/backtest_candles.csv``) is 5,001 rows
spanning 3.5 days of 2022 -- real enough to smoke-test a harness, nowhere near
enough to measure a strategy. Every symbol a rostered leg trades
(``config/strategies.yaml`` x ``config/accounts.yaml``) needs its OWN real
history so ``scripts/research/m20_fleet_exit_sweep.py::resolve_data`` -- the
canonical (symbol, timeframe) -> file resolver every harness now goes through
via ``scripts/ops/backtest_data_source.py`` -- has something real to find.

WHY THE CSVs ARE NOT COMMITTED, ONLY THIS MANIFEST IS
-------------------------------------------------------
``.gitignore`` already has a DELIBERATE, dated rule for this
(`data/*.csv` ignored, `!data/ict_validate_manifest.csv` the one carved-out
exception): "Regenerable in minutes on the free lane ... data belongs in the
archive, not in git." Force-committing 46 fetched CSVs would be the exact
placeholder-data anti-pattern MI-157 already cost this repo once, just with
real numbers instead of flat ones -- and it would immediately go stale (crypto
in particular needs refreshing far more often than a git history should
carry). So this is the "committed manifest pointing to durable storage"
half of row E4's requirement: ``docs/reference/corpus-manifest.json`` is committed and
states exact coverage (symbol, timeframe, rows, start, end, source, and any
disclosed gap); the CSVs themselves are fetched on demand -- by a developer
running this script, or by a CI job that runs it before a harness -- and stay
gitignored exactly like ``data/ohlcv/`` already is.

Usage
-----
    python3 scripts/ops/fetch_backtest_corpus.py                # everything
    python3 scripts/ops/fetch_backtest_corpus.py --symbol BTCUSDT --timeframe 1h
    python3 scripts/ops/fetch_backtest_corpus.py --roster-only   # read the
        pairs straight from config/strategies.yaml x config/accounts.yaml
        instead of the table below (verifies the table has not drifted)
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FETCHER = REPO / "scripts" / "ops" / "fetch_backtest_candles.py"
MANIFEST_PATH = REPO / "docs" / "reference" / "corpus-manifest.json"

# (symbol, timeframe) -> (source, bybit-style interval code, trailing days).
# Every pair a rostered leg trades, per config/strategies.yaml x
# config/accounts.yaml as of 2026-09-25 (--roster-only recomputes this from
# the live config rather than trusting this table).
CRYPTO = "binance_vision"   # Bybit is geoblocked from both this host and GH runners
EQUITY = "yfinance"

PAIRS: dict[tuple[str, str], tuple[str, str, int]] = {
    # --- crypto (Bybit USDT linear perps; Binance-vision futures/um proxy) ---
    ("BTCUSDT", "1h"): (CRYPTO, "60", 180), ("BTCUSDT", "2h"): (CRYPTO, "120", 180),
    ("BTCUSDT", "4h"): (CRYPTO, "240", 180), ("BTCUSDT", "15m"): (CRYPTO, "15", 90),
    ("BTCUSDT", "5m"): (CRYPTO, "5", 30),
    ("ETHUSDT", "1h"): (CRYPTO, "60", 180), ("ETHUSDT", "2h"): (CRYPTO, "120", 180),
    ("ETHUSDT", "4h"): (CRYPTO, "240", 180), ("ETHUSDT", "15m"): (CRYPTO, "15", 90),
    ("SOLUSDT", "1h"): (CRYPTO, "60", 180), ("SOLUSDT", "2h"): (CRYPTO, "120", 180),
    ("SOLUSDT", "4h"): (CRYPTO, "240", 180), ("SOLUSDT", "15m"): (CRYPTO, "15", 90),
    ("SOLUSDT", "5m"): (CRYPTO, "5", 30),
    ("XRPUSDT", "2h"): (CRYPTO, "120", 180), ("XRPUSDT", "4h"): (CRYPTO, "240", 180),
    ("XRPUSDT", "15m"): (CRYPTO, "15", 90), ("XRPUSDT", "5m"): (CRYPTO, "5", 30),
    ("ADAUSDT", "2h"): (CRYPTO, "120", 180), ("ADAUSDT", "4h"): (CRYPTO, "240", 180),
    ("AVAXUSDT", "2h"): (CRYPTO, "120", 180), ("AVAXUSDT", "4h"): (CRYPTO, "240", 180),
    ("AVAXUSDT", "5m"): (CRYPTO, "5", 30),
    # --- equities/ETFs (yfinance; --interval is a Bybit-style code the ---
    # --- adapter maps to a yfinance one -- "60"->1h, "D"->1d) -----------
    ("GDX", "1d"): (EQUITY, "D", 3650), ("GLD", "1d"): (EQUITY, "D", 3650),
    ("GLD", "1h"): (EQUITY, "60", 720), ("IAUM", "1d"): (EQUITY, "D", 3650),
    ("IEF", "1d"): (EQUITY, "D", 3650), ("IWM", "1d"): (EQUITY, "D", 3650),
    ("QLD", "1d"): (EQUITY, "D", 3650), ("QQQ", "1d"): (EQUITY, "D", 3650),
    ("QQQ", "1h"): (EQUITY, "60", 720), ("SCHA", "1d"): (EQUITY, "D", 3650),
    ("SLV", "1d"): (EQUITY, "D", 3650), ("SLV", "1h"): (EQUITY, "60", 720),
    ("SPLG", "1d"): (EQUITY, "D", 3650), ("SPY", "1d"): (EQUITY, "D", 3650),
    ("SPY", "1h"): (EQUITY, "60", 720), ("TLT", "1d"): (EQUITY, "D", 3650),
    ("TLT", "1h"): (EQUITY, "60", 720), ("TQQQ", "1d"): (EQUITY, "D", 3650),
    ("USO", "1h"): (EQUITY, "60", 720),
    # --- declared futures proxies (PROXY_DATA in m20_fleet_exit_sweep.py) ---
    # fetch_backtest_candles.py resolves MGC/MES to GC=F/ES=F internally
    # (ml/datasets/adapters/yf_symbols.py, the one ticker map) but WRITES
    # under whatever --output this script gives it, so the file lands under
    # the PROXY spelling the resolver's PROXY_DATA table expects.
    ("MGC", "15m"): (EQUITY, "15", 60), ("MGC", "1h"): (EQUITY, "60", 720),
    ("MGC", "1d"): (EQUITY, "D", 3650),
    ("MES", "1d"): (EQUITY, "D", 3650),
    ("MHG", "1d"): (EQUITY, "D", 3650),
}

PROXY_WRITE_NAME = {"MGC": "GC_F", "MES": "ES_F", "MHG": "HG_F"}


def roster_pairs() -> set[tuple[str, str]]:
    """Recompute (symbol, timeframe) straight from the live config, so a
    caller can verify PAIRS above has not drifted from the actual roster."""
    sys.path.insert(0, str(REPO))
    from src.config.accounts_loader import load_accounts_dict
    from src.units.strategies import load_strategy_config
    strat = load_strategy_config()
    acc = load_accounts_dict()
    rostered = {s for a in acc.values() for s in (a.get("strategies") or [])}
    pairs = set()
    for name in rostered:
        d = strat.get(name)
        if not d:
            continue
        tf = d.get("timeframe")
        for sym in (d.get("symbols") or ([d["symbol"]] if d.get("symbol") else [])):
            pairs.add((sym, tf))
    return pairs


def fetch_one(symbol: str, timeframe: str, source: str, interval: str, days: int) -> dict:
    write_sym = PROXY_WRITE_NAME.get(symbol, symbol)
    out = REPO / "data" / f"{write_sym}_{timeframe}.csv"
    cmd = [sys.executable, str(FETCHER), "--symbol", symbol, "--source", source,
           "--interval", interval, "--days", str(days), "--output", str(out)]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=180)
    entry = {"symbol": write_sym, "timeframe": timeframe,
             "file": f"data/{write_sym}_{timeframe}.csv",
             "source": source, "proxy_for": symbol if write_sym != symbol else None}
    if proc.returncode != 0 or not out.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-2:]
        entry.update(rows=0, start=None, end=None, gap="; ".join(tail) or "fetch failed")
        return entry
    with out.open(newline="") as f:
        rows = list(csv.reader(f))[1:]
    entry.update(rows=len(rows),
                 start=rows[0][0] if rows else None,
                 end=rows[-1][0] if rows else None, gap=None)
    return entry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default=None, help="fetch one symbol only")
    ap.add_argument("--timeframe", default=None, help="with --symbol, one timeframe only")
    ap.add_argument("--roster-only", action="store_true",
                    help="verify PAIRS matches config/strategies.yaml x "
                         "config/accounts.yaml and exit non-zero on drift, "
                         "without fetching anything")
    args = ap.parse_args(argv)

    if args.roster_only:
        live = roster_pairs()
        declared = set(PAIRS.keys())
        missing = live - declared
        extra = declared - live
        if missing or extra:
            print(f"PAIRS table has drifted from the live roster: "
                  f"missing={sorted(missing)} extra={sorted(extra)}")
            return 1
        print(f"PAIRS table matches the live roster ({len(live)} pairs).")
        return 0

    targets = PAIRS
    if args.symbol:
        targets = {k: v for k, v in PAIRS.items()
                  if k[0] == args.symbol and (not args.timeframe or k[1] == args.timeframe)}
        if not targets:
            print(f"no declared pair for symbol={args.symbol!r} timeframe={args.timeframe!r}")
            return 2

    manifest = MANIFEST_PATH.exists() and json.loads(MANIFEST_PATH.read_text()).get("pairs", [])
    by_key = {(e["symbol"], e["timeframe"]): e for e in (manifest or [])}

    for (symbol, tf), (source, interval, days) in targets.items():
        print(f"{symbol} {tf} (source={source}) ...")
        entry = fetch_one(symbol, tf, source, interval, days)
        by_key[(entry["symbol"], entry["timeframe"])] = entry
        status = f"{entry['rows']} rows" if not entry["gap"] else f"GAP: {entry['gap']}"
        print(f"  {status}")

    out = {
        "_comment": "Per (symbol, timeframe) committed-corpus coverage. Built by "
                    "scripts/ops/fetch_backtest_corpus.py; cross-checked by "
                    "scripts/ci/check_corpus_row_floor.py. The CSVs themselves are "
                    "gitignored (data/*.csv) and fetched on demand -- see "
                    "docs/reference/backtest-data-loading.md.",
        "pairs": sorted(by_key.values(), key=lambda e: (e["symbol"], e["timeframe"])),
    }
    MANIFEST_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(f"manifest written: {MANIFEST_PATH} ({len(by_key)} pairs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
