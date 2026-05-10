# S-AI-WS3 — AI traders WS3: Data foundation

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (master plan); subordinate to [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) and [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
**Status:** ✅ COMPLETE

## Goal

Land a reproducible dataset framework with one buildable family
(`backtest_results`) so future model work can train against versioned
artifacts with full lineage.

## Deliverables

Code (stdlib only, no live runtime touched):
- New: [`ml/datasets/`](../../ml/datasets/) module — `metadata.py`
  (`DatasetMetadata` + `LeakageStatus`), `builder.py`
  (`DatasetBuilder` ABC + `DatasetPaths` + `SchemaViolation` +
  `VersionConflict`), `registry.py`, `validate.py`
  (`validate_dataset` + `ValidationReport`), `cli.py` +
  `__main__.py` (`python -m ml.datasets {list-families,build,validate}`).
- New: [`ml/datasets/families/backtest_results.py`](../../ml/datasets/families/backtest_results.py) —
  `BacktestResultsBuilder` reading `trade_journal.db::backtest_results`
  via SQLite `mode=ro` URI.
- New: [`tests/ml/datasets/`](../../tests/ml/datasets/) —
  `test_metadata.py` (invariants), `test_validate.py` (validator
  happy path + every error class), `test_backtest_results.py`
  (end-to-end build against synthetic SQLite + version conflict +
  filter + missing-DB).

Docs:
- New: [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md),
  [`dataset-schema.md`](../data/dataset-schema.md),
  [`versioning-policy.md`](../data/versioning-policy.md).
- New: [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md) —
  manual operator-driven publication workflow; no auto-push.
- Updated: [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md) —
  layer table cross-references the data docs; live audit row +
  Known Gaps + Architecture Update Rule + Architecture Change Log
  refreshed.
- Updated: [`docs/sprint-plans/ai-traders/ws3-data-foundation.md`](../sprint-plans/ai-traders/ws3-data-foundation.md) —
  status → DONE, acceptance check-offs.
- Updated: [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) —
  WS3 → DONE, master change-log row.
- Updated: [`ROADMAP.md`](../../ROADMAP.md) — WS3 status row, M10
  status, S-AI-WS3 ledger entry.
- This file: sprint log per `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Acceptance (from WS3 sprint plan)

- [x] Documented dataset taxonomy.
- [x] At least one dataset family generated and published
  repeatably.
- [x] Dataset metadata carries enough lineage to reproduce a
  training run later.

## Decisions

- **Stdlib only.** No `pandas`, no `pyarrow`, no `huggingface_hub`.
  JSONL output keeps the artifact line-based, diff-friendly, and
  HF-compatible.
- **Read-only DB access.** `BacktestResultsBuilder` opens
  `trade_journal.db` via `file:.../trade_journal.db?mode=ro` URI.
  Any write attempt errors out at the SQLite layer; the live DB is
  protected even from a misuse of the builder API.
- **Append-only versioning.** `VersionConflict` is raised when a
  version directory already exists unless `overwrite=True` is
  passed. The CLI exposes `--overwrite` only when the operator
  passes it explicitly.
- **Manual HF publication.** `huggingface-datasets.md` documents the
  manual flow (`huggingface-cli upload`); no auto-push behavior.
  WS9 rule cross-referenced.
- **First family is `backtest_results`.** Picked because it has a
  real upstream table populated by M5, no forward-looking labels
  (`leakage_test_status=n/a`), and a small stable column set.
  Other families need dedicated builder sprints with leakage tests
  designed per family.
- **Schema in metadata.** Each `metadata.json` carries the field
  schema so the validator is family-agnostic.
- **Type token list is intentionally small** (`int`, `float`,
  `str`, `bool`). Family schemas can add custom tokens later;
  validator will skip strict type-check for unknown tokens.

## Out of scope (deferred)

- Builders for `market_raw`, `market_features`, `setup_labels`,
  `trade_outcomes`, `account_context`, `review_journal`.
- `python -m ml.datasets publish ...` subcommand.
- Scheduled timer / GitHub Actions workflow auto-running the
  builder.
- WS4 training-center expansion under `ml/`.

## Hand-off

After this sprint ships, the natural follow-ups in priority order:

1. **WS4 — training center.** Repo-native training factory
   structure under `ml/` (`trainers/`, `evaluators/`, `experiments/`,
   `registry/`, `promotion/`, `configs/`, `reports/`). Sprint plan:
   [`docs/sprint-plans/ai-traders/ws4-training-center.md`](../sprint-plans/ai-traders/ws4-training-center.md).
2. **WS5 first baseline.** Pick the simplest baseline (likely
   regime classifier or setup quality scorer) and run it
   end-to-end through the WS3 + WS4 paths.
3. **Per-family dataset builders.** One sprint per remaining family.

## Live runtime impact

None. Stdlib-only additive code; only NEW paths
(`ml/datasets/`, `tests/ml/datasets/`, `docs/data/`,
`docs/integrations/`) plus updates to existing canonical / roadmap
docs. Operator-hold paths (`src/runtime/`, `src/units/accounts/`,
`src/main.py`, `config/accounts.yaml`, `deploy/*`) not modified.
