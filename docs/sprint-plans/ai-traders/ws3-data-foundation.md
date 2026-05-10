# WS3 — Data foundation

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M10
**Status:** 📋 Not started — can start in parallel with WS2 once WS1 lands.

## Objective

Build a dataset and feature system that is versioned, reproducible, and
Hugging Face friendly.

## Dataset families

- `market_raw`
- `market_features`
- `setup_labels`
- `trade_outcomes`
- `backtest_results`
- `account_context`
- `review_journal`

## Tasks

1. Author `docs/data/dataset-taxonomy.md` describing each family, owner,
   freshness target, and consumer.
2. Author `docs/data/dataset-schema.md` with the per-family schemas and
   the mandatory metadata block:
   - source
   - timezone
   - symbol scope
   - timeframe
   - generation script commit SHA
   - label version
   - leakage-test status
   - notes
3. Add naming conventions for datasets and versions (e.g.
   `family/symbol/tf/vNNN`).
4. Implement at least one reproducible dataset builder under
   `ml/datasets/` so the family can be regenerated from code.
5. Set up Hugging Face dataset repos or document the publishing workflow
   under `docs/integrations/`.
6. Add validation scripts that fail on schema drift or missing required
   metadata.
7. Add a retention / versioning policy so old datasets stay traceable.

## Acceptance

- Documented dataset taxonomy.
- At least one dataset family generated and published repeatably.
- Dataset metadata carries enough lineage to reproduce a training run
  later.

## Out of scope

- Training any model on the new dataset (that is WS4 / WS5).
- Heavy backfills on the Oracle VM (WS9 rule).

## Risks

- Look-ahead leakage in label construction. Mitigation: every dataset
  builder must run a leakage test before publishing.
