# Dataset Schema

> **Status:** Canonical (data scope). Updated through
> **S-AI-WS5-B-PART-1** (2026-05-10): `market_raw` schema added.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
> Family roster: [`docs/data/dataset-taxonomy.md`](dataset-taxonomy.md).

## Mandatory metadata block

Every dataset artifact written by
`ml.datasets.builder.DatasetBuilder.build` carries `metadata.json`
from
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
| `label_version` | str | `n/a` for raw families. |
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
Source: `trade_journal.db::backtest_results` (read-only via
`mode=ro` URI).

| Field | Type | Notes |
|---|---|---|
| `id` | int | Primary key. |
| `run_date` | str | ISO 8601. |
| `strategy_version` | str | e.g. `vwap-v2`. |
| `start_date`, `end_date` | str | Backtest window. |
| `total_trades` / `winning_trades` / `losing_trades` | int | |
| `win_rate` | float | In `[0, 1]`. |
| `sharpe_ratio` | float | Free range. |
| `total_pnl` / `total_pnl_pct` / `max_drawdown_pct` | float | |
| `created_at` | str | UTC ISO 8601. |

### `trade_outcomes`

Builder: [`TradeOutcomesBuilder`](../../ml/datasets/families/trade_outcomes.py).
Source: `trade_journal.db::trades` filtered to `status='CLOSED'
AND is_backtest=0 AND pnl IS NOT NULL`. Read-only via `mode=ro`.

| Field | Type | Notes |
|---|---|---|
| `id` | int | Primary key. |
| `timestamp` | str | Signal / entry time. |
| `symbol` | str | |
| `direction` | str | `LONG` \| `SHORT`. |
| `strategy_name` / `setup_type` / `killzone` / `bias` | str | Empty string when null upstream. |
| `pnl` / `pnl_percent` | float | **Outcomes; do not use as features against `won`.** |
| `account_id` | str | Multi-account identifier. |
| `created_at` | str | UTC ISO 8601. |
| `won` | bool | **Label** (`pnl > 0`). `label_version: won-from-pnl-v1`. |

**Leakage discipline:** the trainer's `feature_column(s)` MUST
exclude outcome columns when targeting `won`. The dataset records
`leakage_test_status: skipped`.

### `market_raw`

Builder: [`MarketRawBuilder`](../../ml/datasets/families/market_raw.py).
Source: dispatched to a named adapter (`csv`, `bybit_v5_offvm`,
...). Adapter framework + canonical row shape:
[`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md).

| Field | Type | Notes |
|---|---|---|
| `ts` | str | ISO 8601 UTC timestamp of the bar. |
| `symbol` | str | e.g. `BTCUSDT`. |
| `timeframe` | str | Canonical token: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. |
| `open`, `high`, `low`, `close` | float | OHLC. |
| `volume` | float | Base-units volume; `0.0` when unavailable. |
| `source` | str | Adapter name (`MarketRawAdapter.source`). |

**No labels.** `label_version: n/a`,
`leakage_test_status: n/a`. Downstream `market_features` /
regime-label datasets that derive features own their own leakage
tests.

The builder records the adapter name and its kwargs in
`metadata.notes` so the build is reproducible.

### Other families (placeholder)

`market_features`, `setup_labels`, `account_context`,
`review_journal` do not yet have buildable implementations.

## Validation

`ml.datasets.validate_dataset(path)` checks the artifact integrity
(metadata + per-row schema match + row count). Exposed via the
CLI as `python -m ml.datasets validate <path>` and via
`python -m ml validate-dataset <path>` (passthrough).

## Update rule

Review this doc in the same PR as any change to
`DatasetMetadata`, the per-family schemas above, or the validator's
type-checking semantics.
