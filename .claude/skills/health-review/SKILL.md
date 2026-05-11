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
- `comms/follow_ups.json` — running list of unresolved items earlier
  reviews flagged but couldn't fully resolve (e.g. waiting for a
  trigger condition to fire, deferred design decisions). Read every
  open entry; check whether its `trigger_condition` applies to this
  review window. Schema lives at
  `comms/schema/follow_ups.schema.json`.

## Argument handling

**Default (no argument): review every pending request since the last
review.** Pick every `comms/requests/REQ-*.json` file whose `status`
field is `"pending"`, sort by `created_at` ascending, and produce one
response object per request. This prevents earlier snapshots from
being silently dropped when a later one supersedes them (the workflow
fires every 6h on a cron; a slow review window can accumulate 2–3
pending requests, and each represents a separate machine snapshot the
operator deserves a verdict on). If there are no pending requests,
say so in plain text and stop.

A single diag-relay fetch covers all of them — "current live state"
is shared. Findings, anomalies, and trade grades are computed once
and replicated across each per-request response, with `request_id`
and `reviewed_at` distinct per entry. See § Output for the array
shape.

If the user invoked it with a `REQ-YYYYMMDD-HHMMSS-<digits>` argument:
read `comms/requests/<that-id>.json` directly and emit a single
response object (legacy single-request mode).

If the user invoked it with an all-digits workflow run id: glob
`comms/requests/REQ-*-<run_id>.json` (the suffix matches the GitHub
`run_id`) and emit a single response object.

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

### Follow-up log evaluation

After fetching the diag pulls and before grading, read
`comms/follow_ups.json`. For each entry with `status: "open"` (and
`snoozed_until` in the past or null):

1. Evaluate `trigger_condition` against this review window's diag
   data. Examples: "any rejection on bybit_2 in the 6h window",
   "any /health-review run", "next time the breaker fires."
2. If the trigger applies, perform the `expected_check`. Whatever
   you find — verified-good, verified-bad, or inconclusive — folds
   into the regular findings + `anomalies` array, prefixed with the
   `id` (e.g. `"FU-20260510-001: bybit_2 had 2 rejections, no breaker
   trip yet (threshold is 3)"`).
3. If the trigger doesn't apply (the conditions in this window
   don't match), skip silently — don't pad anomalies with
   "FU-X not triggered."
4. If the diag evidence satisfies `resolution_criteria`, surface it
   in `recommended_action` with phrasing like *"Close FU-XXX
   (resolved by …)."* The operator decides; don't auto-edit the
   file.

Do not write to `comms/follow_ups.json` from this skill. New
follow-ups discovered during a review go in the response's
`anomalies` array with a clear "open as new follow-up" hint in
`recommended_action`; the operator (or a separate skill) is
responsible for editing the file.

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

### Per-trade decision grading (training-data feedstock)

For **every closed (or rejected) trade** in the 6-hour window,
emit a structured grade in `trade_decision_grades[]` so future
training sessions have a labelled feedback signal beyond raw P&L.
The grade is independent of dollar outcome — a small win on a bad
setup is still graded poorly; a stop-out on a textbook setup is
still graded fairly.

**These grades are downstream training input for the AI-traders
baseline pipeline** ([`docs/AI-TRADERS-ROADMAP.md`](../../../docs/AI-TRADERS-ROADMAP.md)).
The current consumers:

- The `trade_outcomes` family ([`ml/datasets/families/trade_outcomes.py`](../../../ml/datasets/families/trade_outcomes.py))
  joins these grades to closed trades for the WS5-A outcome
  probability baseline.
- The `setup_labels` family ([`ml/datasets/families/setup_labels.py`](../../../ml/datasets/families/setup_labels.py))
  uses them for the WS5-C setup-quality scorer.
- Future post-trade-review (WS5-E) and prop-mission-policy
  (WS5-F) baselines will read them directly.

So this section is not optional decoration — it is the labelled
feedstock that retrains the next baseline run. Grade as if the
operator will inspect every row, because the model layer
eventually will.

Use the trade's `signal_logic` blob (in `trades.notes` or the
`order_packages.signal_logic` JSON) to anchor the call. That blob
carries the entry rationale (VWAP std-dev, HTF EMA / band, sweep
buffer, ATR multiplier, etc.) — judge the trade against its own
stated edge and the post-hoc fill / exit data we have.

