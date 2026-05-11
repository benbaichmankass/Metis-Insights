# Training-center VM runbook (S-AI-WS9)

> **Authority:** [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) §
> "Two-VM topology"; [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md).
> **Trust contract:** [`docs/claude/trainer-vm-mode.md`](../claude/trainer-vm-mode.md) —
> every step in this runbook is **autonomous-Claude** under that
> charter unless explicitly noted otherwise. Claude provisions /
> enables / terminates the trainer without operator-in-the-loop.
> **Scope:** Provisioning, accessing, enabling, and tearing down
> the dedicated model-training VM. The live trader VM is OUT OF
> SCOPE — never run training workloads there, and never SSH into
> it from a trainer-scoped session. See
> [`docs/claude/vm-operator-mode.md`](../claude/vm-operator-mode.md)
> for the live-VM trust contract (restrictive, operator-gated for
> mutations).

## Why a separate VM

Live trading must remain deterministic and low-latency. Training
workloads (dataset builds, model fits, evaluator passes) are
bursty and memory-heavy. Co-locating them on the live VM risks
GIL contention, page-cache eviction, and OOM kills that nuke the
trader process. The Always Free Ampere A1 tier gives us 4 OCPU /
24 GB **per tenancy** — enough headroom for both the live trader
(1 OCPU / 6 GB) and the trainer (1 OCPU / 6 GB) with quota to
spare for a third side-car if needed.

The split is also the foundation of the [trainer-VM autonomous
authority](../claude/trainer-vm-mode.md): because the trainer has
no path to influence live trades on its own (the live VM only
loads models that the operator has wired into a strategy's
`shadow_model_ids` YAML field), Claude operates the trainer
without the live-VM trust contract's restrictions.

## What ships when you provision

The `provision-training-vm` workflow + cloud-init produces a VM
in the same OCI compartment + subnet as the live trader, with:

