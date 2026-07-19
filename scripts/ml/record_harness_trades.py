#!/usr/bin/env python3
"""Record standalone-harness backtest trades into a DB as `is_backtest=1` rows.

S-MLOPT-S6-FU-2 (M14). The bridge that lets the `setup_candidates` family's
`backtest_trades_db` source (real-execution labels) be fed from the strategies'
**standalone** backtest harnesses — `scripts/backtest_{squeeze,fade,trend,
ict_scalp}.py` and `src/backtest/run_backtest_vwap.py`.

Those harnesses emit a per-trade JSONL (one object per line) via their `--emit`
/ trades-jsonl flag, e.g. ``{"strategy": "trend_donchian", "entry_time": "...",
"direction": "long", "gross_r": 1.2, "net_r": 0.9, "confidence": 0.4}``. The
S-MLOPT-S7 recorder (`ml.datasets.backtest_recorder`) maps a `SimTrade`-shaped
mapping to an `is_backtest=1` trades row, but the harness JSONL uses different
keys (``entry_time``/``net_r``) and carries no ``exit_ts`` (which the recorder
requires to be non-NULL). This script normalises the harness rows to the
recorder's shape and persists them — reusing `write_backtest_trades` so the
exact same INSERT path (and the `is_backtest=1`-only safety contract) is shared.

**Realized outcome.** ``net_r`` (fee-adjusted realized R) is preferred over
``gross_r``; either becomes the recorder's ``r_multiple`` (and thus the row's
``pnl`` R-proxy → ``won = R > 0``). ``exit_ts`` falls back to ``entry_time``
when the harness JSONL omits it (``setup_candidates`` locates a backtest trade
by its **entry** ``timestamp`` only, so the exit ts is just the recorder's
non-NULL sentinel here).

**Safety.** Writes ONLY ``is_backtest=1`` rows (every live / stats / default
dataset path filters `is_backtest=0`). Point ``--db`` at a TEMP DB or a TEMP
COPY of the journal — never the production money DB. Tier-1 trainer-side
tooling: no live-path file, no order path, no registry write.

Usage (on the trainer VM):

    python -m scripts.ml.record_harness_trades \
      --db datasets-out/backtest_trades.db --symbol BTCUSDT \
      --trades-jsonl runtime_logs/bt/trend.jsonl=trend_donchian \
      --trades-jsonl runtime_logs/bt/squeeze.jsonl=squeeze \
      --run-tag s6fu2-2026-06-03
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# Allow `python scripts/ml/record_harness_trades.py` as well as `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.backtest_recorder import write_backtest_trades  # noqa: E402

_LONG_ALIASES = frozenset({"long", "buy", "1", "+1"})


def harness_row_to_sim_trade(
    row: dict[str, Any],
    *,
    symbol: str,
    default_strategy: str = "",
) -> dict[str, Any] | None:
    """Normalise one harness trade-JSONL object to a `SimTrade`-shaped mapping.

    Returns ``None`` for a row with no realized R (an open / unlabeled trade —
    nothing to learn from), mirroring the recorder's own skip rule.
    """
    r = row.get("net_r")
    if r is None:
        r = row.get("gross_r")
    if r is None:
        r = row.get("r_multiple")
    if r is None:
        return None
    entry_ts = row.get("entry_time") or row.get("entry_ts")
    if not entry_ts:
        return None
    exit_ts = row.get("exit_time") or row.get("exit_ts") or entry_ts
    direction_raw = str(row.get("direction", "")).lower()
    direction = "long" if direction_raw in _LONG_ALIASES else "short"
    # The caller's explicit `--trades-jsonl PATH=STRATEGY` label WINS over the
    # row's self-reported name (ml-infra audit 2026-07-19): backtest_squeeze.py
    # hardcodes strategy="squeeze_breakout" in every emitted row while the live
    # book's name is squeeze_breakout_4h, so with row-field precedence the
    # orchestrators' override was a silent no-op and pooled rows mislabeled.
    # An override is an override; the row field is only the fallback.
    strategy = str(default_strategy or row.get("strategy") or "backtest")
    return {
        "strategy": strategy,
        "symbol": str(row.get("symbol") or symbol),
        "direction": direction,
        "entry_ts": str(entry_ts),
        "exit_ts": str(exit_ts),
        "entry": row.get("entry"),
        "exit": row.get("exit_price") or row.get("exit"),
        "sl": row.get("sl"),
        "tp": row.get("tp"),
        "r_multiple": float(r),
        "exit_reason": row.get("outcome"),
        "meta": {},
    }


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _parse_spec(spec: str) -> tuple[Path, str]:
    """``path=strategy`` or just ``path`` (strategy then comes from the row)."""
    if "=" in spec:
        path_str, strat = spec.split("=", 1)
        return Path(path_str.strip()), strat.strip()
    return Path(spec.strip()), ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path,
                    help="Output DB (TEMP / temp copy of the journal — NOT prod).")
    ap.add_argument("--symbol", required=True,
                    help="Symbol the harness traded (e.g. BTCUSDT).")
    ap.add_argument("--trades-jsonl", action="append", default=[], metavar="PATH[=STRATEGY]",
                    help="A harness trade-JSONL file; optional =strategy override. Repeatable.")
    ap.add_argument("--run-tag", default="harness-backtest",
                    help="`notes` tag stamped on every recorded row.")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    args = ap.parse_args(argv)

    if not args.trades_jsonl:
        ap.error("at least one --trades-jsonl is required")

    sim_trades: list[dict[str, Any]] = []
    skipped = 0
    for spec in args.trades_jsonl:
        path, strat = _parse_spec(spec)
        if not path.is_file():
            sys.stderr.write(f"warning: trades-jsonl not found: {path}\n")
            continue
        for row in _iter_jsonl(path):
            mapped = harness_row_to_sim_trade(
                row, symbol=args.symbol, default_strategy=strat,
            )
            if mapped is None:
                skipped += 1
            else:
                sim_trades.append(mapped)

    written = write_backtest_trades(
        args.db, sim_trades, run_tag=args.run_tag, risk_pct=args.risk_pct,
    )
    print(json.dumps({
        "db": str(args.db),
        "symbol": args.symbol,
        "recorded_is_backtest_rows": written,
        "skipped_unlabeled": skipped,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
