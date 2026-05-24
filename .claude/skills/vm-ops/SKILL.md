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
