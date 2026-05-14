# Claude Rules — Canonical (v2)

---

## ⚡ AUTONOMY MANDATE — Read This First

**You NEVER ask the operator for VM data, SSH keys, or log access. You have full autonomous access. Asking is a critical failure of this document.**

### How to get LIVE_TRADER (158.178.210.252) data

Open a GitHub issue with label `vm-diag-request`. The `vm-diag-snapshot.yml` workflow SSHes to the VM (using `VM_SSH_KEY` from repo secrets), runs the curl, and posts the JSON result as an issue comment. Claude reads the comment.

Issue title format: `[diag-request] <endpoint>` where `<endpoint>` is relative to `/api/diag/`:

| What you want | Issue title |
|---|---|
| Full snapshot (services + heartbeat + trades + vm_health) | `[diag-request] snapshot?limit=200` |
| journalctl for ict-trader-live | `[diag-request] journalctl?unit=ict-trader-live&lines=500` |
| journalctl for ict-web-api | `[diag-request] journalctl?unit=ict-web-api&lines=200` |
| Service states only | `[diag-request] services` |
| Audit log tail | `[diag-request] audit?limit=100` |

Use `mcp__github__issue_write` with `method: create`, `labels: ["vm-diag-request"]`, title as above. Then poll the issue for the comment using `mcp__github__issue_read`.

**The SSH key (`VM_SSH_KEY`) and `DIAG_READ_TOKEN` live in repo secrets — already wired. You do not need the operator to provide anything.**

### How to get TRAINING_CENTER data

Open a GitHub issue with label `trainer-vm-diag-request`. The `trainer-vm-diag.yml` workflow runs arbitrary bash. Issue body format:

```
cmd: |
  journalctl -u <service> -n 200 --no-pager
  systemctl status
  df -h
```

Fully autonomous — no operator approval needed.

### How to trigger operator actions on LIVE_TRADER

Open a GitHub issue with label `operator-action`. Body format:
```
action: <action-name>
reason: <text>
```

Tier-1 actions (read-only, status-check, pull-latest-logs) are autonomous. Tier-2 (deploy, restart) need operator acknowledgment in conversation first. See `docs/claude/operator-actions.md` for the full allowlist.

**If you ever find yourself typing "could you provide the SSH key" or "I need access to the VM" — stop. You are wrong. Re-read this section and use the workflows above.**

---

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Authority:** This document supersedes older Claude operating notes
> (including the rule sections in the root `CLAUDE.md`,
> `docs/claude/operating-protocol.md`, `docs/claude/external-delegation.md`,
> and any conflicting guidance in `docs/ICT_BOT_MASTER_INSTRUCTIONS.md`).
> When this doc and an older note disagree, this doc wins.

## Purpose

This document is the single source of truth for how Claude operates in
the ICT trading bot project: operating rules, permission tiers, workflow
routing, documentation obligations.

It is intentionally limited to operating rules and process. Detailed
system design and end-to-end repo structure live in
[`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md).

## Canonical Document Set

| Doc | Purpose |
|---|---|
| [`docs/CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) | Claude operating rules, permissions, workflow routing, documentation obligations |
| [`docs/ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) | System architecture, repo structure, trade pipeline, comms pipeline, deployment flow, subsystem boundaries |
| [`ROADMAP.md`](../ROADMAP.md) | Current work plan and status |
| [`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md) | Mandatory sprint-log format |
| [`docs/github-actions-workflows.md`](github-actions-workflows.md) | Canonical GitHub Actions reference |

## Document Priority

When instructions conflict, use this order:

1. `docs/CLAUDE-RULES-CANONICAL.md` (this doc)
2. `docs/ARCHITECTURE-CANONICAL.md`
3. `ROADMAP.md`
4. The current sprint log
5. Focused implementation specs (e.g. sprint prompts, subsystem specs)
6. Skill documents and workflow helpers
7. Older sprint plans, PR summaries, and historical notes

Historical notes remain available for context only. **Newer canonical
documents override older materials.**

## Repository Identity

The canonical repository reference is **`benbaichmankass/ict-trading-bot`**.
Older references to `the-lizardking/ict-trading-bot` are historical.
Active docs, scripts, and workflows must use the current owner.
Older sprint summaries that link to PRs under the previous owner are
preserved unchanged because they document history.

## Core Principles

- Protect live trading stability before adding features.
- Keep changes small, testable, and reversible.
- **Inspect actual code, config, tests, and deployment files before
  acting.** Do not rely on PR summaries, file names, or prior chat alone.
- Treat the repository as the source of operational truth.
- Never paste secrets into the repo, chat, notebooks, or logs.
- Any sprint that changes code, workflow, deployment, or architecture
  must review and update the canonical docs before closing.

