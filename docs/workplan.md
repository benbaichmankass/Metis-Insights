# AI Trader — Project Structure & Workplan

> **Status:** Superseded 2026-05-10 by the S-CANON-1 canonical
> doc set. Authority order is now:
> 1. docs/CLAUDE-RULES-CANONICAL.md
> 2. docs/ARCHITECTURE-CANONICAL.md
> 3. ROADMAP.md
> 4. current sprint log under docs/sprint-logs/
>
> This file is preserved for historical context. Do not treat it
> as authoritative on policy or sequencing.

> **Status (historical):** authoritative master workplan for the ICT Trading Bot.
> **Owner:** PM (Ben).
> **Last updated:** 2026-05-06 (S0 — Workflow Foundation).
> **Related docs:** `ROADMAP.md` (sprint-level backlog), `docs/claude/operating-protocol.md`
> (how Claude executes), `docs/claude/milestone-state.md` (where the program is right now),
> `docs/claude/decomposition-rules.md` (milestone → sprint → checkpoint rules).

This document defines the project structure, operating rules, and roadmap for the AI
Trader software. It incorporates the current autonomous workflow design, Telegram
communications design, milestone structure, and the latest product decisions for
execution order and system priorities.[file:1][file:3][file:13]

## Goal

Maintain a portfolio of AI trading strategies that each target at least 1–2% weekly
returns, with at least 2–3% weekly returns overall, while prioritizing safety,
visibility, auditability, and controlled rollout of live behavior.[file:1][file:13]

## Current priorities

The current phase of the project is **system hardening and operational visibility**,
not aggressive expansion of broker/account infrastructure.[file:1][file:13]

Prop-trading infrastructure is explicitly **deferred** for now; it should not be built
until the system is ready to support that trading mode in a deliberate later phase.[file:2]

The **web app** is now a crucial near-term priority because it is needed as a stable
source of truth for understanding what the system is doing in real time and across
sessions.[file:1]

## Core operating principles

1. **Safety before expansion.** No new live behavior should be introduced before risk
   controls, visibility, and validation paths are in place.[file:13]
2. **Repo is the source of truth.** Plans, logs, comms artifacts, workflows, and state
   transitions should be repo-tracked wherever practical.[file:1][file:3]
3. **Claude autonomy is the default.** Claude should keep working unless a task falls
   into a clearly defined approval category.[file:1]
4. **Visibility is mandatory.** The system should always expose enough logs,
   dashboards, and status surfaces for the operator to understand what it is doing.[file:1]
5. **Operator actions must be simple.** Any required VM action should come with a
   one-click Colab notebook or similarly simple copy-ready workflow written for a
   non-technical user.[file:3]
6. **Use paid compute carefully.** Claude should focus on repo architecture, code
   changes, tests, and reviews, while Colab, Google AI Studio, and Hugging Face should
   absorb as much research and heavy compute work as possible.[file:13]

## Milestone and session system

Claude must always create and maintain a **milestone plan**, and each milestone must
be broken into **session-sized sprints** and then further into **checkpoints**,
regardless of whether the work is roadmap-based, ad hoc, or part of the recurring
auto-task routine.[file:1]

The decomposition contract is normative — see
`docs/claude/decomposition-rules.md` for the full rules.

### Milestone types

#### Roadmap milestone
A sprint that progresses the planned roadmap for the trading system, web app,
operator tooling, logging, AI workflows, and deployment quality.[file:1]

#### Ad-hoc milestone
A sprint initiated by the operator to handle urgent bugs, incidents, investigations,
or newly prioritized ideas outside the normal roadmap sequence.[file:1]

#### Auto-task milestone
A structured recurring sprint initiated by Claude's daily auto-task routine using
instructions stored in the repo.[file:1]

### Session requirements

Every session-sized sprint must include the following:

