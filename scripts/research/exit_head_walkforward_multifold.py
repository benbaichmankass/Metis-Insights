#!/usr/bin/env python3
"""Multi-fold embargoed walk-forward re-grade of an exit head's recovered_R.

RQ-20260927-002 — extends RQ-20260927-001's single embargoed split (2025-01-06,
n=119, recovered_R_oos +1.3127) to a genuine walk-forward across several
sequential, non-overlapping test folds, so the promotion decision does not rest
on whether ONE split happened to land favourably.

WHAT THIS DOES, AND WHAT IT DOES NOT CHANGE
--------------------------------------------
Every scoring primitive is IMPORTED, UNMODIFIED, from the same two modules
RQ-20260927-001 used:

* ``scripts/ml/train_exit_head.py`` — ``train_model()`` (LightGBM, unchanged
  hyperparameters), ``FEATURES``/``TARGET`` (the "base" recipe), ``load_rows()``,
  ``EMBARGO_S`` (7 days).
* ``scripts/ml/exit_head_replay.py`` — ``_load_harness()`` (the live-faithful
  ``scripts/backtest_trend.py`` copy), ``_apply_venue_cost_policy()`` (net of
  the full fee+slippage+funding stack), ``replay_trade()`` (which itself
  imports ``src.runtime.exit_head_shadow._feature_row``/``would_exit_for`` —
  the SAME predicate the live monitor uses).

The ONLY new logic here is the FOLD BOUNDARY computation (below), because
``train_exit_head.py``'s own ``fold_blocks()`` partitions the multi-symbol
TRAINING dataset (``rows.jsonl``, keyed by ``trade_key``) for its own
AUC/policy-replay report — a different population from the BTCUSDT
``trend_donchian`` baseline trades this unit's ``recovered_R_oos`` statistic is
defined over (the population ``scripts/backtest_trend.py::run_backtest``
emits). This script mirrors ``fold_blocks()``'s own walk-forward convention
(sequential, trade-count blocks; first block held out as pure training seed;
a trailing partial block dropped and REPORTED, never silently absorbed) rather
than inventing a different one — see ``_fold_boundaries()``.

Usage (on the trainer, where ``rows.jsonl`` and the candle data live)::

    python3 scripts/research/exit_head_walkforward_multifold.py \\
        --strategy trend_donchian --symbol BTCUSDT --timeframe 1h \\
        --rows datasets-out/exit_head/1h/donchian/rows.jsonl \\
        --num-folds 3 --json /tmp/rq0927-002.json

Exit codes: 0 on a computed result (whatever the verdict), 2 when the
population genuinely cannot support the pre-registered floor (never silently
lowered — see ``research/queue/RQ-20260927-002.yaml``).
"""
# wiring: manual-only - a one-off research driver dispatched by hand via a
# trainer-vm-diag issue/workflow_dispatch (research/queue/RQ-20260927-002.yaml
# `run.workflow`). Not meant to be called from a registered CI/scheduled
# workflow -- see that unit's `run.note` for why a one-off dispatch is the
# right shape here rather than a permanent workflow input.
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "ml"))

MIN_FOLD_TEST_N = 64  # this unit's own power floor — identical derivation to
# RQ-20260927-001 / RQ-20260922-007: (1.96+0.8416)^2 / 0.35^2 = 64.07, ceil'd.
_MIN_FOLD_TRAIN_ROWS = 500  # train_exit_head._MIN_FOLD_TRAIN_ROWS, restated
# here rather than imported (it is module-private) so a fold that cannot
# support a fit says so by name instead of failing inside LightGBM.

SHAPE = {"policy": "below_half_r", "tau": 0.10, "below_r": 0.5}
ACTION = "close"


