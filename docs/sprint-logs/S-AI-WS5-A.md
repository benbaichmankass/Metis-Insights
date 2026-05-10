# S-AI-WS5-A — AI traders WS5-A: outcome probability baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/ml/training-center.md`](../ml/training-center.md)
**Status:** ✅ COMPLETE

## Goal

First specialist baseline on the AI-traders track. Decision-useful
question: does the historical win rate per strategy carry signal
for the next closed trade?

## Decisions

- **First baseline = outcome probability**, picked because the
  dataset prereq (`trade_outcomes`) follows the same
  read-only-against-`trade_journal.db` pattern as `backtest_results`
  (WS3) — no live exchange access needed, no historical-data
  acquisition pipeline needed. Confirmed with operator before
  starting.
- **Sprint id `S-AI-WS5-A`**, not S-AI-WS5: WS5 will span multiple
  baselines and the sub-sprint suffix mirrors the dashboard
  build-out arc precedent (S-061..S-064 = sprints A..D). Confirmed
  with operator.
- **Trainer is per-strategy historical winrate.** Trivial; the
  point of WS5-A is to land the harness, not to be predictive.
  Real models follow.
- **Leakage_test_status = skipped on `trade_outcomes`.** Builder
  emits both outcome (`pnl`) and label (`won`); leakage prevention
  is the trainer's responsibility via `feature_column`. Documented
  in `dataset-schema.md`.
- **Headline metrics scalar-only.** Per-strategy breakdowns belong
  in a future evaluation_detail.json artifact; the registry stores
  `Mapping[str, float]`.
- **Threshold of 0.5 for binary classification.** Configurable via
  `evaluator_config.threshold`.

## Deliverables

Code:
- [`ml/datasets/families/trade_outcomes.py`](../../ml/datasets/families/trade_outcomes.py) (new).
- [`ml/datasets/registry.py`](../../ml/datasets/registry.py) (registers `trade_outcomes`).
- [`ml/trainers/per_strategy_winrate.py`](../../ml/trainers/per_strategy_winrate.py) (new).
- [`ml/trainers/__init__.py`](../../ml/trainers/__init__.py) (re-export).
- [`ml/evaluators/classification.py`](../../ml/evaluators/classification.py) (new).
- [`ml/evaluators/__init__.py`](../../ml/evaluators/__init__.py) (re-export).
- [`ml/configs/baseline-trade-outcome-winrate.yaml`](../../ml/configs/baseline-trade-outcome-winrate.yaml) (new).
- [`tests/ml/datasets/test_trade_outcomes.py`](../../tests/ml/datasets/test_trade_outcomes.py) (new).
- [`tests/ml/test_per_strategy_winrate.py`](../../tests/ml/test_per_strategy_winrate.py) (new).

Docs:
- [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md) (updated — `trade_outcomes` row + buildable flag).
- [`docs/data/dataset-schema.md`](../data/dataset-schema.md) (new schema section + leakage-discipline note).
- [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md) (Live audit row + Known Gaps + Change Log).
- [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md) (sub-sprint table + S-AI-WS5-A details).
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (WS5 → IN PROGRESS, change-log row).
- [`ROADMAP.md`](../../ROADMAP.md) (WS5 status row + S-AI-WS5-A ledger).
- This file.

## Acceptance (from WS5 sprint plan)

- [x] Dataset family `trade_outcomes` built and validated against
  synthetic SQLite.
- [x] Trainer + evaluator unit-tested.
- [x] Round-trips end-to-end through the WS4 harness (verified
  via the existing `test_experiments_runner.py` pattern; the new
  manifest is structurally compatible).
- [x] Decision-useful framing documented in the manifest.

## Out of scope (deferred)

- WS5-B (regime classifier) and the rest of WS5.
- Walk-forward / time-aware splitters.
- Generic `predict()` interface decoupling trainer state from
  evaluator.
- `compare` CLI subcommand.
- Per-strategy detail artifact alongside scalar metrics.

## Hand-off

1. **Operator review of the baseline framing.** Confirm whether
   per-strategy historical winrate is the right first baseline
   or whether the next sub-sprint should target a different family.
2. **WS5-B — regime classifier.** Recommended next. Dataset
   prereq: `market_raw` builder — needs an explicit
   data-acquisition decision (historical CSV import vs live
   exchange pull off-VM per WS9).
3. **Tier 2 follow-up: live-path migration onto WS2 types.**
   Still pending; needs operator-ack.

## Live runtime impact

None. Stdlib-only additive code; only NEW paths under
`ml/datasets/families/`, `ml/trainers/`, `ml/evaluators/`,
`ml/configs/`, `tests/ml/`, `docs/data/`, `docs/sprint-plans/`,
`docs/sprint-logs/`, plus updates to existing canonical / roadmap
docs. Operator-hold paths (`src/runtime/`, `src/units/accounts/`,
`src/main.py`, `config/accounts.yaml`, `deploy/*`) not modified.
Builder reads `trade_journal.db` read-only via SQLite `mode=ro`
URI.