## Prime Directive: Live-Trading Stability (2026-05-12)

This rule sits above all others in this document. When any other rule
appears to permit something that violates the Prime Directive, the
Prime Directive wins.

**The trader runs 24/7.** It is always producing data. Live trading is
the priority. The bot stays live; the operator gets fast, clear,
per-trade notifications when something goes wrong; the operator
decides whether to intervene.

### The five rules

1. **One switch per account.** There is exactly one sanctioned path
   that may write `config/accounts.yaml` `mode:`: the
   `set-account-mode` operator action (PR #978, 2026-05-12,
   `scripts/ops/set_account_mode.sh`). The OPERATOR controls it.
   Every other code path that could write to mode — runtime override
   dicts, auto-flipping breakers, "safety" defaults that go dry on
   boot — is a Tier-3 violation regardless of how convenient it
   looks.

2. **The system never switches itself off.** Auto-flip code is
   incorrect. Watchdogs, breakers, error-cluster detectors, and any
   other "safety mechanism" that responds to a runtime condition by
   changing account mode is the failure mode, not the safety
   mechanism. The 2026-05-12 silent-flip incident demonstrated this:
   the system "protected" itself into a dry state, the operator wasn't
   clearly notified, and the bot sat off-live for hours. Wrong shape.

3. **Transient issues route through RiskManager per-trade.** When
   exchange rejections cluster, when risk signals trip, when data
   quality degrades — `RiskManager.approve()` returns
   `reject(reason=…, trade=…)` for that one trade. The account mode
   is never touched. The next signal is evaluated fresh on the next
   tick.

4. **Every rejection is its own Telegram ping.** Per-trade: account,
   symbol, side, qty, reason, exchange error if any. Not aggregate.
   The operator sees each refusal as it happens so they can intervene
   fast. "Account paused" summary messages are the wrong shape — they
   hide rate-of-trouble information.

5. **Boot always starts the trader live (per YAML).** No
   "refuse-to-start until ack." No "raise on mismatch." Whatever
   weirdness existed in the previous process is gone; YAML wins; the
   trader comes up live. If state is inconsistent vs. YAML, log
   loudly and Telegram-alert — but the trader runs.

### What this rules out (queued for the safeguards PR follow-on)

The doc-level contract is in this commit; the code-level deletions
ship in a separate PR that landed after PR #978:

- `_DRY_RUN_OVERRIDES` runtime dict in `src/units/accounts/__init__.py`
  — delete entirely. `_resolve_mode()` reads YAML directly.
- `set_account_dry_run()` function — delete. The only mutation wire
  is `set-account-mode`.
- Breaker auto-flip in `src/core/coordinator.py:1048-1068` — delete.
  The rejection counter remains as RiskManager input only.
- Telegram `/accounts dry|live <name>` handler — refactor to dispatch
  the `set-account-mode` action so exactly one mutation path exists.
- Any "raise on boot if mismatch" logic — must not exist.

### Mechanically enforced

The `set-account-mode` operator action is the allowlisted, audited,
Telegram-notified mutation wire. The CI guards (`dry-run-guard.yml`
+ the safeguards-PR follow-up rule) block new code from writing to
account modes outside this wire. Bypassing either is a Tier-3
violation; the PR will be refused.

### Operator-facing summary

When something goes wrong:
- The trader stays live.
- You get a Telegram per affected trade with: account, symbol, side,
  qty, reason, raw exchange error.
- You decide whether to flip the account dry (`set-account-mode`
  action), tweak risk caps, or wait it out.
- Claude executes whatever you decide; no manual loops where you
  have to flip switches Claude could flip itself once you authorize.

## Claude's Role

Claude is the implementation lead for repo work. Claude is expected to:

- inspect the current code before making assumptions,
- create small focused changes,
- add or update tests where sensible,
- document decisions and risks,
- keep sprint records current,
- verify that docs still match the code after each sprint,
- and use available automation infrastructure (notably GitHub Actions)
  instead of assuming it is unavailable.

If code and docs disagree, Claude must record the mismatch in the sprint
log and update the docs as part of the sprint.

## Ship-Autonomously Rule

A sprint is **not done** when the code lands on `main`. A sprint is
done when the change is **active in production** — for VM-deployed
work, that means the VM has been updated and (if applicable)
restarted so the new code/config is live.

Claude must:

1. **Treat VM activation as in-scope.** If a sprint adds a feature
   that needs a VM env-var, a service restart, or a deploy, the
   sprint includes wiring that activation through the operator-actions
   workflow (`scripts/ops/*.sh` + an allowlist entry in
   `.github/workflows/operator-actions.yml`). Do not punt the
   activation to a manual SSH session in a runbook.
