# WS5 — Baseline models

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — blocked on WS4 (needs the training-center
command path).

## Objective

Prove the system with simple, strong baselines before introducing more
advanced open-source model families.

## Baseline tasks

- Regime classifier
- Setup quality scorer
- Outcome probability model
- Execution quality model
- Post-trade review model
- Prop mission policy layer (deterministic first; optional model assist
  later)

## Tasks (per baseline)

1. Define labels and the leakage test.
2. Build the dataset using a WS3 builder.
3. Train via the WS4 manifest path.
4. Evaluate by regime, symbol group, timeframe where relevant.
5. Compare against a heuristic / rule baseline.
6. Publish a run summary into `ml/reports/`.

## Acceptance

- Each baseline task has a dataset, trainer, evaluator, and summary report.
- No advanced model family is introduced before a baseline exists for the
  same task.
- Each baseline produces decision-useful metrics (PnL-relevant slices),
  not only generic ML metrics.

## Sequencing

First baseline (recommended: regime classifier or setup quality scorer)
doubles as the WS4 acceptance proof. Other baselines can be parallelized
once the first one round-trips.
