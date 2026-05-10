# S-AI-WS5-D — Execution quality baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/data/dataset-{taxonomy,schema}.md`](../data/), [`docs/ml/training-center.md`](../ml/training-center.md), [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
**Status:** ✅ COMPLETE

## Goal

Land WS5-D, the execution-quality baseline. Predicts the
expected entry-slippage (in basis points, signed) for a strategy.
Source is the `trades` ↔ `order_packages` join in
`trade_journal.db` (linked via `order_packages.linked_trade_id`),
which is the canonical execution record: `order_packages.entry`
is the intended fill price at signal time, `trades.entry_price`
is the actual fill, `trades.timestamp - order_packages.created_at`
is the fill latency.

## Decisions

- **Source = trade_journal.db trades JOIN order_packages.** No
  external data source needed; the operator's existing trade
  pipeline already records both sides of the execution boundary.
  Read-only `mode=ro` SQLite URI, same pattern as `setup_labels`
  and `trade_outcomes`.
- **Label = signed entry slippage in basis points.** Continuous
  target consistent with the WS5-C convention (continuous
  R-multiple). Sign convention: **positive = trader paid worse
  than intended** (LONG paid more, SHORT sold lower); negative =
  better fill than intended. Capped at `±slippage_cap_bps`
  (default 200 bps = 2 %) to neutralise rare partial-fill /
  misrecorded-intent outliers, matching the v1 setup-labels
  `r_cap` pattern.
- **Trainer reuse.** `PerStrategyWinRateTrainer` with
  `target_kind: numeric_mean` — same trainer chassis as WS5-C's
  manifests. No new trainer code needed; the WS5-C extension
  already supports per-bucket sample mean of any numeric target.
- **Fill latency carried as bookkeeping, not the label.**
  `fill_latency_seconds = trade.timestamp -
  order_package.created_at` is emitted on every row but is NOT
  the training target. Operators can use it as a feature in a
  future manifest, or as a diagnostic. Treating slippage and
  latency as one composite "execution-quality score" was
  considered and rejected — the two have different units, and
  one well-defined target beats a hand-tuned weighted sum.
- **Leakage discipline.** `entry_slippage_bps`,
  `fill_latency_seconds`, and `actual_entry` are all execution
  outcomes and MUST be excluded from features against
  `entry_slippage_bps`. `intended_entry` is set at signal time
  and is fair game. `leakage_test_status: skipped` (trainer's
  responsibility, same as `setup_labels` / `trade_outcomes`).

## Deliverables

- `ml/datasets/families/execution_quality.py` —
  `ExecutionQualityBuilder` family. SQL inner-join
  (`order_packages.linked_trade_id = trades.id`), filter to
  CLOSED non-backtest trades with non-null + non-zero
  `intended_entry` and non-null `actual_entry`. Slippage clipped
  via `_clip(±slippage_cap_bps)`; signed by direction in
  `_signed_slippage_bps`.
- `ml/datasets/registry.py` — registered
  `ExecutionQualityBuilder`.
- `ml/configs/baseline-execution-quality.yaml` — paired manifest:
  `PerStrategyWinRateTrainer` (numeric_mean) on
  `entry_slippage_bps`, feature column `strategy_name`,
  `RegressionEvaluator` with mse/mae, time-aware holdout on
  `trade_created_at`, `target_deployment_stage: research_only`.
- `tests/ml/datasets/test_execution_quality.py` — 12 tests:
  round-trip + metadata, sign convention for SHORT vs LONG,
  better-fill-is-negative, slippage cap (±200 bps from ±5 %
  raw), drop-unjoined-trades, OPEN / backtest / null entry_price
  drop, drop-zero-or-null-intended-entry, fill latency math,
  strategy filter, invalid-cap error, missing-db error,
  registry inclusion.

## Acceptance

- [x] `pytest tests/ml/datasets/test_execution_quality.py` — 12/12 pass.
- [x] `pytest tests/ml/` — full ML suite 182/182 pass (170 prior +
      12 new; no regression).
- [x] `ruff check ml/datasets/families/execution_quality.py
      tests/ml/datasets/test_execution_quality.py
      ml/datasets/registry.py` — clean.
- [x] `execution_quality` registered: `from ml.datasets import
      list_families` includes it.
- [x] No live runtime touched (research-only family + manifest).
- [x] Read-only SQLite URI (`mode=ro`).

## Out of scope (filed for follow-ups)

- **Paired global-mean sanity manifest.** WS5-A shipped a winrate
  manifest + a global-mean sibling so the operator could read
  the marginal lift. Same pattern would help here:
  `baseline-execution-quality-global.yaml` using
  `ConstantPredictionTrainer` + `RegressionEvaluator`. Filed for
  a follow-up — out of scope to keep this PR focused on the
  baseline itself.
- **Fill-latency target manifest.** A second manifest predicting
  `fill_latency_seconds` on the same family would surface
  per-strategy execution latency. Filed.
- **WS5-E — Post-trade review baseline.** Next sprint per WS5
  plan. Source = `review_journal` (per-trade decision grades from
  the `/health-review` skill), label likely a continuous decision
  score. Will reuse the same trainer.
- **WS5-F — Prop mission policy baseline.** Final WS5 sub-sprint.
  Source = `account_context` (mission rules + drawdown state),
  label = action policy.

## Hand-off

- Three setup/trade quality manifests now live side-by-side:
  - `baseline-trade-outcome-winrate.yaml` (WS5-A) — binary `won`.
  - `baseline-setup-quality.yaml` (WS5-C) — continuous
    `r_multiple` from trade journal `setup_type`.
  - `baseline-setup-quality-audit.yaml` (WS5-C-FU) — continuous
    `r_multiple` from audit-joined `audit_pattern`.
  - `baseline-execution-quality.yaml` (this sprint) —
    continuous `entry_slippage_bps` from execution metadata.
- All four use the same trainer (`PerStrategyWinRateTrainer`)
  with different `target_kind`s and feature columns; the
  evaluator stack is `RegressionEvaluator` (or
  `ClassificationEvaluator` for the binary one) with
  time-aware holdout. Future WS5-E/F sprints reuse this chassis.
- All four target `research_only`. No live tier candidate.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md).

## Live runtime impact

None. New family code is only read during `python -m ml.datasets
build execution_quality ...`; no pipeline / web-api / health
suite path imports it. The SQLite reader uses the same
read-only URI pattern as the existing families.
