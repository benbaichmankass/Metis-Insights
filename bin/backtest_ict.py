#!/usr/bin/env python3
"""
backtest_ict.py — multi-symbol / multi-timeframe backtest CLI for the ICT
strategy (FVG + Order Block + session filter).

Wraps the existing ``src.backtest.backtester.ICTBacktester`` so the same
detection logic that runs on a single CSV via ``src/backtest/run_backtest.py``
can be invoked across many symbol/timeframe pairs from one command. The CLI
is the repo-side counterpart to the Gemini-in-Colab research notebook
referenced in sprint-plan-2026-04-28.md § M7.

Status: scaffolding only — does **not** touch live trading, does not write
to ``trade_journal.db``, and does not modify any pipeline file. Output is
written to stdout and (optionally) to a JSON file.

Usage
-----

Provide a manifest CSV with one row per (symbol, timeframe, data_path)::

    symbol,timeframe,path
    BTCUSDT,5m,data/btc_5m.csv
    ETHUSDT,5m,data/eth_5m.csv

Then run::

    PYTHONPATH=. python bin/backtest_ict.py --manifest data/ict_manifest.csv

Or pass a single ad-hoc pair on the command line::

    PYTHONPATH=. python bin/backtest_ict.py \\
        --pair BTCUSDT:5m:data/btc_5m.csv \\
        --pair ETHUSDT:5m:data/eth_5m.csv \\
        --output reports/ict_multi.json

Each input CSV must have columns: ``timestamp, open, high, low, close, volume``.

Exit codes
----------
* 0 — every pair ran to completion (some may have produced 0 trades).
* 1 — at least one pair raised an exception or produced an unreadable file.
* 2 — argument / manifest error before any pair was attempted.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

logger = logging.getLogger("backtest_ict")


@dataclass
class Pair:
    """One symbol/timeframe/data_path triple to backtest."""
    symbol: str
    timeframe: str
    path: str


@dataclass
class PairResult:
    """Backtest outcome for a single Pair."""
    symbol: str
    timeframe: str
    path: str
    ok: bool
    summary: Optional[dict] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Pair / manifest loading
# ---------------------------------------------------------------------------


def parse_pair_arg(arg: str) -> Pair:
    """Parse a ``SYMBOL:TIMEFRAME:PATH`` string."""
    parts = arg.split(":")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(
            f"Invalid --pair {arg!r}; expected 'SYMBOL:TIMEFRAME:PATH'"
        )
    symbol, timeframe, path = (p.strip() for p in parts)
    return Pair(symbol=symbol, timeframe=timeframe, path=path)


def load_manifest(manifest_path: Path) -> List[Pair]:
    """Load a manifest CSV with columns symbol,timeframe,path."""
    df = pd.read_csv(manifest_path)
    required = {"symbol", "timeframe", "path"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Manifest {manifest_path} is missing columns: {sorted(missing)}"
        )
    pairs: List[Pair] = []
    for _, row in df.iterrows():
        pairs.append(
            Pair(
                symbol=str(row["symbol"]).strip(),
                timeframe=str(row["timeframe"]).strip(),
                path=str(row["path"]).strip(),
            )
        )
    return pairs


# ---------------------------------------------------------------------------
# Per-pair execution
# ---------------------------------------------------------------------------


def _load_ohlcv(path: Path) -> pd.DataFrame:
    """Read and lightly normalise an OHLCV CSV."""
    df = pd.read_csv(path)
    needed = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing OHLCV columns {missing}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def run_pair(pair: Pair, config: Optional[dict] = None) -> PairResult:
    """Backtest a single Pair. Catches all exceptions per-pair."""
    # Imported lazily so unit tests can import this module without pandas-
    # heavy backtester dependencies if they only exercise parsing helpers.
    from src.backtest.backtester import ICTBacktester

    try:
        df = _load_ohlcv(Path(pair.path))
    except FileNotFoundError as exc:
        return PairResult(pair.symbol, pair.timeframe, pair.path, False,
                          error=f"data file not found: {exc}")
    except Exception as exc:  # noqa: BLE001
        return PairResult(pair.symbol, pair.timeframe, pair.path, False,
                          error=f"data load error: {exc}")

    try:
        bt = ICTBacktester(df, config=config)
        bt.run()
        full_summary = bt.summary()
    except Exception as exc:  # noqa: BLE001
        return PairResult(pair.symbol, pair.timeframe, pair.path, False,
                          error=f"backtest error: {exc}")

    # Drop the per-trade list from the per-pair payload to keep the multi-
    # pair report compact; callers can still re-run a single pair to inspect
    # individual trades.
    compact = {k: v for k, v in full_summary.items() if k != "trades"}
    return PairResult(pair.symbol, pair.timeframe, pair.path, True,
                      summary=compact)


def run_all(pairs: Iterable[Pair], config: Optional[dict] = None) -> List[PairResult]:
    """Run every pair sequentially. Pairs do not share state."""
    return [run_pair(p, config=config) for p in pairs]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def aggregate(results: List[PairResult]) -> dict:
    """Build a small headline summary across all successful pair results."""
    ok_results = [r for r in results if r.ok and r.summary]
    total_trades = sum(int(r.summary.get("total_trades", 0)) for r in ok_results)
    total_winners = sum(int(r.summary.get("winners", 0)) for r in ok_results)
    win_rate = (total_winners / total_trades * 100.0) if total_trades else 0.0
    return {
        "pairs_total": len(results),
        "pairs_ok": len(ok_results),
        "pairs_failed": len(results) - len(ok_results),
        "trades_total": total_trades,
        "winners_total": total_winners,
        "win_rate_pct": round(win_rate, 2),
    }


def render_results(results: List[PairResult]) -> dict:
    """Build the full JSON report payload."""
    return {
        "aggregate": aggregate(results),
        "pairs": [asdict(r) for r in results],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Multi-symbol / multi-timeframe ICT backtest CLI.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--manifest",
        type=Path,
        help="CSV with columns symbol,timeframe,path.",
    )
    src.add_argument(
        "--pair",
        action="append",
        default=[],
        help="Ad-hoc pair as 'SYMBOL:TIMEFRAME:PATH'. May be repeated.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the full JSON report.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the aggregate, not per-pair details.",
    )
    p.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="JSON",
        help=(
            "JSON object of ICTBacktester config overrides, e.g. "
            "'{\"ob_confluence_only\": true, \"disable_session_filter\": true}'."
        ),
    )
    return p


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    config: Optional[dict] = None
    if args.config:
        try:
            config = json.loads(args.config)
            if not isinstance(config, dict):
                raise ValueError("--config must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("--config: %s", exc)
            return 2

    try:
        if args.manifest:
            pairs = load_manifest(args.manifest)
        else:
            pairs = [parse_pair_arg(a) for a in args.pair]
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 2

    if not pairs:
        logger.error("no pairs to run")
        return 2

    results = run_all(pairs, config=config)
    report = render_results(results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str))
        logger.info("wrote %s", args.output)

    if args.quiet:
        print(json.dumps(report["aggregate"], indent=2))
    else:
        print(json.dumps(report, indent=2, default=str))

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
