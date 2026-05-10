# Runbook — System Health Check (two-workflow, PR-mediated)

The ICT trading bot's health-check pipeline runs as a **pair of GitHub
Actions** that gate Claude's manual review behind a merged, labeled PR:

| Workflow | What it does | When |
|---|---|---|
| 1. [`health-snapshot-pr.yml`](../../.github/workflows/health-snapshot-pr.yml) | Collects a VM snapshot, runs the layer-1 machine check, emits the layer-2 review request, and opens/updates a PR labeled `health-check-review`. | Cron `0 */6 * * *` + `workflow_dispatch` |
| 2. [`health-review-trigger.yml`](../../.github/workflows/health-review-trigger.yml) | Fires on `pull_request.closed`, gated by `merged == true` **and** the `health-check-review` label. Confirms the merge, lists the review-request files that just landed on `main`, and **POSTs to the Claude review routine's API endpoint** to start layer 2. | On every PR merge into `main` (filtered by label) |

The layered design is unchanged — layer 1 is automated machine triage,
layer 2 is a mandatory Claude review **on every run**, including healthy
ones. What changed is the *delivery* of layer 2: instead of committing
the review request straight to `main`, it now goes through an operator
review/merge step, and the Claude routine is then triggered by a direct
API call rather than by listening on a PR webhook.

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

## Why an API call (not a routine webhook subscription)

Workflow 2 fires the Claude review routine via a direct POST to its
API endpoint instead of having the routine subscribe to GitHub's PR
webhook. Trade-off summary:

| Hop | API trigger | Webhook trigger |
|---|---|---|
| Failure modes | Visible in the Action log; retried up to 5× with `--retry-all-errors`; failure marks the run red. | Silent if the webhook delivery is dropped or the routine's listener mis-filters. |
| Context delivery | Body carries `pr_url`, `pr_number`, `merge_sha`, `review_files[]`, repo — the routine doesn't have to scrape the GitHub event payload. | Routine has to parse the GitHub event and re-fetch the merge diff. |
| Setup | Two repo secrets (`CLAUDE_ROUTINE_URL`, `CLAUDE_ROUTINE_TOKEN`); both rotatable without a code change. | Routine subscribes to webhook; filter logic lives in the routine. |
| Approval gate | Same — PR merge is still the gate; the API call only happens **after** the merged + labeled filter passes. | Same. |

The POST body shape:

```json
{
  "event": "health_review_pr_merged",
  "repo": "benbaichmankass/ict-trading-bot",
  "pr_url": "https://github.com/.../pull/N",
  "pr_number": N,
  "merge_sha": "<sha>",
  "branch": "main",
  "review_files": ["comms/requests/REQ-...\.json", ...]
}
```

## Layer-1 fallback (Anthropic call fails)

If the Anthropic call in layer 1 fails for any reason — rate limit,
billing, network, malformed JSON in the response — the workflow does
**not** abort. Instead, `scripts/run_health_check.py` synthesizes an
`UNKNOWN`-status stub report:

```json
{
  "status": "UNKNOWN",
  "summary": "Layer-1 analysis unavailable: <ErrorClass>: <message>",
  "checks": { "<each section>": {"status": "warn", "note": "layer-1 verdict unavailable"} },
  "action_required": "Manual review required — ...",
  "error": {"type": "...", "message": "..."}
}
```

