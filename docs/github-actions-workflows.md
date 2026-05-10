# GitHub Actions — Canonical Reference

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Authority:** This is the single source of truth for what GitHub
> Actions exist in this repo, how they are triggered, what they do, and
> when Claude may use or modify them. Linked from
> [`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) and
> [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md).

## Why this doc exists

Claude is allowed (and expected) to use GitHub Actions as part of the
project's automation surface — for CI, validation, VM ops, training,
and dataset publishing. Earlier sessions sometimes assumed Actions were
unavailable; that assumption is wrong and has cost work. Inspect this
document first, then read the specific workflow file under
`.github/workflows/`.

## Permission tiers for editing workflows

Workflow files map to permission tiers (see
[`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) §
"Permission Tiers"):

- **Tier 1** — adding or fixing CI checks, lint config, inventory
  workflows, label bootstrap, secret-scan: edit freely after
  validation.
- **Tier 2** — anything that mutates the VM (operator-actions,
  vm-net-fix, vm-web-api-recover, vm-cloud-fix), changes deployment
  behaviour, or changes branch-protection requirements: requires
  explicit operator approval before merge.
- **Tier 3** — none of the workflows currently encode strategy or
  risk policy directly, but a workflow that adds a strategy parameter
  toggle, lifts an account from `dry_run` → `live`, or weakens a guard
  (`dry-run-guard`, `env-gate-guard`, `silent-empty-guard`) is **Tier
  3**: do not merge without explicit approval.

## Conventions

- All trigger-only-by-issue workflows use a corresponding label and
  rely on `bootstrap-labels.yml` to create labels idempotently. The
  declared labels are: `vm-diag-request`, `vm-web-api-recover`,
  `operator-action`, `vm-cloud-fix-request`, `vm-net-diag-request`,
  `vm-net-fix-request`.
- **Job IDs match workflow names** for every CI guard. The GitHub
  status-context name comes from the job ID, so each guard's job
  matches the workflow file name (`pytest-collect`, `secret-scan`,
  `ruff-lint`, `dry-run-guard`, `env-gate-guard`,
  `silent-empty-guard`, `repo-inventory`). `REQUIRED_CONTEXTS` in
  `branch-protection-sync.yml` is the source of truth for which
  contexts gate `main`; it is currently
  `["pytest-collect","secret-scan","ruff-lint","dry-run-guard"]`.
- Issue-driven mutating workflows take the issue body verbatim through
  the `ISSUE_BODY` env var; do **not** interpolate
  `${{ github.event.issue.body }}` directly into shell.
- All VM SSH workflows use the `VM_SSH_KEY` repo secret. Mutating
  actions log a JSON artifact with pre-/post-state.
- Secrets used: `VM_SSH_KEY`, `DIAG_READ_TOKEN`,
  `BRANCH_PROTECTION_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  plus the Oracle Cloud (`OCI_CLI_*`) set used only by `vm-cloud-fix`.

## Workflow Catalogue

### CI / PR guards

| File | Trigger | Purpose | Tier | Notes |
|---|---|---|---|---|
| `pytest-collect.yml` | `pull_request`, `push` to `main` | Runs `pytest --collect-only` to surface import / collection failures. **Blocking** as of S-045. | 1 | Required status check on `main`. |
| `ruff-lint.yml` | `pull_request`, `push` to `main` | Runs ruff with default rules, exclusions in `ruff.toml`. | 1 | Required status check on `main`. |
| `secret-scan.yml` | `pull_request`, `push` to `main` | Runs `scripts/secret_scan.py` over every tracked file. | 1 | Required status check on `main`. |
| `dry-run-guard.yml` | `pull_request` to `main` | Fails PRs that introduce dry-run flag flips that would silently downgrade live mode. Pings operator on hit. | 3 (touching the guard itself) | See `scripts/check_dry_run_in_diff.py`, `docs/claude/trading-mode-flags.md`. |
| `env-gate-guard.yml` | `pull_request` to `main` | Fails PRs that add env-gate reads in protected files without `# allow-silent: <reason>`. | 3 (touching the guard itself) | See `docs/audits/env-gate-purge-2026-05-10.md`. |
| `silent-empty-guard.yml` | `pull_request` to `main` | Fails PRs that add broad `except` handlers in protected read-paths without an explicit justification. | 3 (touching the guard itself) | See `docs/audits/silent-empty-2026-05-10.md`. |
| `repo-inventory.yml` | `pull_request`, `push` to `main` | Advisory; uploads `scripts/repo_inventory.py` output as build artifact. | 1 | Promotion to blocking is a deliberate later step. |

### Repo / branch admin

| File | Trigger | Purpose | Tier |
|---|---|---|---|
| `bootstrap-labels.yml` | `push` to `main` (paths: this file), `workflow_dispatch` | Creates required labels (`vm-diag-request`, `operator-action`, `vm-web-api-recover`, etc.) idempotently. | 1 |
| `branch-protection-sync.yml` | (push / dispatch) | Idempotently PUTs the branch-protection spec for `main`. Required-status-checks contexts are hardcoded in this file. | 2 |

### VM operations (PM-side / sandbox bridges)

