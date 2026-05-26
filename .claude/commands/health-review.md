---
description: Run the autonomous technical/pipeline health review of the live ICT bot (Claude pulls the runtime state itself; no paste needed).
argument-hint: "[optional free-form hint, e.g. 'focus on bybit_2 rejections']"
---

# /health-review — autonomous technical health review

Reviews the live ICT trading bot's **technical/pipeline/data** runtime
state. This command delegates to the
[`health-review` skill](../skills/health-review/SKILL.md), which carries
the full rubric and the autonomous pipeline.

**This is the system-health session of the three-way review split**
(2026-05-26):

- `/health-review` — system/pipeline/data health (this command).
- `/performance-review` — strategy + trading performance + per-decision
  scoring.
- `/ml-review` — model training, registry, promotion/demotion,
  experiments.

If the operator wanted trade scoring or model performance, redirect
them to the right command — don't try to cover everything here.

## What you need before invoking

**Nothing.** Claude pulls the live runtime state itself via the diag
relays (`VM_SSH_KEY` + `DIAG_READ_TOKEN` are in repo secrets —
autonomous read access). The operator does **not** paste, download, or
SSH for a snapshot; asking for one would violate the autonomy mandate.

## What the skill does

Since the last health-review (not a fixed window): pulls live runtime
state, reads the cron health-snapshot report, grades full-pipeline
plumbing + DB integrity + data validity + service state + the trainer
*service* (not models — that's `/ml-review`), reviews recent sprint
logs for doc correctness, drains
`docs/claude/health-review-backlog.json`, and posts a one-line update
to the Claude channel.

## What it produces

A single JSON object matching
`comms/schema/health_review_response.template.json` (narrowed
2026-05-26 — no more `trade_decision_grades[]`/`model_status[]`):
`findings` across heartbeat / ticks / signals / orders / trades /
monitoring / sizing / api_errors / state_consistency / alert_delivery
/ strategy_silence / db_integrity / data_validity /
audit_log_freshness / trainer_service / health_snapshot, plus
`backlog_drain[]`, `sprint_doc_review[]`, `anomalies[]`,
`recommended_action`, `claude_channel_ping`.

`$ARGUMENTS` is passed through as a free-form hint; optional, and only
changes what the reviewer weights, not the rubric.

The `$ARGUMENTS` literal is filled in by the slash-command harness
before execution. Begin.
