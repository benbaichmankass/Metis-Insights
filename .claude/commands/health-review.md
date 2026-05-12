---
description: Run the layer-2 health-check review on a snapshot the operator just pasted.
argument-hint: "[optional free-form hint, e.g. 'focus on bybit_2 rejections']"
---

# /health-review — manual layer-2 review

Operator-driven review of the live ICT trading bot's runtime state.
This command delegates to the [`health-review` skill](../skills/health-review/SKILL.md);
the skill carries the full rubric.

## What you need before invoking

The operator must paste the contents of `health_snapshot.txt` (and
optionally `pipeline_test.json`) into the chat. Both files come from
the most recent run of [`health-snapshot.yml`](../../.github/workflows/health-snapshot.yml):

1. Open the [Actions UI for `Health Snapshot`](https://github.com/benbaichmankass/ict-trading-bot/actions/workflows/health-snapshot.yml).
2. Pick the latest successful run (or dispatch a fresh one).
3. Download `health-snapshot-<run_id>.zip` from the Artifacts section.
4. Extract, paste the `.txt` contents into the chat, then invoke `/health-review`.

Full flow + cadence: [`docs/runbooks/health-check.md`](../../docs/runbooks/health-check.md).

## What the skill produces

A single JSON object matching `comms/schema/health_review_response.template.json`
— populated `findings` across heartbeat / ticks / signals / orders /
trades / monitoring / sizing / api_errors / state_consistency /
alert_delivery / strategy_silence / db_integrity / audit_log_freshness,
plus `trade_decision_grades[]` for every closed/rejected trade in the
window, plus `anomalies[]` and `recommended_action`.

`$ARGUMENTS` is passed through to the skill as a free-form hint; it's
optional and doesn't change the rubric, only what the reviewer
weights.

The `$ARGUMENTS` literal is filled in by the slash-command harness
before execution. Begin.
