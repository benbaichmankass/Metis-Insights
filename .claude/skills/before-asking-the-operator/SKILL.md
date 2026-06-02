---
name: before-asking-the-operator
description: TRIGGER any time you are about to write phrases like "you'll need to", "run this locally", "manually...", "SSH in and", "sudo", "open a terminal", "on the VM, edit", "the operator needs to", "go to the dashboard and create", or any other instruction that attributes work to the operator. Run the runner-check below BEFORE generating that instruction. Almost every instance is a fallback because no direct MCP tool exists for X — but a GitHub Actions runner can do anything a terminal can, given the right stored auth. Default the work to a runner; the operator only owns the three categories in credentials-and-vm-mutations (originate secret value / approve tier-gated decision / physical-external action).
---

# /before-asking-the-operator — the runner-check that runs first

This skill exists because the fallback to "operator does X manually"
is the most common autonomy-contract violation in this repo. It keeps
happening with the same shape:

1. Need to do X.
2. Search MCP tools for a direct verb (`mcp__github__create_secret`,
   `mcp__github__patch_systemd`, ...).
3. Don't find one.
4. **Default to "operator does X manually" / "run this command locally"
   / "you'll need to SSH and...".**

Step 4 skips the actual question: **can a GitHub Actions runner do X?**
A runner is a Linux VM with:

- Full bash, Python, apt-installable tooling
- The `gh` CLI authenticated via stored PATs
- Stored SSH keys, API tokens via `${{ secrets.* }}`
- Outbound network to anywhere
- Established dispatch patterns in this repo
  (`workflow_dispatch`, issue-label triggers)

If a terminal can do X, a runner can do X. The MCP-tool gap is
irrelevant — workflows are the universal escape hatch.

## The check — run BEFORE writing any operator-attributed instruction

Before any sentence that begins "you'll need to...", "open a
terminal...", "manually...", "SSH in and...", "on the VM...", "create
the secret yourself...", "go to the dashboard and...", ask in order:

1. **Is X in one of the three legitimate operator-only categories?**
   - Originating a secret value at a third party (broker dashboard,
     API app creation)
   - Approving a tier-gated decision (Tier-2 ack in chat, Tier-3 PR
     merge, mode-flip confirmation)
   - Physical / external action a bot can't reach (CAPTCHA, KYC,
     phone call)

   If yes → operator does it. Done.

2. **If no, can a runner do X?** Almost always yes for any
   shell-scriptable, API-callable, or file-mutating work.

3. **What auth does the runner need?**

4. **Does that auth already exist as a stored secret?** Check the
   catalogue below before assuming the operator has to provision a
   new PAT.

5. **If yes → ship a workflow.** Adapt an existing dispatch pattern
   (`workflow_dispatch` with inputs, or issue-label trigger). Operator
   role collapses to dispatching it, OR to the upstream secret-add if
   one is needed.

6. **If no auth exists → operator adds ONE secret (a category-1
   operator action), then ship a workflow.**

## Stored auth catalogue (grep first before asking for new PATs)

Before assuming the operator needs to provision new auth, check what's
already wired:

| Secret | Scope it confers |
|---|---|
| `VM_SSH_KEY` | SSH into the live VM (any file, any command). |
| `DIAG_READ_TOKEN` | Bearer for `/api/diag/*` reads on the live VM. |
| `BRANCH_PROTECTION_TOKEN` | PAT with `repo` scope — covers branch protection AND Actions secrets write AND any other repo-admin API. |
| `${{ secrets.GITHUB_TOKEN }}` | Default workflow token — limited scope (issues, PRs, contents within this repo). NOT sufficient for Actions secrets writes or admin operations. |
| `TELEGRAM_BOT_TOKEN` / `CLAUDE_TELEGRAM_BOT_TOKEN` | Telegram delivery from runners. |
| `HF_TOKEN` | Hugging Face Hub uploads. |
| `CLOUDFLARE_API_TOKEN` | Cloudflare DNS / Workers / tunnels. |

If your design needs auth not on this list, **first** grep the
existing workflows for similar use cases — a token may already exist
under a non-obvious name. Only after the grep comes up empty should
"operator provisions a new secret" appear in your draft.

## Workflow dispatch patterns this repo supports

Pick the closest pattern and adapt — do not invent new dispatch
mechanics:

- **`workflow_dispatch` with inputs.** Operator (or Claude via the
  Actions API) clicks Run. Examples: `rotate-account-keys.yml`,
  `init-actions-secrets.yml`, `sync-vm-secrets.yml`.
- **Issue-label trigger.** Open a labelled issue with a structured
  body; workflow runs, comments back, closes. Examples:
  `system-actions.yml`, `vm-diag-snapshot.yml`,
  `vm-web-api-recover.yml`, `trainer-vm-diag.yml`. Use when Claude
  needs to dispatch from a session that lacks the workflow-run MCP
  tool.
- **Issue body parsing.** Body fields like `action: <name>` /
  `cmd: <bash>` ride through `${{ github.event.issue.body }}` or env
  vars, never inline-interpolated into shell commands.

## Past failure examples (what to recognize)

These have happened in this repo. If your draft has the same shape,
stop and rewrite via the check above:

- "SSH to the VM and add a systemd drop-in for the Tradovate env vars"
  → wrong; `sync-vm-secrets.yml` does this.
- "Run `gh secret set` locally for each Tradovate placeholder"
  → wrong; `init-actions-secrets.yml` does this.
- "Open a terminal and run `python -m ml backfill-…`"
  → wrong; the trainer-vm-diag relay's arbitrary-bash `cmd:` block
  does this.
- "Edit `.env` and restart the trader"
  → wrong; `sync-vm-secrets.yml` or `system-actions.yml::set-env`
  does this.

## When NOT to use this skill

The three operator-only categories are real — don't try to automate
them away:

- Originating a secret value at a third party: a runner can't sign
  up for the broker on your behalf.
- Approving a tier-gated decision: that IS the operator's role.
- Physical / external actions (CAPTCHA, KYC, signing into a broker
  Web UI): no runner can do these.

For everything else: runner-not-operator.

## Composes with

- `credentials-and-vm-mutations` — the credentials-and-VM-state slice
  of this rule (with its own bright-line phrases).
- `vm-ops` — for VM-side mutation mechanics.
- `git-actions` — for the dispatch mechanics (label + body format).
- `new-broker` — applies this skill to broker onboarding.
