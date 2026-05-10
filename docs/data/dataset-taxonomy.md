# Dataset Taxonomy

> **Status:** Canonical (data scope). Adopted in **S-AI-WS3**
> (2026-05-10). Updated in **S-AI-WS5-A** (2026-05-10):
> `trade_outcomes` is now buildable.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
>
> **Companion docs:**
> - [`docs/data/dataset-schema.md`](dataset-schema.md) — per-family
>   field schemas + the mandatory metadata block.
> - [`docs/data/versioning-policy.md`](versioning-policy.md) — naming,
>   version bumps, retention.
> - [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md) — publishing workflow.
> - [`ml/datasets/`](../../ml/datasets/) — builder framework + concrete families.

## Purpose

Name every dataset family the AI traders track relies on, who owns
it, how often it should be rebuilt, and which downstream consumers
use it. New families MUST be added here in the same PR that
introduces a builder.

## Family table

| Family | Layer | Purpose | Owner subsystem | Source(s) | Freshness target | Primary consumers |
|---|---|---|---|---|---|---|
| `market_raw` | 1 (data) | Bars, ticks, order-book-derived snapshots, unaltered | `src/exchange/`, `src/runtime/market_data.py` | exchange connectors | tick / candle close cadence | `market_features` builders, backtest harness |
| `market_features` | 2 (feature/context) | Engineered features derived from `market_raw` | future `ml/features/` | `market_raw` | aligned to `market_raw` | model trainers (WS5+) |
| `setup_labels` | 2 (feature/context) | Labels for pattern / setup quality | future `ml/labels/` | `market_raw` + `setup_metadata` from strategy modules | per-sprint, audit-triggered | setup quality scorer (WS5) |
| `trade_outcomes` | 1 (data) | Realized trade results tied back to signals or execution intents; carries the derived `won = pnl > 0` label | `src/units/`, `trade_journal.db` | `trade_journal.db::trades` (CLOSED, non-backtest, non-null pnl) | per closed trade | outcome probability model (WS5-A onwards) |
| `backtest_results` | 1 (data) | Aggregate backtest run summaries (M5 outputs) | `src/bot/test_strategy_consumer.py`, `trade_journal.db` | `trade_journal.db::backtest_results` | per `/test <strategy>` invocation | strategy review (M7), regime baseline comparison |
| `account_context` | 2 (feature/context) | Account state, funding phase, prop-firm restrictions, mission state, active day rules | `src/units/accounts/`, `config/accounts.yaml` | accounts unit + per-account state | aligned to candidate evaluation | prop mission policy assist (WS5), risk-explanation tooling |
| `review_journal` | 1 (data) | Post-trade reviews, mistake tagging, narrative annotations | future `docs/ml/`, M7 | operator + post-trade review model | per trade close | post-trade review model (WS5), retraining triggers (WS8) |

## Builder availability

A family is **scaffolded** when its row exists above. A family is
**buildable** when a `DatasetBuilder` subclass for it lives in
`ml/datasets/families/` and is registered in
`ml/datasets/registry.py`.

| Family | Scaffolded | Buildable | Builder |
|---|---|---|---|
| `market_raw` | ✅ | ⏳ | filed for a follow-up sprint |
| `market_features` | ✅ | ⏳ | WS5 prereq |
| `setup_labels` | ✅ | ⏳ | WS5 prereq |
| `trade_outcomes` | ✅ | ✅ (S-AI-WS5-A) | [`ml/datasets/families/trade_outcomes.py`](../../ml/datasets/families/trade_outcomes.py) |
| `backtest_results` | ✅ | ✅ (S-AI-WS3) | [`ml/datasets/families/backtest_results.py`](../../ml/datasets/families/backtest_results.py) |
| `account_context` | ✅ | ⏳ | filed for a follow-up sprint |
| `review_journal` | ✅ | ⏳ | M7 prereq |

## Adding a new family

1. Add a row to the family table above. Owner subsystem must be a
   real path in this repo or a clearly-marked `future ...`
   placeholder.
2. Add the family's field schema and metadata expectations to
   [`docs/data/dataset-schema.md`](dataset-schema.md).
3. Implement the builder under `ml/datasets/families/<family>.py`
   subclassing `ml.datasets.builder.DatasetBuilder`.
4. Register the builder in `ml/datasets/registry.py`.
5. Add a regression test under
   `tests/ml/datasets/test_<family>.py` that exercises the builder
   against a synthetic fixture (no live DB).
6. If the family carries forward-looking labels, run a leakage
   test and record `leakage_test_status=passed` in the metadata.
   If leakage prevention is the trainer's responsibility (e.g.
   `trade_outcomes` includes both `pnl` and `won`), record
   `leakage_test_status=skipped` and document the rationale in the
   builder docstring.
7. Update the change log in
   [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).

## Update rule

This doc must be reviewed in the same PR as any new family,
renaming, or owner-subsystem change. Schema changes go in
[`dataset-schema.md`](dataset-schema.md). Versioning rules go in
[`versioning-policy.md`](versioning-policy.md).