The stub is written to `artifacts/health/latest.json` exactly like a
real verdict, the layer-2 review request is emitted with
`priority: high` (UNKNOWN is treated like WARNING/CRITICAL for the
operator's eye), the PR is opened, and the Telegram alert fires with
the ⚪ icon naming the underlying error class. The Claude routine
then reviews the **raw snapshot** — which is always present — as the
source of truth.

This preserves the design contract that layer 2 runs on every
execution, even when layer 1 is temporarily unavailable.

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

Merging the PR fires workflow 2, which POSTs to the Claude routine. To
**skip** a run after the PR is open, just close it without merging —
nothing leaves the snapshot branch and the routine is never called.

## Triggering from a sandbox session (smoke-testing the pipeline)

A Claude Code session running on the web sandbox cannot call
`workflow_dispatch` directly — the hosted GitHub MCP server omits the
`actions` toolset (see CLAUDE.md → "PM-side session capabilities"). To
let the sandbox fire workflow 1 end-to-end without an operator click,
`health-snapshot-pr.yml` exposes a third trigger: `issues.opened`
filtered to label `health-snapshot-trigger`. The label is created by
[`bootstrap-labels.yml`](../../.github/workflows/bootstrap-labels.yml).

### Path B — issue-driven (web sandbox, autonomous)

This is the standing pattern. From a sandbox session:

```text
mcp__github__issue_write(method='create',
    title='[health-smoke] e2e smoke test',
    labels=['health-snapshot-trigger'],
    body='Triggering Health Snapshot PR end-to-end.')
```

The workflow runs as if you'd clicked "Run workflow" in the Actions UI,
opens or updates the review PR on `auto/health-check-review`, and then
the final two steps comment back on the trigger issue with:

- the **workflow run URL**,
- the **resulting PR URL** (or a warning if no PR was opened/updated),
- the **layer-1 verdict** read from `artifacts/health/latest.json`
  (`HEALTHY` / `WARNING` / `CRITICAL` / `UNKNOWN`),
- the layer-1 `summary` and (when the fallback fired) the underlying
  `error.type` + `error.message`,
- the **Telegram step exit code** — `0` means the Claude-bot ping was
  delivered, anything else means the helper failed (non-fatal).

…and then close the issue (`completed` on success, `not_planned` on
failure). The sandbox session reads the comment via
`mcp__github__issue_read` and verifies the PR contents via
`mcp__github__get_file_contents` against `artifacts/health/latest.json`
on branch `auto/health-check-review`.

**Verifying the layer-1 fallback didn't silently fire:** look at the
`Layer-1 verdict` line in the issue comment. `UNKNOWN` plus a
`Layer-1 fallback fired — <ErrorClass>: <message>` block means the
Anthropic call did not succeed; check the `ANTHROPIC_API_KEY` secret
and Anthropic billing before re-running. A real verdict (HEALTHY /
WARNING / CRITICAL) confirms layer 1 reached Claude.

### Path A — local Claude Code with `actions` MCP toolset

If you're running this from Claude Code CLI / desktop instead of the
web sandbox, you can install
[`github/github-mcp-server`](https://github.com/github/github-mcp-server)
locally and start it with `GITHUB_TOOLSETS=actions,repos,issues,pull_requests`
(or `all`). That gives the session direct
`mcp__github__run_workflow` / `list_workflow_runs` /
`get_workflow_run_logs` access, and the smoke test can be driven
without going through an issue:

```text
mcp__github__run_workflow(
    owner='benbaichmankass',
    repo='ict-trading-bot',
    workflow_id='health-snapshot-pr.yml',
    ref='main')
# then poll list_workflow_runs / get_workflow_run, fetch failure logs
# via get_workflow_run_logs if conclusion != 'success'.
```

Both paths exercise the same workflow code — Path B is the durable
fallback for the web sandbox; Path A is the cleaner option once the
session has `actions:write` on its MCP server. Use whichever is
available; both leave the same audit trail (run URL, PR, Telegram
ping).

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
to `main` atomically; workflow 2 lists which ones just landed and ships
the whole list to the routine in `review_files[]`.

The routine API call is retried up to 5 times by `curl --retry-all-errors`,
so transient 5xx / network blips don't drop the trigger.

If you want a fresh PR instead of updating the existing one, close the
open one (without merging) and let the next scheduled run reopen it.

## How a Claude review actually happens

1. The PR is merged → `comms/requests/REQ-*.json` is on `main`.
2. Workflow 2 fires, lists the newly-added review request files, and
   POSTs to the Claude routine API endpoint with the merged-PR
   metadata and the list of new request files.
3. The Claude routine wakes up, fetches the request files (and any
   linked artifacts), performs the layer-2 sanity review, and writes
   its findings back. It also marks the request status appropriately
   in the comms state machine (see `comms/README.md`).
4. The VM's `ict-git-sync` timer pulls any state changes the routine
   commits.

The response shape the routine produces is documented in
[`comms/schema/health_review_response.template.json`](../../comms/schema/health_review_response.template.json).

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
only repo-level setting required by the design.

## Required GitHub secrets

These are the actual secret names used by the action; they match the
repo's existing secret store:

| Name | Purpose |
|---|---|
| `VM_SSH_KEY`                | OpenSSH private key for `ubuntu@158.178.210.252` (the bot VM) |
| `ANTHROPIC_API_KEY`         | Claude Haiku 4.5 calls in layer 1 |
| `CLAUDE_ROUTINE_URL`        | Full POST URL of the Claude review routine API endpoint (used by workflow 2) |
| `CLAUDE_ROUTINE_TOKEN`      | Bearer token for the Claude review routine API endpoint |
| `CLAUDE_TELEGRAM_BOT_TOKEN` | **Claude bot token** (separate from the trader's bot) — layer-1 alerts, PR-open ping, routine-fired ping |
| `TELEGRAM_CHAT_ID`          | Shared chat id (same chat receives trader-bot and Claude-bot messages — only the bot token differs) |

The action's Telegram pings are intentionally routed via the Claude
bot token so review-pipeline noise comes from a separate sender than
the trader's live alerts. The `TELEGRAM_CHAT_ID` is shared because
both bots post into the same operator chat.

The Telegram secrets are optional — every alert step tolerates a
missing token silently. `CLAUDE_ROUTINE_URL` and `CLAUDE_ROUTINE_TOKEN`
are **not** optional; workflow 2 fails (loudly) at the first step if
either is unset. `ANTHROPIC_API_KEY` is **soft-required** — if it is
unset or out of credits, layer 1 falls back to an `UNKNOWN`-status stub
and the rest of the run continues (see *Layer-1 fallback* above).

Both routine secrets are designed to be rotated independently — the
workflow reads them at runtime, so updating either one in repo settings
takes effect on the next workflow run with no code change.

## Disabling / pausing

Three options, in increasing scope:

1. **Pause Telegram noise but keep collecting** — leave both workflows
   enabled but unset `CLAUDE_TELEGRAM_BOT_TOKEN`. The PR-open / routine-
   fired pings and layer-1 alerts all skip silently. The PR audit
   trail still lands and the routine is still called.
2. **Stop opening new PRs but keep the existing one** — disable
   workflow 1 from the Actions UI (`Actions → Health Snapshot PR →
   Disable`). Workflow 2 still fires (and POSTs to the routine) if you
   merge the open PR.
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