- Sprint title and purpose.
- Scope and explicit non-goals.
- Checkpoints.
- Dependencies and blockers.
- Risk tier and merge authority.
- Required validation steps.
- Required documentation updates.
- A closing summary with next-step handoff.[file:1]

### Session closing

The final checkpoint of every sprint is documentation and project-state maintenance.
Claude must update all affected documentation, including but not limited to:

- `README.md`
- The roadmap (`ROADMAP.md`)
- Sprint/task logs (`docs/sprints/`, `docs/sprint-plans/`, `docs/sprint-summaries/`)
- Relevant Claude instruction files and skill markdown files (`docs/claude/`)
- Bug log (`docs/claude/bug-log.md`) and lessons log where applicable
- Architecture docs impacted by the sprint (`docs/architecture.md`).[file:1]

Claude must also update the central milestone/session state or handoff file
(`docs/claude/milestone-state.md` + `docs/claude/checkpoints/CHECKPOINT_LOG.md`) so
future sessions can resume from repo state rather than relying on chat continuity
alone.[file:1]

## Decision and merge authority

Claude follows a **three-tier operating model** for merge and approval decisions.[file:1]

### Tier 1 — Claude may self-merge

Claude may self-merge work that:

- Does not directly change live trading behavior.
- Is cleanup, documentation, tests, CI, observability, schemas, dashboard read-path
  work, or isolated tooling changes.
- Affects infrastructure only when safety can be proven by tests, dry-run validation,
  or staging checks.[file:1][file:13]

### Tier 2 — Claude must ping the operator with a merge/hold decision

Claude must send a structured risk-summary ping and wait for a decision when:

- A change touches the live order path, runtime orchestration, deployment timers,
  service behavior, or any integration that could break execution even if strategy
  logic does not change.
- Claude cannot fully prove safety end-to-end.
- A change may cause restart churn, duplicate sends, sync loops, or deployment
  instability.[file:1][file:3]

The ping should include:

- PR title.
- One-sentence summary.
- One-sentence risk if broken.
- Validation already completed.
- Buttons for **Merge** and **Hold**.[file:1]

### Tier 3 — explicit operator approval required before merge

Claude must not merge without explicit approval when a change involves:

- Strategy parameters.
- Entry or exit logic.
- Signal thresholds.
- Position sizing formulas.
- Risk cap values.
- Promotion of any strategy from dry-run to live.[file:1]

This preserves maximum autonomy for engineering work while reserving trading-behavior
changes for deliberate operator review.[file:1]

## VM and operator actions

The operator is non-technical and the system runs on a free-tier Oracle VM, so any
manual action must be made simple and low-risk.[file:3]

### Rule

If Claude needs the operator to do something on the VM, Claude must provide:

- A copy-ready Colab notebook script.
- Short markdown headings and explanations between cells.
- Pre-filled variables and paths.
- Clear instructions for what success should look like.[file:3]

### Required pre-filled values

Claude should use these exact values in any notebook or operator-run script:

```python
SSH_KEY_FILE = 'ict-bot-ovm-private.key'
VM_USER = 'ubuntu'
VM_HOST = "158.178.210.252"
REPO_DIR = '/home/ubuntu/ict-trading-bot'
```

See `docs/claude/colab-workflows.md` § "Operator VM steps" for the canonical notebook
structure and `notebooks/operator/rotate_api_keys.ipynb` for the reference template.

## Cross-references

- `ROADMAP.md` — sprint-level backlog and phase status.
- `CLAUDE.md` — Claude session router and standing rules.
- `docs/claude/operating-protocol.md` — consolidated Claude operating protocol.
- `docs/claude/milestone-state.md` — current milestone / session state.
- `docs/claude/decomposition-rules.md` — milestone → sprint → checkpoint contract.
- `docs/claude/sprint-planning.md` — binding sprint-prompt template.
- `docs/claude/checkpoint-workflow.md` — resume rules and end-of-session handoff.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — append-only session log.
