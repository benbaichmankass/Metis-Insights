# WS9 — Oracle / Hugging Face runtime split

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M10
**Status:** 🔄 Continuous — enforced from WS3 onwards; this file is the
policy of record.

## Objective

Protect the live runtime while using Hugging Face for the heavier AI
lifecycle work.

## Rules

- Oracle VM hosts the live trader, bot, scheduler, small ETL, light
  preprocessing, inference, and only very small CPU-safe experiments.
- Oracle VM **must not** host heavy training, large backtests, or any
  long-running job that could starve the live process.
- Hugging Face hosts datasets, open-source model workflows, artifacts,
  model storage, and heavier training-related operations.
- Jobs that are unpredictable in memory or CPU usage do not belong on the
  Oracle live box.

## Tasks

1. Architecture doc explicitly distinguishes live runtime from training
   infrastructure.
2. Add a short `docs/ml/oracle-vs-hf-policy.md` listing what may and may
   not run on Oracle, with examples (smoke-sized eval = OK, full retrain
   = not OK).
3. Add a CI / pre-commit hint that flags large training entry points
   committed alongside live-runtime systemd unit changes.

## Acceptance

- Architecture doc explicitly distinguishes live runtime from training
  infrastructure.
- Repo contains a short operational policy for what may and may not run
  on Oracle.
