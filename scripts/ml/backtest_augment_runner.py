#!/usr/bin/env python3
"""A1 research-backtest-augment runner (WORK-PLAN-2026-08-02 W1.2).

Produce the **augmented backtest DB** the pooled decision models
(`setup_candidates` / `trade_outcomes` families) train on: run each
config-exact harness in the pooled roster, per symbol, `--emit-trades`, and
record the results as **`is_backtest=1`** rows into ONE `backtest_trades.db`
artifact. That artifact is the W1.2 → W2.1 handoff — the trainer step
(`include_backtest=True` dataset build + retrain + **live-only holdout** eval)
is the FIFO-lane follow-on, NOT this runner.

This runner is a COMPOSITION of two existing, tested tools — it re-implements
neither the fetch/harness half nor the record half:

1. **Emit** — `scripts.research.regime_debt_matrix.emit_trades_for(name, cfg,
   workdir, days, symbol_override=SYM)` resolves the feed (Binance-vision for
   `*USDT` crypto), builds the config-exact harness cmd with the corrected venue
   fee, runs it with `--emit-trades`, and returns the per-trade JSONL path +
   fidelity. `symbol_override` replays the SAME strategy config on each pooled
   symbol (the timeframe stays the config's own — `trend_donchian` stays 1h on
   BTC/ETH/SOL).
2. **Record** — `harness_row_to_sim_trade` + `write_backtest_trades` (the
   `scripts.ml.record_harness_trades` / `ml.datasets.backtest_recorder` path)
   normalise each emit row and INSERT it as an **`is_backtest=1`** row. The
   canonical `trades` schema is created in the fresh temp DB first via
   `Database(db_path=...)` (on a runner there is no journal to copy).

**Roster — VERIFIED against the pinned pooled manifest**
`ml/configs/setup-candidates-metalabel-p2pool-v1.yaml` (M23 P2, `symbol_scope:
all`, `split_strategy: live_holdout`): the 3-strategy harness roster replayed
per symbol. The manifest's build command encodes it explicitly
(`market_raw_paths=<BTC 1h>,<ETH 1h>,<SOL 1h>` + `backtest_trades_db=<tmp.db>`).
The runner hardcodes it below with the manifest reference; **re-read the pinned
manifest before a run** — a manifest bump could add a symbol/strategy, and
augmenting a `(strategy, symbol)` the pooled model never reads is wasted rows
while missing one it does biases the holdout. See
`docs/research/A1-backtest-augment-runner-SCOPE-2026-08-02.md`.

| harness strategy       | timeframe | symbols                     |
|------------------------|-----------|-----------------------------|
| `trend_donchian`       | 1h        | BTCUSDT, ETHUSDT, SOLUSDT   |
| `squeeze_breakout_4h`  | 4h        | BTCUSDT, ETHUSDT, SOLUSDT   |
| `htf_pullback_trend_2h`| 2h        | BTCUSDT, ETHUSDT, SOLUSDT   |

**Safety.** Writes ONLY `is_backtest=1` rows (excluded by every live / stats /
default dataset path) into a TEMP artifact DB — NEVER the production money DB.
Tier-1 research tooling: no live-path file, no order path, no registry write,
authors no cell.

Usage (on the free runner; see `.github/workflows/research-backtest-augment.yml`):

    python scripts/ml/backtest_augment_runner.py \
      --db artifacts/a1/backtest_trades.db --workdir artifacts/a1/work \
      --days 730 --run-tag a1-crypto-2026-08-03 --out-dir artifacts/a1
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Roster VERIFIED against ml/configs/setup-candidates-metalabel-p2pool-v1.yaml
# (2026-08-02). Re-verify against the pinned manifest before a run (docstring).
DEFAULT_ROSTER = ("trend_donchian", "squeeze_breakout_4h", "htf_pullback_trend_2h")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def run(args: argparse.Namespace) -> int:
    # Imports are inside run() so `--help` / arg-parsing (and the unit test's
    # roster/CLI checks) don't require pandas/numpy/yaml on the import path.
    from scripts.research.regime_debt_matrix import emit_trades_for, resolve_strategy
    from scripts.ml.record_harness_trades import harness_row_to_sim_trade
    from ml.datasets.backtest_recorder import write_backtest_trades
    from src.units.db.database import Database

    roster = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else list(DEFAULT_ROSTER)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else list(DEFAULT_SYMBOLS)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    # Create the canonical `trades` schema in the fresh temp DB (write_backtest_trades
    # REQUIRES the table to pre-exist; on a runner there is no journal to copy).
    Database(db_path=str(db_path))

    run_tag = args.run_tag or f"a1-crypto-{date.today().isoformat()}"
    legs: List[Dict[str, Any]] = []
    total_recorded = 0
    for name in roster:
        cfg = resolve_strategy(name)
        if cfg is None:
            legs.append({"strategy": name, "symbol": None,
                         "error": "not declared in strategies.yaml", "recorded": 0})
            print(f"[a1] {name}: NOT in strategies.yaml — skipped", file=sys.stderr)
            continue
        for sym in symbols:
            emit = emit_trades_for(name, cfg, str(workdir), args.days, symbol_override=sym)
            leg: Dict[str, Any] = {
                "strategy": name, "symbol": sym, "timeframe": emit.get("timeframe"),
                "harness": emit.get("harness"), "fidelity": emit.get("fidelity"),
                "feed": (emit.get("feed") or {}).get("source"),
                "fee_bps_roundtrip": emit.get("fee_bps_roundtrip"),
                "omitted_levers": emit.get("omitted_levers"),
                "n_emitted": emit.get("n_emitted", 0), "recorded": 0, "skipped": 0,
            }
            if emit.get("error") or not emit.get("emit_path"):
                leg["error"] = emit.get("error") or "no emit produced"
                legs.append(leg)
                print(f"[a1] {name}@{sym}: emit FAILED — {leg['error']}", file=sys.stderr)
                continue
            sim_trades: List[Dict[str, Any]] = []
            skipped = 0
            for obj in _iter_jsonl(Path(emit["emit_path"])):
                mapped = harness_row_to_sim_trade(obj, symbol=sym, default_strategy=name)
                if mapped is None:
                    skipped += 1
                else:
                    sim_trades.append(mapped)
            # Persist the leg's OWN fidelity claim onto every row it writes.
            # The runner has always KNOWN this (it prints it in the summary
            # below) but never stored it, so `backtest_fidelity_calibrate`
            # could not read it and would certify a leg as `calibrated`
            # ("TRUSTED OOS evidence") on rows the producing harness itself
            # reports faithful=False. Measured 2026-08-07: `trend_donchian` —
            # the primary calibration target — omits five EXIT levers.
            written = write_backtest_trades(
                db_path, sim_trades, run_tag=run_tag, risk_pct=args.risk_pct,
                fidelity=leg.get("fidelity"),
                omitted_levers=leg.get("omitted_levers"),
            )
            leg["recorded"] = written
            leg["skipped"] = skipped
            total_recorded += written
            legs.append(leg)
            print(f"[a1] {name}@{sym}: emitted {leg['n_emitted']} → recorded {written} "
                  f"is_backtest=1 rows ({leg['fidelity']}, {skipped} skipped)", file=sys.stderr)

    generated_at = datetime.now(timezone.utc).isoformat()
    ok = [leg for leg in legs if not leg.get("error")]
    payload = {
        "generated_at": generated_at, "run_tag": run_tag, "db": str(db_path),
        "days": args.days, "roster": roster, "symbols": symbols,
        "manifest": "ml/configs/setup-candidates-metalabel-p2pool-v1.yaml",
        "legs_total": len(legs), "legs_ok": len(ok),
        "legs_failed": len(legs) - len(ok),
        "total_recorded_is_backtest_rows": total_recorded, "legs": legs,
    }

    out_dir = Path(args.out_dir) if args.out_dir else db_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backtest_augment_result.json").write_text(json.dumps(payload, indent=2, default=str))

    # SUMMARY.md — state the POPULATION (which (strategy,symbol,timeframe) legs
    # were recorded, per-leg fidelity + count) so a reader can never mistake a
    # partial roster for "the whole augment" (diagnostic-provenance).
    L = ["# A1 backtest-augment runner", "",
         f"_Generated {generated_at}; run-tag `{run_tag}`; {args.days}d; "
         f"manifest `setup-candidates-metalabel-p2pool-v1.yaml`_",
         f"_Output: `{db_path}` — **`is_backtest=1` rows only** (temp artifact DB, "
         "never the production money DB)_", "",
         f"**{total_recorded}** `is_backtest=1` rows recorded across "
         f"**{len(ok)}/{len(legs)}** legs "
         f"({len(legs) - len(ok)} failed).", "",
         "| strategy | symbol | tf | harness | fidelity | feed | fee bps | emitted | recorded | note |",
         "|---|---|---|---|---|---|--:|--:|--:|---|"]
    for leg in legs:
        note = leg.get("error") or (
            f"omitted: {', '.join(leg['omitted_levers'])}" if leg.get("omitted_levers") else "")
        L.append(
            f"| `{leg['strategy']}` | {leg.get('symbol') or '—'} | "
            f"{leg.get('timeframe') or '—'} | {leg.get('harness') or '—'} | "
            f"{leg.get('fidelity') or '—'} | {leg.get('feed') or '—'} | "
            f"{leg.get('fee_bps_roundtrip')} | {leg.get('n_emitted', 0)} | "
            f"{leg.get('recorded', 0)} | {note} |")
    L += ["",
          "**Handoff (W2.1, trainer FIFO lane — NOT this runner):** build "
          "`include_backtest=True` pooled `setup_candidates` datasets scoped to "
          f"run-tag `{run_tag}`, retrain, and evaluate on a **live-only holdout**; "
          "that verdict is the A1 answer. Authors no cell; a promotion is Tier-3.",
          "",
          "_Read-only research tooling. Writes only `is_backtest=1` rows into a "
          "temp artifact DB._"]
    summary = "\n".join(L)
    (out_dir / "SUMMARY.md").write_text(summary)
    print(summary)
    print(f"\n[a1] wrote {out_dir / 'SUMMARY.md'} + {db_path}", file=sys.stderr)
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A1 backtest-augment runner (W1.2).")
    p.add_argument("--db", default="artifacts/a1/backtest_trades.db",
                   help="Output SQLite (TEMP artifact — is_backtest=1 rows only; NOT prod).")
    p.add_argument("--workdir", default="artifacts/a1/work",
                   help="Scratch dir for per-(strategy,symbol) candle CSVs + emit JSONLs.")
    p.add_argument("--only", default=None,
                   help="CSV of harness strategy names (default: the pooled roster "
                        f"{', '.join(DEFAULT_ROSTER)}).")
    p.add_argument("--symbols", default=None,
                   help=f"CSV of symbols to replay each strategy on (default: "
                        f"{', '.join(DEFAULT_SYMBOLS)}).")
    p.add_argument("--days", type=int, default=730, help="Trailing days of candles.")
    p.add_argument("--run-tag", default=None,
                   help="notes tag on every recorded row (default: a1-crypto-<UTC-date>).")
    p.add_argument("--risk-pct", type=float, default=1.0)
    p.add_argument("--out-dir", default=None,
                   help="Where SUMMARY.md + result JSON land (default: the --db dir).")
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
