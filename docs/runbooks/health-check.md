# Runbook — System Health Check (two-workflow, PR-mediated)

The ICT trading bot's health-check pipeline runs as a **pair of GitHub
Actions** that gate Claude's manual review behind a merged, labeled PR:

| Workflow | What it does | When |
|---|---|---|
| 1. [`health-snapshot-pr.yml`](../../.github/workflows/health-snapshot-pr.yml) | Collects a VM snapshot, runs the layer-1 machine check, emits the layer-2 review request, and opens/updates a PR labeled `health-check-review`. | Cron `0 */6 * * *` + `workflow_dispatch` |
| 2. [`health-review-trigger.yml`](../../.github/workflows/health-review-trigger.yml) | Fires on `pull_request.closed`, gated by `merged == true` **and** the `health-check-review` label. Confirms the merge, lists the review-request files that just landed on `main`, and pings the Claude review handoff. | On every PR merge into `main` (filtered by label) |

The layered design is unchanged — layer 1 is automated machine triage,
layer 2 is a mandatory Claude review **on every run**, including healthy
ones. What changed is the *delivery* of layer 2: instead of committing
the review request straight to `main`, it now goes through an operator
review/merge step.

## Why merged-PR + label is the trigger

GitHub does not emit a dedicated "merged" event. The standard pattern
is `pull_request.closed` filtered by `github.event.pull_request.merged
== true`. The label `health-check-review` is the operational filter:
any other PR that closes against `main` is ignored, even if the title
or the changed paths look similar. The label is applied automatically
by `peter-evans/create-pull-request` in workflow 1, so the operator
never has to remember to set it.

This matters because:

- The operator gets a **single auditable artifact** — the PR — to glance
  at before allowing layer 2 to land. Closing without merging is a
  legitimate "skip this run" outcome.
- The trader is never affected by churn on the snapshot branch; only
  the merge writes to `main`, so the VM's git-sync timer only sees
  approved review requests.
- Re-running the workflow is **idempotent** (see below) and never
  duplicates a PR.

## What the label means

`health-check-review` is the operational filter on workflow 2. **Only**
workflow 1 should apply it. The handoff in workflow 2 is gated on it,
so adding the label by hand to an unrelated PR would falsely trigger
the handoff. The artifact lives on a branch (`auto/health-check-review`)
and only reaches `main` when an operator merges the PR — there is no
dry-run knob in the new model because closing the PR without merging
is already the dry-run.

The `automated` label is informational only — it is not part of any
filter.

## Manually forcing a run

```bash
# Trigger workflow 1 right now (from the GitHub UI):
#   Actions → Health Snapshot PR → Run workflow
# Or via gh CLI:
gh workflow run "Health Snapshot PR"

# Open the resulting PR:
gh pr list --label health-check-review --state open
```

Merging the PR fires workflow 2. To **skip** a run after the PR is
open, just close it without merging — nothing leaves the snapshot
branch.

## Idempotency / dedupe

Workflow 1 always pushes to a single fixed branch: `auto/health-check-review`.
`peter-evans/create-pull-request` updates the open PR if one exists,
opens a new PR otherwise.

Within the artifact set:

- `artifacts/health/health_snapshot.txt` and `artifacts/health/latest.json`
  are overwritten on every run — the PR always shows the latest.
- `artifacts/health/health_check_<UTC-ISO>.json` is per-run and piles
  up in the PR; useful for forensic comparison if a PR sits open over
  multiple cycles.
- `comms/requests/REQ-*.json` filenames are keyed by the GitHub `run_id`
  (12-char numeric slug), so each run gets a unique file. A re-run of
  the **same** workflow run is a no-op (the writer skips with `already
  exists`). A new scheduled run gets a fresh `run_id`.

If an open PR sits unmerged across several scheduled runs, every run's
`REQ-*.json` accumulates in the PR. Merging once delivers all of them
to `main` atomically; workflow 2 lists which ones just landed.

If you want a fresh PR instead of updating the existing one, close the
open one (without merging) and let the next scheduled run reopen it.

## How a Claude review actually happens

