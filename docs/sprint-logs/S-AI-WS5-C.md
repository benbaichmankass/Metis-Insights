# S-AI-WS5-C — Setup quality scorer baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md), [`docs/data/dataset-{taxonomy,schema}.md`](../data/), [`docs/ml/training-center.md`](../ml/training-center.md), [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
**Status:** ✅ COMPLETE

## Goal

Land the next WS5 baseline: a setup-quality scorer trained on a
new `setup_labels` family. First continuous-target (regression)
baseline on the AI-traders track. Plus: thread the AI-traders
training workflow through the architecture canonical doc and the
`/health-review` skill so future training sessions follow the
established path rather than reinventing.

## Decisions

- **Sprint id `S-AI-WS5-C`.** Operator-confirmed scoping
  (2026-05-10):
  1. **Source = trade journal CLOSED trades** (smallest scope;
     reuses the existing data path; ships fastest).
  2. **Label = continuous R-multiple** (`pnl_percent / risk_pct`,
     capped). Higher decision-utility than binary good/bad —
     operators can rank setups, not just classify.
  3. **Trainer = reuse `PerStrategyWinRateTrainer` + new
     manifest** with a small extension (`target_kind` config knob).
- **Trainer extension over new trainer.** The operator-chosen
  reuse path requires extending `PerStrategyWinRateTrainer` to
  support a continuous target. Cleanest implementation: a
  `target_kind` config knob with `binary` (default = WS5-A
  behavior, bool-coerced label) and `numeric_mean` (cast to
  float, per-bucket sample mean). State shape unchanged; the
  `per_group_rate` field is overloaded — for binary it's the
  win rate, for `numeric_mean` it's the mean target. Backward
  compat verified by re-running WS5-A's existing test suite.
- **`setup_labels` is a separate family, not an extension of
  `trade_outcomes`.** They share the same SQL source but differ
  in scope (setup_labels filters to non-empty `setup_type`) and
  in label (setup_labels adds `r_multiple`). Future setup-quality
  variants (e.g., per-killzone, per-bias) reuse this family.
- **Risk model = fixed 1% per trade by default.** R-multiple needs
  a risked-amount denominator. The trade journal doesn't carry an
  explicit `risk_percent` column, so the family takes a
  configurable `risk_pct` kwarg (default `1.0` — typical ICT
  default). For an actual baseline run the operator can override
  per-account.
- **R-multiple cap = ±3R by default.** Configurable `r_cap` kwarg.
  Protects against outliers (a 30% gain on a 1%-risked trade is
  almost certainly position drift through a regime change, not a
  typical winner; including it skews the per-bucket mean badly).
- **Time-aware holdout split.** `created_at` is the canonical
  ordering column; eval is the latest 20% of trades. Mirrors the
  WS5-A / WS5-B-PART-2 PR 2B pattern.
- **Architecture-canonical update.** The user explicitly asked
  that the AI-traders training workflow be discoverable from
  the architecture docs and from the `/health-review` skill so
  future training sessions follow the documented path. Three
  edits: (a) `/health-review` skill flags the
  `trade_decision_grades[]` block as feedstock for specific
  downstream baselines (`trade_outcomes`, `setup_labels`); (b)
  `ARCHITECTURE-CANONICAL.md` gains a new "AI-traders training
  workflow" section listing the five-step session pipeline +
  established manifests; (c) `training-center.md` gains a
  "Training session workflow" section + table of established
  baseline manifests.

## Deliverables

Code (stdlib + sqlite3 only):
- [`ml/datasets/families/setup_labels.py`](../../ml/datasets/families/setup_labels.py)
  — new `SetupLabelsBuilder`. Reads CLOSED non-backtest trades with
  non-empty `setup_type`; computes `r_multiple` capped at
  `±r_cap`. Optional filters: `strategy_name`, `symbol`,
  `risk_pct`, `r_cap`.
- [`ml/datasets/registry.py`](../../ml/datasets/registry.py)
  — registers `SetupLabelsBuilder`.
- [`ml/trainers/per_strategy_winrate.py`](../../ml/trainers/per_strategy_winrate.py)
  — extended with `target_kind: binary | numeric_mean` config
  knob via a small `_coerce_label` helper. `binary` is the default,
  matching WS5-A's existing behavior exactly. `numeric_mean` casts
  the target to float and computes the per-bucket sample mean.
  State shape preserved; `target_kind` recorded in `model_state`
  for registry traceability.
- [`ml/configs/baseline-setup-quality.yaml`](../../ml/configs/baseline-setup-quality.yaml)
  — manifest pairing `PerStrategyWinRateTrainer (numeric_mean)`
  with `RegressionEvaluator`, targeting `r_multiple` with
  `setup_type` as the feature. `split_strategy:
  time_aware_holdout`, `time_column: created_at`,
  `target_deployment_stage: research_only`.

Tests (158 passing in `tests/ml/`; 27 new):
- [`tests/ml/datasets/test_setup_labels.py`](../../tests/ml/datasets/test_setup_labels.py)
  — 10 cases: round-trip with `validate_dataset`, empty / null /
  whitespace `setup_type` filtered, OPEN / backtest / null-pnl
  filtered, R-multiple cap applied symmetrically, `risk_pct`
  scaling, invalid kwargs, missing DB, strategy filter, registry
  inclusion.
