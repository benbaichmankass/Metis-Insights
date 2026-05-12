# Health-check runbook

> **Adopted 2026-05-12 (noise-cleanup).** Replaces the prior two-layer
> PR-mediated routine. See "What changed" below for the migration
> details and which files were removed.

## What this is

A single GitHub Actions workflow — [`health-snapshot.yml`](../../.github/workflows/health-snapshot.yml)
— that periodically grabs a runtime-state snapshot from the live VM
and uploads it as an Action artifact. The operator downloads the
artifact when they want a sanity-check, then runs `/health-review`
in a Claude Code session with the pasted contents.

```
cron  ──►  health-snapshot.yml  ──►  SSH to VM
                                    └─ scripts/collect_health_snapshot.sh
                                    └─ scripts/run_pipeline_health_test.sh
                                    └─ actions/upload-artifact  (artifacts/health/**)
                                                  │
                                                  ▼
                                       Actions UI download (operator)
                                                  │
                                                  ▼
                                       /health-review (Claude session)
```

No Layer-1 LLM call. No `comms/requests/REQ-*.json` artifact. No PR
auto-merge. No Telegram ping. The snapshot is the deliverable; the
operator decides when to look at it.

## Cadence

`cron: '0 2 * * *'` — once daily at 02:00 UTC. The prior schedule (4x
daily) produced unread snapshots; matching the cron to the operator's
review cadence keeps Actions-minute usage low and noise zero.

The workflow also accepts `workflow_dispatch` for manual ad-hoc runs
and `issues.opened` with the `health-snapshot-trigger` label for
sandbox-session-driven invocations.

## Files produced

Each run uploads `health-snapshot-<run_id>` containing:

| File | Source | Notes |
|---|---|---|
| `health_snapshot.txt` | `scripts/collect_health_snapshot.sh` on the VM | Sectioned with `=== NAME ===` headers (META, PROCESSES, HEARTBEAT, TICKS, SIGNALS, ORDERS, TRADES, POSITIONS, MONITORING, API, ERRORS, STORAGE, DB, AUDIT_LOG, VM, END). Source of truth for layer-2 review. |
| `pipeline_test.json` | `scripts/run_pipeline_health_test.sh` on the VM | Active dry-run smoke (`safe_place_order(client=None)`). Status field is `ok \| warn \| fail`. `warn` with note "plumbing-on-rejection path exercised" is the expected outcome when no exchange client is wired in. |

Retention: 30 days. Older snapshots fall out of the Actions storage
automatically.

## Running a manual review

1. Open the [Actions tab](https://github.com/benbaichmankass/ict-trading-bot/actions/workflows/health-snapshot.yml).
2. Pick the most recent successful run (or dispatch a fresh one with `workflow_dispatch`).
3. Scroll to **Artifacts** at the bottom of the run page → download `health-snapshot-<run_id>.zip`.
4. Extract. Open a Claude Code session on this repo.
5. Paste the contents of `health_snapshot.txt` (and optionally `pipeline_test.json`) into the chat. Invoke `/health-review`.

The skill at [`.claude/skills/health-review/SKILL.md`](../../.claude/skills/health-review/SKILL.md) carries the full review rubric (heartbeat / ticks / signals / orders / trades / monitoring / sizing / api_errors / state_consistency / alert_delivery / strategy_silence / db_integrity / audit_log_freshness) and the per-trade decision-grading rubric.

## Triggering from a sandbox session

A PM-side Claude Code on the web session can fire the snapshot via a labelled issue:

```
mcp__github__issue_write(
  method='create',
  title='[health-snapshot] manual run',
  labels=['health-snapshot-trigger'],
  body='manual snapshot for layer-2 review'
)
```

The workflow runs, comments back the artifact URL on the issue, and closes it. The session then downloads the artifact via the comment-provided link.

## Required secrets

| Secret | Purpose |
|---|---|
| `VM_SSH_KEY` | SSH private key for `ubuntu@158.178.210.252`. Reused from the other VM-touching workflows (operator-actions, vm-diag-snapshot, vm-web-api-recover). |

Optional repo variables `VM_SSH_HOST`, `VM_SSH_USER` can override the
defaults; the workflow uses the canonical Oracle VM by default.

## What changed (2026-05-12 cleanup)

Before this cleanup, the health-check pipeline was a four-stage
two-layer system that produced more noise than signal:

1. `health-snapshot-pr.yml` (the predecessor) — cron every 6h.
2. → `scripts/run_health_check.py` called Anthropic for a Layer-1
   machine verdict. Operator disabled this 2026-05-10 via `--skip-llm`,
   so every run synthesised an `UNKNOWN` stub with WARNING status.
3. → `scripts/write_health_review_request.py` minted a
   `comms/requests/REQ-*.json` that the Telegram bot would deliver
   asking for a Layer-2 manual review. Nobody answered them, so they
   expired, firing a second Telegram ping per request on expiry.
4. → A `peter-evans/create-pull-request` PR was opened with the
   artifacts + comms request, auto-merged, and `health-review-trigger.yml`
   fired on merge to ping the operator one more time.

Net effect: every 6h the operator got at minimum two Telegram messages
(new request + queued auto-merge) plus the trickle of expiry pings as
old unanswered requests aged out. No actionable signal.

The 2026-05-12 cleanup deleted:

- `scripts/run_health_check.py` (Layer-1 LLM call + UNKNOWN stub builder)
- `scripts/write_health_review_request.py` (comms-request emitter)
- `.claude/health_check_prompt.md` (Layer-1 LLM severity rubric)
- `.github/workflows/health-review-trigger.yml` (post-merge ping)
- `.github/workflows/health-snapshot-pr.yml` (replaced with the leaner `health-snapshot.yml`)
- 8 stale `comms/requests/REQ-*.json` files in the backlog.

And added a gate in `src/bot/comms_handler.py::_deliver` /
`_alert_expired` that silently drains any in-flight backlog with topic
matching `"Health review*"` — no Telegram fire, but the normal
EXPIRED + archive lifecycle still runs so the audit log is preserved.

## Re-enabling Layer-1 (if ever needed)

Don't, at least not with the same pattern. If a future operator wants
automated grading, the right answer is a different design — the
deleted Layer-1 was firing the `--skip-llm` fallback unconditionally
and producing zero useful output. Start by:

1. Decide what an automated verdict should look like (deterministic
   rules over the snapshot? smaller LLM? scoring against thresholds?).
2. Write the new grader.
3. Wire it into `health-snapshot.yml` as a step that fails the
   workflow if the verdict is CRITICAL — the workflow failure is the
   signal; no separate comms request needed.

Don't bring back the comms-request + Telegram fanout.