Unchanged from the prior design — the merged `comms/requests/REQ-*.json`
flows through the **existing** comms channel
(see [`comms/README.md`](../../comms/README.md) and
[`docs/claude/comms-architecture.md`](../claude/comms-architecture.md)):

1. The PR is merged → `comms/requests/REQ-*.json` is on `main`.
2. The VM's `ict-git-sync` timer pulls the new request.
3. The Telegram bot picks it up on its next poll, delivers the
   notification, and flips status to `sent`.
4. The reviewer (Claude or operator) reads the `context` field, which
   already contains the inlined machine verdict, run id, branch,
   commit, and pointers to the Actions artifacts.
5. They reply with a JSON blob matching
   [`comms/schema/health_review_response.template.json`](../../comms/schema/health_review_response.template.json).
6. The bot files the answer under `.response.answers[0].free_text` and
   flips status to `answered`.

## Pending vs completed reviews — quick check

```bash
# pending review PRs (not yet merged):
gh pr list --label health-check-review --state open

# review requests on main that haven't been answered yet:
ls comms/requests/REQ-*.json | xargs -I{} jq -r 'select(.status != "answered" and .status != "acknowledged") | "\(.status)  \(.request_id)  \(.topic)"' {}

# answered but not yet acknowledged:
ls comms/requests/REQ-*.json | xargs -I{} jq -r 'select(.status == "answered") | "\(.request_id)  \(.response.answers[0].received_at)"' {}
```

## Required GitHub repository setting

`peter-evans/create-pull-request` cannot open PRs unless
**Settings → Actions → General → Workflow permissions** has:

- [x] **Allow GitHub Actions to create and approve pull requests**

Without this setting, workflow 1 will create the branch and push the
commit, but the PR-creation step will fail with a 403. This is the
only repo-level setting required by the new design.

## Required GitHub secrets

These are the actual secret names used by the action; they match the
repo's existing secret store:

| Name | Purpose |
|---|---|
| `VM_SSH_KEY`                | OpenSSH private key for `ubuntu@158.178.210.252` (the bot VM) |
| `ANTHROPIC_API_KEY`         | Claude Haiku 4.5 calls in layer 1 |
| `CLAUDE_TELEGRAM_BOT_TOKEN` | **Claude bot token** (separate from the trader's bot) — layer-1 alerts, PR-open ping, merge handoff ping |
| `TELEGRAM_CHAT_ID`          | Shared chat id (same chat receives trader-bot and Claude-bot messages — only the bot token differs) |

The action's Telegram pings are intentionally routed via the Claude
bot token so review-pipeline noise comes from a separate sender than
the trader's live alerts. The `TELEGRAM_CHAT_ID` is shared because
both bots post into the same operator chat.

The Telegram secrets are optional — every alert step tolerates a
missing token silently. If they are unset, the workflow still runs and
the PR is still created; only the Telegram pings are skipped.

## Disabling / pausing

Three options, in increasing scope:

1. **Pause Telegram noise but keep collecting** — leave both workflows
   enabled but unset `CLAUDE_TELEGRAM_BOT_TOKEN`. The PR-open / merge-
   handoff pings and layer-1 WARNING/CRITICAL alerts all skip silently.
   The PR audit trail still lands.
2. **Stop opening new PRs but keep the existing one** — disable
   workflow 1 from the Actions UI (`Actions → Health Snapshot PR →
   Disable`). Workflow 2 still fires if you merge the open PR.
3. **Stop the whole pipeline** — disable both workflows. The trader is
   unaffected (no part of it imports from `comms/`).

Do **not** delete `comms/requests/REQ-*.json` manually — the comms
state machine in `src/comms/state.py` reclaims them via the
`expired`/`cancelled` lifecycle.

## Safety scope

Unchanged from the prior design:

- The collector is **read-only**. It does not write to any path under
  `src/runtime/`, `src/units/`, or any open-positions store.
- The trader does not import from `comms/` (see the safety note in
  [`comms/README.md`](../../comms/README.md)) — a malformed review
  request cannot influence live strategy behavior.
- The Action runs out-of-band on GitHub-hosted runners; the only
  side-effect on the VM is reading log files over SSH.
- The PR gate adds an explicit operator approval step before any
  review request reaches `main`, narrowing the blast radius further.
