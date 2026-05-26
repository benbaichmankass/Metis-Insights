---
description: Run the autonomous ML lifecycle review — trainer health, per-model status, promotion/demotion recommendations against the 7-stage ladder, and experiment proposals.
argument-hint: "[optional free-form hint, e.g. 'focus on btc-regime models']"
---

# /ml-review — autonomous ML lifecycle review

Reviews the ICT bot's **model + training lifecycle** since the last
ml-review. Delegates to the
[`ml-review` skill](../skills/ml-review/SKILL.md).

**This is the ML-lifecycle session of the three-way review split**
(2026-05-26):

- `/health-review` — system/pipeline/data health.
- `/performance-review` — strategy + trading performance + per-decision
  scoring.
- `/ml-review` — model training, registry, promotion/demotion,
  experiments (this command).

If the operator wanted system plumbing or strategy trade scoring,
redirect to the right command.

## What you need before invoking

**Nothing.** Claude pulls the trainer-VM state through the
`trainer-vm-diag.yml` relay (arbitrary SSH bash) and the live-VM ML
mirror via `/api/bot/ml/*` + `/api/bot/shadow/*` +
`/api/bot/trades/scores`. No paste, no SSH.

## What the skill does

Since the last ml-review: pulls trainer-service state + training
cycle log + dataset builds + the full registry + every shadow
prediction stream and its joined realized-trade outcomes, grades
trainer-center health (`trainer_service`, `trainer_datasets`,
`trainer_registry`, `trainer_models`), emits a per-model status line
for **every** model in `python -m ml list-models`, identifies
promotion / demotion candidates against the 7-stage ladder, proposes
AI experiments to expand coverage, drains
`docs/claude/ml-review-backlog.json`, and posts a one-line update to
the Claude channel.

Promotion past `shadow` is **Tier-3** — proposed only. The operator
decides.

## What it produces

A single JSON object matching
`comms/schema/ml_review_response.template.json`: `trainer_findings`
(service/datasets/registry/models), `model_status[]` (one per model),
`promotion_recommendations[]`, `experiments_proposed[]`,
`backlog_drain[]`, `anomalies[]`, `recommended_action`,
`claude_channel_ping`.

`$ARGUMENTS` is a free-form hint; weights the reviewer's focus,
doesn't change the rubric. Filled by the harness before execution.
Begin.
