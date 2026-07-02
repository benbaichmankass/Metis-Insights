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
authority](../claude/trainer-vm-mode.md): the trainer has no path
to live order influence on its own. As of the 2026-05-19 shadow-
default flip the gate is the **stage**, not the YAML — models at
`shadow` auto-wire and log predictions without changing order
decisions; `advisory` and higher stages are the only ones that
influence orders, and the `shadow → advisory` promotion is
operator-approved.

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

If any of these fail, Claude checks `/var/log/cloud-init-output.log`
via the `trainer-vm-diag` relay and either re-triggers the workflow
after termination (idempotent provisioning) or fires a follow-up diag
`cmd:` to fix in place. Both paths are autonomous under the trainer
charter — no operator involvement.

## First action — WS5 baseline kickoff (autonomous-Claude)

After verifying the bootstrap, the very first thing Claude does is
train every WS5 baseline manifest and promote each to `shadow` in
the model registry. Until something is at `shadow` or higher, the
WS7 factory's `LIVE_INFLUENCE_STAGES` gate refuses to load it, so
this step is the unblock for the shadow harness.

Single command:

```bash
# On the trainer VM, as ubuntu:
bash scripts/ops/train_and_register_ws5_baselines.sh
```

What it does (per JSONL audit row in
`runtime_logs/trainer/ws5_baseline_kickoff.jsonl`):

1. For each `ml/configs/baseline-*.yaml`:
   1. Runs `python -m ml train` (auto-registers at
      `research_only`, which aliases to `candidate` on read via
      `ml.manifest.canonical_stage`).
   2. Walks the promotion ladder up to `shadow` by calling
      `python -m ml promote <model_id> <next_stage> --by claude-trainer
      --reason "..." --gates-acknowledged` once per step.
2. Emits a JSONL `manifest_done` event with the final model_id +
   stage, then advances to the next manifest.
3. Short-circuits on the first training or promotion failure
   (overall_rc=1).

**Knobs (env vars):**

- `TARGET_STAGE` — defaults to `shadow`. Override to push further
  (e.g. `advisory`). Per the trainer charter § 3.a, Claude is
  autonomous up to `shadow` (may write the registry up to `advisory`);
  the `shadow → advisory` transition is the operator gate. The
  `shadow → advisory` promotion additionally requires a sprint-log
  entry under `docs/sprint-logs/S-AI-WS5-PROMOTION-*` per § 3.b — this
  script doesn't write that log; Claude writes it in the same PR that
  bumps `TARGET_STAGE`. (Ladder collapsed 7→3 on 2026-06-16; canonical
  `candidate → shadow → advisory`; legacy `limited_live`/`live_approved`
  alias to `advisory`.)
- `PROMOTION_BY` — defaults to `claude-trainer`. Recorded on every
  `StatusEvent` row the registry appends.
- `PROMOTION_REASON` — defaults to a structured boilerplate citing
  the charter. Override to attach a specific sprint or session id.
- `MANIFESTS` — defaults to every `baseline-*.yaml` under
  `ml/configs/`. Space-separated override to target a subset.
- `LOG_PATH`, `REPO_ROOT`, `VENV_DIR`, `DATASETS_ROOT`,
  `EXPERIMENTS_ROOT`, `REGISTRY_ROOT` — paths, see the script's
  header docstring for defaults.

**Idempotency note:** every run appends a new `RunRecord` to each
manifest's existing entry (one entry per `model_id`, runs are
accumulated). Don't worry about re-running by accident — the
registry won't grow N×9 rows; you just get more runs under the same
9 (or fewer) model_ids, with the newest run's metrics surfaced at
the top level.

After this completes, run `python -m ml list-models` (no `--status`
filter; that flag is for the legacy WS4 status enum) to see the
registered set. As of the 2026-05-19 shadow-default flip every
registered model is automatically picked up by the auto-wire path
on the live VM — no `shadow_model_ids` YAML edit required to start
logging. Use `shadow_model_ids: [...]` only to pin specific models
or to opt a strategy out entirely.

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
and emits JSONL events to `runtime_logs/training_cycle.jsonl`. A
manifest failure is logged (`manifest_failed`, `overall_rc=1`) and the
loop continues to the next manifest rather than aborting the cycle —
so one bad manifest costs one skip, not the rest of the fleet.

**Checkpoint/resume (2026-07-02, BL-20260702-TRAINER-OOM):** each
invocation reads/writes `runtime_logs/trainer/cycle_progress_<UTC-date>.json`
and skips any manifest already `done`/`skipped` today, so a same-day
re-run (either a manual retry or the `ict-trainer-catchup.timer` at
`05:00 UTC`) resumes from where the prior run stopped instead of
retraining everything. `ict-trainer.service` also now caps memory
(`MemoryHigh=4G`/`MemoryMax=5G`, `OOMPolicy=continue`) so a single
manifest's OOM no longer takes the whole cycle down. Full design:
[`docs/ml/training-center.md`](../ml/training-center.md#checkpointresume--catch-up-timer-2026-07-02-bl-20260702-trainer-oom).

The recurring cycle stops at `research_only` (which aliases to
`candidate` on read via `ml.manifest.canonical_stage`) — that's the
source of fresh candidates over time. The "First action" bootstrap
above is what walks each one up to `shadow` so the harness can load
them; the recurring cycle then keeps producing more candidates the
operator can promote (or that a future "auto-promote on metric
threshold" follow-up can promote autonomously).

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

Claude fires `trainer-vm-diag` with `cmd: cat /var/log/cloud-init-output.log`
to read the failure. Common failures: apt-mirror flake (fix with a
follow-up diag `cmd: sudo apt-get install -y <pkg>`), git-clone HTTP
timeout (network blip; retry via diag `cmd:` with the clone). All
recovery paths are autonomous via the diag relay.

### SSH connection refused

OCI Security List for the subnet doesn't include TCP/22 ingress.
Use the existing `vm-cloud-fix.yml` pattern (or run it directly
with `--port 22`) to add the rule — autonomous-Claude.

### Bootstrap script: `manifest_failed` at `phase: train`

`python -m ml train` exited non-zero on a baseline manifest. Most
common cause: `DATASETS_ROOT` is empty — the trainer assumes the
dataset has been built first. Run the dataset build commands from
each baseline's manifest comments, or run `python -m ml build-dataset
<family>` for the families the manifest references, then re-run
the bootstrap.

### Bootstrap script: `promote_failed` with `gates: ...`

A specific promotion transition has additional gates that
`--gates-acknowledged` couldn't bypass. Check
`ml/promotion.py::gates_for` for the transition; some transitions
(typically `candidate → shadow` and beyond; the legacy
`backtest_approved` aliases to `candidate`) require a metric threshold
proof to be documented in the reason. Re-run with
`PROMOTION_REASON` updated to include the proof, or lower
`TARGET_STAGE` to a stage that doesn't require the gate.

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