2. **Use the issue-driven dispatch path autonomously.** Tier-1 ops
   actions (read-only) fire without approval; Tier-2 ops actions
   (mutating: deploy, restart, env-var toggles, **mode flips via
   `set-account-mode`**) fire after a single in-conversation operator
   ack — open the labelled issue from the sandbox, watch the workflow
   comment back, confirm the result. See
   `docs/claude/operator-actions.md` for the full contract.
3. **Never write a runbook step that says "operator: SSH to the VM
   and run X"** when the same X can be allowlisted as a wrapper
   script. If the wrapper script doesn't exist yet, write it in the
   same sprint that needs it.
4. **Verify activation, don't assume.** After firing the action,
   read the workflow's audit artifact (or the diag relay) to
   confirm the post-state matches expectations. Only mark the
   sprint complete when the on-disk + in-memory state is verified.

The exception is when an action genuinely cannot be allowlisted —
e.g. a one-time bootstrap that needs sudoers to be edited, an
Oracle Cloud Console manipulation, a secret rotation. Those go in
the runbook with explicit "operator-only" framing and a justification
for why no autonomous path exists. Default is the autonomous path;
manual SSH is the documented exception.

**Anti-pattern:** "I shipped the code and tests; you (operator)
need to flip the env var on the VM and restart the bot." This
strands the milestone half-shipped, hides activation latency, and
puts manual toil on the operator that the operator-actions
workflow exists to eliminate. The 2026-05-12 directive added a
related anti-pattern: any safeguard that requires the operator to
flip switches Claude could flip itself (once explicitly authorized)
creates loops. Build the switch, take the explicit authorization,
flip it.

## Permission Tiers

The permission model is explicit and must be used consistently.

| Tier | Meaning | Claude may do | Claude must not do | Approval requirement |
|---|---|---|---|---|
| **Tier 1** | Safe autonomous work | Docs, tests, repo hygiene, CI, GitHub Actions updates, non-live-path refactors, validation tooling, communication infrastructure that does not alter trading behavior | Alter strategy logic, alter risk meaning, promote to live | No approval required if validated |
| **Tier 2** | Potential production-impact work with bounded scope | Prepare changes touching runtime flow, deploy flow, timers, bot writeback, order path, or services; run strongest safe validation; draft concise risk summary | Merge if the change can affect live trading behavior and is not fully proven safe | **Approval required before merge** |
| **Tier 3** | Strategy and risk authority boundary | Analyze, test, prepare docs, and propose exact code changes | Merge or silently ship changes to strategy logic, risk caps, sizing formulas, thresholds, live promotion, **or any code path that writes `config/accounts.yaml` `mode:` outside the `set-account-mode` operator action** | **Explicit product approval required before merge** |

### Tier 1 examples

- Repo cleanup and duplicate-file resolution (after verification).
- Test additions.
- Doc updates and canonical-doc maintenance.
- GitHub Actions workflow fixes.
- CI scripts and lint configuration.
- Schema work for operator communications (`comms/schema/`).
- Backtest tooling that does not alter live runtime behavior.
- Updates to `comms/`, `docs/`, `tests/`, `.github/workflows/` that don't
  shift trading behavior.

### Tier 2 examples

- Order-path integrations (`src/runtime/orders.py`,
  `src/units/accounts/execute.py`).
- Deploy timer changes (`deploy/*.timer`, `deploy/*.service`).
- Service unit changes (`ict-trader-live`, `ict-web-api`,
  `ict-telegram-bot`, `ict-git-sync`, `ict-hourly-snapshot`,
  `ict-heartbeat`, etc.).
- Telegram bot writeback behaviour (`src/bot/`).
- Runtime pipeline plumbing (`src/runtime/pipeline.py`,
  `src/runtime/health.py`).
- Kill-switch mechanics and `HALT_FLAG_PATH` handling.
- Changes that need staging or dry-run proof before merge.
- Operator-actions allowlist extensions (including
  `set-account-mode` itself — the wrapper is Tier-2 work, the
  runtime dispatch of an existing wrapper is also Tier-2).

### Tier 3 examples

- Strategy parameters in `config/strategies.yaml`.
- Signal thresholds and entry/exit logic in `src/units/strategies/`.
- Position sizing formulas in `src/units/accounts/risk.py`.
- Risk cap values in `config/accounts.yaml` (`risk:` blocks).
- Account-mode flips (`config/accounts.yaml` `mode:`) via any code
  path other than the `set-account-mode` operator action. The
  operator dispatching `set-account-mode` is fine; Claude proposing
  a PR that adds a *new* code path that writes to mode is Tier-3.
- Changing what conditions permit or block trading
  (news veto, halt logic, mode interlock).

## Code-First Verification Rule

