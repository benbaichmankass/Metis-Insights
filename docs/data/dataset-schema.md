# Dataset Schema

> **Status:** Canonical (data scope). Adopted in **S-AI-WS3**
> (2026-05-10).
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
> Family roster lives in
> [`docs/data/dataset-taxonomy.md`](dataset-taxonomy.md).

## Mandatory metadata block

Every dataset artifact written by `ml.datasets.builder.DatasetBuilder.build`
carries a `metadata.json` produced from
[`ml.datasets.metadata.DatasetMetadata`](../../ml/datasets/metadata.py).
Fields below are mandatory. `__post_init__` raises `ValueError` if
any required string is empty, the version doesn't match `vNNN`,
`row_count` is negative, or `generated_at` is naive.

| Field | Type | Notes |
|---|---|---|
| `family` | str | Must match a row in `dataset-taxonomy.md`. |
| `version` | str | `vNNN`, monotonic per `(family, symbol_scope, timeframe)`. |
| `symbol_scope` | str | `all`, single symbol (`BTCUSDT`), or comma-joined list. |
| `timeframe` | str | `all` or e.g. `1m`, `15m`, `1h`. |
| `source` | str | Free-form pointer at the upstream system (e.g. `trade_journal.db`, `bybit_v5`). |
| `timezone_name` | str | Default `UTC`. Always `UTC` for the live trader. |
| `generation_commit_sha` | str | `git rev-parse HEAD` from the repo when the builder ran, or `unknown`. The CLI accepts `--commit-sha` to override. |
| `label_version` | str | `n/a` for raw families. Bumped when label semantics change. |
| `leakage_test_status` | enum | `passed` / `skipped` / `n/a` / `failed`. `n/a` is only acceptable when the family carries no labels. `failed` is allowed only when the artifact is for offline diagnosis and is not a candidate dataset. |
| `builder` | str | Python qualname of the builder class. |
| `builder_version` | str | Bumped when the builder's output shape changes (even if the family schema is stable). |
| `row_count` | int | Validated against actual line count by `validate_dataset(...)`. |
| `schema` | mapping | Field name → type token (`int`, `float`, `str`, `bool`). The validator type-checks rows against this. |
| `notes` | str | Free-form. Default empty. |
| `generated_at` | datetime | tz-aware UTC ISO 8601. |
| `schema_version` | str | Currently `v1`. Bumped when this metadata schema itself changes. |

## Per-family field schemas

### `backtest_results`

Builder: [`BacktestResultsBuilder`](../../ml/datasets/families/backtest_results.py).
Source: `trade_journal.db::backtest_results` (read-only via SQLite
`mode=ro` URI).

| Field | Type | Source column | Notes |
|---|---|---|---|
| `id` | int | `id` | Primary key from upstream table. |
| `run_date` | str | `run_date` | ISO 8601 string from M5 consumer. |
| `strategy_version` | str | `strategy_version` | Strategy + version tag (e.g. `vwap-v2`). |
| `start_date` | str | `start_date` | Backtest window start. |
| `end_date` | str | `end_date` | Backtest window end. |
| `total_trades` | int | `total_trades` | |
| `winning_trades` | int | `winning_trades` | |
| `losing_trades` | int | `losing_trades` | |
| `win_rate` | float | `win_rate` | In `[0, 1]`. |
| `sharpe_ratio` | float | `sharpe_ratio` | Free range. |
| `total_pnl` | float | `total_pnl` | Account currency. |
| `total_pnl_pct` | float | `total_pnl_pct` | Fraction of starting equity. |
| `max_drawdown_pct` | float | `max_drawdown_pct` | Fraction. |
| `created_at` | str | `created_at` | UTC ISO 8601. |

**Excluded from the family schema** (present in the upstream table
but deliberately omitted to keep the family stable for now):
`profit_factor`, `expectancy`, `max_drawdown`, `avg_win`, `avg_loss`,
`largest_win`, `largest_loss`. Adding any of these is a schema bump
(builder_version goes up; existing datasets stay valid).

### Other families (placeholder)

The remaining families in `dataset-taxonomy.md` (`market_raw`,
`market_features`, `setup_labels`, `trade_outcomes`,
`account_context`, `review_journal`) do not yet have buildable
implementations. Their schemas will land alongside the first builder
for each in dedicated follow-up sprints.

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

Non-zero exit code on failure; the report is emitted as JSON for
automation.

## Update rule

This doc must be reviewed in the same PR as any change to
`DatasetMetadata`, the per-family schemas above, or the validator's
type-checking semantics.
