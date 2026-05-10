# Training-center VM runbook (S-AI-WS9)

> **Authority:** [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) §
> "Two-VM topology"; [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md).
> **Scope:** Provisioning, accessing, enabling, and tearing down
> the dedicated model-training VM. The live trader VM is OUT OF
> SCOPE — never run training workloads there. See
> [`docs/claude/vm-operator-mode.md`](../claude/vm-operator-mode.md)
> for the live-VM trust contract.

## Why a separate VM

Live trading must remain deterministic and low-latency. Training
workloads (dataset builds, model fits, evaluator passes) are
bursty and memory-heavy. Co-locating them on the live VM risks
GIL contention, page-cache eviction, and OOM kills that nuke the
trader process. The Always Free Ampere A1 tier gives us 4 OCPU /
24 GB **per tenancy** — enough headroom for both the live trader
(1 OCPU / 6 GB) and the trainer (1 OCPU / 6 GB) with quota to
spare for a third side-car if needed.

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
  - `ict-trainer.service` — installed, **disabled**.
  - `ict-trainer.timer` — installed, **disabled**.
- **Marker:** `/etc/ict-trainer-vm.role` contains `training-center`
  so any future ops script can `grep` it to distinguish the VMs.

The VM is **idle** on first boot. No training runs until you
explicitly enable the unit (see § Enable training cycles below).

## How to provision

Two paths. Both call the same workflow; both are idempotent (a
non-TERMINATED VM with the target display name short-circuits to
a `status=already_exists` result).

### Path A — workflow_dispatch (recommended for first run)

1. GitHub → Actions → **provision-training-vm** → Run workflow.
2. Optional: override `display_name`. Default is `ict-trainer-vm`.
3. Click Run.
4. Wait ~2–5 minutes. The job summary at the bottom of the run
   prints the public IP + instance OCID.

### Path B — issue-driven (for Claude sessions)

Create a new issue with the label `provision-training-vm` and
body:

```
confirm: yes
reason: <one-line audit trail>
```

The workflow fires automatically, runs to completion, posts the
result as an issue comment, and closes the issue. Claude
sessions use this path because they can't trigger
`workflow_dispatch` directly.

## Quota guardrail

The script refuses to provision if adding the new VM would push
total OCPU above the 4-OCPU Always Free ceiling. You'll see
`status=quota_would_exceed` in the result. To resolve:

- Terminate an unused VM in the compartment (OCI Console →
  Compute → Instances → Terminate).
- OR override the shape to a paid one (requires editing
  `scripts/ops/provision_training_vm.py::DEFAULT_SHAPE` — out of
  scope for the Always Free path).

## How to SSH in

Same private key as the live VM:

```bash
ssh -i ~/.ssh/ict-bot-ovm-private.key ubuntu@<public-ip-from-result>
```

If your local key path differs, adjust. The cloud-init bootstrap
log is at `/var/log/cloud-init-output.log` on the VM; tail it to
confirm the bootstrap completed cleanly.

## Verify the bootstrap

```bash
# On the VM:
cat /etc/ict-trainer-vm.role          # → training-center
ls /home/ubuntu/ict-trading-bot       # repo is present
systemctl status ict-trainer.service  # → loaded, inactive (DISABLED)
python3.11 --version                  # → 3.11.x
```

If any of these fail, check `/var/log/cloud-init-output.log` for
the apt/git error and re-trigger the workflow after fixing
(the script is idempotent; it'll skip because the instance
already exists, so you'll need to terminate first OR SSH in and
fix manually).

## Enable training cycles (OPTIONAL — operator opts in)

The trainer service is installed but disabled. To enable:

```bash
# On the VM:
sudo systemctl enable --now ict-trainer.service
# For periodic runs, also enable the timer:
sudo systemctl enable --now ict-trainer.timer
```

**Note:** at the time of writing, `scripts/ops/run_training_cycle.sh`
(the unit's `ExecStart`) does not yet exist on `main`. Starting the
unit before that script lands will fail loudly — that's the
intended safety. Wait for the WS9 follow-up that ships the
training cycle script.

## Cross-VM data sync (FILED — not yet wired)

The trainer needs read access to the live VM's `trade_journal.db`
for label feedstock. Two options, neither shipped yet:

1. **Rsync from live → trainer**, scheduled on the live VM
   (gives the trainer a read-only snapshot every N minutes).
2. **Diag API over HTTPS** — the trainer queries
   `/api/diag/journal?table=trades&limit=N` against the live VM's
   FastAPI surface (already exists; token-gated).

Decision is operator's. Until decided, copy the DB manually for
the first training pass:

```bash
# From your laptop:
scp -i ~/.ssh/ict-bot-ovm-private.key \
    ubuntu@<live-ip>:/home/ubuntu/ict-trading-bot/trade_journal.db \
    /tmp/trade_journal.db
scp -i ~/.ssh/ict-bot-ovm-private.key \
    /tmp/trade_journal.db \
    ubuntu@<trainer-ip>:/home/ubuntu/ict-trading-bot/
```

## How to tear down

`provision-training-vm.yml` does NOT teardown. To remove:

1. OCI Console → Compute → Instances.
2. Find `ict-trainer-vm`.
3. More Actions → Terminate. Check "Permanently delete the
   attached boot volume" to free quota.

A teardown workflow is filed as a follow-up but not yet shipped —
provisioning + teardown are different blast-radius decisions,
worth manual operator action for now.

## Troubleshooting

### `status=service_error` with `code=NotAuthorizedOrNotFound`

The OCI API key user lacks the IAM policy to launch instances in
the compartment. Required policy verbs:

```
Allow group <ops-group> to manage instance-family in compartment <X>
Allow group <ops-group> to use virtual-network-family in compartment <X>
```

### `status=quota_would_exceed`

You've already used your Always Free OCPU budget. Terminate an
unused VM first.

### Cloud-init failure (VM up, but `/etc/ict-trainer-vm.role` missing)

SSH in and check `/var/log/cloud-init-output.log`. Common
failures: apt-mirror flake (re-run the affected `apt install`
manually), git-clone HTTP timeout (network blip; clone manually).

### SSH connection refused

OCI Security List for the subnet doesn't include TCP/22 ingress.
Use the existing `vm-cloud-fix.yml` pattern (or run it directly
with `--port 22`) to add the rule.

## Related runbooks

- [`docs/runbooks/health-check.md`](health-check.md) — live VM
  health snapshot (does NOT apply to the trainer VM).
- [`docs/runbooks/strategy-testing.md`](strategy-testing.md) — M5
  backtest pipeline (runs on the live VM today; can migrate to
  the trainer in a follow-up).
- [`docs/claude/vm-operator-mode.md`](../claude/vm-operator-mode.md) —
  live VM trust contract.
