# WS7 — Deployment tiers

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — partial overlap with WS4 model-registry work.

## Objective

All models move through staged influence levels instead of jumping from
training to live trading.

## Stages

1. Research only
2. Candidate
3. Backtest approved
4. Shadow mode
5. Advisory mode
6. Limited live influence
7. Live approved

## Tasks

1. Stage metadata in the WS4 model registry.
2. Build a shadow-mode execution path: model scores opportunities without
   affecting live execution. Log version + score alongside the
   deterministic decision.
3. Build advisory mode: model outputs annotate or veto only if the
   operator chooses that stage.
4. Require explicit operator approval before any model influences
   strategy behavior in live mode.
5. Deterministic fallback when a model is unavailable.
6. Logs for model version, score, final decision path.

## Acceptance

- A new model can run in shadow mode without changing live trading
  behavior.
- Promotion from one stage to the next requires documented evidence.
- Every live-influencing model has a fallback / disable path.
