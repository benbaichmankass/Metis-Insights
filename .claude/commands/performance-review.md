---
description: Run the autonomous trading-performance review — per-strategy aggregates, per-order-package A-F grading, comparison to real PnL, M13 insights cross-check, and Tier-3 tweak proposals.
argument-hint: "[optional free-form hint, e.g. 'focus on vwap on bybit_2']"
---

# /performance-review — autonomous trading performance review

Reviews the live ICT bot's **trading performance** since the last
performance-review. Delegates to the
[`performance-review` skill](../skills/performance-review/SKILL.md).

**This is the trading-performance session of the three-way review
split** (2026-05-26):

- `/health-review` — system/pipeline/data health.
- `/performance-review` — strategy + trading performance + per-decision
  scoring (this command).
- `/ml-review` — model training, registry, promotion/demotion,
  experiments.

If the operator wanted system plumbing or model perf, redirect to the
right command.

## What you need before invoking

**Nothing.** Claude pulls the order-package + trade + M13 insights
data itself via the diag relays + `/api/bot/*` endpoints. No paste,
no SSH.

## What the skill does

Since the last performance-review: pulls all order packages + trades
in the window, aggregates per-strategy stats (win rate, PnL, hold
times, rejection clusters), grades every order package A/B/C/D/F
keyed by `order_package_id` (+ the three training-friendly labels),
appends each grade to `comms/claude_strategy_scores.jsonl`, reads the
M13 AI-analyst insights cache and cross-checks its claims against the
same data, proposes Tier-3 tweaks with evidence, drains
`docs/claude/performance-review-backlog.json`, and posts a one-line
update to the Claude channel.

Strategy / risk-cap changes are **Tier-3** — proposed only. The
operator approves.

## What it produces

A single JSON object matching
`comms/schema/performance_review_response.template.json`:
`strategy_performance[]`, `trade_decision_grades[]` (also persisted
to `comms/claude_strategy_scores.jsonl`), `insights_review[]`,
`proposed_tweaks[]`, `backlog_drain[]`, `anomalies[]`,
`recommended_action`, `claude_channel_ping`.

`$ARGUMENTS` is a free-form hint; weights the reviewer's focus,
doesn't change the rubric. Filled by the harness before execution.
Begin.
