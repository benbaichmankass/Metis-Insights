# S-AI-WS5-C-FU — Setup-quality scorer V2 (audit-joined source)

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS5-C.md`](S-AI-WS5-C.md), [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md), [`docs/ml/training-center.md`](../ml/training-center.md)
**Status:** ✅ COMPLETE

## Goal

Evolve the setup-quality scorer from "trade-journal CLOSED trades
only" (S-AI-WS5-C, `setup_labels` family) to a richer source that
**joins recorded setups in `runtime_logs/signal_audit.jsonl`** with
the matching CLOSED trade in `trade_journal.db`. The audit join
surfaces setup-time features (the ICT pattern, signal-time
confidence, side, bars-back-of-setup) that the bare trade-journal
row does not carry — giving downstream feature engineering a
larger surface to draw on.

## Decisions

- **Operator direction (2026-05-10).** Source = recorded setups +
  trade outcomes. Label = continuous R-multiple. Trainer = same
  `PerStrategyWinRateTrainer` with `target_kind: numeric_mean`.
  This sprint executes that direction as a follow-up to
  S-AI-WS5-C, which shipped the trade-journal-only baseline first
  to keep PR scope small.
- **Separate family, not a switch on the existing one.** Added
  `setup_labels_audit` as its own builder. Reasons:
  - Different inputs (`audit_log_path` + `db_path` vs
    `db_path`), different schema (additional `audit_*` columns),
    different join logic. Hiding both modes behind one builder
    via a config flag would push complexity into shared code with
    no upside; downstream callers pick the family they want.
  - The v1 family stays as-is — its tests, manifest, and
    semantics are preserved. v2 is purely additive.
- **Composite-key join `(strategy, symbol, timestamp ± window)`.**
  No stable `signal_id` exists across audit events and trade
  rows (confirmed: audit log has `logged_at_utc` but no
  identifier the trade row carries). The composite key matches
  audit `(strategy, symbol)` to trade `(strategy_name, symbol)`
  and accepts the closest event within `match_window_seconds`
  (default 60 s) of the trade's `created_at` (fall back to
  `timestamp`). Each audit event is consumed at most once
  (`consumed: set[id]` of matched events) so two trades cannot
  steal the same signal.
- **Reject "rejected" audits.** Events with non-empty
  `stage_rejections`, or with no `entry`/`price` set, are
  treated as "setup did not fire" and dropped. The dataset is
  about labelled setups — a rejected setup has no associated
  trade outcome.
- **Schema enrichment, not replacement.** v2 row carries the same
  trade fields as v1 (renamed with a `trade_*` prefix where
  ambiguous) plus `audit_event`, `audit_logged_at_utc`,
  `audit_side`, `audit_pattern`, `audit_confidence`,
  `audit_bars_back_of_setup`, and a `match_offset_seconds`
  bookkeeping column for QA. Same `r_multiple` label, same
  `won` derivation, same `r_cap` / `risk_pct` knobs.
- **Survivorship is acknowledged, not solved.** Only setups that
  fired into a closed trade are labelled. v1 had the same
  property by construction; v2 doesn't change it. Solving it
  would require labelling rejected setups with a counterfactual
  outcome, which is out of scope for a research-only baseline.
- **New manifest `baseline-setup-quality-audit.yaml`.** Same
  trainer / evaluator stack as the v1 manifest; feature column is
  `audit_pattern` (the ICT pattern label written by the pipeline
  at signal time, e.g. `FVG`, `OB`) instead of `setup_type` (which
  in the current trade-journal flow equals `strategy_name` — a
  weaker feature). Time column is `trade_created_at`. The two
  manifests are directly comparable: same metrics
  (`mse`, `mae`), same holdout fraction (0.2), same target
  deployment stage (`research_only`).

## Deliverables

- `ml/datasets/families/setup_labels_audit.py` — `SetupLabelsAuditBuilder`
  family. JSONL audit reader (skip non-`{turtle_soup_eval, pipeline_result}`
  events, skip rejected, skip events missing `entry`/`price`/`strategy`/
  `symbol`/`logged_at_utc`); SQL trade reader inheriting v1's filter
  (CLOSED, non-backtest, non-empty `setup_type`, non-null `pnl`); join
  via `_match_audit` (nearest by `|trade_time - audit_time|` within
  `match_window_seconds`).
- `ml/datasets/registry.py` — registered `SetupLabelsAuditBuilder` so
  `list_families()` / `get_builder("setup_labels_audit")` work.
- `ml/configs/baseline-setup-quality-audit.yaml` — V2 manifest using
  `audit_pattern` as the feature column.
- `tests/ml/datasets/test_setup_labels_audit.py` — 12 tests covering
  round-trip + metadata, drop-rejected-audits, drop-no-entry-audits,
  match-window enforcement (60 s vs 3600 s), nearest-event match,
  one-audit-to-one-trade, strategy/symbol mismatch drop, OPEN /
  backtest / empty-setup-type drop, missing-audit-log error,
  invalid-window error, garbled-JSONL line skip, registry inclusion.

## Acceptance

- [x] `pytest tests/ml/datasets/test_setup_labels_audit.py` — 12 / 12 pass.
- [x] `pytest tests/ml/` — full ML suite 170 / 170 pass (no regression
      against existing setup_labels v1 tests, regime classifier, trade
      outcomes, trainers, splitters, manifest).
- [x] `ruff check ml/datasets/families/setup_labels_audit.py
      tests/ml/datasets/test_setup_labels_audit.py` — clean.
- [x] `setup_labels_audit` registered: `from ml.datasets import
      list_families` includes it.
- [x] No live runtime touched (research-only family + manifest).
- [x] No trade journal / audit log read at runtime — files are read
      only by an explicit dataset build.

## Out of scope (filed for follow-ups)

- **WS5-D — Execution quality baseline.** `trade_outcomes` joined
  with execution metadata (slippage, fill latency); next sprint
  in the WS5 plan.
- **Counterfactual labels for rejected setups.** Would need a
  forward-replay of price action from the rejection point to
  estimate "what would have happened". Out of scope for a
  research-only baseline.
- **Audit log offline copy.** Currently `audit_log_path` defaults
  to a relative path. Production training will need an explicit
  archive of `runtime_logs/signal_audit.jsonl` snapshots; that
  belongs with the WS3 dataset publishing pipeline (HF cron),
  not with the family code.
- **Per-strategy audit pattern coverage.** `pipeline_result`
  carries `pattern` for every strategy; `turtle_soup_eval`
  carries it only when set. Strategies that don't write
  `pipeline_result` events with a `pattern` field will yield
  rows where `audit_pattern` is empty. The trainer's
  per-feature-value bucketing handles empty as just another
  bucket — a future sprint can investigate whether to drop
  empty-pattern rows or treat the strategy explicitly.

## Hand-off

- v1 manifest (`baseline-setup-quality.yaml`, S-AI-WS5-C) and v2
  manifest (`baseline-setup-quality-audit.yaml`, this sprint) are
  directly comparable: same trainer + evaluator + holdout
  fraction. The MAE delta between them is the operator-readable
  signal that "did the audit-time pattern label add information
  beyond the trade-journal `setup_type`?".
- Both manifests target `research_only`. No live tier candidate.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md).

## Live runtime impact

None. New family code is only read during `python -m ml.datasets
build setup_labels_audit ...`; no pipeline / web-api / health
suite path imports it.
