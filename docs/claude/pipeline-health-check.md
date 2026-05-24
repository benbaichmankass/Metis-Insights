# Pipeline Health Check

> **Status:** Contract document. Adopted 2026-05-12 as part of the
> Prime Directive follow-on (see
> [`CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) § Prime
> Directive and [`ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
> § Mode Mutation Contract).
> **Implementation:** safeguards PR (follow-on to PR #978). Code-level
> wiring is pending; this doc fixes the contract so the PR lands
> against a clear spec.

## Purpose

The Prime Directive requires that:

- The trader runs 24/7. The system never switches itself off.
- Transient issues route through the RiskManager as per-trade
  rejections, with a per-trade Telegram ping carrying the reason
  **verbatim** (the precise `reject(reason=…)` text + the raw
  exchange error, if any).
- The operator gets fast, clear feedback per rejection so they can
  intervene quickly — not aggregate "account paused" pings.

The pipeline-health-check workflow is the automated diagnostic layer
that fires on every rejection. Its job is to answer one question for
the operator: *"is this rejection a single transient, or is the
pipeline showing a pattern I need to act on?"* It pulls live diag,
runs sanity checks, and surfaces a verdict to Telegram and the
dashboard within seconds.

## Contract

### Trigger

The trader process emits a rejection event whenever
`RiskManager.approve()` (or any downstream gate) returns a non-approve
verdict for an order. Each rejection event is:

1. **Logged to Telegram** as its own per-trade ping with this body
   shape (verbatim, no aggregation):

   ```
   [reject] account=<id> symbol=<sym> side=<buy|sell> qty=<q>
   reason: <verbatim reason text>
   exchange_error: <verbatim exchange error if any, else ->
   strategy: <strategy name + setup>
   trader: <process pid + git_sha>
   tick: <utc iso>
   ```

2. **Written to disk** under
   `runtime_logs/rejections/<utc-ts>-<rejection-id>.json` with the
   full structured event (signal metadata, full exception trace if
   applicable, RiskManager input context).

3. **Surfaced to the pipeline-health-check workflow** by the
   `ict-claude-bridge.service` process. The bridge tails the
   rejections directory, debounces clusters (see § Debounce below),
   and opens a labelled issue (`pipeline-health-check`) with the
   rejection metadata in the body. That issue triggers the workflow.

### Workflow steps

`.github/workflows/pipeline-health-check.yml` is triggered on
`issues.opened` filtered to label `pipeline-health-check`. Steps:

1. **Pre-check** — fast: extract the rejection metadata from the
   issue body (parsed through env, never inline `${{ }}` per the
   system-actions convention).
2. **Diag pulls** — SSH to the live VM and fetch via
   `/api/diag/*`:
   - `/api/diag/status` (heartbeat + accounts + last_tick_utc),
   - `/api/diag/audit?limit=200` (recent pipeline events),
   - `/api/diag/journal?table=order_packages&limit=20` (recent
     orders),
   - `/api/diag/journal?table=trades&limit=20` (recent trades).
3. **Sanity script** — `scripts/ops/pipeline_health_check.py`
   inspects the pulled data + the rejection metadata and emits a
   structured verdict:

   ```json
   {
     "rejection_id": "...",
     "verdict": "transient|cluster|wedged",
     "findings": {
       "heartbeat_age_s": 12,
       "ticking": true,
       "recent_rejections_window_min": 5,
       "recent_rejections_count": 3,
       "same_account_run_length": 2,
       "same_reason_run_length": 2,
       "exchange_error_class": "170131|null",
       "account_modes_match_yaml": true
     },
     "recommended_action": "<one line; 'none' is valid>",
     "reviewed_at": "2026-05-12T06:30:00Z"
   }
   ```

   Verdict semantics:
   - **`transient`** — single rejection, no concerning pattern.
     Bot stays trading; this is the expected response shape for
     non-clustered failures.
   - **`cluster`** — 3+ rejections in a rolling 5-minute window on
     the same account or same reason class. The pipeline is
     producing repeated failures the operator should look at, but
     the trader is still ticking.
   - **`wedged`** — heartbeat stale (> cadence × 3) or the
     `account_modes_match_yaml` check fails (a runtime mode drift
     vs YAML, i.e. the same class as the 2026-05-12 silent-flip
     incident). Always `operator_attention_required: true`.

4. **Telegram update** — the workflow's final step queues a ping
   via the existing `scripts/ops/notify_run.sh` + `send_ping.py`
   infrastructure. Priority:
   - `transient` → `low` (informational).
   - `cluster` → `high`.
   - `wedged` → `urgent`.

   Message body shape:
   ```
   [pipeline-health] verdict=<transient|cluster|wedged>
   rejection: <rejection-id> account=<id> reason=<verbatim>
   findings: ticking=<bool> rejections-in-5min=<n> same-account-run=<n>
   action: <recommended action or 'none'>
   ```

5. **Web-app update** — the workflow writes
   `runtime_logs/pipeline_health/latest.json` (and an
   `archive/<ts>.json` companion) on the VM. A new Tier-1 endpoint
   `GET /api/bot/pipeline-health/latest` exposes the latest
   verdict. The dashboard renders this as a banner on the live
   overview — the operator sees the verdict and recent-rejection
   pattern without having to open Telegram.

6. **Issue close-out** — the workflow comments back on the
   triggering issue with the full verdict bundle and closes it
   (`completed`).

### Debounce

The claude-bridge debounces rejection → issue opening so that a
burst doesn't flood the workflow lane:

- For verdict-class `transient`-likely events (heuristic: same
  account hasn't rejected in the last 60s), open one issue.
- For likely-cluster events (same account has ≥ 2 rejections in the
  last 60s), open one issue and **suppress** subsequent issues for
  the same account for the next 90s. The first issue's workflow
  will pull the full audit window and report the cluster verdict;
  duplicates would just re-report the same thing.
- The per-trade Telegram pings are **not** debounced — every
  rejection still emits its own Telegram. The debounce only
  applies to the workflow trigger so we don't fire the GitHub
  Action 50 times in a cluster.

### Concurrency

The workflow uses `concurrency: group: pipeline-health-check,
cancel-in-progress: true` so a fresh trigger preempts a stale run.
This matches the diag-relay's queue-stage cancellation pattern
(FU-20260511-005 / PR #953). The cancelled() handler posts a
"preempted by newer event" comment on the issue so the operator
knows what happened.

## Existing infrastructure reused

- **Diag pulls**: `/api/diag/*` already supports the needed reads
  (status, audit, journal). No new endpoints required.
- **Telegram queue**: `scripts/ops/notify_run.sh` +
  `scripts/send_ping.py` already drain through
  `ict-claude-bridge.service` to `@claude_ict_comms_bot`.
- **Issue-triggered workflows**: `vm-diag-snapshot.yml`,
  `vm-web-api-recover.yml`, and `system-actions.yml` all use the
  pattern. This workflow follows the same shape (label-filtered
  `issues.opened`, body-through-env, structured artifact).
- **Audit log**: `runtime_logs/signal_audit.jsonl` is already the
  per-tick event log; the rejection event extends it (new
  `event=trade_rejected` row with the verbatim reason). The new
  `runtime_logs/rejections/` directory is the structured-event
  side-channel for the workflow trigger; the audit log remains the
  full record.

## New surface

- `.github/workflows/pipeline-health-check.yml` (new workflow)
- `scripts/ops/pipeline_health_check.py` (new sanity-check script)
- `runtime_logs/rejections/` (new directory; bridge tails it)
- `runtime_logs/pipeline_health/` (new directory; workflow writes)
- `GET /api/bot/pipeline-health/latest` (new Tier-1 endpoint)
- Dashboard component on the live overview (new render path)

## Failure modes

- **Workflow can't reach the VM** — marks the issue with verdict
  `unknown_diag_unreachable`, queues a `high` Telegram ping. The
  operator still got the per-trade Telegram from the trader
  directly, so the rejection itself is never silent.
- **Bridge fails to open the issue** — per-trade Telegram still
  fires from the trader; the workflow never runs. The next
  bridge restart picks up unprocessed rejections from
  `runtime_logs/rejections/`. No data loss.
- **Workflow lane wedges** — cancel-in-progress + the
  cancelled() handler ensure forward progress; stuck runs are
  preempted, not blocking.
- **Verdict script raises** — the workflow catches and falls back
  to verdict `unknown_script_error`, includes the traceback in the
  Telegram ping and the issue comment.

## What this is NOT

- Not a place to switch the trader off. The verdict can be
  `wedged` and `operator_attention_required: true`, but the
  workflow does not flip any account's mode. The Prime Directive's
  "one switch" rule stands: only the operator can dispatch
  `set-account-mode`. The pipeline-health workflow's job is to
  give the operator information; the decision stays with the
  operator.
- Not a replacement for `/health-review`. The health-review skill
  is the human-driven layer-2 review of the live bot's runtime
  state at a moment in time (snapshot + diag pulls). The
  pipeline-health workflow is the automated layer that fires per
  rejection. Both feed the operator different signals.
- Not a backpressure mechanism. The trader keeps trading; if the
  rejection cluster indicates a wedged state, the operator decides
  whether to flip mode via `set-account-mode`, adjust risk caps via
  YAML PR, or wait it out.

## Cross-references

- [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md)
  § Prime Directive — the operator-facing rules that drive this
  contract.
- [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
  § Mode Mutation Contract — the system-design counterpart.
- [`docs/claude/system-actions.md`](system-actions.md) — the
  established issue-triggered workflow pattern this one inherits.
- [`docs/claude/diag-relay.md`](diag-relay.md) — the diag-pull
  pattern reused here.
- [`.claude/skills/health-review/SKILL.md`](../../.claude/skills/health-review/SKILL.md)
  — the human-driven layer-2 review that this automated layer
  complements (not replaces).
