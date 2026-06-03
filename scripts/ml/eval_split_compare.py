#!/usr/bin/env python3
"""Compare a manifest's authored holdout eval against purged walk-forward CV.

S-MLOPT-S1 (M14 ML-Optimization, Phase 0.1). Tier-1 trainer-side tooling:
this never edits a manifest on disk, never registers a model, and never
touches a live-path file. It runs `ml.experiments.runner.run_experiment`
twice over the SAME dataset — once with the manifest's authored
`evaluator_config.split_strategy` (the optimistic 80/20 time-aware holdout),
once with that strategy overridden to `purged_walk_forward` — and prints the
metric delta so the operator can see how much the honest, leak-free estimate
moves vs the holdout.

Run on the trainer VM (where the datasets live), e.g.:

    cd /home/ubuntu/ict-trading-bot && . .venv/bin/activate
    python -m scripts.ml.eval_split_compare \
      --manifest ml/configs/setup-quality-lgbm-v2.yaml \
      --datasets-root datasets-out \
      --n-folds 5 --label-horizon 1 --embargo-fraction 0.01

Output is a single JSON object on stdout (deltas = purged_wf - holdout).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# Allow `python scripts/ml/eval_split_compare.py` as well as `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.experiments.runner import run_experiment  # noqa: E402


def _run(
    manifest_dict: dict[str, Any],
    *,
    datasets_root: Path,
    experiments_root: Path,
) -> dict[str, Any]:
    """Write `manifest_dict` to a temp file and run it (no registration)."""
    with tempfile.TemporaryDirectory() as td:
        mpath = Path(td) / "manifest.yaml"
        mpath.write_text(yaml.safe_dump(manifest_dict), encoding="utf-8")
        artifacts, _ = run_experiment(
            manifest_path=mpath,
            datasets_root=datasets_root,
            experiments_root=experiments_root,
            registry_root=experiments_root / "_registry_throwaway",
            register=False,
        )
        out: dict[str, Any] = {
            "split_strategy": manifest_dict.get("evaluator_config", {}).get(
                "split_strategy", "holdout"
            ),
            "metrics": dict(artifacts.metrics),
        }
        if artifacts.cv_folds_path is not None:
            cv = json.loads(artifacts.cv_folds_path.read_text())
            out["n_folds"] = cv["n_folds"]
            out["folds"] = [
                {"n_train": f["n_train"], "n_eval": f["n_eval"], "metrics": f["metrics"]}
                for f in cv["folds"]
            ]
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--datasets-root", default=Path("datasets-out"), type=Path)
    ap.add_argument(
        "--experiments-root",
        default=None,
        type=Path,
        help="Throwaway experiments dir (default: a temp dir).",
    )
    ap.add_argument("--n-folds", type=int, default=5)
    ap.add_argument("--min-train-fraction", type=float, default=0.5)
    ap.add_argument(
        "--label-horizon",
        type=int,
        default=1,
        help="Rows each sample's label spans forward (PURGE width).",
    )
    ap.add_argument(
        "--embargo-fraction",
        type=float,
        default=0.0,
        help="EMBARGO buffer as a fraction of the dataset (rounded up).",
    )
    ap.add_argument("--embargo-n", type=int, default=None)
    args = ap.parse_args(argv)

    manifest_dict = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest_dict, dict):
        sys.stderr.write(f"manifest {args.manifest} is not a YAML mapping\n")
        return 2

    tmp_exp_ctx = None
    if args.experiments_root is not None:
        experiments_root = args.experiments_root
    else:
        tmp_exp_ctx = tempfile.TemporaryDirectory()
        experiments_root = Path(tmp_exp_ctx.name)

    try:
        # 1) Authored eval (the optimistic 80/20 holdout, as the manifest ships).
        baseline = _run(
            dict(manifest_dict),
            datasets_root=args.datasets_root,
            experiments_root=experiments_root / "baseline",
        )

        # 2) Purged walk-forward CV override (manifest on disk is untouched).
        cv_dict = json.loads(json.dumps(manifest_dict))  # deep copy
        eval_cfg = dict(cv_dict.get("evaluator_config", {}))
        eval_cfg["split_strategy"] = "purged_walk_forward"
        eval_cfg["n_folds"] = args.n_folds
        eval_cfg["min_train_fraction"] = args.min_train_fraction
        eval_cfg["label_horizon"] = args.label_horizon
        if args.embargo_n is not None:
            eval_cfg["embargo_n"] = args.embargo_n
        else:
            eval_cfg["embargo_fraction"] = args.embargo_fraction
        cv_dict["evaluator_config"] = eval_cfg
        purged = _run(
            cv_dict,
            datasets_root=args.datasets_root,
            experiments_root=experiments_root / "purged_wf",
        )
    finally:
        if tmp_exp_ctx is not None:
            tmp_exp_ctx.cleanup()

    # Deltas (purged_wf - holdout) for metric keys both runs share.
    shared = sorted(set(baseline["metrics"]) & set(purged["metrics"]))
    deltas = {
        k: float(purged["metrics"][k]) - float(baseline["metrics"][k])
        for k in shared
    }

    print(
        json.dumps(
            {
                "model_id": manifest_dict.get("model_id"),
                "dataset": manifest_dict.get("dataset"),
                "holdout": baseline,
                "purged_wf": purged,
                "deltas_purged_minus_holdout": deltas,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
