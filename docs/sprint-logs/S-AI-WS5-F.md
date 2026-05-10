# S-AI-WS5-F — Prop mission policy baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/data/dataset-{taxonomy,schema}.md`](../data/), [`docs/ml/training-center.md`](../ml/training-center.md), [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md), [`config/accounts.yaml`](../../config/accounts.yaml)
**Status:** ✅ COMPLETE

## Goal

Land WS5-F, the final WS5 baseline. Adds the
**`account_context`** family that joins
`trade_journal.db::trades` with prop-account mission rules
declared in `config/accounts.yaml`. Emits one row per non-backtest
prop trade — both **taken** trades and **rejected** signals — with
the binary action label `was_taken`. Closes the WS5 baseline-model
sub-sprint sequence (A through F), establishing one labelled
dataset family per category in the WS5 plan.

## Decisions

- **Operator-stated source `account_context`** matches the
  WS5 plan: mission rules + drawdown state. Pre-implementation
  audit (this sprint) confirmed:
  - Mission rules **are** persisted: `config/accounts.yaml`
    carries per-account `phase_requirements` + `risk` blocks,
    plus `account_state` (evaluation / funded) and
    `overnight_restricted`.
  - Drawdown state is **partially** persisted:
    `runtime_state/prop_state.json` carries account-level
    cumulative counters, but per-trade equity / drawdown
    snapshots are NOT recorded in `trades`. For the baseline,
    static mission rules + the parsed `skip_reason` are enough
    to learn per-strategy acceptance rates. Per-trade equity
    snapshots are filed for follow-up instrumentation (see "Out
    of scope" below).
- **Label = `was_taken` (binary).** Derived from
  `trades.status`:
  - `was_taken=True` → status ∈ `{open, closed, CLOSED}`
  - `was_taken=False` → status ∈ `{rejected, exchange_rejected,
    REJECTED}`
  - Other status values (e.g. `dry_run`) drop the row — we don't
    know how to label them.
  Status normalisation is case-insensitive, fixing a latent
  inconsistency where the codebase writes `closed` (lowercase)
  in production but the existing label families
  (`setup_labels`, `setup_labels_audit`, `trade_outcomes`) all
  filter on `status = 'CLOSED'` (uppercase) — see Out of scope.
- **Trainer reuse: binary mode of `PerStrategyWinRateTrainer`.**
  Same chassis as WS5-A's winrate baseline; no
  `target_kind: numeric_mean` knob (binary is the default). This
  is the second binary baseline in the family (WS5-A is the
  first); WS5-C/D/E run on the continuous `target_kind:
  numeric_mean` extension.
- **Skip reason as a dataset column, not a feature.** When a
  signal is rejected, `entry_reason` carries
  `REJECTED: <CODE> | <base reason>` (e.g.
  `REJECTED: SKIP_MISSION_MET | turtle_soup signal`). The
  family parses `<CODE>` into a `skip_reason` column for
  diagnostics. The manifest does NOT use `skip_reason` as a
  feature — that would leak the answer (rejected rows always
  have a reason, taken rows never do).
- **Prop accounts only.** YAML loader filters to
  `type: prop` accounts; `live` rows are excluded (no mission
  gate applies). The trade-side filter is built dynamically
  from the YAML keys, so adding a new prop account in YAML
  automatically extends the dataset on the next build.
- **Leakage discipline.** `pnl`, `pnl_percent`,
  `position_size`, and `skip_reason` are all post-decision
  outcomes; trainers targeting `was_taken` MUST exclude them.
  Only signal-time / mission-rule columns are fair game as
  features. `leakage_test_status: skipped` (trainer's
  responsibility).

## Deliverables

- `ml/datasets/families/account_context.py` —
  `AccountContextBuilder`. `_load_prop_accounts` parses
  `config/accounts.yaml` and keeps only `type: prop` blocks.
  `_mission_view` projects each block into the per-row mission
  columns (`max_dd_pct`, `daily_usd_cap`, etc.).
  `_normalise_status` lowercases for the
  taken/rejected/dropped triage. `_parse_skip_reason` splits
  `REJECTED: CODE | base` into the `<CODE>` cell, case-
  insensitive on the prefix.
- `ml/datasets/registry.py` — registered
  `AccountContextBuilder`.
- `ml/configs/baseline-prop-mission-policy.yaml` — paired
  manifest: per-`strategy_name` mean acceptance rate using the
  binary `PerStrategyWinRateTrainer` + `ClassificationEvaluator`,
  threshold 0.5, holdout 0.2, `target_deployment_stage:
  research_only`.
- `tests/ml/datasets/test_account_context.py` — 12 tests:
  round-trip + metadata, status → was_taken truth table
  (case-insensitive), unknown status drop, prop-only filter,
  account_id filter, per-account mission rules attached,
  skip-reason parser (3 happy paths + 1 non-rejection), backtest
  filter, no-prop-accounts-yields-nothing, missing-db error,
  missing-YAML error, registry inclusion.

## Acceptance

- [x] `pytest tests/ml/datasets/test_account_context.py` — 12/12
      pass.
- [x] `pytest tests/ml/` — full ML suite 206/206 pass (194 prior
      + 12 new; no regression).
- [x] `ruff check ml/datasets/families/account_context.py
      tests/ml/datasets/test_account_context.py
      ml/datasets/registry.py` — clean.
- [x] `account_context` registered: `from ml.datasets import
      list_families` includes it.
- [x] No live runtime touched (research-only family + manifest).
- [x] Read-only SQLite URI (`mode=ro`); YAML is read with
      `safe_load`.

## Out of scope (filed for follow-ups)

- **Per-trade equity / drawdown snapshots.** Adding
  `equity_at_entry` and `drawdown_pct_at_entry` columns to the
  `trades` table would let WS5-F drop the static-mission-rules
  workaround in favour of per-trade dynamic state. The current
  baseline learns "this strategy is rejected X% of the time
  under this mission profile" but cannot learn "this strategy
  is rejected X% of the time when account is at Y% drawdown."
  Follow-up: a small DB-migration sprint to backfill the
  columns and a v2 `account_context` builder that reads them.
- **Status-casing cleanup.** The existing label families
  (`setup_labels`, `setup_labels_audit`, `trade_outcomes`)
  filter on `status = 'CLOSED'` (uppercase) but production
  writes `'closed'` (lowercase). The current tests use
  uppercase fixtures, so the bug is silent in CI but produces
  empty datasets in production. WS5-F's `_normalise_status`
  handles both for its own filter; the existing families
  should be patched to do the same. Filed for a janitor sprint.
- **Per-account weighted training.** A future manifest could
  weight rows by `pos_size_cap` or `risk_pct_setting` so that
  prop accounts with stricter rules don't get dwarfed by looser
  accounts in the per-strategy aggregate. Out of scope for the
  baseline.
- **WS5 sub-sprint closeout.** WS5 is now A through F.
  Implementation of the full WS5 baseline-model series is
  COMPLETE. Next milestone on the AI-traders track is shadow
  mode (WS7) per the master plan; WS6 (open-source model layer)
  follows.

## Hand-off

- Six baseline manifests now live side-by-side, covering the
  full WS5 plan:
  - `baseline-trade-outcome-winrate.yaml` (WS5-A) — binary `won`.
  - `baseline-regime-classifier.yaml` (WS5-B-PART-2 PR 2B) —
    multiclass regime label.
  - `baseline-setup-quality.yaml` (WS5-C) — continuous
    `r_multiple` from trade-journal `setup_type`.
  - `baseline-setup-quality-audit.yaml` (WS5-C-FU) — continuous
    `r_multiple` from audit-joined `audit_pattern`.
  - `baseline-execution-quality.yaml` (WS5-D) — continuous
    `entry_slippage_bps`.
  - `baseline-post-trade-review.yaml` (WS5-E) — continuous
    `decision_grade_score`.
  - `baseline-prop-mission-policy.yaml` (this sprint) — binary
    `was_taken`.
- All seven share the same trainer chassis
  (`PerStrategyWinRateTrainer` for the per-bucket mean cases,
  `RegimeClassifierTrainer` for the multiclass case). Five
  evaluators are time-aware holdout (regression metrics); two
  are classification (`ClassificationEvaluator`,
  `MulticlassClassificationEvaluator`).
- All seven target `research_only` — no live tier candidates.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md);
  WS5 status flips to "✅ DONE (A–F)".

## Live runtime impact

None. New family code is only read during `python -m ml.datasets
build account_context ...`; no pipeline / web-api / health suite
path imports it. The SQLite reader uses the read-only URI
pattern as the existing families. YAML is read with
`yaml.safe_load`.
