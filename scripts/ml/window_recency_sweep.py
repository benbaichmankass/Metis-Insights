#!/usr/bin/env python3
"""Train-window-length / recency-weighting sweep against a FIXED recent holdout.

S-MLOPT-S2 (M14 Session 0.2). Tier-1 trainer-side tooling — never edits a
manifest on disk, never registers a model, never touches a live-path file.

Closes MB-20260601-001: widening the BTC regime training window to 5y *lowered*
`f1_volatile` (older history dilutes the recent volatility regime). This sweeps
the train-window length and a recency-decay variant, **all evaluated on the same
fixed recent holdout**, so the configs are directly comparable. Purge a
`label_horizon`-row gap between train and holdout so the comparison stays
leakage-free (reuses the S-MLOPT-S1 primitive).

Run on the trainer VM (datasets live there), e.g.:

    cd /home/ubuntu/ict-trading-bot && . .venv/bin/activate
    python -m scripts.ml.window_recency_sweep \
      --manifest ml/configs/btc-regime-1h-lgbm-v2.yaml \
      --datasets-root datasets-out --windows-years 1,2,3,5 \
      --decay-half-life-days 180 --metric-key f1_volatile

Output is a single JSON object on stdout: per-window n_train + full metrics,
plus the best config for `--metric-key`.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.experiments.runner import _load_jsonl  # noqa: E402
from ml.experiments.splitters import purge_and_embargo_indices  # noqa: E402
from ml.manifest import TrainingManifest  # noqa: E402
from ml.trainers.sample_weights import _parse_ts  # noqa: E402

_YEAR_SECONDS = 365.25 * 86400.0


def _resolve(qualname: str):
    mod, _, attr = qualname.rpartition(".")
    return getattr(importlib.import_module(mod), attr)


def _evaluate_window(
    *,
    sorted_rows: list[dict[str, Any]],
    times: list[float],
    holdout_start: int,
    train_lo_time: float,
    purge_horizon: int,
    trainer,
    evaluator,
    trainer_config: dict[str, Any],
    evaluator_config: dict[str, Any],
) -> dict[str, Any]:
    """Fit on rows in [train_lo_time, purge boundary) and score the fixed holdout."""
    # Purge the last `purge_horizon` rows before the holdout (label overlap).
    candidate = [
        i for i in range(holdout_start)
        if times[i] >= train_lo_time
    ]
    train_idx = purge_and_embargo_indices(
        candidate, holdout_start, len(sorted_rows), purge_horizon, 0
    )
    train_rows = [sorted_rows[i] for i in train_idx]
    holdout_rows = sorted_rows[holdout_start:]
    if not train_rows:
        return {"n_train": 0, "metrics": None, "error": "empty_train_window"}
    state = dict(trainer.fit(train_rows, trainer_config))
    metrics = dict(evaluator.score(state, holdout_rows, evaluator_config))
    return {"n_train": len(train_rows), "metrics": metrics}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--datasets-root", default=Path("datasets-out"), type=Path)
    ap.add_argument("--windows-years", default="1,2,3,5")
    ap.add_argument("--holdout-fraction", type=float, default=0.2)
    ap.add_argument(
        "--label-horizon", type=int, default=1,
        help="Purge gap (rows) between the train window and the holdout.",
    )
    ap.add_argument(
        "--decay-half-life-days", type=float, default=180.0,
        help="Recency half-life for the '<max>y+decay' variant (0 disables it).",
    )
    ap.add_argument(
        "--metric-key", default="f1_volatile",
        help="Metric to rank configs by (higher is better).",
    )
    args = ap.parse_args(argv)

    manifest = TrainingManifest.from_yaml(args.manifest)
    dataset_dir = manifest.dataset.path_under(args.datasets_root)
    rows = _load_jsonl(dataset_dir / "data.jsonl")
    if not rows:
        sys.stderr.write("dataset is empty\n")
        return 2

    eval_cfg = dict(manifest.evaluator_config)
    time_col = str(eval_cfg.get("time_column", "ts"))
    sorted_rows = sorted(rows, key=lambda r: r.get(time_col, ""))
    n = len(sorted_rows)
    times = [_parse_ts(r.get(time_col)) for r in sorted_rows]
    if any(t is None for t in times):
        bad = next(i for i, t in enumerate(times) if t is None)
        sys.stderr.write(
            f"row {bad} has an unparseable {time_col!r}: {sorted_rows[bad].get(time_col)!r}\n"
        )
        return 2

    holdout_start = int(round(n * (1.0 - args.holdout_fraction)))
    holdout_start = max(1, min(holdout_start, n - 1))
    holdout_lo_time = times[holdout_start]

    trainer = _resolve(manifest.trainer)()
    evaluator = _resolve(manifest.evaluator)()
    base_trainer_cfg = dict(manifest.trainer_config)

    windows = [float(w) for w in str(args.windows_years).split(",") if w.strip()]
    results: dict[str, Any] = {}
    for w in windows:
        lo = holdout_lo_time - w * _YEAR_SECONDS
        results[f"{w:g}y"] = _evaluate_window(
            sorted_rows=sorted_rows, times=times, holdout_start=holdout_start,
            train_lo_time=lo, purge_horizon=args.label_horizon,
            trainer=trainer, evaluator=evaluator,
            trainer_config=base_trainer_cfg, evaluator_config=eval_cfg,
        )

    # Largest window + recency decay variant.
    if args.decay_half_life_days and windows:
        w_max = max(windows)
        decay_cfg = dict(base_trainer_cfg)
        decay_cfg["sample_weight"] = {
            "half_life_days": args.decay_half_life_days,
            "time_column": time_col,
        }
        results[f"{w_max:g}y+decay(hl={args.decay_half_life_days:g}d)"] = _evaluate_window(
            sorted_rows=sorted_rows, times=times, holdout_start=holdout_start,
            train_lo_time=holdout_lo_time - w_max * _YEAR_SECONDS,
            purge_horizon=args.label_horizon,
            trainer=trainer, evaluator=evaluator,
            trainer_config=decay_cfg, evaluator_config=eval_cfg,
        )

    # Rank by the target metric.
    ranked = [
        (name, r["metrics"][args.metric_key])
        for name, r in results.items()
        if r.get("metrics") and args.metric_key in r["metrics"]
    ]
    ranked.sort(key=lambda kv: kv[1], reverse=True)

    print(json.dumps({
        "model_id": manifest.model_id,
        "dataset": manifest.dataset.to_dict(),
        "time_column": time_col,
        "n_rows": n,
        "holdout_fraction": args.holdout_fraction,
        "holdout_n": n - holdout_start,
        "label_horizon_purge": args.label_horizon,
        "metric_key": args.metric_key,
        "results": results,
        "ranked_by_metric": ranked,
        "best": ranked[0][0] if ranked else None,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
