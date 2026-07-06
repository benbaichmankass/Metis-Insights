# GitHub Actions — Canonical Reference

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> Last updated: 2026-05-13 (added trainer-VM workflows, vwap-backtest,
> doc-audit, complete autonomy matrix, exact MCP trigger calls).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Authority:** This is the single source of truth for what GitHub
> Actions exist in this repo, how they are triggered, what they do, and
> when Claude may use or modify them. Linked from
> [`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) and
> [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md).
>
> **Other repo:** `benbaichmankass/ict-trader-dashboard` has **no**
> `.github/workflows/` directory as of this writing.

---

## ⛔ STOP — Read before touching any workflow

**Before creating a new workflow or writing any SSH/diag/backtest
automation, check the catalogue below first.** Most common operations
already have a workflow. Creating a duplicate wastes PRs, clutters the
Actions tab, and adds dead code.

| Need | Existing workflow | Trigger |
|---|---|---|
| Run any command on the **trainer VM** | `trainer-vm-diag.yml` | `trainer-vm-diag-request` label |
| Run any command on the **live VM** (read-only) | `vm-diag-snapshot.yml` | `vm-diag-request` label |
| Mutate the **live VM** (restart, deploy, mode-flip) | `system-actions.yml` | `system-action` label |
| Run a **VWAP backtest** on the trainer VM | `vwap-backtest.yml` | `vwap-backtest-trigger` label |
| Check network reachability from GitHub runner | `vm-net-diag.yml` | `vm-net-diag-request` label |
| Restart the live VM web API | `vm-web-api-recover.yml` | `vm-web-api-recover` label |
| Take a health snapshot of the live VM | `health-snapshot.yml` | `health-snapshot-trigger` label |

If the use case is not in this table, read the full catalogue before
deciding to create a new workflow.

---

## Why this doc exists

Claude is allowed (and expected) to use GitHub Actions as part of the
project's automation surface — for CI, validation, VM ops, training,
and dataset publishing. Earlier sessions sometimes assumed Actions were
unavailable; that assumption is wrong and has cost work. Inspect this
document first, then read the specific workflow file under
`.github/workflows/`.

**Key constraint:** The GitHub MCP tools available to Claude Code on the
web do **not** include `run_workflow`, `download_artifact`, or
`get_run_logs`. All Claude-initiated triggers must use one of the MCP
patterns listed below. The `.mcp.json` in the repo root enables
`workflow_dispatch` via the CLI-side GitHub MCP server (takes effect
after a session restart with `GITHUB_TOKEN` set).

---

## MCP trigger patterns

Claude has three ways to fire a workflow without operator-UI access:

### Pattern A — Issue-driven (preferred for most ops workflows)

Open a GitHub issue with the correct label. The workflow's
`issues: types: [opened]` handler picks it up, runs, comments the
result back on the issue, and closes the issue.

**MCP call:**
```
mcp__github__issue_write
  owner: benbaichmankass
  repo: ict-trading-bot
  title: "[<tag>] <description>"
  body: |
    <key>: <value>
    ...
  labels: ["<label-name>"]
```

The issue `body` is the payload: each workflow documents its expected
keys. Always quote the body through the `body` field — never inline
`${{ github.event.issue.body }}` into shell; the workflow reads through
`ISSUE_BODY` env var to prevent shell injection.

### Pattern B — Push-sentinel (for push-triggered workflows)

Some workflows use `push: paths: [.github/triggers/<name>]` as an
alternative trigger so they can be fired without UI or `gh` access.
Touch the sentinel file to start a run.

**MCP call:**
```
mcp__github__create_or_update_file
  owner: benbaichmankass
  repo: ict-trading-bot
  path: .github/triggers/<workflow-name>
  message: "chore: trigger <workflow-name>"
  content: <base64 of "timestamp\n">
  branch: main
  sha: <current file SHA, if file exists>
```

Get the current SHA first with `mcp__github__get_file_contents` so the
update call doesn't fail. The sentinel file carries no semantic content;
only its mtime matters.

### Pattern C — workflow_dispatch (operator-UI only for now)

Workflows that have only `workflow_dispatch` (no issue trigger, no
sentinel) require an operator to click "Run workflow" in the GitHub
Actions UI. Claude cannot fire these from a web sandbox session.
After a session restart with `.mcp.json` loaded and `GITHUB_TOKEN` set,
the CLI GitHub MCP server exposes `mcp__github__create_workflow_dispatch`
which would unlock these.

---

## Autonomy levels

| Level | Meaning |
|---|---|
| **AUTONOMOUS** | Claude may trigger without operator confirmation. Trainer-VM ops, read-only diag, doc audit, health snapshots, and anything in the sandbox that is read-only or trainer-scoped. |
| **OPERATOR-APPROVAL** | Claude must get operator sign-off in conversation before opening the issue / touching the sentinel. Mutating live-VM ops, OCI infrastructure changes. |
| **AUTO** | Runs automatically (CI guard on PR/push, cron schedule). Claude never needs to trigger these; they self-fire. |

---

## Quick-reference cheat sheet

| Workflow | Autonomy | Trigger pattern | Label / sentinel |
|---|---|---|---|
| `pytest-collect.yml` | AUTO | PR/push | — |
| `ruff-lint.yml` | AUTO | PR/push | — |
| `secret-scan.yml` | AUTO | PR/push | — |
| `dry-run-guard.yml` | AUTO | PR | — |
| `env-gate-guard.yml` | AUTO | PR | — |
| `silent-empty-guard.yml` | AUTO | PR | — |
| `arch-doc-guard.yml` | AUTO | PR | — |
| `repo-inventory.yml` | AUTO | PR/push | — |
| `bootstrap-labels.yml` | AUTO / AUTONOMOUS | push (paths: this file) + B-sentinel or `workflow_dispatch` | `.github/triggers/bootstrap-labels` |
| `branch-protection-sync.yml` | AUTO | push to `main` + workflow_dispatch | — |
| `health-snapshot.yml` | AUTONOMOUS | A or E (every 6h) | `health-snapshot-trigger` |
| `vm-diag-snapshot.yml` | AUTONOMOUS | A | `vm-diag-request` |
| `trainer-vm-diag.yml` | AUTONOMOUS | A | `trainer-vm-diag-request` |
| `vm-web-api-recover.yml` | AUTONOMOUS | A | `vm-web-api-recover` |
| `vm-net-diag.yml` | AUTONOMOUS | A | `vm-net-diag-request` |
| `doc-audit-weekly.yml` | AUTONOMOUS | A or E (Mon 12:00 UTC) | `doc-audit-now` |
| `vwap-backtest.yml` | AUTONOMOUS | A | `vwap-backtest-trigger` |
| `provision-training-vm.yml` | AUTONOMOUS | A | `provision-training-vm` |
| `provision-training-vm-auto-retry.yml` | AUTO | E (every 10 min) | — |
| `deploy-trainer-bootstrap.yml` | AUTONOMOUS | B | `.github/triggers/deploy-trainer-bootstrap` |
| `system-actions.yml` | OPERATOR-APPROVAL | A | `system-action` |
| `vm-net-fix.yml` | OPERATOR-APPROVAL | A | `vm-net-fix-request` |
| `vm-cloud-fix.yml` | OPERATOR-APPROVAL | A | `vm-cloud-fix-request` |
| `vm-cloud-open-ib-port.yml` | OPERATOR-APPROVAL | A | `vm-cloud-open-ib-port` |
| `oci-storage-verify.yml` | AUTONOMOUS | C (workflow_dispatch) | — |
| `oci-storage.yml` | OPERATOR-APPROVAL | C (workflow_dispatch) | — |
| `training-run.yml` | OPERATOR-APPROVAL | push to `claude/training-plan-*` | — |
| `training-rerun-5m.yml` | OPERATOR-APPROVAL | push to experiment paths | — |
| `hf-cron.yml` | OPERATOR-APPROVAL | C (workflow_dispatch) | — |
| `continue-work.yml` | AUTONOMOUS | C (workflow_dispatch) | — |
| `purge-artifacts.yml` | AUTO | E (daily 03:00 UTC) + C (workflow_dispatch) + A (`purge-artifacts-now`) | `purge-artifacts-now` |

---

## Workflow Catalogue

### CI / PR guards (all AUTO)

These run on every PR or push to `main`. Claude never triggers them;
they self-fire. Touching a guard's own logic is **Tier 3**.

| File | Trigger | Purpose | Required on `main`? |
|---|---|---|---|
| `pytest-collect.yml` | PR, push `main` | `pytest --collect-only` — surfaces import/collection failures. | Yes |
| `pytest-run.yml` | PR, push `main` | `pytest -q tests/` — runs the **full** suite (collection only checks imports). Required since 2026-05-22 (#1721). | Yes |
| `ruff-lint.yml` | PR, push `main` | `ruff check` with rules from `ruff.toml`. | Yes |
| `secret-scan.yml` | PR, push `main` | `scripts/secret_scan.py` over every tracked file. | Yes |
| `dry-run-guard.yml` | PR to `main` | Fails PRs that flip dry-run flags, silently downgrading live mode. Telegrams operator on hit. | Yes |
| `env-gate-guard.yml` | PR to `main` | Fails PRs adding env-gate reads in protected files without `# allow-silent: <reason>`. | Yes |
| `silent-empty-guard.yml` | PR to `main` | Fails PRs adding broad `except` handlers in protected read-paths without justification. | Yes |
| `canonical-config-loaders.yml` | PR to `main` | Fails PRs that add a hand-rolled parser for `config/accounts.yaml` outside the canonical loader module. | Yes |
| `canonical-db-resolver.yml` | PR to `main` | Fails PRs that add an inline `trade_journal.db` path fallback outside the canonical resolver. | Yes |
| `arch-doc-guard.yml` | PR to `main` | Emits `::warning` when high-impact subsystems change without an architecture-doc update. Always exits 0 (advisory). | No |
| `repo-inventory.yml` | PR, push `main` | Uploads `scripts/repo_inventory.py` output as build artifact. Advisory only. | No |

`REQUIRED_CONTEXTS` in `branch-protection-sync.yml` is the authoritative
list of blocking checks. Currently (2026-05-22): `["pytest-collect","pytest-run","secret-scan","ruff-lint","dry-run-guard","env-gate-guard","silent-empty-guard","canonical-config-loaders","canonical-db-resolver"]`.

---

### Repo / branch admin

#### `purge-artifacts.yml`

**Autonomy:** AUTO (daily cron) / AUTONOMOUS (Claude dispatches via
labelled issue; operator may dispatch via the Actions UI for a custom
window).

**Trigger:** `schedule` (daily 03:00 UTC) + `workflow_dispatch` +
`issues.opened` filtered to label `purge-artifacts-now`.

**Purpose:** Reclaims GitHub Actions storage by deleting build
artifacts. The free plan ships **0.5 GB** of Actions storage; once
that fills, GitHub bills overage (or blocks the account if a $0 budget
is set). Lowering `retention-days` on a producing workflow only affects
*future* uploads — already-uploaded artifacts keep their original
retention until expiry. This workflow deletes them now.

**Behaviour:**
- **Scheduled run** (03:00 UTC daily) deletes artifacts older than
  **7 days**. Acts as a safety net so storage stays trimmed even if a
  future workflow re-introduces a long retention.
- **Manual dispatch** takes two inputs:
  - `older_than_days` — `0` deletes ALL artifacts (one-shot recovery
    when storage is full). `N > 0` deletes anything older than N days.
  - `dry_run` — `true` logs the candidate set without calling DELETE.
- **Issue-driven** (Claude path, since the hosted GitHub MCP has no
  `workflow_dispatch`): open an issue with label
  `purge-artifacts-now`. Empty body → one-shot total purge
  (`older_than_days=0`, `dry_run=false`); body may override with
  `older_than_days: <N>` / `dry_run: <true|false>` lines. Result table
  posts back as an issue comment, then the issue is closed. The reply
  step uses `if: always()` so cancelled runs (e.g. job timeout) still
  emit a `⏱` status note — never silently hung.

**Performance:** deletes run in **parallel chunks of 10** via
`Promise.all`. Job timeout is **60 minutes**, sized for the one-shot
total-purge recovery scenario (thousands of artifacts on a maxed-out
500 MB cap); scheduled daily runs at 7-day retention finish in
seconds.

**Companion:** every artifact-uploading workflow in this repo is now
capped at ≤ 7 days retention (most at exactly 7; `repo-inventory.yml`
at 3; `get-diag-token.yml` at 1). Run logs persist 90 days
independently and are the durable audit trail; artifacts are bounded
working state, not records.

---

#### `bootstrap-labels.yml`

**Autonomy:** AUTO (self-fires on merge of this file) / AUTONOMOUS (Claude
may re-run via B-sentinel if a label gets deleted).

**Trigger:** `push` to `main` (paths: this file) + `workflow_dispatch`.

**Purpose:** Idempotently creates all labels that other workflows filter
on. Safe to re-run at any time; existing labels are left alone.

**MCP trigger (re-run if label deleted):**
```
mcp__github__create_or_update_file
  path: .github/triggers/bootstrap-labels
  message: "chore: re-sync labels"
  branch: main
```
Or just touch `.github/workflows/bootstrap-labels.yml` in a PR to main.

**Current label set:** `vm-diag-request`, `vm-web-api-recover`,
`system-action`, `vm-cloud-fix-request`, `vm-net-diag-request`,
`vm-net-fix-request`, `health-snapshot-trigger`, `cf-worker-deploy`,
`provision-training-vm`, `doc-audit-now`, `doc-drift`,
`vwap-backtest-trigger`, `trainer-vm-diag-request`.

---

#### `branch-protection-sync.yml`

**Autonomy:** AUTO (self-fires on every push to `main`) + `workflow_dispatch`.

**Trigger:** `push` to `main` (every merge) and `workflow_dispatch`. A
change to `REQUIRED_CONTEXTS` takes effect automatically on the next push
to `main` — no operator click required. This is how the 2026-05-22
`pytest-run` promotion (#1721) applied: the merge to `main` ran this
workflow, which PUT the updated contexts.

**Purpose:** PUTs the branch-protection spec for `main` idempotently.
Hardcoded `REQUIRED_CONTEXTS` is authoritative.

**Secrets:** `BRANCH_PROTECTION_TOKEN` (PAT, fine-grained,
`administration:write`). Unset → the workflow no-ops (skips the PUT and
leaves protection unchanged).

**MCP trigger:** none needed — it self-fires on merge to `main`.
`workflow_dispatch` is available to the operator in the Actions UI as a
manual re-sync. To read the live protection state from a PM-side session,
fire the `branch-protection-report.yml` relay (open a `[bp-report]` issue).

---

### Health & diagnostics

#### `health-snapshot.yml`

**Autonomy:** AUTONOMOUS.

**Trigger:** Schedule every 6h (cron `0 */6 * * *`), `workflow_dispatch`,
and `issues.opened` with label `health-snapshot-trigger`.

**Purpose:** SSHes into the **live VM**, runs
`scripts/collect_health_snapshot.sh`, uploads artifact. The
`/health-review` skill reads pasted-by-operator artifact content (not
repo-resident), so no merge step is needed.

**MCP trigger (autonomous, Pattern A):**
```
mcp__github__issue_write
  title: "[health-snapshot] on-demand snapshot"
  body: "reason: Claude-initiated snapshot for health review"
  labels: ["health-snapshot-trigger"]
```
Workflow SSHes, uploads artifact, comments result URL, closes issue.

**Secrets:** `VM_SSH_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

---

#### `vm-diag-snapshot.yml`

**Autonomy:** AUTONOMOUS — read-only relay, no operator approval needed.

**Trigger:** `issues.opened` (label `vm-diag-request`), `workflow_dispatch`.

**Purpose:** Hits `/api/diag/<path>` on the live VM via SSH tunnel, posts
the JSON response as an issue comment, closes the issue. The PM-side
session's primary window into the live VM.

**Issue format:**
```
title: "[diag-request] <path-and-query>"
body: (empty body is fine; the path comes from the title)
labels: ["vm-diag-request"]
```

Examples:
```
title: "[diag-request] snapshot?limit=200"
title: "[diag-request] audit?limit=50"
title: "[diag-request] status"
title: "[diag-request] journalctl?unit=ict-trader-live.service&lines=100"
```

**Full pattern:** [`docs/claude/diag-relay.md`](claude/diag-relay.md).

**Secrets:** `VM_SSH_KEY`, `DIAG_READ_TOKEN`.

---

#### `trainer-vm-diag.yml`

**Autonomy:** AUTONOMOUS — trainer VM is fully autonomous Claude territory.
Claude may open these issues at any time without operator approval.

**Trigger:** `issues.opened` (label `trainer-vm-diag-request`),
`workflow_dispatch`.

**Purpose:** Unrestricted SSH relay into the **trainer VM**
(`158.178.209.121`). Runs any bash command, posts full output as issue
comment, closes issue. Commands delivered via stdin (no shell injection).

**Issue format:**
```
title: "[trainer-diag] <description>"
body: |
  cmd: <bash command>
labels: ["trainer-vm-diag-request"]
```

Multi-line commands:
```
body: |
  cmd: |
    cd /home/ubuntu/ict-trading-bot
    tail -n 200 runtime_logs/trainer/dataset_builds.jsonl
```

**MCP trigger (Pattern A):**
```
mcp__github__issue_write
  title: "[trainer-diag] check trainer service status"
  body: "cmd: journalctl -u ict-trainer.service -n 100 --no-pager"
  labels: ["trainer-vm-diag-request"]
```

**Common commands Claude uses autonomously:**
- `journalctl -u ict-trainer.service -n 100 --no-pager` — service log
- `cat runtime_logs/trainer/dataset_builds.jsonl | tail -50` — build log
- `python -m ml.registry list` — registry state
- `ls -la ml/models/` — model artifact inventory
- `systemctl status ict-trainer.service ict-trainer.timer` — service state
- `df -h && free -m` — resource usage
- `cat /etc/ict-trainer-vm.role` — role marker verification

**Full permission scope:** `docs/claude/trainer-vm-mode.md` § 9.

**Secrets:** `VM_SSH_KEY`. **Variables:** `TRAINER_VM_IP` (default
`158.178.209.121`), `TRAINER_VM_USER` (default `ubuntu`).

---

### VM operations

#### `system-actions.yml`

**Autonomy:** OPERATOR-APPROVAL — always confirm with operator before opening
this issue. Tier-1 actions (`status-check`, `pull-latest-logs`) need only
in-conversation approval; Tier-2 actions (`pull-and-deploy`,
`restart-bot-service`, `reboot-vm`, `set-account-mode`) need explicit
operator ack.

**Trigger:** `issues.opened` (label `system-action`), `workflow_dispatch`.

**Purpose:** Single bridge for the allowlisted mutating live-VM operations.
Runs via SSH; logs a JSON audit artifact (pre-/post-state). The issue body
is passed verbatim through `ISSUE_BODY` env var.

**Issue format:**
```
title: "[system-action] <action-name>"
body: |
  action: <name>
  reason: <text>
labels: ["system-action"]
```

For `set-account-mode`:
```
body: |
  action: set-account-mode
  account: bybit_2
  mode: live
  reason: <operator-approved reason>
```

**Allowlisted actions:**
- `status-check` — read-only health dump (Tier 1)
- `pull-latest-logs` — fetch recent log files (Tier 1)
- `pull-and-deploy` — git pull + restart services (Tier 2)
- `restart-bot-service` — `systemctl restart ict-trader-live.service` (Tier 2)
- `reboot-vm` — full VM reboot (Tier 2)
- `set-account-mode` — flip `mode: live|dry_run` in `accounts.yaml` (Tier 2, sanctioned wire)

**Full contract:** [`docs/claude/system-actions.md`](claude/system-actions.md).

**Secrets:** `VM_SSH_KEY`, `DIAG_READ_TOKEN`.

---

#### `vm-web-api-recover.yml`

**Autonomy:** AUTONOMOUS — self-heal when the web API is down (curl exit 7).
No operator approval needed; restart-only, no edits.

**Trigger:** `issues.opened` (label `vm-web-api-recover`).

**Purpose:** `systemctl restart ict-web-api.service` + health probe on the
live VM. Fires when `/api/diag/*` returns curl exit 7 (FastAPI down).

**MCP trigger (Pattern A):**
```
mcp__github__issue_write
  title: "[vm-web-api-recover] web API is down — exit 7"
  body: "Diag relay returning curl exit 7; restarting ict-web-api.service."
  labels: ["vm-web-api-recover"]
```

**Secrets:** `VM_SSH_KEY`.

---

#### `sync-vm-secrets.yml`

**Autonomy:** TIER-2 (one operator OK in chat). **Canonical broker-credential
propagation path** (added 2026-06-02). One workflow mirrors the full declared
set of broker-credential Actions secrets to the live trader's `.env` and
restarts the trader. Adding a new broker = append env-var names to
`REQUIRED_SECRETS` or `OPTIONAL_SECRETS` in the workflow + `SYNC_REQUIRED`/
`SYNC_OPTIONAL` in `scripts/ops/sync_vm_secrets.sh`. No per-broker workflow
file.

**Trigger:** `workflow_dispatch` (operator UI / Claude via Actions API).

**Purpose:** Idempotent Actions → VM env-file mirror via SSH `SendEnv` —
secret values never reach run logs or audit artifacts. Restart fires only
when the `.env` actually changed. Replaces the per-account pattern of the
legacy `rotate-account-keys.yml` (still in place as the Bybit-only path
pending migration).

**Secrets:** `VM_SSH_KEY`, all entries in `REQUIRED_SECRETS` (currently
`BYBIT_API_KEY_1/2`, `BYBIT_API_SECRET_1/2`), and OPTIONAL ones for any
broker that's been onboarded (the broker-credential vars for any onboarded broker).

---

#### `init-actions-secrets.yml`

**Autonomy:** AUTONOMOUS — creates empty placeholder Actions secrets only.
Never reads, logs, or operates on values.

**Trigger:** `workflow_dispatch` (UI / Actions API), `issues.opened` (label
`init-actions-secrets`, added 2026-06-02 / PR #2652).

**Purpose:** Pre-create empty placeholder Actions secret slots so the
operator pastes values into pre-existing slots (Settings → Secrets →
Update) instead of clicking "New repository secret" N times for each new
broker. Already-set names are skipped, never overwritten.

**MCP trigger (Pattern A — issue-label):**
```
mcp__github__issue_write
  title: "[init-actions-secrets] pre-create <broker> placeholders"
  body: |
    names: BROKER_USERNAME,BROKER_PASSWORD,...
    reason: pre-create empty <broker> placeholders for operator-paste flow
  labels: ["init-actions-secrets"]
```

**Secrets:** `BRANCH_PROTECTION_TOKEN` (PAT with `repo` scope — already
provisioned for `branch-protection-sync.yml`; covers Actions-secret writes).

---

#### `vm-net-diag.yml`

**Autonomy:** AUTONOMOUS — read-only network diagnostics.

**Trigger:** `issues.opened` (label `vm-net-diag-request`),
`workflow_dispatch`.

**Purpose:** SSH + port-reachability checks; tests TCP/8001 from the
runner. Posts results as issue comment.

**MCP trigger (Pattern A):**
```
mcp__github__issue_write
  title: "[vm-net-diag] check port 8001 reachability"
  body: "Checking TCP/8001 access from external runner."
  labels: ["vm-net-diag-request"]
```

---

#### `vm-net-fix.yml`

**Autonomy:** OPERATOR-APPROVAL — modifies the live VM's local firewall.

**Trigger:** `issues.opened` (label `vm-net-fix-request`),
`workflow_dispatch`.

**Purpose:** Opens TCP/8001 via `ufw allow` + `iptables -I INPUT ACCEPT`;
verifies post-fix.

**MCP trigger (Pattern A, after operator approval):**
```
mcp__github__issue_write
  title: "[vm-net-fix] open port 8001"
  body: "Operator approved. Opening ufw + iptables for dashboard API port."
  labels: ["vm-net-fix-request"]
```

---

#### `vm-cloud-fix.yml`

**Autonomy:** OPERATOR-APPROVAL (Tier 3 — cloud-side infrastructure change).

**Trigger:** `issues.opened` (label `vm-cloud-fix-request`),
`workflow_dispatch`.

**Purpose:** Adds an Oracle Cloud Security List ingress rule for the
dashboard API port. This is a cloud-side change (OCI VCN Security List),
not a VM-local change.

**Secrets:** `VM_SSH_KEY`, OCI CLI set (`OCI_CLI_USER`, `OCI_CLI_FINGERPRINT`,
`OCI_CLI_TENANCY`, `OCI_CLI_REGION`, `OCI_CLI_KEY_CONTENT`).

---

#### `vm-cloud-open-ib-port.yml`

**Autonomy:** OPERATOR-APPROVAL (Tier 3 — cloud-side infrastructure change).

**Trigger:** `issues.opened` (label `vm-cloud-open-ib-port`),
`workflow_dispatch`.

**Purpose:** Adds an Oracle Cloud Security List ingress rule for the **IB
Gateway API port (4002)** scoped to the **private subnet only**
(`INGRESS_SOURCE_CIDR`, default `10.0.0.0/24`) — the trader on the micro
reaches the isolated gateway VM (`10.0.0.251`) across the subnet
(gateway-isolation, Plan B). Distinct from `vm-cloud-fix.yml`: it **hard-refuses
any `/0` (public) source** and does **no** public-internet `/api/health` probe
(the broker socket is never public; MES is verified via the trader logs). Shares
`scripts/ops/cloud_open_port.py` (now `DASHBOARD_PORT` / `INGRESS_SOURCE_CIDR` /
`INGRESS_DESC` parameterized).

**Targeting the right subnet (host override):** OCI ingress rules govern traffic
*into* the subnet they're attached to, so the rule must land on the **gateway's**
subnet Security List. The issue body accepts `host:` / `source:` / `port:` lines
(e.g. `host: <gateway public IP>`, `source: 10.0.0.0/16`) to point the run at the
gateway VM's IMDS → its subnet SL, rather than the default SSH host (the micro).
Added in #3312 after the micro-subnet rule alone didn't open the path.

**Secrets:** `VM_SSH_KEY`, OCI CLI set (`OCI_CLI_USER`, `OCI_CLI_FINGERPRINT`,
`OCI_CLI_TENANCY`, `OCI_CLI_REGION`, `OCI_CLI_KEY_CONTENT`).

---

### Trainer VM lifecycle

#### `provision-training-vm.yml`

**Autonomy:** AUTONOMOUS — provisioning new trainer-VM infrastructure is
fully Claude-autonomous per `trainer-vm-mode.md` § 3.

**Trigger:** `issues.opened` (label `provision-training-vm`),
`workflow_dispatch`.

**Purpose:** Provisions a new OCI Always Free Ampere A1 VM for the model
training center. Idempotent — refuses to create a duplicate if a
non-TERMINATED `ict-trainer-vm` already exists.

**Issue format:**
```
title: "[provision-training-vm] S-AI-WS9 training center"
body: |
  confirm: yes
  reason: S-AI-WS9 — provision model training center VM
labels: ["provision-training-vm"]
```

The `confirm: yes` line is an audit-trail formality, not a human gate.
Claude always includes it.

**Secrets:** `VM_SSH_KEY`, OCI CLI set.

---

#### `provision-training-vm-auto-retry.yml`

**Autonomy:** AUTO — cron-driven retry loop; Claude never triggers this.

**Trigger:** Schedule every 10 minutes, `workflow_dispatch`.

**Purpose:** Retries `provision-training-vm.yml` until OCI has Ampere A1
capacity. When the VM is detected as existing, opens a one-time
notification issue and goes silent on subsequent ticks.

**Note:** This workflow runs perpetually until the trainer VM exists. To
stop it entirely, disable the schedule in GitHub Actions settings.

---

#### `deploy-trainer-bootstrap.yml`

**Autonomy:** AUTONOMOUS — trainer VM bootstrap is fully autonomous.

**Trigger:** `workflow_dispatch`, and `push` to `main` paths:
`.github/triggers/deploy-trainer-bootstrap` (the push sentinel).

**Purpose:** Full one-shot bootstrap sequence on the trainer VM after
provisioning:
1. Verify role marker (`/etc/ict-trainer-vm.role`)
2. Deploy vm-to-vm SSH key
3. Pull latest `main`
4. Create venv + install deps
5. Sync `trade_journal.db` + `signal_audit.jsonl` from live VM (read-only)
6. Build all WS5 dataset families
7. Train + register + promote WS5 baselines to `target_stage`
8. Enable `ict-trainer.service` + `ict-trainer.timer`
9. Close the notification issue

**MCP trigger (Pattern B — push sentinel):**
```
mcp__github__get_file_contents + mcp__github__create_or_update_file
  path: .github/triggers/deploy-trainer-bootstrap
  message: "chore: trigger deploy-trainer-bootstrap"
  content: <base64("triggered: <timestamp>\n")>
  branch: main
  sha: <current SHA from get_file_contents>
```

**Default inputs** (applied when push-triggered): `trainer_vm_ip=158.178.209.121`,
`target_stage=shadow`, `close_issue=938`.

**Timeout:** 90 minutes (dataset build + training can take ~60 min).

**Secrets:** `VM_SSH_KEY`, OCI CLI set.

---

### Backtesting

#### `vwap-backtest.yml`

**Autonomy:** AUTONOMOUS — backtests on the trainer VM are fully autonomous.

**Trigger:** `issues.opened` (label `vwap-backtest-trigger` or title prefix
`[vwap-backtest]`), `workflow_dispatch`.

**Purpose:** SSHes into the **trainer VM**, fetches fresh 5m BTCUSDT data
from Bybit, runs the VWAP HTF-filter comparison backtest, posts results
as an issue comment. Runs on the trainer VM to avoid compute contention
on the live trader.

**Issue format:**
```
title: "[vwap-backtest] HTF filter comparison — BTCUSDT 5m"
body: |
  compare: true
  days: 365
  num_windows: 8
  window_days: 30
labels: ["vwap-backtest-trigger"]
```

All body keys are optional (all have defaults). Dispatch variant uploads
an artifact.

**MCP trigger (Pattern A):**
```
mcp__github__issue_write
  title: "[vwap-backtest] HTF comparison — latest data"
  body: "compare: true\ndays: 365\nnum_windows: 8\nwindow_days: 30"
  labels: ["vwap-backtest-trigger"]
```

**Secrets:** `VM_SSH_KEY`. **Variables:** `TRAINER_VM_SSH_HOST` (default
`158.178.209.121`).

---

### Documentation

#### `doc-audit-weekly.yml`

**Autonomy:** AUTONOMOUS — Claude may fire an on-demand audit at any time.

**Trigger:** Schedule Monday 12:00 UTC, `workflow_dispatch`, and
`issues.opened` (label `doc-audit-now`).

**Purpose:** Runs `scripts/ops/audit_verification_checklist.py` against
`docs/ARCHITECTURE-CANONICAL.md`. If any `[x]` line references a path
that no longer exists, files a new issue tagged `doc-drift`.

**MCP trigger (Pattern A, autonomous):**
```
mcp__github__issue_write
  title: "[doc-audit] on-demand architecture doc audit"
  body: "reason: Claude-initiated audit — checking for stale paths"
  labels: ["doc-audit-now"]
```

---

### OCI block storage

#### `oci-storage.yml`

**Autonomy:** OPERATOR-APPROVAL — provisions/modifies live trading VM storage.
Environment `production-oci` carries an approval gate.

**Trigger:** `workflow_dispatch` (inputs: `mode = dry-run | execute`).

**Purpose:** Provisions / re-checks the OCI block volume (`ict-bot-data-vol`)
for the live trading VM: create → attach → mkfs → mount → fstab → rsync
migrate → install systemd drop-ins → restart services → verify.

**MCP trigger:** None from sandbox. Operator clicks in Actions UI.

**Secrets:** `VM_SSH_PRIVATE_KEY` (env `production-oci`), OCI CLI set.

---

#### `oci-storage-verify.yml`

**Autonomy:** AUTONOMOUS — read-only storage health check.

**Trigger:** `workflow_dispatch`.

**Purpose:** SSHes the live VM, runs `scripts/verify_storage_setup.sh`,
posts the report to a labelled `oci-verify` issue and to the run summary.

**MCP trigger:** No Pattern A trigger exists. Operator dispatches from
Actions UI (or via CLI GitHub MCP after session restart).

**Note:** The 6-hourly `health-snapshot.yml` includes `=== STORAGE ===`
in its artifact, so a dedicated dispatch is only needed when responding
to a suspected mount regression.

**Secrets:** `VM_SSH_KEY`.

---

### Training / ML

#### `training-run.yml`

**Autonomy:** OPERATOR-APPROVAL — results inform Tier-3 strategy decisions.

**Trigger:** `push` to `claude/training-plan-*` (paths:
`experiments/*/hypotheses.py`, `scripts/training/**`, this file),
`workflow_dispatch`.

**Purpose:** Autonomous training run; commits results to
`claude/training-results-<run-id>`, opens draft `TRAINING-RESULTS:` PR,
fires Telegram ping.

**Secrets:** `VM_SSH_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`HF_TOKEN`.

---

#### `training-rerun-5m.yml`

**Autonomy:** OPERATOR-APPROVAL.

**Trigger:** `push` (paths in `experiments/2026-05-07-vwap-accuracy/`),
`workflow_dispatch`.

**Purpose:** Re-runs the VWAP-accuracy experiment at the production 5m
timeframe on a runner with live market-data egress.

---

#### `hf-cron.yml`

**Autonomy:** OPERATOR-APPROVAL.

**Trigger:** `workflow_dispatch` (daily cron disabled as of CP-2026-05-02-02).

**Purpose:** Manual one-shot HuggingFace AutoTrain run.

**MCP trigger:** None from sandbox. Operator dispatches from Actions UI.

**Secrets:** `HF_TOKEN`, `VM_SSH_KEY`.

---

### Session continuity

#### `continue-work.yml`

**Autonomy:** AUTONOMOUS.

**Trigger:** `workflow_dispatch`.

**Purpose:** Bounded sprint-continuation handoff: validates
`automation/session_handoff/next_session.json`, surfaces fields, appends
history, uploads artifact.

**MCP trigger:** No Pattern A trigger. Operator dispatches from Actions UI
(or CLI GitHub MCP after session restart).

---

## How Claude should use these workflows

**Reading live VM state:**
```
mcp__github__issue_write
  title: "[diag-request] snapshot?limit=200"
  labels: ["vm-diag-request"]
```
Full pattern: [`docs/claude/diag-relay.md`](claude/diag-relay.md).

**Reading trainer VM state (any command):**
```
mcp__github__issue_write
  title: "[trainer-diag] <description>"
  body: "cmd: <bash command>"
  labels: ["trainer-vm-diag-request"]
```
Full pattern: [`docs/claude/trainer-vm-mode.md`](claude/trainer-vm-mode.md) § 9.

**Restarting the web API (live VM, autonomous):**
```
mcp__github__issue_write
  title: "[vm-web-api-recover] web API down"
  labels: ["vm-web-api-recover"]
```

**Triggering a Tier-2 operator action (live VM, approval required):**
1. Confirm operator approval in conversation.
2. Open issue:
```
mcp__github__issue_write
  title: "[system-action] restart-bot-service"
  body: "action: restart-bot-service\nreason: <operator-approved reason>"
  labels: ["system-action"]
```

**Re-triggering trainer bootstrap (push sentinel):**
```
mcp__github__get_file_contents + mcp__github__create_or_update_file
  path: .github/triggers/deploy-trainer-bootstrap
  branch: main
```

**Running a VWAP backtest (trainer VM, autonomous):**
```
mcp__github__issue_write
  title: "[vwap-backtest] <description>"
  body: "compare: true\ndays: 365"
  labels: ["vwap-backtest-trigger"]
```

**Firing an on-demand doc audit (autonomous):**
```
mcp__github__issue_write
  title: "[doc-audit] on-demand"
  labels: ["doc-audit-now"]
```

**Adding a CI check:**
Edit/create a workflow under `.github/workflows/`. If it should be a
required status check, add its job ID to `REQUIRED_CONTEXTS` in
`branch-protection-sync.yml` in the same PR.

**Checking workflow output:**
The hosted GitHub MCP does **not** expose `download_artifact` or
`get_run_logs`. For autonomous workflows: consume results from the issue
comment (the workflow posts output there and closes the issue). For
operator-dispatched workflows with artifacts: ask the operator to
download + paste the artifact content.

---

## Modification policy

- Every change to a workflow under `.github/workflows/` must mention
  this doc in the PR body when it changes triggers, secrets, allowed
  actions, or tier classification.
- **New workflows must be listed in this catalogue before merge.**
- New issue-driven workflows must have their label added to `bootstrap-labels.yml`
  in the same PR.
- Removing or weakening a guard workflow (`dry-run-guard`, `env-gate-guard`,
  `silent-empty-guard`, `arch-doc-guard`) is Tier 3 and requires explicit
  operator approval.

---

## Required secrets quick reference

| Secret | Scope | Used by |
|---|---|---|
| `VM_SSH_KEY` | repo | All VM SSH workflows (live VM and trainer VM) |
| `VM_SSH_PRIVATE_KEY` | env `production-oci` | `oci-storage` mutating job only (env scope carries the approval gate) |
| `DIAG_READ_TOKEN` | repo | `vm-diag-snapshot`, post-action verification in `system-actions` |
| `BRANCH_PROTECTION_TOKEN` | repo | `branch-protection-sync` (PAT, fine-grained, `administration:write`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | repo | `dry-run-guard`, `env-gate-guard`, `silent-empty-guard`, `training-run` |
| `OCI_CLI_USER`, `OCI_CLI_FINGERPRINT`, `OCI_CLI_TENANCY`, `OCI_CLI_REGION`, `OCI_CLI_KEY_CONTENT` | repo | `vm-cloud-fix`, `oci-storage`, `provision-training-vm`, `provision-training-vm-auto-retry` |
| `HF_TOKEN` | repo | `hf-cron`, `training-run` |

## Optional repo variables

| Variable | Default | Used by |
|---|---|---|
| `VM_SSH_HOST` | `141.145.193.91` | Live VM SSH workflows |
| `VM_SSH_USER` | `ubuntu` | Live VM SSH workflows |
| `TRAINER_VM_IP` | `158.178.209.121` | `trainer-vm-diag.yml`, `deploy-trainer-bootstrap.yml` |
| `TRAINER_VM_USER` | `ubuntu` | `trainer-vm-diag.yml` |
| `TRAINER_VM_SSH_HOST` | `158.178.209.121` | `vwap-backtest.yml` |

## Complete workflow index (catalog completeness — BL-20260602-003)

The cheat-sheet above covers the day-to-day workflows; this index lists the
**remaining** workflow files so every `.github/workflows/*.yml` is named in this
doc (autonomy levels are the same scale as above — verify against the workflow
header before triggering a mutating one).

| Workflow | Category | Autonomy | Trigger | Purpose |
|---|---|---|---|---|
| `account-class-guard.yml` | CI guard | AUTO | PR | Fails PR when an accounts.yaml account is missing account_class. |
| `canonical-doc-coherence.yml` | CI guard | AUTO | PR/push | Mechanical guard against governance-doc drift (the doc-coherence 'teeth'). |
| `new-table-wiring-guard.yml` | CI guard | AUTO | PR | Fails+pings when a PR adds a persistent table without wiring it into the canonical store. |
| `writer-conformance-guard.yml` | CI guard | AUTO | PR | Fails+pings when a PR adds a journal writer that bypasses the canonical resolver/conformance. |
| `diag-relay-sweep.yml` | Ops relay | AUTONOMOUS | label `vm-diag-request` + cron | Sweeps stale, never-answered diag-relay issues (dropped-webhook backstop). |
| `arm-candidate-diag.yml` | Migration relay | AUTONOMOUS | label `arm-candidate-diag-request` | SSH diag/verification relay for the Ampere migration candidate VM. |
| `vm-bybit-diag.yml` | Ops relay | AUTONOMOUS | label `vm-bybit-diag-request` | One-shot bybit_2 ErrCode 10010 ('Unmatched IP') diagnostic. |
| `news-key-check.yml` | Ops relay | AUTONOMOUS | label `news-key-check` | Validate NEWS_API_KEY end-to-end through the bot's news path. |
| `prop-report.yml` | Ops relay (write) | OPERATOR-APPROVAL | label `prop-report` | Inbound Breakout prop fill/close report-back → POST /api/bot/prop/report (Tier-2). |
| `set-diag-token.yml` | Secret→VM | OPERATOR-APPROVAL | label `set-diag-token` | Push DIAG_READ_TOKEN secret onto the live VM + restart web-api. |
| `cancel-queued-runs.yml` | Repo housekeeping | AUTONOMOUS | label `cancel-queued-runs` / dispatch | Bulk-cancel stale `queued` runs that never got a runner. |
| `delete-merged-branches.yml` | Repo housekeeping | AUTONOMOUS | label `delete-merged-branches` | Stale merged-branch cleanup (web sessions can't delete branches directly). |
| `pr-opener.yml` | Control path | AUTONOMOUS | push-sentinel | Git-push-triggered PR opener — the MCP-independent PR-creation path. |
| `vm-driver.yml` | Control path | AUTONOMOUS | push-sentinel | Git-push-triggered remote driver — MCP-independent control path. |
| `ict-scalp-backtest.yml` | Backtest | AUTONOMOUS | label / dispatch | Self-contained ict_scalp_5m backtest workflow. |
| `provision-live-vm.yml` | VM provisioning | OPERATOR-APPROVAL | label / dispatch | Provision the CANDIDATE live-trader VM (Ampere 2 OCPU/12 GB). |
| `provision-gateway-vm.yml` | VM provisioning | OPERATOR-APPROVAL | label / dispatch | Provision the dedicated IB-Gateway VM (Ampere 1 OCPU/6 GB). |
| `provision-ib-gateway.yml` | VM provisioning | OPERATOR-APPROVAL | label / dispatch | Install/re-provision the headless IB Gateway (IBC). |
| `deploy-candidate.yml` | Migration | OPERATOR-APPROVAL | label / dispatch | Phase-2 migration: deploy the bot stack onto the Ampere candidate. |
| `cutover-live.yml` | Migration | OPERATOR-APPROVAL | label / dispatch | Cut the live trader over to the Ampere candidate VM. |
| `reserve-live-ip.yml` | Infra | OPERATOR-APPROVAL | label / dispatch | Make the live trader's public IP a reserved (static) IP. |
| `terminate-instance.yml` | Infra | OPERATOR-APPROVAL | label / dispatch | Terminate an OCI instance by display name (frees Always-Free budget). |
| `vm-resize-live.yml` | Infra | OPERATOR-APPROVAL | label / dispatch | Resize the live trader VM within the OCI Always-Free pool. |
| `vm-devnull-deploy-bootstrap.yml` | One-shot repair | OPERATOR-APPROVAL | label / dispatch | One-shot bootstrap to break the /dev/null auto-deploy chicken-and-egg. |
| `vm-fix-devnull.yml` | One-shot repair | OPERATOR-APPROVAL | label / dispatch | One-shot repair of a broken /dev/null on the live trader VM. |
| `vm-ib-gateway-deploy.yml` | IB-gateway ops | AUTONOMOUS | label / dispatch | Autonomous code deploy for the dedicated IB-Gateway VM. |
| `vm-ib-gateway-recover.yml` | IB-gateway ops | AUTONOMOUS | label `vm-ib-gateway-recover` | Autonomous self-heal (docker restart) for the IB Gateway container. |
| `vm-ib-gateway-selftest.yml` | IB-gateway ops | OPERATOR-APPROVAL | label / dispatch | Controlled end-to-end test of the reactive IB-gateway self-heal. |
| `vm-ib-gateway-stop.yml` | IB-gateway ops | OPERATOR-APPROVAL | label / dispatch | Decommission the IB Gateway container on the target host. |
| `vm-ib-gateway-watchdog-enable.yml` | IB-gateway ops | OPERATOR-APPROVAL | label / dispatch | Re-enable the IB-gateway watchdog (counterpart to vm-ib-gateway-stop). |
| `vm-git-credential-bootstrap.yml` | One-shot repair | AUTONOMOUS | label `vm-git-credential-bootstrap` / dispatch | One-shot bootstrap to break the git-fetch-auth chicken-and-egg after the repo went private (BL-20260706-GITSYNC-AUTH-BROKEN) — sets a global git credential on the VM from the outside so the existing (even pre-fix) deploy script's fetch starts authenticating immediately. Also doubles as the recovery step for a stale on-disk deploy script: after its own verification fetch, if the worktree reads behind `origin/main` it `git reset --hard origin/main` + runs `scripts/deploy_pull_restart.sh` directly, so a broken on-disk deploy script can't block itself from being fixed. Resolved + live-verified 2026-07-06. |
| `vm-ib-gateway-live-login-test.yml` | IB-gateway ops | OPERATOR-APPROVAL | label / dispatch | One-shot: verify LIVE IBKR login + 2FA without disturbing the running gateway. |
