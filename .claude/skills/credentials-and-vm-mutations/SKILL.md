---
name: credentials-and-vm-mutations
description: Invoke BEFORE writing any operator-facing instruction that involves credentials, the live VM's runtime state, or systemd. Owns the canonical autonomy contract — operator originates secret values only; everything else (propagation, VM mutation, restart, verification) is yours via workflows. Self-check your draft against the bright-line phrases here; if any appear, your instruction is non-compliant and routes around an existing workflow. Use when adding a broker, rotating keys, updating an env var on the VM, flipping account mode, restarting a service, or whenever a precedent runbook is about to shape your operator steps.
---

# /credentials-and-vm-mutations — the autonomy contract for VM state

This skill exists because operator-facing instructions keep getting
written by anchoring on a precedent runbook (IB, Bybit) and copying its
shape, instead of by deriving from the autonomy contract in CLAUDE.md.
The fix is to invoke this skill at the moment you're about to write the
operator section, so the contract loads BEFORE the precedent.

If you reached this skill from a precedent, **stop reading the precedent
and use the contract below as your template.** The precedent is one
specific application of the contract; the contract is what you derive
from.

## The rule — operator-only actions, exhaustive

When writing operator instructions in any format (runbook, chat reply,
PR description, error message, comment), every operator step MUST fall
into one of these three categories. **If a step doesn't fit, it's yours
to do via a workflow.**

1. **Originate a secret value** — add a value to GitHub Actions secrets,
   a broker dashboard, or a third-party config. Only a human can mint
   the value; it has to exist *somewhere* before automation can read it.
2. **Approve a tier-gated decision** — Tier-2 ack in chat, Tier-3 PR
   approval, mode-flip confirmation. The decision itself is the gate
   that releases the next automation step.
3. **Physical / external action** — sign up for a service, click a
   CAPTCHA, fund an account, complete KYC, get on a phone with a
   broker. Anything that requires a body at a screen the bot can't
   reach.

**Anything else is yours.** Mechanical, repeatable, scripted, VM-side,
or state-mutating work runs through a workflow you dispatch. If the
workflow doesn't exist, ship it FIRST as your prerequisite (a Tier-1
PR), then write the operator steps assuming the workflow is in place.
The operator never edits a systemd file, never SSHes to a VM, never
runs a command on your behalf.

## Bright-line phrases — if you wrote them, your draft is non-compliant

Scan your own draft for these strings. Each one is a signal that you
collapsed operator-touching VM work into an instruction when you should
have used a workflow. The list is illustrative, not exhaustive — apply
the rule above to anything that smells like it.

- "SSH to the VM" / "ssh ubuntu@…"
- "sudo \\$EDITOR" / "edit `/etc/systemd/system/…`" / "edit the systemd drop-in"
- "sudo systemctl daemon-reload" / "restart the service" / "sudo systemctl restart"
- "On the live VM, run …" / "On the trainer, run …"
- "Update the `.env` file" / "edit the `EnvironmentFile`"
- "Manually copy this file to …"
- "Check the systemd journal" / "tail the journal on the VM"
- "Run this command locally and paste the output"
- "Drop these env vars into the systemd unit"

If any appear in an operator-facing section, **rewrite as a workflow
dispatch you perform after the operator's ping**. The bright-line set
is meant to catch the *shape* of the failure, not enumerate every
possible variant.

## The credential lifecycle (the canonical sequence)

For every credential change, the sequence is exactly these five steps
and never anything else:

1. **Operator originates** the value at the third party (steps in
   category 1 / 3 of the rule above).
2. **Operator adds** the value to GitHub Actions secrets with the exact
   env-var name from the broker's config dataclass.
3. **Operator pings** you ("X creds provisioned").
4. **You dispatch** the propagation workflow (see catalogue below) — it
   SSHes the live VM via SSH `SendEnv`, writes the new value to the
   appropriate runtime location (systemd drop-in, `.env`, etc.), and
   restarts whatever needs restarting. Values **never appear in run
   logs or audit artifacts** — that's the security contract every
   propagation workflow must inherit.
5. **You verify** via the `diag-data` skill that the post-state matches
   intent (auth probe succeeds, balance read returns, journalctl shows
   the expected restart).

If step 4's workflow doesn't exist yet, your sequence becomes 0 → 1 → … :
**step 0 is "I open the PR adding the workflow,"** and that PR has to
land before step 2 is useful.

## The mutation workflow catalogue

| Workflow | Owns |
|---|---|
| `rotate-account-keys.yml` | Bybit `api_key` + `api_secret` rotation per account (`account_id` choice input). |
| `system-actions.yml` | Allowlisted live-VM mutations — `set-account-mode`, `pull-and-deploy`, `restart-bot-service`, `reboot-vm`, the dual-write toggles, the backfills. Tier-1 autonomous, Tier-2 after one chat ack. |
| `vm-web-api-recover.yml` | Self-heal of `ict-web-api.service` (the diag-API process). |
| `vm-diag-snapshot.yml` | Read-only diag pulls (composes with `diag-data`). |
| `trainer-vm-diag.yml` | Arbitrary bash on the trainer VM, fully autonomous. |
| **Missing for your case?** | Open a Tier-1 PR adding the workflow before writing the operator steps that depend on it. Mirror `rotate-account-keys.yml`'s `SendEnv` pattern so secret values never reach logs. |

## When to invoke this skill

- About to write operator-facing instructions for any broker
  integration, key rotation, env-var change, or VM-touching mutation.
  Invoke this skill **before** drafting the operator section.
- A precedent runbook (`ib-integration.md`, `rotate-account-keys.yml`'s
  comments, anything else) is about to shape your operator steps.
  Invoke this skill instead and *derive* from the contract above.
- You drafted operator instructions and want to self-check before
  sending or merging. Run the bright-line scan above on your draft.

## Composes with

- `vm-ops` — broader VM operations contract; this skill is the
  credentials/state-mutation slice.
- `git-actions` — for workflow dispatch mechanics (label + issue body).
- `new-broker` — applies this skill specifically to adding a broker.
- `diag-data` — for the post-mutation verification read.
