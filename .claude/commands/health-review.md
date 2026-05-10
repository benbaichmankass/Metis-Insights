---
description: Run the layer-2 health-check review on the latest snapshot landed on main.
argument-hint: "[REQ-id | run_id]   (optional; defaults to latest)"
---

# /health-review — manual layer-2 review

Drop-in replacement for the autonomous Claude routine described in
[`docs/runbooks/health-check.md`](../../docs/runbooks/health-check.md)
§ "How a Claude review actually happens". Use this when you want to
reply to a `comms/requests/REQ-*.json` health-review request without
copy-pasting the prompt out of Telegram or waiting for the routine
trigger to fire.

## What you'll do

1. **Resolve which run to review.** If `$ARGUMENTS` is set:
   - Looks like `REQ-YYYYMMDD-HHMMSS-<digits>` → that's the request id.
     The corresponding file is `comms/requests/$ARGUMENTS.json`.
   - All-digits (a workflow run id) → find the request by globbing
     `comms/requests/REQ-*-$ARGUMENTS.json` (the suffix matches the
     GitHub `run_id`).
   If `$ARGUMENTS` is empty: pick the most recent request — the file
   under `comms/requests/REQ-*.json` whose embedded `created_at` is
   newest, OR the one that points at the same artifacts as
   `artifacts/health/latest.json` on the current HEAD.

2. **Read the inputs.** All paths are relative to the repo root.
   - `artifacts/health/latest.json` — the layer-1 machine verdict
     (status / summary / 11 `checks` / optional `error` block).
   - `artifacts/health/health_snapshot.txt` — the raw VM snapshot.
     Sectioned with `=== NAME ===` headers (META, PROCESSES,
     HEARTBEAT, TICKS, SIGNALS, ORDERS, TRADES, POSITIONS,
     MONITORING, API, ERRORS, VM, END).
   - `artifacts/health/pipeline_test.json` (when present) — active
     dry-run smoke result.
   - The resolved `comms/requests/REQ-*.json` — request topic, run
     metadata, machine verdict embedded.
   - `.claude/health_check_prompt.md` — severity rubric for layer 1;
     useful for cross-checking the LLM's grade against the snapshot.
   - `comms/schema/health_review_response.template.json` — the
     output shape you must produce.

3. **Decide the verdict.** Cross-check the layer-1 output against the
   raw snapshot. If layer 1 fell back to `UNKNOWN` (Anthropic credit
   issue, network, etc.), the snapshot is the source of truth — grade
   it yourself using the rubric in `.claude/health_check_prompt.md`.

   Map your findings to the layer-2 dimensions
   (note these differ from layer 1):
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
   - `concern`  — operator should look. Always ⇒ `operator_attention_required: true`.

4. **Emit the response.** Output a single JSON object — no prose, no
   markdown fences — that conforms exactly to
   `comms/schema/health_review_response.template.json`. Populate every
   field. Use the request's `request_id` verbatim. Set `reviewed_at`
   to the current UTC ISO-8601 timestamp. Set `reviewer` to `claude`.

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

   Map `overall_assessment`:
   - `healthy`     — every finding is `ok`.
   - `caution`     — at least one `watch`, no `concern`.
   - `investigate` — any `concern`. ⇒ `operator_attention_required: true`.

5. **Notes guidance.** Each `note` ≤ 120 chars. Reference specifics
   from the snapshot (filenames, ages, counts, error classes) so the
   operator can verify quickly. Empty sections in the snapshot grade
   `watch` with a "no recent activity" note rather than `ok`.

6. **What NOT to do.**
   - Don't write any files. The response is plain-text JSON in the
     conversation. The operator pastes it into the comms request's
     answer per `comms/schema/response.schema.json`.
   - Don't try to call the live trader, modify `accounts.yaml`, or
     touch anything under `src/`. Reviews are read-only.
   - Don't fabricate data — if a section is absent from the snapshot,
     say so in the note rather than guessing.

7. **If the inputs are missing.** If `artifacts/health/latest.json`
   isn't present on the current HEAD (e.g. the most recent review
   PR hasn't been merged yet), say so in plain text and stop —
   don't synthesize a review without evidence.

The `$ARGUMENTS` literal is filled in by the slash-command harness
before execution. Begin.
