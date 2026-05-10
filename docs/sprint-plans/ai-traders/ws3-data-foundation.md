# WS3 — Data foundation

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M10
**Sprint id:** **S-AI-WS3** (started + closed 2026-05-10)
**Status:** ✅ DONE

## Objective

Build a dataset and feature system that is versioned, reproducible,
and Hugging Face friendly.

## Dataset families

Full table in [`docs/data/dataset-taxonomy.md`](../../data/dataset-taxonomy.md):

- `market_raw` (scaffolded)
- `market_features` (scaffolded)
- `setup_labels` (scaffolded)
- `trade_outcomes` (scaffolded)
- `backtest_results` (scaffolded **+ buildable**, this sprint)
- `account_context` (scaffolded)
- `review_journal` (scaffolded)

## Tasks (delivered)

1. [x] `docs/data/dataset-taxonomy.md` — every family, owner,
   freshness target, primary consumer.
2. [x] `docs/data/dataset-schema.md` — mandatory metadata block +
   `backtest_results` field schema. Other families' schemas filed for
   per-family follow-ups.
3. [x] Naming conventions — `<family>/<symbol_scope>/<timeframe>/vNNN`
   in [`docs/data/versioning-policy.md`](../../data/versioning-policy.md).
4. [x] At least one reproducible dataset builder —
   `BacktestResultsBuilder` under
   [`ml/datasets/families/backtest_results.py`](../../../ml/datasets/families/backtest_results.py).
5. [x] Hugging Face publishing workflow doc —
   [`docs/integrations/huggingface-datasets.md`](../../integrations/huggingface-datasets.md).
   Manual / operator-driven; no `huggingface_hub` dep added.
6. [x] Validation script —
   [`ml.datasets.validate.validate_dataset`](../../../ml/datasets/validate.py)
   + CLI (`python -m ml.datasets validate <path>`).
7. [x] Retention / versioning policy —
   [`docs/data/versioning-policy.md`](../../data/versioning-policy.md).
   Append-only; pruning is operator-acknowledged Tier 2.

## Acceptance

- [x] Documented dataset taxonomy.
- [x] At least one dataset family generated and published
  repeatably (`backtest_results` via `python -m ml.datasets build
  backtest_results ...`).
- [x] Dataset metadata carries enough lineage to reproduce a
  training run later — `generation_commit_sha`, builder qualname +
  version, source, leakage-test status, schema, row_count, all in
  `metadata.json`.

## Out of scope (deferred)

- Builders for `market_raw`, `market_features`, `setup_labels`,
  `trade_outcomes`, `account_context`, `review_journal`. Each is
  filed as its own follow-up so leakage tests can be written
  per-family.
- A `python -m ml.datasets publish ...` subcommand wrapping
  `huggingface-cli`. Filed as a follow-up; today the publish flow
  is manual.
- Wiring the dataset CLI into a scheduled timer / GitHub Actions
  workflow. Out of scope; operator-driven only for now.
- WS4 training-center expansion under `ml/`.

## Risks (mitigated)

- **Look-ahead leakage in label construction.** Mitigation: every
  builder's metadata records `leakage_test_status`. The first
  family (`backtest_results`) is `n/a` because it stores aggregate
  metrics from completed backtests — there is no forward-looking
  label.
- **Builder accidentally writing to live DB.** Mitigation:
  `BacktestResultsBuilder` opens `trade_journal.db` via
  `mode=ro` URI, so any write attempt errors out at the SQLite
  layer.
- **Dataset disk pressure on Oracle VM.** Mitigation: WS9 rule
  documented in `huggingface-datasets.md`; heavy builds run off
  the live VM.

## Deliverables (this sprint)

Code:
- `ml/datasets/__init__.py`, `metadata.py`, `builder.py`,
  `registry.py`, `validate.py`, `cli.py`, `__main__.py`.
- `ml/datasets/families/__init__.py`,
  `ml/datasets/families/backtest_results.py`.
- `tests/ml/__init__.py`, `tests/ml/datasets/__init__.py`,
  `test_metadata.py`, `test_validate.py`,
  `test_backtest_results.py`.

Docs:
- `docs/data/dataset-taxonomy.md` (new).
- `docs/data/dataset-schema.md` (new).
- `docs/data/versioning-policy.md` (new).
- `docs/integrations/huggingface-datasets.md` (new).
- `docs/architecture/ai-model-platform.md` (updated: live + Known
  Gaps + Change Log + Architecture Update Rule + Mermaid).
- This file (status → DONE, acceptance check-offs).
- `docs/AI-TRADERS-ROADMAP.md` (WS3 → DONE; change-log row).
- `ROADMAP.md` (WS3 → DONE; S-AI-WS3 ledger row).
- `docs/sprint-logs/S-AI-WS3.md` (new).