**Letter grade rubric (one per trade):**
- `A` — Textbook. Setup-config aligned, HTF unblocked, R:R ≥ 1.5,
  hit TP cleanly, no premature exit. The kind of trade we want
  more of.
- `B` — Good. Same as A but with one minor deviation (slightly
  low confidence, R:R ~1.0, TP1 partial only, fill slippage).
- `C` — Acceptable. Setup fired correctly and risk was contained
  (e.g., stopped at SL with the documented multiplier), but the
  EV looks marginal in retrospect.
- `D` — Poor. Setup fired but went against HTF or had thin
  confidence; only saved by mean reversion or noise.
- `F` — Bad. Should not have fired at all (config mismatch,
  htf_blocked=true overridden, oversized, against published
  bias). Or should have stayed in (premature trail-stop on what
  would clearly have run further given the same signal logic).

**Three standardized categorical labels (per trade):**
- `entry_quality`: one of
  `optimal | acceptable | late | early | should_skip | unknown`
- `exit_quality`: one of
  `optimal | tp_appropriate | sl_appropriate | premature_exit |
  held_too_long | unknown`
- `risk_management`: one of
  `correct | oversize | undersize | sl_too_tight | sl_too_wide |
  unknown`

These three labels are the training-friendly fields; the letter
grade is a single rolled-up summary for human scanning.

**Per-trade entry shape (one object per trade):**

```json
{
  "trade_id": 1135,
  "timestamp": "2026-05-10T10:14:38+00:00",
  "symbol": "BTCUSDT",
  "direction": "long",
  "setup": "vwap",
  "entry_price": 80725.9,
  "exit_price": 80794.7,
  "stop_loss": 80700.41,
  "take_profit_1": 80784.64,
  "position_size": 0.002,
  "exit_reason": "tp_cross",
  "decision_grade": "A",
  "entry_quality": "optimal",
  "exit_quality": "tp_appropriate",
  "risk_management": "correct",
  "rationale": "≤ 240 chars — why this grade given signal_logic + outcome",
  "alternative_action": "≤ 160 chars — what we'd do differently next time, or 'none'"
}
```

Use `unknown` honestly when the diag bundle didn't carry enough
context to grade a dimension (e.g., truncated `signal_logic`,
missing exit price). **Do not fabricate** a grade where the data
doesn't support one — `unknown` + a short rationale is the
contract.

