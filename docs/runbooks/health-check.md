# Runbook — System Health Check (two-layer)

The ICT trading bot has a two-layer health-check pipeline driven by the
GitHub Action [`.github/workflows/health-check.yml`](../../.github/workflows/health-check.yml).

| Layer | What it does | Where it lives | Cadence |
|-------|--------------|----------------|---------|
| 1 — Automated machine check | Bash collector → Claude Haiku 4.5 → JSON verdict | [`scripts/collect_health_snapshot.sh`](../../scripts/collect_health_snapshot.sh), [`scripts/run_health_check.py`](../../scripts/run_health_check.py) | every 6h (schedule) + manual |
| 2 — Mandatory Claude review | Schema-valid `comms/requests/REQ-*.json` asking Claude to sanity-review the same run | [`scripts/write_health_review_request.py`](../../scripts/write_health_review_request.py) | every layer-1 run, no exceptions |

The two layers are independent. Layer 1 is a fast machine triage that
fires Telegram alerts on `WARNING`/`CRITICAL`. Layer 2 produces a
structured ask for a human/Claude reviewer to look at the same logs and
file findings — it runs **on every execution**, including healthy ones,
because a clean machine check is not a substitute for a sanity review.

## What the workflow does, step by step

1. SSH to the production VM with the `VM_SSH_PRIVATE_KEY` secret.
2. Runs [`scripts/collect_health_snapshot.sh`](../../scripts/collect_health_snapshot.sh)
   over SSH and captures stdout to `health_snapshot.txt`. The collector
   is read-only — it does not touch runtime state, strategy logic, or
   any open positions.
3. Layer 1 — calls [`scripts/run_health_check.py`](../../scripts/run_health_check.py)
   which sends the snapshot to Claude Haiku 4.5 with the system prompt
   in [`.claude/health_check_prompt.md`](../../.claude/health_check_prompt.md),
   parses a strict-JSON verdict, and writes:
   - `runtime_logs/health_checks/health_check_<UTC-ISO>.json` (per-run)
   - `runtime_logs/health_checks/latest.json` (overwrites each run)
   - On `WARNING`/`CRITICAL`, a Telegram alert via the bot's existing
     `src.runtime.notify.send_telegram_direct` helper.
4. Layer 2 — calls [`scripts/write_health_review_request.py`](../../scripts/write_health_review_request.py)
   which writes a schema-valid request to
   `comms/requests/REQ-YYYYMMDD-HHMMSS-<run-slug>.json`. The slug is
   derived from the GitHub `run_id`, so re-running the same workflow
   run is a no-op (idempotent — see below).
5. Telegram ping ("Health review pending — REQ-…") via
   [`scripts/notify_session.py`](../../scripts/notify_session.py)
   so the operator (and any subscribed Claude session) knows there is a
   fresh review to action. This fires on **every** run, not just bad
   ones.
6. On schedule runs, the new comms request is committed back to the
   default branch so the VM picks it up on its next `ict-git-sync`
   pull. `workflow_dispatch` runs are dry-run by default — toggle the
   `skip_commit_back: false` input if you want a manual trigger to
   commit-back.
7. The full snapshot, layer-1 report, and `latest.json` are uploaded
   as a GitHub Actions artifact (`health-check-<run-id>`, retention 30
   days).

## How a Claude review actually happens

The comms request is delivered through the **existing** comms channel
(see [`comms/README.md`](../../comms/README.md) and
[`docs/claude/comms-architecture.md`](../claude/comms-architecture.md)).
No new infrastructure was added.

- The Telegram bot picks up `comms/requests/REQ-*.json` on its next
  poll, delivers a notification, and flips status to `sent`.
- The reviewer (Claude or operator) reads the `context` field, which
  contains the inlined machine verdict, run id, branch, commit, and
  pointers to the Actions artifacts.
- They reply with a JSON blob matching
  [`comms/schema/health_review_response.template.json`](../../comms/schema/health_review_response.template.json).
- The bot files the answer under `.response.answers[0].free_text` and
  flips status to `answered` (then `acknowledged` once the next Claude
  session sees it).

## Pending vs completed reviews — quick check

```bash
# pending or in-flight (not yet answered):
ls comms/requests/REQ-*.json | xargs -I{} jq -r 'select(.status != "answered" and .status != "acknowledged") | "\(.status)  \(.request_id)  \(.topic)"' {}

# answered but not yet acknowledged:
ls comms/requests/REQ-*.json | xargs -I{} jq -r 'select(.status == "answered") | "\(.request_id)  \(.response.answers[0].received_at)"' {}

# any health-review request specifically:
ls comms/requests/REQ-*.json | xargs grep -l '"task": "claude_health_review:'
```

## Idempotency / dedupe

The `request_id` is `REQ-<YYYYMMDD>-<HHMMSS>-<slug>` where the slug is
the last 12 chars of the GitHub `run_id`. A retry of the same run hits
the same filename and the writer skips it (`already exists`). A fresh
schedule run gets a fresh `run_id`, so there's a 1:1 mapping between
workflow runs and review requests — no thundering-herd if the workflow
is rerun.

## Disabling / pausing

Two options:

1. **Pause Telegram noise but keep the audit trail** — flip the
   `Notify operator that a Claude review is pending` step to `if: false`
   in the workflow. Layer 1 alerts on WARNING/CRITICAL still fire.
2. **Stop the workflow entirely** — disable the workflow from the
   GitHub Actions UI (`Actions → System Health Check → Disable`). The
   schedule pauses; manual `workflow_dispatch` still works.

Do **not** delete the `comms/requests/REQ-*.json` files manually; the
state machine in `src/comms/state.py` will reclaim them via the
`expired`/`cancelled` lifecycle. Stale files are also archived
automatically per `comms/README.md`.

## Required GitHub secrets

| Name | Purpose |
|---|---|
| `VM_SSH_PRIVATE_KEY` | SSH key for `ubuntu@158.178.210.252` (the bot VM) |
| `ANTHROPIC_API_KEY`  | Claude Haiku 4.5 calls in layer 1 |
| `TELEGRAM_BOT_TOKEN` | Operator alerts (layer 1 + layer 2 ping) |
| `TELEGRAM_CHAT_ID`   | Same |

The Telegram secrets are optional for layer 1 — the analyzer skips the
alert silently if either is missing. Layer 2's "review pending" ping
likewise tolerates a missing token.

## Safety scope

Per the design contract:

- The collector is **read-only**. It does not write to any path under
  `src/runtime/`, `src/units/`, or any open-positions store.
- The trader does not import from `comms/` (see the safety note in
  [`comms/README.md`](../../comms/README.md)) — so a malformed review
  request cannot influence live strategy behavior.
- The Action runs out-of-band on GitHub-hosted runners; the only
  side-effect on the VM is reading log files over SSH.
