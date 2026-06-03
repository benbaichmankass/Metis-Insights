# Dataset Taxonomy

> **Status:** Canonical (data scope). Adopted in **S-AI-WS3**
> (2026-05-10). Updated through **S-MLOPT-S5** (2026-06-03):
> `trade_outcomes`, `market_raw`, `market_features`,
> `setup_labels`, and `setup_candidates` (triple-barrier, M14
> Phase 1.1) are now buildable.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
>
> **Companion docs:**
> - [`docs/data/dataset-schema.md`](dataset-schema.md) — per-family
>   schemas + mandatory metadata block.
> - [`docs/data/versioning-policy.md`](versioning-policy.md).
> - [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md).
> - [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md) —
>   `market_raw` adapter framework (S-AI-WS5-B-PART-1).
> - [`ml/datasets/`](../../ml/datasets/) — builder framework + concrete families.

## Family table

| Family | Layer | Purpose | Owner subsystem | Source(s) | Freshness target | Primary consumers |
|---|---|---|---|---|---|---|
| `market_raw` | 1 (data) | Bars, ticks, order-book-derived snapshots, unaltered | `ml/datasets/adapters/`, `src/exchange/` | adapter-dispatched (CSV, Bybit-V5 off-VM, future yfinance / etc.) | per-fetch / per-pull | `market_features` builders, regime classifier (WS5-B-PART-2) |
| `market_features` | 2 (feature/context) | Engineered features (rolling vol, log returns, vol bucket) + 3-class regime label derived from `market_raw` | `ml/datasets/families/market_features.py` | `market_raw` | aligned to `market_raw` | regime classifier (WS5-B-PART-2 PR 2B), future feature consumers |
| `setup_labels` | 2 (feature/context) | Labels for pattern / setup quality (binary `won` + continuous `r_multiple`); `source` (`live`/`backtest`) col + `include_backtest` flag (S-MLOPT-S7) | `ml/datasets/families/setup_labels.py` | `trade_journal.db::trades` (CLOSED, non-empty `setup_type`; `is_backtest=0` unless `include_backtest`) | per closed setup-tagged trade | setup quality scorer (WS5-C); meta-label augmentation (S-MLOPT-S7) |
| `setup_candidates` | 2 (feature/context) | **Dense synthetic** candidate setups from bar history: CUSUM-sampled events labeled by **triple-barrier** (TP/SL/timeout sized to local vol); emits `won` + `r_multiple` + `barrier_touched` with signal-time features and an `is_live_trade` split flag for the mandatory real-trade holdout | `ml/datasets/families/setup_candidates.py` + `ml/datasets/labeling/triple_barrier.py` | a built `market_raw` dataset | aligned to `market_raw` | meta-labeling decision model (S-MLOPT-S6), setup quality |
| `trade_outcomes` | 1 (data) | Realized trade results; derived `won = pnl > 0` label; `source` (`live`/`backtest`) col + `include_backtest` flag (S-MLOPT-S7) | `src/units/`, `trade_journal.db` | `trade_journal.db::trades` (CLOSED, non-null pnl; `is_backtest=0` unless `include_backtest`) | per closed trade | outcome probability model (WS5-A onwards); backtest-augmented training (S-MLOPT-S7) |
| `backtest_results` | 1 (data) | Aggregate backtest run summaries (M5 outputs) | `src/bot/test_strategy_consumer.py`, `trade_journal.db` | `trade_journal.db::backtest_results` | per `/test <strategy>` invocation | strategy review (M7), regime baseline comparison |
| `account_context` | 2 (feature/context) | Account state, funding phase, prop-firm restrictions, mission state | `src/units/accounts/`, `config/accounts.yaml` | accounts unit + per-account state | aligned to candidate evaluation | prop mission policy assist (WS5-F) |
| `review_journal` | 1 (data) | Post-trade reviews, mistake tagging, narrative annotations | future `docs/ml/`, M7 | operator + post-trade review model | per trade close | post-trade review model (WS5-E), retraining triggers (WS8) |

## Builder availability

| Family | Scaffolded | Buildable | Builder |
|---|---|---|---|
| `market_raw` | ✅ | ✅ (S-AI-WS5-B-PART-1) | [`ml/datasets/families/market_raw.py`](../../ml/datasets/families/market_raw.py) (CSV adapter live; Bybit off-VM scaffold env-gated, fetch wiring filed) |
| `market_features` | ✅ | ✅ (S-AI-WS5-B-PART-2 PR 2B) | [`ml/datasets/families/market_features.py`](../../ml/datasets/families/market_features.py) (derives `log_return`, `rolling_log_return_vol`, `vol_bucket` + 3-class `regime_label` from a built `market_raw` dataset; forward-window labels guarantee no feature/label leakage by construction) |
| `setup_labels` | ✅ | ✅ (S-AI-WS5-C) | [`ml/datasets/families/setup_labels.py`](../../ml/datasets/families/setup_labels.py) (reads `trade_journal.db::trades` filtered to CLOSED, non-backtest, non-empty `setup_type`; emits `r_multiple = pnl_percent / risk_pct` capped at `±r_cap`) |
| `setup_candidates` | ✅ | ✅ (S-MLOPT-S5) | [`ml/datasets/families/setup_candidates.py`](../../ml/datasets/families/setup_candidates.py) (de Prado CUSUM events + triple-barrier labels over a built `market_raw` dataset; entry at next-bar open, features past-only + label future-only → `leakage_test_status: passed` by construction; conservative realistic fills; `is_live_trade` reserves the REAL-trade holdout) |
| `trade_outcomes` | ✅ | ✅ (S-AI-WS5-A) | [`ml/datasets/families/trade_outcomes.py`](../../ml/datasets/families/trade_outcomes.py) |
| `backtest_results` | ✅ | ✅ (S-AI-WS3) | [`ml/datasets/families/backtest_results.py`](../../ml/datasets/families/backtest_results.py) |
| `account_context` | ✅ | ⏳ | WS5-F prereq |
| `review_journal` | ✅ | ⏳ | M7 prereq |

## Adding a new family

1. Add a row to the family table above.
2. Add the family's field schema and metadata expectations to
   [`docs/data/dataset-schema.md`](dataset-schema.md).
3. Implement the builder under `ml/datasets/families/<family>.py`
   subclassing `ml.datasets.builder.DatasetBuilder`.
4. Register the builder in `ml/datasets/registry.py`.
5. Add a regression test under
   `tests/ml/datasets/test_<family>.py`.
6. If the family carries forward-looking labels, run a leakage
   test and record `leakage_test_status=passed` in metadata.
   If leakage prevention is the trainer's responsibility, record
   `leakage_test_status=skipped` and document the rationale.
   If the family is raw (no labels), record `leakage_test_status=n/a`.
7. Update the change log in
   [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).

## Update rule

Review this doc in the same PR as any new family, renaming, or
owner-subsystem change.
