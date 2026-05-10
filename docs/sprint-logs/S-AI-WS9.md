# S-AI-WS9 — Training-center VM provisioning + two-VM topology

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws9-runtime-split.md`](../sprint-plans/ai-traders/ws9-runtime-split.md)
**Status:** ✅ COMPLETE (provisioning plumbing; operator triggers the run)

## Goal

Make the "no heavy training on the Oracle live VM" non-negotiable
enforced by **topology**, not just policy. Ship the provisioning
infrastructure for a dedicated training-center VM: Always Free
Ampere A1, same OCI compartment + subnet as the live trader,
SSH-key-shared with the live VM for operator convenience, idle
on first boot so the Always Free quota isn't consumed by an
empty trainer.

## Decisions

- **Always Free Ampere A1, 1 OCPU / 6 GB.** Fits inside the
  4-OCPU tenancy ceiling alongside the live trader; matches the
  live trader's likely shape; gives the ML stack room to breathe.
  E2.1.Micro (1 OCPU / 1 GB) was the alternative — rejected
  because the `oci` SDK + pandas + ml deps want more memory.
- **Same compartment + subnet as the live trader.** Cross-VM
  rsync / SSH / future replication work assumes intra-VCN
  reachability. Operator can move to a dedicated subnet later;
  not needed for first ship.
- **Shared SSH key with the live trader.** Operator's call. Single
  private to rotate; one set of `authorized_keys`. The workflow
  reads `VM_SSH_KEY` (the live VM's private), runs
  `ssh-keygen -y -f` to derive the public, injects it into the
  trainer's `authorized_keys` via OCI instance metadata. A
  `TRAINER_VM_SSH_KEY` override is filed for future key
  separation if the threat model demands it.
- **Idempotent provisioning.** The script refuses to create a
  duplicate: a non-TERMINATED instance with the target
  `display_name` short-circuits to a `status=already_exists`
  result, exit 0. Re-running the workflow is a no-op.
- **Quota guardrail.** Before launching, the script sums OCPUs
  across all non-TERMINATED instances in the compartment. If the
  total + requested OCPU would exceed `ALWAYS_FREE_OCPU_QUOTA` (4),
  it emits `status=quota_would_exceed` and exits 0. Advisory, not
  hard-fail — the operator sees the result on the issue comment.
- **Cloud-init installs but doesn't start `ict-trainer.service`.**
  The systemd unit + timer ship to `/etc/systemd/system/` and
  `systemctl daemon-reload` runs, but neither is `enable`d or
  `start`ed. The operator opts in to training cycles explicitly.
  Reasoning: an idle Always Free VM costs zero. A trainer fighting
  the live VM for shared resources costs everything. Default
  to safety.
- **`ict-trainer.service`'s `ExecStart` references a script that
  doesn't yet exist** (`scripts/ops/run_training_cycle.sh`).
  Starting the unit before that script lands fails loudly with a
  clear error — that IS the safety. Filed as a follow-up.
- **Two trigger paths.** `workflow_dispatch` for the operator UI
  + `issues.opened` filtered to label `provision-training-vm` for
  Claude sessions. Body must contain `confirm: yes` to fire.
  Same pattern as `operator-actions.yml`.
- **JSONL stdout for the script.** Each event (`launching`,
  `poll`, `ready`, `already_exists`, `quota_would_exceed`,
  `provisioning_failed`, `service_error`) is a single JSON
  object on its own line. The workflow parses the stream into a
  Markdown summary posted as an issue comment.
- **Live VM IMDS discovery for compartment + subnet.** Workflow
  SSHes into the live trader and queries
  `http://169.254.169.254/opc/v2/instance/` for the
  `compartmentId`, then uses the OCI API to look up the VNIC's
  subnet. Same pattern `vm-cloud-fix.yml` uses. Avoids
  hard-coding compartment / subnet OCIDs in workflow vars.

## Deliverables

- `scripts/ops/provision_training_vm.py` (new) — OCI SDK
  provisioning script. Idempotent, quota-aware, emits JSONL.
  ~340 LOC. Stdlib + `oci>=2.119`.
