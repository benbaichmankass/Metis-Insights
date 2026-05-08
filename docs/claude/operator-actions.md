# Operator actions — PM-side mutating bridge

> Companion to `docs/claude/vm-operator-mode.md` (VM-resident `/vm`
> tiers) and `docs/claude/diag-relay.md` (PM-side **read-only**
> bridge). This doc covers the third axis: a **narrow, allowlisted
> set of mutating actions** a PM-side / web-sandbox session can drive
> on the VM via the GitHub Actions workflow `operator-actions`.

---

## 1. Why this exists

Before this surface, a PM-side session (web sandbox, dev laptop) had
two mutually exclusive options when something went wrong on the VM:

1. **Read-only diag** (`/api/diag/*` via `vm-diag-snapshot.yml`) — fine
   for diagnosis, useless for recovery.
2. **Wake the operator on Telegram** and ask them to SSH in — the
   only mutation path, but adds human latency to every recovery.

`operator-actions.yml` is the small, audited middle path: a fixed
allowlist of recovery / hygiene actions that don't touch strategy
behaviour, with pre/post verification baked into the workflow.

It does **not** replace the operator. Tier-2 actions (anything that
restarts a live runtime or reboots the box) still require the
operator to either click "Run workflow" themselves, or to approve
Claude's request and fire the dispatch on Claude's behalf — see § 4.

---

## 2. Allowlist

Exactly four actions. Adding a fifth requires a PR that updates this
doc, the workflow's `inputs.action.options` list, and the wrapper
mapping in both `operator-actions.yml` and the test in
`tests/ops/test_operator_actions_workflow.py`.

| Action | Tier | Wrapper | Mutates? |
|---|---|---|---|
| `status-check` | 1 | `scripts/ops/status_check.sh` | no |
| `pull-latest-logs` | 1 | `scripts/ops/pull_logs.sh` | no |
| `restart-bot-service` | 2 | `scripts/ops/restart_bot.sh` | systemd unit only |
| `reboot-vm` | 2 (last resort) | `scripts/ops/reboot_vm.sh` | full host |

**Docker is intentionally absent.** The repo's canonical runtime is
systemd (`deploy/*.service` units installed via
`scripts/install_systemd_units.sh`). The root-level `Dockerfile`
predates the systemd switch and is not part of the live deploy. If
Docker ever becomes canonical, add `restart-docker-stack` here and
to the workflow at the same time.

---

## 3. Tier policy (PM-side dispatch)

Mirrors the existing `docs/claude/operating-protocol.md` decision
tiers but applied to *workflow dispatch* rather than PR merge.

### Tier 1 — autonomous

Claude may dispatch these without operator approval:

- `status-check`
- `pull-latest-logs`

Pre-conditions: none beyond the standard "session has a clear reason
to run it" (a flagged issue, a CI failure on `vm-diag-snapshot`,
operator request, scheduled health check). The wrapper itself is
read-only.

Post-action: Claude reads the artifact, summarises in the relevant
issue / PR / Telegram thread, then stops.

### Tier 2 — ping the operator first

Claude **must not** dispatch these without an operator ack. The ack
flow is:

1. Claude opens an issue (or appends to an open ping thread) using
   the message format in § 7.
