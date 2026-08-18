---
name: vm-ops
description: Inspect and act on the production VMs (live trader + trainer) autonomously through GitHub Actions. Use to check service health, restart a service, deploy, flip an account mode, run a trainer command, or any tiered VM mutation. Covers what's autonomous vs operator-gated by tier. Composes with diag-data (reads) and git-actions (dispatch mechanics).
---

# /vm-ops — operate the VMs through GitHub Actions

You are the operator of both VMs; you act through workflows, never by asking a
human to SSH in. Two VMs, two trust contracts (`CLAUDE.md` § VM authority
split):

- **Live trader VM** — restricted. Reads autonomous; mutations are tiered.
- **Trainer VM** — autonomous. You provision, install, run training, manage
  systemd, all without operator approval (`docs/claude/trainer-vm-mode.md`).

## Reads — use the `diag-data` skill

Service state, heartbeat, journal, journalctl, audit. Live VM via
`vm-diag-snapshot`/`diag_fetch`; trainer via the `trainer-vm-diag` relay
(arbitrary bash). Don't ask the operator — pull it.

## Cloud-state reads — the `oci-inventory` workflow

`diag-data` answers *"what is the trader doing?"*. This answers the different
question **"what does the cloud actually contain, and does it match what we
say?"** — dispatch `oci-inventory.yml` (`mode=report`).

Read-only: `list_instances` only, no provisioning, no termination. It diffs live
OCI compute against `comms/cloud/expected_topology.json` (four verdicts —
`match` / `drift` / `missing` / `undeclared`) and reports Ampere free-tier usage
against **both** ceilings.

⚠️ **`not_declared` is NOT a pass** — it means no baseline exists to compare
against, which is a different fact from "everything matches".

⚠️ **The Ampere budget returns no single pass/fail on purpose.** Which ceiling
binds depends on the tenancy account type, which is visible only in the OCI
console and is not readable from this API. It reports the evidence; it does not
assert the answer.

**If you change VM topology, update `expected_topology.json` in the same PR** —
otherwise the next report reports drift against you.

## Live-VM mutations — the `system-actions` workflow

Privileged live-VM changes run through `.github/workflows/system-actions.yml`
(allowlisted, audited). Dispatch by opening an issue labelled `system-action`
with body:
```
action: <name>
reason: <why>
```
(plus `account:` + `mode:` for `set-account-mode`). See the `git-actions` skill
for the dispatch mechanics and `docs/claude/system-actions.md` for the full
allowlist + per-action contract.

**Tier gate (who can fire what without asking):**
- **Tier 1 (autonomous):** read-only / analysis actions — `status-check`,
  `pull-latest-logs`, the audit actions.
- **Tier 2 (one operator OK in chat, then you fire it):** `pull-and-deploy`,
  `restart-bot-service`, `reboot-vm`, the enable/disable toggles, the backfills,
  `set-account-mode`, `fix-data-dir`, `rotate-account-keys`.
- **Tier 3 (never via this workflow — PR + explicit approval):** strategy
  params, risk caps, live order code. Two named carve-outs only:
  `set-account-mode` (the sole `mode:` write path) and `rotate-account-keys`
  (applies an operator-supplied key from Actions secrets).

If an action you need isn't allowlisted, add the wrapper (`scripts/ops/*.sh`) +
the allowlist entry + the test in the same PR — don't punt it to a manual step.

## Trainer-VM actions — autonomous via `trainer-vm-diag`

Anything on the trainer (install deps, run `python -m ml ...`, manage
`ict-trainer.service`, build datasets) goes through the `trainer-vm-diag`
relay's arbitrary-bash `cmd:` block. No operator approval. Full charter +
the stage gate (shadow → advisory is the operator-approved live switch):
`docs/claude/trainer-vm-mode.md`.

## Self-heal

`ict-web-api.service` down (diag curl exit 7) → fire `vm-web-api-recover`
(label `vm-web-api-recover`), retry the read once.

## Verify, don't assume

After a mutation, pull the post-state via `diag-data` and confirm it matches
intent before reporting done. Report honestly if the verify pull failed.