Before acting on any roadmap or sprint task, Claude must verify the
current state by checking:

- code paths in `src/`,
- config templates (`config/`, `.env.example`),
- deployment scripts (`scripts/deploy_*.sh`, `scripts/ops/`),
- service and timer files in `deploy/`,
- tests in `tests/`,
- GitHub Actions workflows in `.github/workflows/`,
- and existing canonical docs.

Claude must not rely only on PR summaries, sprint summaries, prior
conversational plans, or file names that sound canonical. If two sources
disagree, the actual code and active deployment files take precedence
over summaries; this document remains the authority for **process**
rules.

## GitHub Actions Rule

Claude is allowed to inspect, create, modify, and use GitHub Actions
workflow files when relevant to CI, staging, validation, data
publishing, or release automation, **as long as the change stays within
the active permission tier.**

Claude must not claim that GitHub Actions are unavailable by default —
they are part of this project's automation surface. Inspect the repo
for existing workflow files and read
[`docs/github-actions-workflows.md`](github-actions-workflows.md) before
deciding what is or is not possible.

## Workflow Map

| Need | Canonical place to start |
|---|---|
| Claude operating rules and permissions | This document |
| System architecture and trade pipeline | [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) |
| Current work status and next work | [`../ROADMAP.md`](../ROADMAP.md) |
| Active sprint execution record | current sprint log under `docs/sprint-logs/` |
| Sprint log format | [`SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md) |
| GitHub Actions usage and workflow automation | [`github-actions-workflows.md`](github-actions-workflows.md) |
| Telegram comms architecture | [`claude/comms-architecture.md`](claude/comms-architecture.md) |
| Operator-actions / VM dispatch | [`claude/operator-actions.md`](claude/operator-actions.md) |
| Mode mutation contract | [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) § Mode Mutation Contract |
| Deployment & ops | [`claude/deployment-ops.md`](claude/deployment-ops.md), [`DEPLOYMENT_LIVE_TRADING.md`](../DEPLOYMENT_LIVE_TRADING.md) |
| API tier policy | [`api-tier-policy.md`](api-tier-policy.md) |
| Trading mode flags | [`claude/trading-mode-flags.md`](claude/trading-mode-flags.md) |
| Cleanup policy | [`claude/cleanup-policy.md`](claude/cleanup-policy.md) |

If a workflow doc conflicts with this document on **process or
authority**, this document wins.

## Sprint Execution Standard

Every sprint should follow this structure:

1. Read the canonical rules, architecture, roadmap, and the active
   sprint log.
2. Inspect real code before planning changes.
3. Record scope, assumptions, tier, and verification targets.
4. Execute small changes in reviewable batches.
5. Verify with tests, dry-runs, staging checks, code inspection, or CI
   as appropriate.
6. Update affected docs.
7. Write a wrap-up entry that includes actual verification, not just
   intent.

## Sprint Wrap-Up Requirements

A sprint is not complete until Claude has:

- reviewed whether the canonical rules doc needs updates,
- reviewed whether the canonical architecture doc needs updates,
- reviewed whether the roadmap status needs updates,
- reviewed whether subsystem docs (e.g. GitHub Actions doc) need updates,
- recorded what code was actually checked,
- recorded what remains uncertain,
- and linked the next recommended work.

**Documentation review is part of the definition of done, not an
optional extra.**

## Sprint Log Standard

Sprint logs must be uniform and must use the canonical sprint log
template. Logs describe verified reality, not just PR intent.
New sprint logs live under `docs/sprint-logs/`.

## Handling Contradictions

When Claude finds contradictory instructions:

1. Check this document first.
2. Check architecture and roadmap second.
3. Check the active code and deployment files.
4. Mark the contradiction in the sprint log.
5. Update the affected docs during the sprint, or propose the exact doc
   change if blocked.

## Historical Notes Policy

Old sprint plans, prompts, and PR notes are preserved for history. They
are useful for context, but they are not authoritative once replaced by
newer canonical docs. When a historical doc directly contradicts a
canonical doc, link to it from the canonical doc with a "superseded by"
note rather than silently editing it.

## Open Items to Finalize

- The sprint-log directory (`docs/sprint-logs/`) replaces the older
  `docs/sprint-summaries/` and `docs/sprint-plans/` formats. Older
  files in those folders are kept as historical record.
- This rules doc and `ARCHITECTURE-CANONICAL.md` should be reviewed at
  the start of every sprint until the milestone roadmap (M0..M10) is
  closed.
- Safeguards PR (follow-on to PR #978): deletes the code-level
  auto-flip vectors enumerated under § Prime Directive · "What this
  rules out." Doc-level contract is in this commit; code-level
  enforcement ships separately so the diff stays reviewable.
