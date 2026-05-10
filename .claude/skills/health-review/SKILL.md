---
name: health-review
description: Layer-2 review of the LIVE ICT TRADING BOT's runtime health (NOT a code review or codebase audit). Reads the most recent machine-side health snapshot at artifacts/health/latest.json plus artifacts/health/health_snapshot.txt and emits a JSON response matching comms/schema/health_review_response.template.json. Use when the operator says "run the health review", "/health-review", "do the layer-2 review", or after a Telegram ping that says "auto-merge queued — run /health-review for the layer-2 review". Do NOT invoke this skill for code-quality audits, security reviews, or repo-scope assessments — those are separate skills (review, security-review).
---

# /health-review — manual layer-2 review of the live ICT bot's runtime

**This skill reviews the live trading bot's runtime state, not the codebase.**
It is the manual replacement for the autonomous Claude routine described
in [`docs/runbooks/health-check.md`](../../../docs/runbooks/health-check.md)
§ "How a Claude review actually happens". Use this when the operator
wants to reply to a `comms/requests/REQ-*.json` health-review request
without copy-pasting the prompt out of Telegram.

If the user asked for a *code* review, *codebase audit*, *security
review*, or *dependency check* — STOP. This is the wrong skill.
Direct them to the `review` or `security-review` skill instead.

## Inputs (all on the current `main` HEAD of this repo)

The workflow `health-snapshot-pr.yml` lands fresh artifacts on `main`
every cycle by squash-merging a labelled PR. Read these files:

- `artifacts/health/latest.json` — layer-1 machine verdict
  (`status` / `summary` / 11 `checks` / optional `error` block).
  When the operator has set `--skip-llm` (current default per
  decision 2026-05-10), `status` is `UNKNOWN` and `error.type` is
  `LayerOneSkipped` — that's expected, not a problem.
- `artifacts/health/health_snapshot.txt` — raw VM snapshot. Sectioned
  with `=== NAME ===` headers (META, PROCESSES, HEARTBEAT, TICKS,
  SIGNALS, ORDERS, TRADES, POSITIONS, MONITORING, API, ERRORS, VM,
  END). This is the source of truth.
- `artifacts/health/pipeline_test.json` (when present) — active
  dry-run smoke result from the live trader's
  `scripts/smoke_test_trade.py --dry-run`.
- `comms/requests/REQ-*.json` — review request files. Each carries the
  topic, run metadata, and the embedded machine verdict. Pick the
  most recent one (newest `created_at`) unless the user passed a
  specific request id as the skill argument.
- `.claude/health_check_prompt.md` — severity rubric for layer 1.
- `comms/schema/health_review_response.template.json` — output shape.

## Argument handling

If the user invoked the skill with no argument: pick the most recent
`comms/requests/REQ-*.json` (newest `created_at`).

If the user invoked it with a `REQ-YYYYMMDD-HHMMSS-<digits>` argument:
read `comms/requests/<that-id>.json` directly.

If the user invoked it with an all-digits workflow run id: glob
`comms/requests/REQ-*-<run_id>.json` (the suffix matches the GitHub
`run_id`).

## Decision procedure

Cross-check the layer-1 output against the raw snapshot. When layer 1
fell back to `UNKNOWN` (Anthropic credit issue, `--skip-llm`, network),
the snapshot is the source of truth — grade it yourself using the
rubric in `.claude/health_check_prompt.md`.

Map findings to the layer-2 dimensions (these differ from layer 1):

- `heartbeat`  — freshness of `runtime_logs/heartbeat.txt`.
- `ticks`      — recency + status field of recent tick records.
- `signals`    — actionable signal volume vs the 24h baseline.
- `orders`     — submitted/rejected counts; any borrow-side errors.
- `trades`     — fills, orphaned trades, P&L attribution.
- `monitoring` — `run_monitor_tick` activity; verdict application.
- `sizing`     — position-size sanity vs per-account caps in
  `config/accounts.yaml`. (Look for over-sized opens, leverage
  drift, capacity-zero rejections.)
- `api_errors` — 4xx/5xx burst rates, repeated tracebacks. Combines
  layer 1's `api` + `errors` checks.

Status grades:
- `ok`       — no anomaly worth flagging.
- `watch`    — anomaly present but bounded; no immediate action.
- `concern`  — operator should look. ⇒ `operator_attention_required: true`.

`overall_assessment` mapping:
- `healthy`     — every finding is `ok`.
- `caution`     — at least one `watch`, no `concern`.
- `investigate` — any `concern`. ⇒ `operator_attention_required: true`.

## Output

Emit a single JSON object — no prose, no markdown fences, no leading
or trailing whitespace beyond the JSON. Conform to
`comms/schema/health_review_response.template.json` exactly. Populate
every field. Use the request's `request_id` verbatim. Set
`reviewed_at` to the current UTC ISO-8601 timestamp. Set `reviewer`
to `claude`.

Schema reminder:

```json
{
  "request_id": "REQ-YYYYMMDD-HHMMSS-<slug>",
  "reviewed_at": "YYYY-MM-DDTHH:MM:SS+00:00",
  "reviewer": "claude",
  "overall_assessment": "healthy | caution | investigate",
  "findings": {
    "heartbeat":  {"status": "ok | watch | concern", "note": "..."},
    "ticks":      {"status": "ok | watch | concern", "note": "..."},
    "signals":    {"status": "ok | watch | concern", "note": "..."},
    "orders":     {"status": "ok | watch | concern", "note": "..."},
    "trades":     {"status": "ok | watch | concern", "note": "..."},
    "monitoring": {"status": "ok | watch | concern", "note": "..."},
    "sizing":     {"status": "ok | watch | concern", "note": "..."},
    "api_errors": {"status": "ok | watch | concern", "note": "..."}
  },
  "anomalies": ["...free-form list..."],
  "recommended_action": "what to do next, or 'none'",
  "operator_attention_required": false
}
```

## Notes guidance

- Each `note` ≤ 120 chars. Reference specifics from the snapshot
  (filenames, ages, counts, error classes) so the operator can verify
  quickly.
- Empty sections in the snapshot grade `watch` with a "no recent
  activity" note rather than `ok`.
- Don't fabricate data — if a section is absent from the snapshot,
  say so in the note rather than guessing.

## What NOT to do

- Don't write any files. The response is plain-text JSON in the
  conversation. The operator pastes it into the comms request's
  answer per `comms/schema/response.schema.json`.
- Don't try to call the live trader, modify `config/accounts.yaml`,
  or touch anything under `src/`. Reviews are read-only.
- Don't open issues, PRs, or commit changes — this is a sanity-review
  skill, not a remediation skill.
- Don't ask scoping questions. The scope is fixed: read the latest
  health-check artifacts and emit the response JSON. If the user
  meant a code review, the skill description is wrong — they should
  invoke `review` or `security-review` instead.

## If the inputs are missing

If `artifacts/health/latest.json` isn't present on the current HEAD
(e.g., the most recent review PR hasn't been merged yet), say so in
plain text and stop — don't synthesize a review without evidence.
