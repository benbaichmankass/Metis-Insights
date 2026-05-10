# S-AI-WS5-E — Post-trade review baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/data/dataset-{taxonomy,schema}.md`](../data/), [`docs/ml/training-center.md`](../ml/training-center.md), [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md), [`.claude/skills/health-review/SKILL.md`](../../.claude/skills/health-review/SKILL.md), [`comms/schema/health_review_response.template.json`](../../comms/schema/health_review_response.template.json)
**Status:** ✅ COMPLETE

## Goal

Land WS5-E, the post-trade-review baseline. This is the first
WS5 baseline whose label is **operator/reviewer-derived**, not
P&L-derived: the `decision_grade` (A/B/C/D/F) recorded by the
`/health-review` skill in `trade_decision_grades[]` per the
schema at [`comms/schema/health_review_response.template.json`](../../comms/schema/health_review_response.template.json).
A setup can grade well even on a stop-out (textbook execution
that simply lost) and grade poorly on a small win (lucky outcome
from bad process), so this signal is orthogonal to the P&L
labels in WS5-A / WS5-C / WS5-D.

## Decisions

- **Source = answered comms request artifacts.** The
  `/health-review` skill emits a JSON payload conforming to the
  template; the operator pastes it into Telegram; the bot files
  it under `comms/requests/REQ-*.json :: .response.answers[*]
  .free_text`; once acknowledged, the artifact is archived to
  `comms/archive/`. The new family scans both directories,
  parses the embedded JSON, and emits one row per
  `trade_decision_grades[]` entry. No new persistence layer
  needed — the comms artifact path is the canonical store.
- **Label = `decision_grade_score` (numeric mapping of letter
  grade).** `A→4, B→3, C→2, D→1, F→0`. Continuous-target
  convention (per WS5-C) plus an ordinal interpretation that
  matches operator intuition: "this strategy averages a 3.2 over
  the past 30 trades." Unknown / missing letters drop the row.
  The raw letter is preserved on the row alongside the score.
- **Trainer reuse.** `PerStrategyWinRateTrainer` with
  `target_kind: numeric_mean` — same chassis as WS5-C, WS5-C-FU,
  WS5-D. No new trainer code.
- **Empty-state behaviour is acceptable.** Until the operator
  starts answering health-review prompts with the JSON template,
  the family yields zero rows. The framework's row-count check
  will reject the (empty) build, which is the correct "data not
  flowing yet" signal — not a code defect. The family + manifest
  are deployed in advance so the moment a real response lands,
  the training surface is wired.
- **Leakage discipline.** `decision_grade`,
  `decision_grade_score`, `entry_quality`, `exit_quality`,
  `risk_management`, `rationale`, and `alternative_action` are
  all reviewer outputs and MUST be excluded from features
  against `decision_grade_score`. Only signal-time / outcome-side
  metadata (`setup`, `symbol`, `direction`, `entry_price`,
  `stop_loss`, `take_profit_1`, `position_size`, `exit_reason`)
  may be used as features. `leakage_test_status: skipped`
  (trainer's responsibility, same rule as the other label
  families).
- **Letter-grade case-insensitive.** Reviewer typos (e.g. `b`
  instead of `B`) are upper-cased before lookup. The mapping
  table is the only authority; non-mapped letters drop the row.

## Deliverables

- `ml/datasets/families/review_journal.py` —
  `ReviewJournalBuilder` family. Path-glob over `requests/` +
  `archive/` (controlled by `include_archive` knob, default on).
  `_parse_review_payload` JSON-parses the free-text answer and
  validates that it carries a `trade_decision_grades` list.
  `_GRADE_TO_SCORE` is the only score-mapping authority.
  Field-level coercion (`_coerce_str` / `_coerce_int` /
  `_coerce_float`) handles upstream type drift (template fields
  are documented but not validated against a schema).
- `ml/datasets/registry.py` — registered `ReviewJournalBuilder`.
- `ml/configs/baseline-post-trade-review.yaml` — paired manifest:
  per-`setup` mean `decision_grade_score`, time-aware holdout on
  `reviewed_at`, `target_deployment_stage: research_only`.
- `tests/ml/datasets/test_review_journal.py` — 12 tests:
  round-trip + metadata, all five letter grades round-trip to
  scores, unknown / empty / null grade dropped, pending request
  yields nothing, garbled / no-grades / empty-grades-list cases
  skipped while a sibling valid response still emits, archive
  inclusion + exclusion knob, setup filter, empty comms root
  yields nothing, missing comms root error, lower-case letter
  upper-cased before mapping.

## Acceptance

- [x] `pytest tests/ml/datasets/test_review_journal.py` — 12/12 pass.
- [x] `pytest tests/ml/` — full ML suite 194/194 pass (182 prior
      + 12 new; no regression).
- [x] `ruff check ml/datasets/families/review_journal.py
      tests/ml/datasets/test_review_journal.py
      ml/datasets/registry.py` — clean.
- [x] `review_journal` registered: `from ml.datasets import
      list_families` includes it.
- [x] No live runtime touched (research-only family + manifest).

## Out of scope (filed for follow-ups)

- **Bidirectional cross-check with `trade_outcomes`.** Once
  health-review responses start landing, a follow-up sprint
  could JOIN `review_journal.trade_id` ↔
  `trade_outcomes.trade_id` to compare reviewer grade vs P&L
  outcome on the same trade — surfacing the gap between
  "good process" and "good outcome" the operator already
  intuits.
- **Multi-target training.** The same family carries
  `entry_quality`, `exit_quality`, and `risk_management` as
  ordinal sub-grades. A future manifest could train one model
  per sub-grade.
- **WS5-F — Prop mission policy baseline.** Final WS5
  sub-sprint. Source = `account_context` (mission rules +
  drawdown state); will need a similar source-availability
  audit before commitment.
- **Retroactive grading.** Past closed trades have no review
  responses. A future sprint could ask the operator to review
  trades retroactively (batched), or have a sanity reviewer
  produce synthetic grades from a `signal_logic` + outcome
  rubric for backfill.

## Hand-off

- Five baseline manifests now live side-by-side, each predicting
  a different per-trade quality signal:
  - `baseline-trade-outcome-winrate.yaml` (WS5-A) — binary `won`.
  - `baseline-setup-quality.yaml` (WS5-C) — continuous
    `r_multiple` from trade journal `setup_type`.
  - `baseline-setup-quality-audit.yaml` (WS5-C-FU) — continuous
    `r_multiple` from audit-joined `audit_pattern`.
  - `baseline-execution-quality.yaml` (WS5-D) — continuous
    `entry_slippage_bps` from execution metadata.
  - `baseline-post-trade-review.yaml` (this sprint) —
    continuous `decision_grade_score` from reviewer responses.
- All five share the same trainer chassis
  (`PerStrategyWinRateTrainer` with `target_kind: numeric_mean`
  for the four continuous targets, vanilla winrate for WS5-A's
  binary target). All five evaluators are time-aware holdout.
  All five target `research_only` — no live tier candidates.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md).

## Live runtime impact

None. New family code is only read during `python -m ml.datasets
build review_journal ...`; no pipeline / web-api / health suite
path imports it. The family reads files in `comms/requests/`
+ `comms/archive/` but does NOT modify them.
