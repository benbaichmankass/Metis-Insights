---
description: Run the autonomous layer-2 health review of the live ICT bot (Claude pulls the runtime state itself; no paste needed).
argument-hint: "[optional free-form hint, e.g. 'focus on bybit_2 rejections']"
---

# /health-review — autonomous layer-2 review

Reviews the live ICT trading bot's runtime state. This command delegates
to the [`health-review` skill](../skills/health-review/SKILL.md), which
carries the full rubric and the autonomous pipeline.

## What you need before invoking

**Nothing.** Claude pulls the live runtime state itself via the diag
relays (`VM_SSH_KEY` + `DIAG_READ_TOKEN` are in repo secrets — autonomous
read access). The operator does **not** paste, download, or SSH for a
snapshot; asking for one would violate the autonomy mandate. A pasted
`health_snapshot.txt`, if one happens to be in the chat, is accepted only
as an optional cross-check.

## What the skill does

Covers everything since the last review (not a fixed window): pulls live
runtime state, grades full-pipeline + trainer-center health, scores every
trade in the window and **persists each score by `trade_id` to
`comms/claude_trade_scores.jsonl`**, reviews recent sprint logs for doc
correctness, validates DB integrity + data validity, and **drains
`docs/claude/health-review-backlog.json`**.

## What it produces

A single JSON object matching `comms/schema/health_review_response.template.json`
— `findings` across heartbeat / ticks / signals / orders / trades /
monitoring / sizing / api_errors / state_consistency / alert_delivery /
strategy_silence / db_integrity / data_validity / audit_log_freshness /
trainer_* / net_positions / strategy_attribution / advisory_scores /
allocator_path, plus `trade_decision_grades[]` (every closed/rejected
trade in the window), `backlog_drain[]`, `sprint_doc_review[]`,
`anomalies[]`, and `recommended_action`.

`$ARGUMENTS` is passed through as a free-form hint; optional, and only
changes what the reviewer weights, not the rubric.

The `$ARGUMENTS` literal is filled in by the slash-command harness before
execution. Begin.
