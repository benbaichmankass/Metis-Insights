# Dataset Schema

> **Status:** Canonical (data scope). Adopted in **S-AI-WS3**
> (2026-05-10). Updated in **S-AI-WS5-A** (2026-05-10):
> `trade_outcomes` schema added.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
> Family roster lives in
> [`docs/data/dataset-taxonomy.md`](dataset-taxonomy.md).

## Mandatory metadata block

Every dataset artifact written by `ml.datasets.builder.DatasetBuilder.build`
carries a `metadata.json` produced from
[`ml.datasets.metadata.DatasetMetadata`](../../ml/datasets/metadata.py).
Fields below are mandatory.

| Field | Type | Notes |
|---|---|---|
| `family` | str | Must match a row in `dataset-taxonomy.md`. |
| `version` | str | `vNNN`, monotonic per `(family, symbol_scope, timeframe)`. |
| `symbol_scope` | str | `all`, single symbol, or comma-joined list. |
| `timeframe` | str | `all` or e.g. `1m`, `15m`, `1h`. |
| `source` | str | Free-form pointer at the upstream system. |
| `timezone_name` | str | Default `UTC`. |
| `generation_commit_sha` | str | `git rev-parse HEAD` or `unknown`. |
| `label_version` | str | `n/a` for raw families; bumped when label semantics change. |
| `leakage_test_status` | enum | `passed` / `skipped` / `n/a` / `failed`. |
| `builder` | str | Python qualname. |
| `builder_version` | str | Bumped when output shape changes. |
| `row_count` | int | Validated against actual line count. |
| `schema` | mapping | Field name → type token (`int`, `float`, `str`, `bool`). |
| `notes` | str | Free-form. |
| `generated_at` | datetime | tz-aware UTC ISO 8601. |
| `schema_version` | str | Currently `v1`. |

## Per-family field schemas

### `backtest_results`

Builder: [`BacktestResultsBuilder`](../../ml/datasets/families/backtest_results.py).
Source: `trade_journal.db::backtest_results` (read-only via SQLite
`mode=ro` URI).

| Field | Type | Notes |
|---|---|---|
| `id` | int | Primary key from upstream table. |
| `run_date` | str | ISO 8601 string. |
| `strategy_version` | str | e.g. `vwap-v2`. |
| `start_date` / `end_date` | str | Backtest window. |
| `total_trades` / `winning_trades` / `losing_trades` | int | |
| `win_rate` | float | In `[0, 1]`. |
| `sharpe_ratio` | float | Free range. |
| `total_pnl` | float | Account currency. |
| `total_pnl_pct` | float | Fraction. |
| `max_drawdown_pct` | float | Fraction. |
| `created_at` | str | UTC ISO 8601. |

**Excluded from family schema** (in upstream table but omitted to
keep the family stable): `profit_factor`, `expectancy`,
`max_drawdown`, `avg_win`, `avg_loss`, `largest_win`, `largest_loss`.

### `trade_outcomes`

Builder: [`TradeOutcomesBuilder`](../../ml/datasets/families/trade_outcomes.py).
Source: `trade_journal.db::trades` filtered to `status='CLOSED'
AND is_backtest=0 AND pnl IS NOT NULL`. Read-only via
`mode=ro` URI.

| Field | Type | Source column | Notes |
|---|---|---|---|
| `id` | int | `id` | Primary key. |
| `timestamp` | str | `timestamp` | Signal / entry time. |
| `symbol` | str | `symbol` | e.g. `BTCUSDT`. |
| `direction` | str | `direction` | `LONG` \| `SHORT`. |
| `strategy_name` | str | `strategy_name` | Empty string when null upstream. |
| `setup_type` | str | `setup_type` | Empty string when null upstream. |
| `killzone` | str | `killzone` | Empty string when null upstream. |
| `bias` | str | `bias` | Empty string when null upstream. |
| `pnl` | float | `pnl` | **Outcome.** Used to derive the label; see leakage discipline below. |
| `pnl_percent` | float | `pnl_percent` | **Outcome.** Same. |
| `account_id` | str | `account_id` | Multi-account identifier. |
| `created_at` | str | `created_at` | UTC ISO 8601. |
| `won` | bool | derived (`pnl > 0`) | **Label.** `label_version: won-from-pnl-v1`. |

**Leakage discipline (important).** The dataset includes both
`pnl` (outcome) and `won` (label derived from `pnl`). A trainer
that consumes `pnl` or `pnl_percent` as a feature trivially predicts
`won`. The builder records `leakage_test_status: skipped` because
leakage prevention is the trainer's responsibility — specifically,
each manifest's `trainer_config.feature_column(s)` MUST exclude
outcome columns when the target is `won`. The first-cut WS5-A
baseline uses `feature_column: strategy_name` which is leakage-safe.

**Excluded from family schema** (in upstream table but omitted):
`entry_price`, `exit_price`, `stop_loss`, `take_profit_*`,
`position_size`, `entry_reason`, `exit_reason`, `notes`. These
may be added in a builder_version bump when a downstream baseline
needs them.

### Other families (placeholder)

`market_raw`, `market_features`, `setup_labels`, `account_context`,
`review_journal` do not yet have buildable implementations. Their
schemas land alongside the first builder for each.

## Validation

`ml.datasets.validate_dataset(path)` checks:

1. The directory exists and contains both `metadata.json` and
   `data.jsonl`.
2. `metadata.json` parses into a valid `DatasetMetadata`.
3. Every line in `data.jsonl` is a JSON object whose keys are a
   subset of `schema`.
4. Every value's type matches the type token in `schema` (or is
   `null`).
5. `metadata.row_count` matches the actual line count.

Validation is also exposed via the CLI:

```
python -m ml.datasets validate <dataset-path>
```

## Update rule

This doc must be reviewed in the same PR as any change to
`DatasetMetadata`, the per-family schemas above, or the validator's
type-checking semantics.