def _fold_boundaries(ordered_entry_times: List[float], num_folds: int) -> List[Dict[str, Any]]:
    """Sequential, non-overlapping trade-count blocks — the SAME convention
    ``train_exit_head.fold_blocks()`` uses (start at ``block_n``, step by
    ``block_n``; first block is pure training seed; a trailing partial block
    is dropped and reported, never silently folded in).

    Returns one dict per fold: ``{index, test_start_idx, test_end_idx,
    test_start_ts}`` (indices into the caller's sorted trade list).
    """
    n = len(ordered_entry_times)
    block_n = n // (num_folds + 1)
    folds = []
    for i in range(1, num_folds + 1):
        start = block_n * i
        end = block_n * (i + 1)
        if end > n:
            break
        folds.append({
            "index": i, "test_start_idx": start, "test_end_idx": end,
            "test_start_ts": ordered_entry_times[start],
        })
    covered = block_n * (len(folds) + 1)
    if covered < n:
        print(f"  note: {n - covered} trailing trade(s) not in any test fold "
              f"(partial block below block_n={block_n})", file=sys.stderr)
    return folds, block_n


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--strategy", default="trend_donchian")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--rows", default="datasets-out/exit_head/1h/donchian/rows.jsonl")
    p.add_argument("--num-folds", type=int, default=3)
    p.add_argument("--embargo-days", type=float, default=7.0)
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv[1:])

    import yaml
    import train_exit_head as teh  # scripts/ml/train_exit_head.py
    from exit_head_replay import (  # scripts/ml/exit_head_replay.py
        _load_harness, _apply_venue_cost_policy, replay_trade,
    )
    from scripts.ops import backtest_data_source

    with open(os.path.join(_REPO_ROOT, "config/strategies.yaml"), encoding="utf-8") as fh:
        conf = yaml.safe_load(fh)
    strategies = conf.get("strategies", conf)
    cfg = strategies.get(a.strategy)
    if not isinstance(cfg, dict):
        print(f"ERROR: no strategy {a.strategy!r} in config/strategies.yaml", file=sys.stderr)
        return 2

    res = backtest_data_source.resolve_or_refuse(a.symbol, a.timeframe, None)
    if not res.ok:
        print(backtest_data_source.refusal_message(
            res, harness="exit_head_walkforward_multifold.py",
            legacy_default=None), file=sys.stderr)
        return 2

    harness = _load_harness()
    cost_basis = _apply_venue_cost_policy(harness, a.symbol)
    df = harness._load_candles(res.path)

    trades: List[Any] = []
    baseline = harness.run_backtest(
        df.copy(), donchian=int(cfg.get("donchian", 20)),
        atr_period=int(cfg.get("atr_period", 14)),
        atr_stop_mult=float(cfg.get("atr_stop_mult", 2.5)),
        trail_mult=float(cfg.get("trail_mult", 3.0)),
        timeout_bars=200, cooldown_bars=1, timeframe=a.timeframe,
        symbol=a.symbol, min_confidence=float(cfg.get("min_confidence", 0.0)),
        long_only=bool(cfg.get("long_only")), trades_out=trades)

    ordered = sorted(trades, key=lambda t: t.entry_time)
    entry_epochs = [
        datetime.fromisoformat(str(t.entry_time).replace("Z", "+00:00")).timestamp()
        if not isinstance(t.entry_time, (int, float)) else float(t.entry_time)
        for t in ordered
    ]
    fold_defs, block_n = _fold_boundaries(entry_epochs, a.num_folds)
    if len(fold_defs) < a.num_folds:
        print(f"COULD NOT MEASURE: population n={len(ordered)} supports only "
              f"{len(fold_defs)} of {a.num_folds} requested folds at "
              f"block_n={block_n} (needs n >= {(a.num_folds + 1) * MIN_FOLD_TEST_N} "
              f"for every fold to clear the n>={MIN_FOLD_TEST_N} floor)",
              file=sys.stderr)

    from pathlib import Path

    rows_path = Path(a.rows) if os.path.isabs(a.rows) else Path(_REPO_ROOT) / a.rows
    rows = teh.load_rows(rows_path)
    embargo_s = a.embargo_days * 86400

    fold_results = []
    for fd in fold_defs:
        test_trades = ordered[fd["test_start_idx"]:fd["test_end_idx"]]
        train_cutoff_epoch = fd["test_start_ts"] - embargo_s
        train_rows = [r for r in rows if float(r.get("bar_t", 0)) <= train_cutoff_epoch]

        fold_out: Dict[str, Any] = {
            "fold_index": fd["index"],
            "n_test_trades": len(test_trades),
            "train_rows_n": len(train_rows),
            "test_start": datetime.fromtimestamp(fd["test_start_ts"], tz=timezone.utc).isoformat(),
            "train_cutoff": datetime.fromtimestamp(train_cutoff_epoch, tz=timezone.utc).isoformat(),
        }
        if len(test_trades) < MIN_FOLD_TEST_N:
            fold_out["read_state"] = "unestablishable"
            fold_out["reason"] = (
                f"n_test_trades={len(test_trades)} < floor {MIN_FOLD_TEST_N}")
            fold_results.append(fold_out)
            continue
        if len(train_rows) < _MIN_FOLD_TRAIN_ROWS:
            fold_out["read_state"] = "unestablishable"
            fold_out["reason"] = (
                f"train_rows_n={len(train_rows)} < floor {_MIN_FOLD_TRAIN_ROWS}")
            fold_results.append(fold_out)
            continue

        booster = teh.train_model(train_rows)
        artifact = {"features": teh.FEATURES, "shape": SHAPE}

        def predict(vec, _booster=booster):
            # provenance: predict — the head's RAW score, P(class=1) for this
            # BINARY LightGBM booster (train_model() above trains on the
            # binary target `holding_pays` via lgb.LGBMClassifier; a raw
            # lgb.Booster's predict() on a binary objective returns the
            # positive-class probability, never a class label or a
            # multiclass max — there is no multiclass head in this family).
            # Interpreted by would_exit_for per SHAPE, matching
            # exit_head_replay.py's own identical closure.
            return float(_booster.predict(vec)[0])

        records = [replay_trade(df, t, artifact, predict, ACTION) for t in test_trades]
        baseline_r = sum(r["baseline_r"] for r in records)
        replayed_r = sum(r["replayed_r"] for r in records)
        n_fired = sum(1 for r in records if r["exit_head_fired"])
        fold_out.update({
            "read_state": "measured",
            "baseline_gross_r": round(baseline_r, 4),
            "replayed_gross_r": round(replayed_r, 4),
            "recovered_r_oos": round(replayed_r - baseline_r, 4),
            "recovered_r_per_trade_oos": round(
                (replayed_r - baseline_r) / len(test_trades), 5),
            "n_fired_oos": n_fired,
        })
        fold_results.append(fold_out)

    measured = [f for f in fold_results if f.get("read_state") == "measured"]
    n_total = sum(f["n_test_trades"] for f in measured)
    pooled_recovered_r = sum(f["recovered_r_oos"] for f in measured)
    n_positive = sum(1 for f in measured if f["recovered_r_oos"] > 0)

    payload = {
        "schema_version": 1,
        "research_unit": "RQ-20260927-002",
        "strategy": a.strategy, "symbol": a.symbol, "timeframe": a.timeframe,
        "model_id": "exit-head-donchian-1h-v1",
        "baseline_summary_net_total_r": baseline.get("net_total_r"),
        "retrained_recipe": (
            "scripts/ml/train_exit_head.py train_model() unmodified: LightGBM, "
            "target=holding_pays, features=base, shape below_half_r tau=0.10 "
            "below_r=0.5 (unchanged from the live artifact)"),
        "method": "walk_forward_retrain_multi_fold",
        "num_folds_requested": a.num_folds,
        "num_folds_measured": len(measured),
        "embargo_days": a.embargo_days,
        "block_n": block_n,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "n_total_baseline_trades": len(ordered),
        "cost_basis": cost_basis,
        "net_of_cost_note": (
            "net-of-full-cost BY CONSTRUCTION: both arms are charged the "
            "identical per-trade fee_r(original trade); the gross paired "
            "delta equals the net paired delta."),
        "folds": fold_results,
        "pooled": {
            "n_oos_total": n_total,
            "recovered_r_oos_pooled": round(pooled_recovered_r, 4),
            "recovered_r_per_trade_oos_pooled": (
                round(pooled_recovered_r / n_total, 5) if n_total else None),
            "folds_positive": n_positive,
            "folds_measured": len(measured),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = json.dumps(payload, indent=2, sort_keys=True)
    print(out)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            fh.write(out)
    return 0 if measured else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
