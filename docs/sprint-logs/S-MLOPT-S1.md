# Sprint Log: S-MLOPT-S1

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: Add purged & embargoed walk-forward cross-validation (de
  Prado, *AFML* Ch. 7) to the ML experiments layer as an **opt-in** eval
  mode, so models can be measured honestly out-of-sample instead of on the
  optimistic 80/20 time-aware holdout.
- Secondary goals: Open the M14 ML-Optimization Program (master plan + roadmap
  scaffolding); ship a re-eval tool and report the metric delta for 1–2 real
  models vs the 80/20 holdout.

## Tier
- Tier 1.
- Justification: Trainer-side tooling, tests, and docs only. No
  `config/*.yaml`, no `src/runtime/`, no order-path / live-VM-consumed file
  touched. The new eval mode is opt-in; no manifest's default eval mode was
  changed (the one move that would be Tier-3-adjacent is explicitly avoided).

## Starting Context
- Active roadmap items: M14 ML-Optimization Program did not exist yet — this
  sprint (Phase 0.1) bootstraps it. The work was pre-named in
  `docs/sprint-plans/ai-traders/ws4-followups.md` § Out-of-scope ("Aggregated
  walk-forward") and in the `setup-quality-lgbm-v2` manifest's notes
  ("K-fold time-aware CV pass is a follow-up").
- Prior sprint reference: S-AI-WS4-FU (introduced `split_strategy` +
  `split_walk_forward`, which returns only the last fold).
- Known risks at start: keeping the default eval path byte-for-byte unchanged;
  defining purge/embargo semantics that are genuinely leak-free and testable.

## Repo State Checked
- Branch or commit reviewed: `claude/purged-walk-forward-splitter-cKEdI`, off
  `origin/main` (verified `HEAD == origin/main` after fetch; local `main` ref
  was stale).
- Deployment state reviewed: training runs on the trainer VM via
  `scripts/ops/run_training_cycle.sh` (`python -m ml train` per manifest);
  REPO_ROOT=`/home/ubuntu/ict-trading-bot`, DATASETS_ROOT=`$REPO_ROOT/datasets-out`.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md` (tiers), `ROADMAP.md`
  (milestone ledger), the dashboard/bot `CLAUDE.md` API contract.

## Files and Systems Inspected
- Code files inspected: `ml/experiments/splitters.py`, `ml/experiments/runner.py`,
  `ml/evaluators/{base,classification,multiclass_classification,regression}.py`,
  `ml/manifest.py`, `ml/cli.py`, `ml/registry/model_registry.py` (register sig).
- Config files inspected: `ml/configs/btc-regime-1h-lgbm-v2.yaml`,
  `ml/configs/setup-quality-lgbm-v2.yaml` (read-only; not edited).
- Deployment files inspected: `scripts/ops/run_training_cycle.sh`,
  `.github/workflows/trainer-vm-diag.yml`.
- Docs inspected: `docs/sprint-plans/ai-traders/ws4-followups.md`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Tests inspected: `tests/ml/test_splitters.py`, `tests/ml/test_experiments_runner.py`.

## Work Completed
- `ml/experiments/splitters.py`:
  - `split_purged_walk_forward(rows, config)` — expanding-origin walk-forward
    folds; each fold's train set is PURGED of label-overlap rows and EMBARGOED
    by an extra buffer. Forward-only ⇒ clean `label_horizon + embargo_n` gap.
  - `purge_and_embargo_indices(...)` — reusable two-sided de Prado primitive.
  - `iter_folds(...)` + `MULTI_FOLD_STRATEGIES`; `purged_walk_forward` wired
    into the single-split `split()` dispatcher (returns last fold).
- `ml/experiments/runner.py`: opt-in multi-fold CV path — fit+score per fold,
  pool metrics (`_aggregate_fold_metrics`: rates sample-weighted by `n_eval`,
  counts summed), write `cv_folds.json`, record `n_folds`, persist a full-data
  refit as the deployable `model_state`. Default path unchanged.
- `scripts/ml/eval_split_compare.py`: re-eval tool — holdout vs purged WF-CV
  delta over the same dataset, no manifest edit, no registration.
- Tests: leak regression test + boundary pinning in `test_splitters.py`;
  runner CV + "holdout writes no cv artifact" in `test_experiments_runner.py`.
- Docs: `docs/ml/optimization-roadmap.md` (program master plan); `ROADMAP.md`
  M14 row + header bump.

## Validation Performed
- Tests run: `tests/ml/test_splitters.py` + `tests/ml/test_experiments_runner.py`
  → 35 passed; full `tests/ml/` (ex. pandas-only `tests/ml/datasets`) → 298
  passed, 1 skipped. `py_compile` clean; CI ruff-lint green on the PR.
- Manual code verification: smoke-ran `split_purged_walk_forward` and
  `eval_split_compare.py` locally on synthetic data with the constant-baseline
  trainer (no pandas/LightGBM) — gap held at exactly `label_horizon + embargo_n`,
  pooled metrics + per-fold artifact correct.
- Real-dataset re-eval: ran on the trainer VM via the `trainer-vm-diag` relay
  (issue #2675) in a throwaway git worktree — results below.
- Gaps not yet verified: see "Re-eval results" + "Risks".

## Re-eval results (purged WF-CV vs 80/20 holdout)
<!-- FILLED FROM TRAINER-VM-DIAG ISSUE #2675 -->
_Pending the trainer-relay run; appended on return._

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none (no architecture change; opt-in tooling).
- Trade pipeline doc updates: n/a (no pipeline stage touched).
- Roadmap updates: `ROADMAP.md` — M14 milestone row + "Last Updated" header.
- Subsystem doc updates: new `docs/ml/optimization-roadmap.md` (M14 master plan).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- The M14 program referenced by the sprint prompt (master plan +
  `ROADMAP.md` section) did not exist. Resolved by bootstrapping both as part
  of this (the program's first) sprint, rather than updating a non-existent
  status row.
- `setup-quality-lgbm-v2.yaml` notes describe the K-fold CV pass as a pending
  follow-up; left as-is (config not edited per constraints) — the machinery it
  asks for now exists, so a future Tier-3 PR can flip it.

## Risks and Follow-Ups
- Remaining technical risks: `label_horizon` is expressed in **rows** (a
  conservative, dataset-agnostic proxy for the true label window). For
  `setup_labels`, real trade hold-times vary; a row-count horizon understates
  overlap. A future sprint can derive the horizon from a label-end column.
- Remaining product decisions (Tier 3): adopting purged WF-CV as a manifest's
  *default* eval (S-MLOPT-S3) and any promotion decision off the new metrics.
- Blockers: none.

## Deferred Items
- S-MLOPT-S2: combinatorial purged CV (CPCV) + deflated-metric summary (the
  two-sided primitive is already in place).
- S-MLOPT-S3: per-manifest default-eval adoption (one PR per model, with delta
  evidence).

## Next Recommended Sprint
- Suggested next sprint: S-MLOPT-S2 (CPCV + deflated metrics).
- Why next: closes the multiple-testing / backtest-overfit gap that a single
  walk-forward pass doesn't quantify.
- Required verification before starting: review the S-MLOPT-S1 re-eval deltas
  to confirm the WF-CV path produces sane, comparable metrics on real datasets.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched (`docs/TRADE-PIPELINE.md` n/a).
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
