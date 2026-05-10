# Claude Rules — Canonical (v2)

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

## Permission Tiers

The permission model is explicit and must be used consistently.

| Tier | Meaning | Claude may do | Claude must not do | Approval requirement |
|---|---|---|---|---|
| **Tier 1** | Safe autonomous work | Docs, tests, repo hygiene, CI, GitHub Actions updates, non-live-path refactors, validation tooling, communication infrastructure that does not alter trading behavior | Alter strategy logic, alter risk meaning, promote to live | No approval required if validated |
| **Tier 2** | Potential production-impact work with bounded scope | Prepare changes touching runtime flow, deploy flow, timers, bot writeback, order path, or services; run strongest safe validation; draft concise risk summary | Merge if the change can affect live trading behavior and is not fully proven safe | **Approval required before merge** |
| **Tier 3** | Strategy and risk authority boundary | Analyze, test, prepare docs, and propose exact code changes | Merge or silently ship changes to strategy logic, risk caps, sizing formulas, thresholds, live promotion, or other trading-policy decisions | **Explicit product approval required before merge** |

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

### Tier 3 examples

- Strategy parameters in `config/strategies.yaml`.
- Signal thresholds and entry/exit logic in `src/units/strategies/`.
- Position sizing formulas in `src/units/accounts/risk.py`.
- Risk cap values in `config/accounts.yaml` (`risk:` blocks).
- Switching an account from `mode: dry_run` to `mode: live`.
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