- `deploy/training-vm-cloud-init.yaml` (new) — Cloud-init
  bootstrap: apt packages, repo clone, systemd unit + timer files,
  role marker, daemon-reload. Idempotent.
- `.github/workflows/provision-training-vm.yml` (new) — two
  triggers (dispatch + issue-labelled), IMDS discovery, OCI API
  subnet lookup, JSONL parsing, Markdown summary, issue
  comment + close.
- `.github/workflows/bootstrap-labels.yml` — new
  `provision-training-vm` label registered.
- `docs/ARCHITECTURE-CANONICAL.md` — new "Two-VM topology"
  subsection under the AI-traders training workflow; change-log
  row; two new Known gaps entries (cross-VM data flow,
  `run_training_cycle.sh` body).
- `docs/runbooks/training-vm.md` (new) — operator runbook:
  why a separate VM, what ships, how to provision, quota
  guardrail, SSH, bootstrap verification, enable training,
  cross-VM data sync (filed), teardown, troubleshooting.
- `tests/test_provision_training_vm.py` (new) — 7 tests:
  idempotency (existing instance / terminated-existing-treated-
  as-absent), quota guardrail, happy path (launch + public IP +
  tags), cloud-init injection (base64-encoded), provisioning
  timeout, missing-env-var → `config_error`. Skips locally when
  `oci` SDK isn't installed; CI has it.

## Acceptance

- [x] `python -c "import ast; ast.parse(open('scripts/ops/provision_training_vm.py').read())"` — clean.
- [x] `python -c "import yaml; yaml.safe_load(open(<each YAML>))"` — clean on workflow + cloud-init + bootstrap-labels.
- [x] `ruff check` clean on script + tests.
- [x] 7 mocked tests written; skip locally (no `oci` SDK in sandbox); pass on CI.
- [x] Architecture doc carries the two-VM topology in a prominent
      subsection, with the change-log row + known-gap entries.
- [x] Operator runbook covers provision / SSH / enable / teardown
      / troubleshoot.
- [x] Workflow idempotent: re-running with an existing
      `ict-trainer-vm` short-circuits.
- [x] Workflow quota-safe: refuses with a clear message if
      provisioning would exceed 4 OCPU.
- [x] Cloud-init leaves `ict-trainer.service` DISABLED.
- [x] PR template (from WS10) + arch-doc-guard (from WS10)
      both apply to this PR — high-impact paths are touched, AND
      arch docs are updated. Guard should stay silent.

## Out of scope (filed for follow-ups)

- **`scripts/ops/run_training_cycle.sh`** — the body of the
  trainer service. Cadence + entrypoint not yet decided
  (manual one-shot? daily timer? on-merge to a training branch?).
  Operator decision; ship in a follow-up PR once the call's made.
- **Cross-VM `trade_journal.db` access** — rsync vs diag-API
  decision. The runbook explains the manual `scp` workaround
  until then.
- **Teardown workflow** — provisioning ships; teardown stays
  manual (OCI Console → Terminate). Different blast radius;
  filed for a future PR if operator wants it automated.
- **Trainer-VM-specific secrets** — `OCI_CLI_KEY_CONTENT` etc.
  are reused; the trainer doesn't need Bybit secrets (it doesn't
  trade). If we later run shadow inference on the trainer
  against fresh market data, a separate read-only Bybit key
  would be needed. Filed.
- **Separate SSH keypair** — the workflow accepts a
  `TRAINER_VM_SSH_KEY` override; operator can switch later
  without code change.

## Live runtime impact

**None until the operator triggers the workflow.** This PR is
plumbing only — the actual provisioning is a deliberate operator
action. Once triggered:

- A new VM appears in the OCI compartment, idle.
- Live trader VM is untouched.
- Always Free quota: total OCPU usage rises by 1 (assuming the
  live VM uses 1 OCPU; verified before launch by the quota
  guardrail).
- No outbound traffic from the new VM until the operator enables
  `ict-trainer.service` (which currently fails fast because the
  ExecStart script doesn't exist).

This is the WS9 "Oracle / Hugging Face runtime split"
deliverable. The Hugging Face half (open-source model layer,
WS6) is a separate workstream; the trainer VM is the foundation
both layers will need.
