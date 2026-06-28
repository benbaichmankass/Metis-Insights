# Retired training manifests

Manifests in this subdirectory are **excluded from the daily training cycle**.
`scripts/ops/run_training_cycle.sh` and `scripts/ops/train_and_register_ws5_baselines.sh`
enumerate manifests with `find ml/configs -maxdepth 1 …`, so anything under
`ml/configs/retired/` is no longer trained automatically. They are kept in-repo
(not deleted) so they remain runnable ad hoc:

```
python -m ml train ml/configs/retired/<manifest>.yaml --datasets-root ./datasets-out
```

| Manifest | model_id | Retired | Why |
|---|---|---|---|
| `baseline-trade-outcome-winrate.yaml` | `trade-outcome-winrate-baseline-v0` | 2026-06-28 | WS5-A **demo baseline** ("not a candidate for any live tier"). Predicts P(win)=per-strategy historical win rate, thresholded at 0.5; on a sub-50%-win-rate (≈84%-loss) holdout it predicts "loss" for everything → `f1=precision=recall=0` **by construction** (Brier ≈0.145 is fine). It has produced the identical degenerate result on every daily retrain for weeks, burning cycle time and recurringly tripping the ML-review "degenerate model" flag for a non-bug. Re-run ad hoc if a one-off winrate-vs-feature sanity comparison is ever wanted. |
| `baseline-trade-outcome-global.yaml` | `trade-outcome-global-baseline-v0` | 2026-06-28 | WS4-FU constant global-mean sanity baseline; same `target=won`@0.5 → same by-construction `f1=0`. Retired alongside its winrate twin for the same reasons. |

Operator decision recorded 2026-06-28 (system-review follow-up): **retire from the
daily cycle** rather than re-metric, since these are one-time sanity baselines
whose comparison can be reproduced on demand.
