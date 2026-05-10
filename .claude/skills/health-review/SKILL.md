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

## Mandatory pre-review step — fetch the live 6-hour log window

**The snapshot alone is not enough.** The collector at
`scripts/collect_health_snapshot.sh` only greps `*.log` files for
ticks/signals/orders/trades, but the live pipeline writes to
`runtime_logs/signal_audit.jsonl` (NDJSON, not `.log`). As a result,
the snapshot's `=== TICKS / SIGNALS / ORDERS / TRADES ===` sections
will frequently say "no … logs in last 1440m" even when the bot is
actively trading. This is a known collector limitation, not a bot
outage — and it means a snapshot-only review will always under-report
activity.

So before grading, **always pull the live audit + journal tables via
the diag relay** (see `docs/claude/diag-relay.md`). This is the main
substance of the layer-2 review: Claude must look at the actual
signals, orders, and trades produced over the recent window and
sanity-check both the technical pipeline (does each signal that
should have produced an order actually produce one? do orders that
fill become trades?) and the decision quality (are the signals
reasonable for the current market context? are the position sizes,
sides, and SL/TP wired through correctly?).

Open a single `[diag-request]` issue per query, then poll
`mcp__github__issue_read` for the workflow's reply comment. Required
pulls:

1. **6-hour audit tail** — `audit?limit=600` (≈100 events/hr cap).
   Tail of `runtime_logs/signal_audit.jsonl`. Filter the returned
   NDJSON to events whose `ts` is within the last 6h.
2. **Recent order packages** — `journal?table=order_packages&limit=100`.
   Compare against the audit tail: every `signal → order` transition
   should produce a row here.
3. **Recent trades** — `journal?table=trades&limit=100`. Same idea
   for `order → fill → trade`.
4. **Status snapshot** — `status` (heartbeat + status.json + vm_health).
   Cross-check against the embedded HEARTBEAT block.

If the relay returns curl exit 7 (`Failed to connect to 127.0.0.1`),
the web-api is down — fire `vm-web-api-recover` and retry once. If
it still fails, downgrade gracefully: emit the review with a
`concern` on `api_errors` and `operator_attention_required: true`,
note that the 6h log review could not be performed, and stop. Do
not fabricate findings from the snapshot alone.

### Sanity-check rubric for the 6-hour window

Beyond freshness counts, judge **decision quality**:

- **Signal → order plumbing.** For every signal in the audit tail
  with `outcome=actionable` (or equivalent), there should be a
  corresponding `order_packages` row within seconds. Gaps → `concern`
  on `orders`.
- **Order → trade plumbing.** Every filled order should have a row
  in `trades`. Orphaned orders (filled with no trade row, or trade
  rows with no parent order) → `concern` on `trades`.
- **Side / size sanity.** Spot-check 3–5 orders: does the side match
  the signal direction? Is the qty within the per-account cap in
  `config/accounts.yaml`? Is leverage reasonable (no `qty=1` BTC
  on a $200 account)?
- **SL/TP wiring.** Each order should carry SL and TP metadata
  (visible in `order_packages.metadata` or signal_audit). Missing
  → `watch`; systematic absence → `concern`.
- **Repeated rejections.** Multiple consecutive `failed_exchange`
  / `failed_risk_gate` / `borrow_unavailable` events on the same
  symbol → `concern` on `orders` (something upstream is wedged).
- **Monitoring cadence.** `run_monitor_tick` events should appear
  on the documented cadence. Long gaps → `concern` on `monitoring`.
- **Signal reasonableness.** This is the qualitative check. Are
  signals firing at sensible times (not 100 in 5 minutes, not 0 over
  6 hours during active sessions)? Are the strategies named in the
  audit consistent with what's enabled in `config/strategies.yaml`?
  Anomalies here go in the free-form `anomalies` array.

The pipeline-test result in `artifacts/health/pipeline_test.json` is
an out-of-band dry-run of `safe_place_order`. A `warn` with note
"plumbing-on-rejection path exercised" is the **expected** outcome
when no exchange client is wired into the smoke; do not grade it
as `concern`.

## Decision procedure

Cross-check the layer-1 output against the raw snapshot **and the
live diag pulls from the pre-review step**. When layer 1 fell back
to `UNKNOWN` (Anthropic credit issue, `--skip-llm`, network), the
snapshot + diag pulls are the source of truth — grade them yourself
using the rubric in `.claude/health_check_prompt.md` plus the
sanity-check rubric above.

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

- Each `note` ≤ 120 chars. Reference specifics from the snapshot or
  live diag pulls (counts, ages, error classes, sample symbols / qtys)
  so the operator can verify quickly.
- Prefer counts from the live diag tail (`audit?limit=600` over the
  last 6h) over the snapshot's TICKS/SIGNALS/ORDERS/TRADES sections —
  the snapshot collector is known to under-report (see pre-review
  step above).
- An empty section in the snapshot is **not** automatically `watch`
  if the diag tail shows activity in the same window — grade by what
  the diag tail says, and add an anomaly noting the snapshot/diag
  disagreement so the collector bug stays visible.
- Don't fabricate data — if a diag pull failed and you couldn't
  verify a dimension, say so in the note (e.g. "audit pull failed,
  graded from snapshot only").

## What NOT to do

- Don't skip the 6-hour log review. The pre-review step is the
  substance of this routine; emitting a verdict from the snapshot
  alone is the failure mode this skill exists to prevent.
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