2. Operator replies "Approve" (or clicks the action workflow's "Run
   workflow" button themselves — equivalent intent).
3. Either Claude (if a future MCP tool grants `workflow_dispatch`)
   or the operator triggers the workflow with the agreed `action` +
   `reason`.

Tier-2 actions:

- `restart-bot-service`
- `reboot-vm`

Why ping first: even though the action itself is narrowly scoped,
the *blast radius* of restarting the live trader (open positions
held by the trader process, in-flight orders) is not provable from
inside the workflow. The operator owns that judgement.

### Tier 3 — never via this workflow

Out of scope for `operator-actions` regardless of approval:

- Strategy parameter changes (`config/strategies.yaml`)
- Risk caps (`src/runtime/risk_counters.py`, `config/risk_caps.yaml`)
- Per-account dry-run → live promotion (`config/accounts.yaml`)
- Live order code (`src/runtime/orders.py`)
- Anthropic / exchange / Telegram key rotation
- Disabling/masking `ict-trader-live.service` (stopping is Tier-2 in
  the VM-runner protocol; **disabling/masking is Tier 3** there too)

If you want any of these, you do not want this workflow. Open a PR.

---

## 4. Reboot is last resort

The reboot doctrine is explicit because the cost of a wrong reboot
is the highest of any action here:

1. **Try `status-check` first** to confirm the failure mode.
2. **Try `restart-bot-service` next** if the failure is contained
   to the trader process.
3. **Only escalate to `reboot-vm`** when:
   - the trader unit refuses to come back after restart, AND
   - the failure pattern indicates a host-level issue (kernel log
     errors, network stack unresponsive, `systemd-tmpfiles` disk
     pressure, OOM-killer thrashing), AND
   - the operator has acked the Tier-2 ping for `reboot-vm`.

Why: a reboot drops every SSH session, kills any in-flight `/vm`
runner mid-execution, and depends on systemd auto-start to bring
all services back cleanly. If a unit's `[Install]` section is wrong
or a dependency loops, recovery requires manual Oracle Cloud
Console intervention — which the PM-side session cannot drive. See
`docs/audit/sprint-013-deployment-runbook.md`.

The wrapper uses `shutdown -r +1` (1 min delay) rather than
`reboot` (immediate). The minute-of-grace lets the operator abort
with `sudo shutdown -c` if something looks wrong in the log
preview that streams while the workflow is running.

---

## 5. Audit trail

Every workflow run produces:

1. **An artifact** (`operator-action-<action>-<run_id>.zip`)
   containing:
   - `audit-bundle.json` — structured: action, reason, tier, exit
     code, pre-state, post-state, output excerpt
   - `pre-state.json` — the diag `/api/diag/status` bundle from
     before the action (or `diag_skipped` / `diag_unreachable`)
   - `post-state.json` — same, after the action
   - `action-output.txt` — full stdout/stderr of the wrapper
2. **A run-log preview** in the workflow's "Execute action wrapper"
   step (capped at 4 KB).
3. **A repo-side audit record** at
   `runtime_logs/operator_actions/<utc-ts>-<action>.json` written by
   the wrapper itself. Picked up by the next `ict-git-sync` cycle
   and visible to PM-side sessions via the diag relay's
   `log_file?name=…` route (file alias to be added if frequent
   inspection is needed; today the file is fetchable via the
   workflow artifact route end-to-end).

Retention: GitHub artifact retention is 30 days. Repo-side
`runtime_logs/operator_actions/*.json` records are retained
indefinitely (they are tiny — < 1 KB each).

---

## 6. Verification matrix

| Action | Pre-check | Action | Post-check | Failure behaviour |
|---|---|---|---|---|
| `status-check` | none | `systemctl is-active` for canonical units + heartbeat age + audit tail | wrapper exits 0 if all canonical units active, 1 otherwise | exit 1 = at least one unit not `active`; investigate before any restart |
| `pull-latest-logs` | none | dump journalctl + signal_audit + status.json | wrapper exits 0 if all readable | exit 1 = log paths missing → investigate diag relay first |
| `restart-bot-service` | capture pre-state via `is-active` + `status` | `systemctl restart ict-trader-live.service` | poll `is-active` until "active" or 30 s timeout; dump 30 journal lines | exit 1 → unit failed to come back; ping operator with journal tail |
| `reboot-vm` | dump uptime + canonical unit states + 10 journal lines | `shutdown -r +1` | workflow polls SSH for ≤ 5 min; post-fetch `/api/diag/status` | SSH not back in 5 min → manual recovery required (Oracle Cloud Console) |

The `restart-bot-service` wrapper additionally **defers** if any
`claude-vm-runner@*.service` unit is currently active, mirroring the
guard in `scripts/deploy_pull_restart.sh` — exit 3, no restart
attempted. Re-dispatch the action a few minutes later when the `/vm`
invocation has finished.

---

## 7. Operator ping format (Tier 2)

Short, decision-oriented. Paste into the issue or Telegram thread
when requesting approval for a Tier-2 action.

```
Action requested: restart-bot-service
Why needed: <one sentence — what symptom triggered this>
Risk if not done: <one sentence — what breaks if we hold>
Expected impact: <one sentence — what changes when this runs>
Verification plan: <one line — what artifact / diag call confirms success>
[Approve] [Hold]
```

For `reboot-vm` add a fifth line:

```
Lower-blast-radius alternatives tried: <list, e.g. "restart-bot-service x1, no recovery">
```

---

## 8. Runner architecture (control-plane choice)

The workflow runs on `runs-on: ubuntu-latest` (GitHub-hosted) and
SSHs to the VM. This is **deliberate**.

**Why not self-hosted runner on the VM?**

- A self-hosted runner sharing the VM would orchestrate its own
  reboot. The runner process dies as the VM goes down; the workflow
  step that called `shutdown` returns nonzero; the post-reboot
  reconnect step is on a runner that may not be available again
  until well after the workflow times out. Recovery is ambiguous.
- The control-plane / data-plane separation keeps the question
  "did the workflow succeed?" answerable independently of "is the
  VM healthy?". For `reboot-vm` and `restart-bot-service` that
  separation is the whole point.

**Why not GitHub Actions matrix or Codespaces?**

- Overkill for a single-target, single-action workflow.
- Costs more in minutes than the SSH path.

**Why fixed-form SSH instead of `appleboy/ssh-action`?**

- Smaller dependency surface to audit. The diag-relay workflow set
  the precedent and it has been reliable; this workflow follows the
  same shape so reviewers don't need to re-evaluate.

---

## 9. Required GitHub repo configuration

All already in place except the optional reboot sudoers entry.

### Secrets (Settings → Secrets and variables → Actions → Secrets)

| Name | Used by | Required? |
|---|---|---|
| `VM_SSH_KEY` | this workflow + `vm-diag-snapshot` | yes |
| `DIAG_READ_TOKEN` | pre/post `/api/diag/status` verification | yes (else verification skipped) |

### Variables (Settings → Secrets and variables → Actions → Variables)

| Name | Default | Override when |
|---|---|---|
| `VM_SSH_HOST` | `158.178.210.252` | VM moved |
| `VM_SSH_USER` | `ubuntu` | VM user changed |

---

## 10. VM sudoers setup (one-time, manual)

`restart-bot-service` works today: `ubuntu` already has
`NOPASSWD: /bin/systemctl` from the existing deploy flow.

`reboot-vm` requires one additional sudoers entry. Edit
`/etc/sudoers.d/ict-operator-actions` (create if missing) on the VM,
mode `0440`, owner `root:root`, contents:

```
# operator-actions reboot path — see docs/claude/operator-actions.md § 10
ubuntu ALL=(ALL) NOPASSWD: /sbin/shutdown -r *
```

Validate with `sudo -n /sbin/shutdown -r --help` as `ubuntu`. Until
this entry exists, `reboot-vm` will exit 1 with a clear error — it
will not silently do nothing.

---

## 11. What this surface deliberately is *not*

- Not a general remote-shell. There is no command-string input.
- Not a code-deploy path. `git fetch` + `systemctl restart` is the
  job of the existing `ict-git-sync.timer` + `deploy_pull_restart.sh`
  flow. Don't conflate the two — the next sprint that wants to
  trigger a deploy from a workflow should write a *separate*
  workflow with its own gates.
- Not a strategy or risk-config pathway. Anything that mutates
  trading behaviour goes through a PR, period.
- Not a replacement for the Telegram `/vm` dispatcher. That path
  remains the way the operator triggers freeform agentic VM work.
  Operator-actions is the **inverse**: a PM-side session triggering
  *only* a fixed action.

---

## 12. Cross-references

- `docs/claude/vm-operator-mode.md` § 9 — PM-side read-only diag
  contract (the bridge that **predates** this one and shares the
  same SSH wiring).
- `docs/claude/diag-relay.md` — full operator + session flow for
  the read-only relay; shape mirrors the operator-actions flow on
  the request side.
- `docs/claude/operating-protocol.md` § 4 — merge-authority tiers
  (the *PR* tiers; this doc is the *dispatch* tiers, distinct).
- `scripts/deploy_pull_restart.sh` — canonical deploy flow; the
  `claude-vm-runner` defer guard there is mirrored here.
- `.github/workflows/operator-actions.yml` — the workflow itself.
- `scripts/ops/*.sh` — wrapper scripts (one per action).
- `tests/ops/` — workflow + script validation.
