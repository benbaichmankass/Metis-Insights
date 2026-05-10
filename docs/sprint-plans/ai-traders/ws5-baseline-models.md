# WS5 — Baseline models

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 🔄 IN PROGRESS — sub-sprint A closed 2026-05-10.

## Decomposition

WS5 lands as a series of per-baseline sub-sprints. Each baseline:
1. Adds its dataset family builder (if not already buildable).
2. Adds its trainer + evaluator.
3. Round-trips end-to-end through the WS4 training-center harness.
4. Lands a manifest under `ml/configs/`.
5. Documents the leakage discipline for that family.

| Sub-sprint | Baseline | Dataset prereq | Status |
|---|---|---|---|
| **S-AI-WS5-A** | Outcome probability (per-strategy historical winrate) | `trade_outcomes` | ✅ DONE 2026-05-10 |
| S-AI-WS5-B | Regime classifier | `market_raw` | 📋 queued |
| S-AI-WS5-C | Setup quality scorer | `setup_labels` | 📋 queued |
| S-AI-WS5-D | Execution quality model | `trade_outcomes` (with execution metadata) | 📋 queued |
| S-AI-WS5-E | Post-trade review model | `review_journal` | 📋 queued (depends on M7) |
| S-AI-WS5-F | Prop mission policy assist | `account_context` | 📋 queued (deterministic-first per master plan) |

## Objective

Prove the system with simple, strong baselines before introducing
advanced open-source model families.

## Tasks (per baseline)

1. Define labels and the leakage discipline.
2. Build the dataset family (if not already buildable).
3. Train via the WS4 manifest path.
4. Evaluate by regime, symbol group, timeframe where relevant.
5. Compare against a heuristic / rule baseline.
6. Publish a run summary into `ml/reports/` (when applicable).

## Acceptance (per baseline)

- [ ] Each baseline has a dataset, trainer, evaluator, and summary.
- [ ] No advanced model family is introduced before a baseline
  exists for the same task.
- [ ] Each baseline produces decision-useful metrics, not only
  generic ML metrics.

## S-AI-WS5-A — Outcome probability

**Closed 2026-05-10.** Decision-useful question: "does the historical
win rate per strategy carry signal for the next closed trade?"

Deliverables:
- New: [`ml/datasets/families/trade_outcomes.py`](../../../ml/datasets/families/trade_outcomes.py) +
  registry registration. Read-only against `trade_journal.db`.
- New: [`ml/trainers/per_strategy_winrate.py`](../../../ml/trainers/per_strategy_winrate.py) +
  re-export.
- New: [`ml/evaluators/classification.py`](../../../ml/evaluators/classification.py) +
  re-export. Headline metrics: accuracy, precision, recall, f1,
  brier, n_eval.
- New: [`ml/configs/baseline-trade-outcome-winrate.yaml`](../../../ml/configs/baseline-trade-outcome-winrate.yaml).
- New: [`tests/ml/datasets/test_trade_outcomes.py`](../../../tests/ml/datasets/test_trade_outcomes.py),
  [`test_per_strategy_winrate.py`](../../../tests/ml/test_per_strategy_winrate.py).
- Updated: dataset taxonomy + schema docs + AI-platform doc +
  AI-TRADERS-ROADMAP + ROADMAP.
- Sprint log:
  [`docs/sprint-logs/S-AI-WS5-A.md`](../../sprint-logs/S-AI-WS5-A.md).

Acceptance:
- [x] Dataset built and validated against synthetic SQLite (5
  fixture rows → 3 emitted, OPEN / backtest / null-pnl skipped).
- [x] Trainer + evaluator unit-tested.
- [x] End-to-end round trip via
  [`tests/ml/test_experiments_runner.py`](../../../tests/ml/test_experiments_runner.py)
  pattern (manifest can be wired through the WS4 runner with
  `--datasets-root` pointing at the tmp_path build).
- [x] Decision-useful framing: per-strategy rate vs global-only
  baseline is the natural comparison; the same harness can run
  the WS4 `ConstantPredictionTrainer` with `target_column=won`
  for the global-only sanity check.

## Out of scope (deferred per sub-sprint hand-off)

- WS5-B onwards (other baselines).
- Walk-forward / time-aware splitters (current splitter is
  stable-suffix holdout from WS4).
- Generic `predict()` interface to decouple trainer state from
  evaluator (still hard-coupled per baseline).
- A `compare` CLI subcommand (filed for a follow-up).
