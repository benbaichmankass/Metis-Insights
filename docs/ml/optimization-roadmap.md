# M14 — ML Optimization Program (master plan)

> **Status:** Phase 0 in flight. This file is the scope-specific master plan
> for the **M14 ML-Optimization Program**; the milestone/sprint **status of
> record** lives in [`ROADMAP.md`](../../ROADMAP.md) (§ M14). When the two
> disagree, `ROADMAP.md` wins — see the instruction hierarchy in
> [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md).
>
> Bootstrapped 2026-06-03 alongside the program's first sprint (S-MLOPT-S1).

## Why

The ML training center (M9/M10) ships models, registers them, and runs them in
shadow. What it does **not** yet do well is tell us, honestly, **how good a
model actually is out-of-sample**. Every manifest today evaluates on a single
optimistic 80/20 time-aware holdout (`split_strategy: time_aware_holdout`).
For a financial time series with labels that span a forward horizon, a plain
holdout leaks: training rows whose label window reaches into the test block
make the score look better than the model will perform live.

M14 is the program that closes that gap — first the **evaluation rigor**, then
the **search / tuning** that rigor unlocks. We do not tune what we cannot
measure honestly, so Phase 0 (evaluation) comes before any
hyper-parameter / feature search.

## Guardrails (apply to every M14 sprint)

- **Trainer-side, Tier-1.** M14 work is tooling, evaluation, and analysis on
  the **trainer** VM / `ml/` tree. It does **not** edit `config/*.yaml`,
  `src/runtime/`, order-path code, or any unit the live VM consumes.
- **Opt-in, default-preserving.** New eval modes / search tooling land as
  opt-in. Changing a manifest's *default* eval mode, or promoting a model, is
  a separate, explicitly-approved step (Tier-3 for promotion) — never folded
  into the tooling sprint that introduces the capability.
- **Honest numbers only.** Report the metric a change actually produced,
  against a named baseline. No "should improve."

## Phases

### Phase 0 — Evaluation rigor (measure before you tune)

| Sprint | Title | Scope | Status |
|---|---|---|---|
| **S-MLOPT-S1** | Purged & embargoed walk-forward CV | `PurgedWalkForwardSplitter` (de Prado, AFML Ch. 7) in `ml/experiments/splitters.py`; opt-in `purged_walk_forward` eval mode in the runner (multi-fold, pooled metrics, `cv_folds.json` artifact); leak regression test; `scripts/ml/eval_split_compare.py` to report the metric delta vs the 80/20 holdout. **No manifest default changed.** | ✅ Done 2026-06-03 |
| S-MLOPT-S2 *(planned)* | Combinatorial purged CV (CPCV) + deflated metrics | Generalize the purge/embargo helper to non-forward folds (the helper already supports both sides); add CPCV path + a deflated-Sharpe / multiple-testing-aware summary so backtest-overfit risk is quantified. | 📋 Backlog |
| S-MLOPT-S3 *(planned)* | Per-manifest eval-mode adoption | Once CV deltas are reviewed, propose (operator-gated) flipping specific manifests' default eval to purged WF-CV — one PR per model, with the delta evidence. | 📋 Backlog |

### Phase 1 — Search / tuning (planned, gated on Phase 0)

Hyper-parameter sweeps, feature-set search, and target-engineering — each
scored under the Phase-0 honest evaluator, not a holdout. Scoped after Phase 0
lands so we never tune against a leaky metric. Detailed sprint list TBD.

## S-MLOPT-S1 — what shipped (Phase 0.1)

- **`PurgedWalkForwardSplitter`** (`ml/experiments/splitters.py`):
  - `split_purged_walk_forward(rows, config)` — expanding-origin walk-forward
    folds. Each fold's training set is **purged** of rows whose forward label
    window (`label_horizon` rows) overlaps the test block, and **embargoed**
    by an additional buffer. Because the folds are forward-only, de Prado's
    *post-test* embargo collapses into a *pre-test* buffer, giving a clean gap
    of `label_horizon + embargo_n` rows between the last train row and the
    first test row.
  - `purge_and_embargo_indices(...)` — the reusable, **two-sided** de Prado
    purge+embargo primitive (handles training data on either side of the test
    block, so it is ready for the Phase-0.2 CPCV work). Unit-tested directly
    for the post-test embargo branch.
  - Config keys (under `evaluator_config`): `n_folds`, `min_train_fraction`,
    `time_column`, `label_horizon` (PURGE width, rows), and either
    `embargo_fraction` (of the dataset, rounded up) or `embargo_n` (rows).
- **Opt-in runner CV path** (`ml/experiments/runner.py`): when
  `split_strategy == "purged_walk_forward"`, the runner fits + scores each
  fold, **pools** the per-fold metrics (rate metrics sample-weighted by
  `n_eval`, count metrics summed), writes a per-fold `cv_folds.json` artifact,
  records `n_folds`, and persists a **full-data refit** as the deployable
  `model_state` (the CV metrics estimate *its* generalization). The default
  `holdout` / `time_aware_holdout` path is byte-for-byte unchanged.
- **Leak regression test** (`tests/ml/test_splitters.py`): pins that no
  future-dated row leaks into any train fold, with purge + embargo boundaries
  asserted both on chronological position and on the time column. Runner-level
  CV tests in `tests/ml/test_experiments_runner.py`.
- **`scripts/ml/eval_split_compare.py`**: runs a manifest's authored holdout
  eval and the purged WF-CV override over the same dataset (no manifest edit,
  no registration) and prints the metric delta. This is how a model is
  re-evaluated under purged WF-CV on the trainer VM.

The two candidate re-eval targets are `btc-regime-1h-lgbm-v2` (multiclass) and
`setup-quality-lgbm-v2` (regression) — the latter's manifest already files a
"K-fold time-aware CV pass" follow-up that this sprint delivers the machinery
for. Re-eval results land in the S-MLOPT-S1 sprint log.

## References

- López de Prado, *Advances in Financial Machine Learning* (2018), Ch. 7
  ("Cross-Validation in Finance") — purging and embargoing.
- Predecessor follow-up that named this work:
  [`docs/sprint-plans/ai-traders/ws4-followups.md`](../sprint-plans/ai-traders/ws4-followups.md)
  § "Out of scope (deferred)" — *Aggregated walk-forward*.
- Training center overview: [`docs/ml/training-center.md`](training-center.md).
