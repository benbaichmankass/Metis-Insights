# WS8 — Monitoring and feedback loops

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — schedule after the first model is in shadow
mode (WS7).

## Objective

Trading-specific observability and post-deployment review loops.

## Tasks

1. Log model version and config for each scored opportunity.
2. Track model confidence, downstream decision, realized outcome, veto
   impact.
3. Feature drift and outcome drift monitoring.
4. Strategy / model attribution: was the move from the strategy, the
   model, the filter, or execution conditions?
5. Retraining trigger policy based on drift, stale data, degraded
   business metrics.
6. Post-trade review workflow that feeds back into the WS3 datasets
   (closing the loop).

## Acceptance

- Logging is sufficient to reconstruct why a model influenced or did not
  influence a trade.
- Defined policy for retraining, rollback, review.
- Monitoring focuses on trading outcomes, not only offline ML metrics.