- [`tests/ml/test_setup_quality.py`](../../tests/ml/test_setup_quality.py)
  — 10 cases: numeric-mean trainer, non-numeric target skipping,
  unknown `target_kind` rejection, binary backward-compat,
  predictor resolution, end-to-end trainer + RegressionEvaluator
  round-trip with hand-computed expected MSE / MAE, manifest
  parses.

Docs (architecture-rule compliance: docs in same PR):
- [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md)
  — `setup_labels` row updated to "buildable" with the new owner
  subsystem + label semantics.
- [`docs/data/dataset-schema.md`](../data/dataset-schema.md)
  — header refreshed.
- [`docs/ml/training-center.md`](../ml/training-center.md)
  — predictor table extended; new `target_kind` section
  documents the trainer's binary/numeric-mean modes; new
  "Training session workflow" section that connects the
  `/health-review` skill, the established baseline manifests,
  and the promotion gate; setup-quality demo added to the
  end-to-end commands.
- [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
  — `setup_labels` + setup-quality scorer audit rows added;
  Planned section pruned; change log row added.
- [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
  — new "AI-traders training workflow (separate from live
  trading)" section. Five-step pipeline anchored on the
  `/health-review` skill's `trade_decision_grades[]` as
  labelled feedstock; lists the four established manifests with
  pointers; restates the operator-gated promotion rule.
- [`.claude/skills/health-review/SKILL.md`](../../.claude/skills/health-review/SKILL.md)
  — extended the "Per-trade decision grading (training-data
  feedstock)" section to flag the specific downstream consumers
  (`trade_outcomes`, `setup_labels`, future WS5-E / WS5-F).
- [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
  — WS5-C row marked done; deliverables block added.
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) +
  [`ROADMAP.md`](../../ROADMAP.md) — banners + ledgers + status
  rows + change log.
- This file.

## Acceptance

- [x] `setup_labels` family is buildable from `trade_journal.db`.
- [x] `r_multiple` is computed correctly + capped + scales by
  `risk_pct`.
- [x] Empty / null / whitespace `setup_type` rows are filtered.
- [x] OPEN, backtest, and null-pnl trades are filtered.
- [x] `PerStrategyWinRateTrainer` accepts `target_kind: numeric_mean`
  and computes per-bucket sample mean of a continuous target.
- [x] `target_kind: binary` (default) preserves WS5-A behavior
  exactly (existing tests pass unchanged).
- [x] `RegressionEvaluator` consumes the trainer's output and
  reports MSE / MAE / n_eval matching hand-computed expected
  values.
- [x] `baseline-setup-quality.yaml` manifest parses via
  `TrainingManifest.from_yaml`.
- [x] `/health-review` skill, `ARCHITECTURE-CANONICAL.md`, and
  `training-center.md` all link the AI-traders training workflow
  to the live `/health-review` feedstock so future sessions
  follow the documented path.
- [x] Full `tests/ml/` suite green (158 tests).

## Out of scope (filed for follow-ups)

- Per-killzone / per-bias / multi-feature setup-quality variants
  (the per-`setup_type` baseline must land first as the sanity
  reference).
- A paired global-mean sanity manifest (analogous to
  `baseline-trade-outcome-global.yaml`) so the operator can
  `python -m ml compare` against marginal-only.
- Train-only `risk_pct` derivation (currently a fixed kwarg;
  promotion-ready variants should derive risk from the
  `order_packages` SL distance).
- HF publication CLI for `setup_labels`.

## Hand-off

1. **Operator**: when ready to materialise the baseline, run the
   build per `docs/ml/training-center.md` § "Setup-quality
   scorer demo (WS5-C)". `risk_pct=1.0` and `r_cap=3.0` are the
   defaults; override per the live account's actual risk model.
2. **Next sprint (S-AI-WS5-D)**: execution quality. Consumes
   `trade_outcomes` + execution metadata.

## Live runtime impact

None. All changes are under `ml/` + `docs/` + `.claude/skills/`.
Operator-hold paths (`src/runtime/`, `src/units/accounts/`,
`src/main.py`, `config/accounts.yaml`, `deploy/*`) untouched.
Manifest's `target_deployment_stage: research_only` keeps the
model out of any live tier without operator approval.

## Workflow doc rationale

The user's specific ask (2026-05-10) was to ensure the AI-traders
training workflow is documented in the `/health-review` skill and
in the architecture so future training sessions know to use it.
Three edits answer that:

1. **`/health-review` skill** — the
   `trade_decision_grades[]` block was already described as
   "training-data feedstock"; this PR makes the consumers
   explicit (`trade_outcomes` for WS5-A, `setup_labels` for
   WS5-C, future WS5-E / WS5-F). A reviewer following the skill
   now knows the grades aren't decoration — they retrain the
   next baseline.
2. **`ARCHITECTURE-CANONICAL.md`** — gains a new "AI-traders
   training workflow" section. Five-step pipeline (collect →
   build → train → compare → promote) with explicit pointers
   to the four established manifests. Restates the
   research-only / operator-gated promotion rule so future
   sessions don't accidentally promote a candidate.
3. **`training-center.md`** — gains a "Training session
   workflow" section + a manifest registry table. When a future
   session needs to retrain or add a baseline, they have a
   single doc that lists the established options + the rule
   for when to add a new one.