- **Display name:** `ict-trainer-vm` (override via workflow input).
- **Shape:** `VM.Standard.A1.Flex` — 1 OCPU / 6 GB Ampere A1.
- **Image:** latest Ubuntu 22.04 aarch64.
- **SSH:** authorized for the existing `VM_SSH_KEY` (same key as
  the live VM unless you've configured `TRAINER_VM_SSH_KEY`).
- **Tags:** `ict-role=training-center`, `ict-managed-by=provision_training_vm.py`,
  `ict-workstream=S-AI-WS9`.
- **Repo:** cloned read-only to `/home/ubuntu/ict-trading-bot`.
- **Packages:** python3.11, git, rsync, jq, curl.
- **Systemd:**
  - `ict-trainer.service` — installed, **disabled** by cloud-init.
    Claude enables it autonomously when the first training cycle
    is ready to run — see § "Enable training cycles" below.
  - `ict-trainer.timer` — installed, **disabled** by cloud-init.
    Same autonomous-enable pattern.
- **Marker:** `/etc/ict-trainer-vm.role` contains `training-center`
  so any future ops script (or Claude session) can `grep` it to
  distinguish trainer from live (per [`trainer-vm-mode.md` § 2](../claude/trainer-vm-mode.md#2-detection--how-claude-knows-its-targeting-the-trainer-vm)).

The VM is **idle** on first boot. The cloud-init bootstrap installs
the systemd units in a disabled state so a misconfigured manifest
can't OOM the host before Claude has a chance to inspect it.

## How to provision (autonomous-Claude)

Two paths. Both call the same workflow; both are idempotent (a
non-TERMINATED VM with the target display name short-circuits to
a `status=already_exists` result).

### Path A — workflow_dispatch (operator-driven)

1. GitHub → Actions → **provision-training-vm** → Run workflow.
2. Optional: override `display_name`. Default is `ict-trainer-vm`.
3. Click Run.
4. Wait ~2–5 minutes. The job summary at the bottom of the run
   prints the public IP + instance OCID.

Use this path only when a human is already at the GitHub UI; Path
B is faster end-to-end for Claude-driven provisioning.

### Path B — issue-driven (autonomous-Claude default)

Claude opens a new issue with the label `provision-training-vm`
and body:

```
confirm: yes
reason: <one-line audit trail>
```

The `confirm: yes` line is preserved as an audit-trail formality —
Claude includes it autonomously in the issue body it authors (no
human action required). The workflow fires on `issues.opened`
when the label is attached at creation time, runs to completion,
posts the result as an issue comment, and closes the issue.

If the issue is opened without the label attached at the moment
of the `issues.opened` event, the workflow won't fire (it filters
on the label in the job's `if:` condition). Add the label at
creation, not after — adding it after means re-opening or
re-filing.

## Quota guardrail

The script refuses to provision if adding the new VM would push
total OCPU above the 4-OCPU Always Free ceiling. You'll see
`status=quota_would_exceed` in the result. To resolve:

- Terminate an unused VM in the compartment (autonomous: Claude
  can `oci compute instance terminate` against the trainer family;
  for non-trainer VMs, route through the operator).
- OR override the shape to a paid one (requires editing
  `scripts/ops/provision_training_vm.py::DEFAULT_SHAPE` — out of
  scope for the Always Free path).

## How to SSH in (autonomous-Claude)

Same private key as the live VM:

```bash
ssh -i ~/.ssh/ict-bot-ovm-private.key ubuntu@<public-ip-from-result>
```

Claude can SSH into the trainer freely. SSH into the **live** VM
remains forbidden from trainer-scoped sessions per the trust
contract — read-only `/api/diag/*` is the only allowed cross-VM
read path.

The cloud-init bootstrap log is at `/var/log/cloud-init-output.log`
on the trainer VM; tail it to confirm the bootstrap completed
cleanly.

## Verify the bootstrap (autonomous-Claude)

```bash
# On the trainer VM:
cat /etc/ict-trainer-vm.role          # → training-center
ls /home/ubuntu/ict-trading-bot       # repo is present
systemctl status ict-trainer.service  # → loaded, inactive (DISABLED)
python3.11 --version                  # → 3.11.x
```

If any of these fail, check `/var/log/cloud-init-output.log` for
the apt/git error and re-trigger the workflow after fixing
(the script is idempotent; it'll skip because the instance
already exists, so you'll need to terminate first OR SSH in and
fix manually — both autonomous-Claude under the trainer charter).

## Enable training cycles (autonomous-Claude)

The trainer service is installed but disabled by cloud-init.
Claude enables it autonomously once the first training cycle is
ready to run:

```bash
# On the trainer VM:
sudo systemctl enable --now ict-trainer.service
# For periodic runs, also enable the timer:
sudo systemctl enable --now ict-trainer.timer
```

`scripts/ops/run_training_cycle.sh` (the unit's `ExecStart`) ships
on `main` as of PR #793 (S-AI-WS9-FU). Starting the unit before
that script was on `main` failed loudly — that gate is now closed.

The training cycle pulls `main`, builds a venv if needed, iterates
the manifests under `ml/configs/` (or `TRAINING_MANIFESTS` if set),
emits JSONL events to `runtime_logs/training_cycle.jsonl`, and
short-circuits on the first manifest failure with overall_rc=1.

## Cross-VM data sync (autonomous-Claude, read-only)

The trainer needs read access to the live VM's `trade_journal.db`
for label feedstock. Two options, both autonomous-Claude because
both are read-only against the live VM:

1. **Rsync from live → trainer**, scheduled on the trainer VM (a
   cron on the trainer pulls from the live VM every N minutes).
   This is the Claude-preferred path — keeps the live VM
   unaware of trainer concerns.
2. **Diag API over HTTPS** — the trainer queries
   `/api/diag/journal?table=trades&limit=N` against the live VM's
   FastAPI surface (already exists; token-gated via
   `DIAG_READ_TOKEN`).

Both produce JSONL audit rows at
`runtime_logs/trainer/db_pulls.jsonl` per the trust contract
§ 3.b. Either is fine; Claude picks based on the volume of data
needed (rsync for full DB; diag API for small windows).

## How to tear down (autonomous-Claude)

`provision-training-vm.yml` does NOT teardown. To remove:

1. OCI Console (operator path) — Compute → Instances → Find
   `ict-trainer-vm` → More Actions → Terminate (check "Permanently
   delete the attached boot volume" to free quota).
2. Programmatic (Claude path) — invoke `oci compute instance
   terminate --instance-id <ocid> --preserve-boot-volume false`
   using the same OCI secrets the provision workflow uses. Log
   the action to `runtime_logs/trainer/teardowns.jsonl`.

A teardown workflow that wraps option 2 is filed as a follow-up;
until it lands, Claude executes the OCI CLI call from a
short-lived workflow_dispatch or directly via the OCI Python SDK.

## Troubleshooting

### `status=service_error` with `code=NotAuthorizedOrNotFound`

The OCI API key user lacks the IAM policy to launch instances in
the compartment. Required policy verbs:

```
Allow group <ops-group> to manage instance-family in compartment <X>
Allow group <ops-group> to use virtual-network-family in compartment <X>
```

Operator-side fix — Claude cannot edit IAM policies.

### `status=quota_would_exceed`

You've already used your Always Free OCPU budget. Terminate an
unused VM first (autonomous-Claude if it's a trainer-family VM;
operator if it's anything else).

### Cloud-init failure (VM up, but `/etc/ict-trainer-vm.role` missing)

SSH in (autonomous-Claude) and check
`/var/log/cloud-init-output.log`. Common failures: apt-mirror flake
(re-run the affected `apt install` manually), git-clone HTTP timeout
(network blip; clone manually).

### SSH connection refused

OCI Security List for the subnet doesn't include TCP/22 ingress.
Use the existing `vm-cloud-fix.yml` pattern (or run it directly
with `--port 22`) to add the rule — autonomous-Claude.

## Related runbooks

- [`docs/runbooks/health-check.md`](health-check.md) — live VM
  health snapshot (does NOT apply to the trainer VM; uses the
  live-VM trust contract).
- [`docs/runbooks/strategy-testing.md`](strategy-testing.md) — M5
  backtest pipeline (runs on the live VM today; can migrate to
  the trainer in a follow-up — that migration is autonomous-Claude
  per the charter).
- [`docs/claude/trainer-vm-mode.md`](../claude/trainer-vm-mode.md) —
  trainer VM trust contract (autonomous-Claude, this runbook's authority).
- [`docs/claude/vm-operator-mode.md`](../claude/vm-operator-mode.md) —
  live VM trust contract (restrictive; cross-VM boundary lives here).