| File | Trigger | Purpose | Tier | Allowed actions |
|---|---|---|---|---|
| `operator-actions.yml` | `workflow_dispatch`, `issues.opened` (label `operator-action`) | Single bridge for allowlisted mutating VM operations. | 2 (Tier-2 actions need a `reason`) | `status-check`, `pull-latest-logs`, `pull-and-deploy`, `restart-bot-service`, `reboot-vm` |
| `vm-diag-snapshot.yml` | `workflow_dispatch`, `issues.opened` (label `vm-diag-request`) | Read-only relay for `/api/diag/*`. Posts JSON as issue comment, closes issue. | 1 | Read only |
| `vm-web-api-recover.yml` | `issues.opened` (label `vm-web-api-recover`) | Restarts `ict-web-api.service` only. Restart-only, no edits. | 2 | `systemctl restart ict-web-api.service` |
| `vm-net-diag.yml` | `workflow_dispatch`, `issues.opened` (label `vm-net-diag-request`) | Read-only network diagnostics; checks 8001 reachability. | 1 | Read only |
| `vm-net-fix.yml` | `workflow_dispatch`, `issues.opened` (label `vm-net-fix-request`) | Opens TCP/8001 via `ufw` + `iptables -I INPUT ACCEPT`; verifies. | 2 | Local firewall only |
| `vm-cloud-fix.yml` | `workflow_dispatch`, `issues.opened` (label `vm-cloud-fix-request`) | Adds Oracle Cloud Security List ingress rule for the dashboard API port. | 3 (cloud-side change) | OCI ingress rule |

### Training / data

| File | Trigger | Purpose | Tier |
|---|---|---|---|
| `training-run.yml` | `push` to `claude/training-plan-*` (paths: `experiments/*/hypotheses.py`, `scripts/training/**`, this file), `workflow_dispatch` | Autonomous training run; commits results to `claude/training-results-<run-id>`, opens draft `TRAINING-RESULTS:` PR, fires Telegram ping. | 2 (results inform Tier-3 strategy decisions) |
| `training-rerun-5m.yml` | `push` (paths in `experiments/2026-05-07-vwap-accuracy/`), `workflow_dispatch` | Re-runs the VWAP-accuracy experiment at the production 5m timeframe on a runner that has live market-data egress. | 2 |
| `hf-cron.yml` | `workflow_dispatch` | Manual one-shot HuggingFace AutoTrain run. Daily cron disabled (CP-2026-05-02-02). | 2 |

### Sprint / session continuity

| File | Trigger | Purpose | Tier |
|---|---|---|---|
| `continue-work.yml` | `workflow_dispatch` (typically) | Bounded sprint-continuation handoff: validates `automation/session_handoff/next_session.json`, surfaces fields, appends history, uploads artifact. | 1 |

## How Claude should use these workflows

- **Reading VM state from a sandbox session** — open an issue with
  the right label (e.g. `vm-diag-request` with title
  `[diag-request] snapshot?limit=200`). The workflow runs, comments the
  result, closes the issue. Full pattern in
  [`docs/claude/diag-relay.md`](claude/diag-relay.md).
- **Restarting the web API** — open an issue with label
  `vm-web-api-recover`. No body required.
- **Triggering a Tier-2 operator action** — confirm operator approval
  in conversation, then open an issue with label `operator-action` and
  body `action: <name>\nreason: <text>`. Allowed names listed above.
- **Adding a CI check** — edit/create a workflow under
  `.github/workflows/`, then if it should be a required status check,
  add it to the `required_contexts` array in
  `branch-protection-sync.yml` in the same PR.
- **Inspecting workflow outputs** — the GitHub MCP available to Claude
  Code on the web does not currently include `run_workflow` /
  `download_artifact` / `get_run_logs`. For autonomy, drive workflows
  via the issue-trigger pattern and consume the result as a comment.

## Modification policy

- Every change to a workflow under `.github/workflows/` must mention
  this doc in the PR body when it changes triggers, secrets, allowed
  actions, or tier classification.
- New workflows must be listed in this catalogue (table + brief
  description) before merge.
- Removing or weakening a guard workflow (`dry-run-guard`,
  `env-gate-guard`, `silent-empty-guard`) is Tier 3 and requires
  explicit operator approval — guards exist precisely so silent
  downgrades cannot ship.

## Required secrets quick reference

| Secret | Used by |
|---|---|
| `VM_SSH_KEY` | All VM SSH workflows |
| `DIAG_READ_TOKEN` | `vm-diag-snapshot`, post-action verification in `operator-actions` |
| `BRANCH_PROTECTION_TOKEN` (PAT, fine-grained, `administration:write`) | `branch-protection-sync` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `dry-run-guard`, `env-gate-guard`, `silent-empty-guard`, `training-run` (operator pings) |
| `OCI_CLI_USER`, `OCI_CLI_FINGERPRINT`, `OCI_CLI_TENANCY`, `OCI_CLI_REGION`, `OCI_CLI_KEY_CONTENT` | `vm-cloud-fix` |
| `HF_TOKEN` (where present) | `hf-cron`, `training-run` (HF dataset publishing) |

## Optional repo variables

| Variable | Default | Used by |
|---|---|---|
| `VM_SSH_HOST` | `158.178.210.252` | VM SSH workflows |
| `VM_SSH_USER` | `ubuntu` | VM SSH workflows |