When the 6-hour window contains many trades, prefer per-trade
grades for closes + at least one representative grade per
rejection cluster. If there are >20 trades, batch the lowest-grade
cohort first (Cs, Ds, Fs) so the operator and the training
pipeline see the negative signal up front; aggregate the As / Bs
in a single summary entry that lists the trade ids covered.

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
- `state_consistency` — for each account in `config/accounts.yaml`,
  compare YAML `mode` against the runtime `live` field in
  `runtime_logs/runtime_status.json` (and, when accessible, the
  Telegram process's `accounts_status` reply). Drift between the
  YAML declaration and any runtime view → `concern`. The two
  most common causes: (a) the runtime override dict has been
  mutated by a circuit-breaker auto-trip (look for the matching
  alert in `alert_delivery`); (b) the dashboard's read-projection
  defaulted dry because of a stale resolver bug — recheck once the
  runtime_status.py default-flip fix has landed.
- `alert_delivery` — verify the in-process `AlertsQueue`
  (`src/units/dashboards/alerts.py`) is being drained. Check (i)
  the diag tail / journalctl for any `alert_drainer:` log lines in
  the 6h window, (ii) whether known-trip events (auto-pauses,
  capacity-zero clusters, exception storms) have a corresponding
  Telegram message logged in `bot.log`. If known critical events
  fired but no drain log accompanied them → `concern` with note
  "alerts queued, drainer silent — operator unnotified".
- `strategy_silence` — every strategy enabled in
  `config/strategies.yaml` should produce per-tick audit events
  in `runtime_logs/signal_audit.jsonl` (`turtle_soup_eval`,
  `vwap_eval`, etc.). Count by `event` over the 6h window. Any
  enabled strategy with **zero `*_eval` events** for more than
  one hour during an active session → `concern` with the
  strategy name and the silence duration. This is the dimension
  the 2026-05-10 incident exposed: VWAP went silent for 8h, but
  because VWAP wasn't writing per-tick audit events at all the
  silence was indistinguishable from "no signal." Fixed in PR
  that adds `vwap_eval`; if a future strategy is added without
  an audit emitter, this check is what catches it.

Status grades:
- `ok`       — no anomaly worth flagging.
- `watch`    — anomaly present but bounded; no immediate action.
- `concern`  — operator should look. ⇒ `operator_attention_required: true`.

`overall_assessment` mapping:
- `healthy`     — every finding is `ok`.
- `caution`     — at least one `watch`, no `concern`.
- `investigate` — any `concern`. ⇒ `operator_attention_required: true`.

## Output

**Single-request mode** (the user passed an explicit `REQ-…` or run
id): emit a single JSON object — no prose, no markdown fences, no
leading or trailing whitespace beyond the JSON. Conform to
`comms/schema/health_review_response.template.json` exactly. Populate
every field. Use the request's `request_id` verbatim. Set
`reviewed_at` to the current UTC ISO-8601 timestamp. Set `reviewer`
to `claude`.

**Multi-request mode** (default — N pending requests since last
review): emit a single JSON array containing N response objects, one
per pending request, sorted by request `created_at` ascending. Each
element conforms to the same schema as single-request mode. The
shared fields (`findings`, `anomalies`, `trade_decision_grades`,
`recommended_action`, `operator_attention_required`) are computed
once from the single diag-relay pull and replicated verbatim across
every entry; only `request_id` and `reviewed_at` differ per element
(every element uses the same `reviewed_at` — they were all reviewed
in the same pass).

When N=1, emit the single object form (not a one-element array) so
the response stays byte-identical to the legacy shape.

Schema reminder:

```json
{
  "request_id": "REQ-YYYYMMDD-HHMMSS-<slug>",
  "reviewed_at": "YYYY-MM-DDTHH:MM:SS+00:00",
  "reviewer": "claude",
  "overall_assessment": "healthy | caution | investigate",
  "findings": {
    "heartbeat":          {"status": "ok | watch | concern", "note": "..."},
    "ticks":              {"status": "ok | watch | concern", "note": "..."},
    "signals":            {"status": "ok | watch | concern", "note": "..."},
    "orders":             {"status": "ok | watch | concern", "note": "..."},
    "trades":             {"status": "ok | watch | concern", "note": "..."},
    "monitoring":         {"status": "ok | watch | concern", "note": "..."},
    "sizing":             {"status": "ok | watch | concern", "note": "..."},
    "api_errors":         {"status": "ok | watch | concern", "note": "..."},
    "state_consistency":  {"status": "ok | watch | concern", "note": "..."},
    "alert_delivery":     {"status": "ok | watch | concern", "note": "..."},
    "strategy_silence":   {"status": "ok | watch | concern", "note": "..."}
  },
  "anomalies": ["...free-form list..."],
  "trade_decision_grades": [
    {
      "trade_id": 0,
      "timestamp": "YYYY-MM-DDTHH:MM:SS+00:00",
      "symbol": "BTCUSDT",
      "direction": "long | short",
      "setup": "vwap | turtle_soup | ...",
      "entry_price": 0.0,
      "exit_price": 0.0,
      "stop_loss": 0.0,
      "take_profit_1": 0.0,
      "position_size": 0.0,
      "exit_reason": "tp_cross | sl_hit | trail | manual | rejected | ...",
      "decision_grade": "A | B | C | D | F",
      "entry_quality": "optimal | acceptable | late | early | should_skip | unknown",
      "exit_quality": "optimal | tp_appropriate | sl_appropriate | premature_exit | held_too_long | unknown",
      "risk_management": "correct | oversize | undersize | sl_too_tight | sl_too_wide | unknown",
      "rationale": "≤ 240 chars",
      "alternative_action": "≤ 160 chars, or 'none'"
    }
  ],
  "recommended_action": "what to do next, or 'none'",
  "operator_attention_required": false
}
```

`trade_decision_grades` is REQUIRED. Pass an empty array (`[]`) only
when the 6-hour window genuinely contained no closed or rejected
trades.

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
