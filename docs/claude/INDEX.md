# Claude instruction index

This directory is Claude Code's task-specific memory. The root
[`CLAUDE.md`](../../CLAUDE.md) routes here. This index lists **every** file
under `docs/claude/` plus the canonical docs and skills it relies on.

## Start here (instruction hierarchy)

Authority order (highest first) — full statement in
[`CLAUDE.md`](../../CLAUDE.md) § Instruction hierarchy and
[`CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) § Document
Priority:

1. [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) — how you operate: access, honesty, permission tiers, workflows, session discipline.
2. [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) — system architecture, trade/comms pipeline, contracts.
3. [`ROADMAP.md`](../../ROADMAP.md) — **single source** of every milestone/sprint, status, and dates.
4. current sprint log under [`docs/sprint-logs/`](../sprint-logs/) — format: [`SPRINT-LOG-TEMPLATE-CANONICAL.md`](../SPRINT-LOG-TEMPLATE-CANONICAL.md).
5. skills under [`.claude/skills/`](../../.claude/skills/).
6. [`CLAUDE.md`](../../CLAUDE.md) — repo orientation + dashboard REST-API reference.
7. these `docs/claude/*` notes + historical material.

**Every session:** start by reading CLAUDE.md + CLAUDE-RULES-CANONICAL.md +
the latest ROADMAP/sprint entry; end by running the **`doc-freshness`**
skill and logging any minor leftover to
[`health-review-backlog.json`](health-review-backlog.json). GitHub Actions
reference: [`docs/github-actions-workflows.md`](../github-actions-workflows.md).

## Update-as-you-go rule

End every session by updating the smallest relevant doc when you learn
something durable:

- recurring bug → `debug-memory.md` / `bug-log.md`
- cleanup decision → `cleanup-report.md` or `cleanup-policy.md`
- test rule → `testing-policy.md`
- external workflow → `external-delegation.md`, `colab-workflows.md`, `huggingface-workflows.md`
- deployment lesson → `deployment-ops.md`
- secret/key rule → `security-secrets.md`
- minor issue noticed but not fixed → `health-review-backlog.json` (the autonomous `/health-review` drains it)

Remove stale instructions when they waste context; mark superseded docs
historical rather than silently deleting unique content.

## Skills ([`.claude/skills/`](../../.claude/skills/))

Composable workflows — prefer a skill over improvising; chain them. **31 skills**,
grouped by the kind of session they serve. ⚠️ This list was **12 of 31** from
its creation until 2026-08-31 — 19 skills, including `system-review`,
`full-system-audit`, `session-coordination`, `backlog-drain` and `research-driver`,
were absent from the index that routes sessions to them. A session searching here
for "is there a skill for X" got a **negative with no denominator** and would
reasonably conclude none existed. The authoritative list is the directory itself —
`ls .claude/skills/` — and this index is a convenience view of it.

**Review cadence**

- `duty` — The DUTY PASS — the short, bounded session that gives every detected signal an OWNER: reads the one generated due-list (`docs/claude/DUE.md`) and drives each row to a written disposition (acted / filed / escalated / not-due). Start here; it is not a review and never replaces one.
  - ⚠️ **`docs/claude/READOUT.md` sits BESIDE `DUE.md`, and neither supersedes the other.** The due-list says what is DUE; the readout (E1/A1, `scripts/ops/constraint_readout.py --write`) says where the chain is HELD UP, with the book and the money, the in-flight set against the ceiling, and the decisions waiting on a person. Phase D's plan called for deleting the due-list; it was NOT deleted, because four of its source classes — probes, monitoring cadences, the recurrence ledger, red crons and unlanded automation PRs — have no counterpart in the readout. ⚠️ The readout is a **dated snapshot**, not a live read: check its `generated_at` before quoting it, and re-run rather than trusting its age.
- `system-review` — Master SYSTEM REVIEW session — the WORK is the review; the report is just its deliverable.
- `system-report` — Back-compat alias for /system-review — the master SYSTEM REVIEW session (the work is the review; the report is its deliverable).
- `health-review` — Autonomous layer-2 review of the LIVE ICT TRADING BOT's TECHNICAL runtime health — pipeline plumbing, DB integrity, data validity, service state, alert delivery, sprint-doc drift.
- `performance-review` — Autonomous review of the ICT trading bot's TRADING PERFORMANCE and its RESEARCH PIPELINE — per-strategy aggregate stats, per-order-package decision grading, comparison against actual closed-trade PnL, and proposed tweaks to consider.
- `ml-review` — Autonomous review of the ICT bot's ML LIFECYCLE — trainer service health, training cycles since the last review, dataset builds, per-model status (latest training metrics + shadow/live track record), promotion/demotion recommendations against the 3-stage ladder (candidate→shadow→advisory), per-model fit within the unified-confidence framework, and AI-experiment proposals to continue expanding ML coverage.
- `full-system-audit` — The EXHAUSTIVE whole-system audit PROGRAM across all three repos (bot, dashboard, android), both VMs, the git history, and the canonical store — not a quick consistency check, and not a per-file review.
- `backlog-drain` — A DEDICATED session whose only job is CLOSING backlog rows — not reviewing, not filing.
- `doc-freshness` — Session-end (and on-demand) check that the canonical instruction docs do not contradict each other, the code/config on disk, or the changes this session made — AND that this session's material decisions actually landed in every durable surface they belong in (roadmap + sprint log + the right review backlog), so nothing flows through the cracks.
- `workplan-vs-architecture` — Reconcile what the project INTENDED to build (the operator's workplan/goals + ROADMAP milestones) against what is ACTUALLY built (ARCHITECTURE-CANONICAL.md + the code/config on disk).

**Session process**

- `session-coordination` — Binding cross-session workflow governance — the session preflight (read the rules + know your tool/capability limits), the MANDATORY live coordination board (GitHub issue #6927 — post updates + questions, NOT gated on merging), the multi-session MERGE PROTOCOL that serializes PRs so concurrent sessions don't race a merge and force each other into behind-rebase retest churn, and CROSS-SESSION RESOURCE OPTIMIZATION — route CPU-heavy work to free GitHub runners (not the scarce 1-core trainer VM), serialize the VM with a board FIFO lane, and flag any dead run loudly (docs/claude/vm-resource-management.md).
- `session-handoff` — Recognize when a session has run long enough that continuing to a NEW unrelated work item in the SAME context window is wasting compute (repeated context-compaction, cross-subsystem thrash), then close the current unit of work cleanly with no loose ends and hand off with a concrete, self-contained prompt for a fresh session to continue.
- `delegate-work` — How to DELEGATE and PARALLELIZE a big-scope or long-running task across sub-agents and sub-sessions so it runs correctly and efficiently instead of as one slow serial slog.
- `llm-delegate` — Offload a BOUNDED coding/research subtask to a cheap external LLM running as an ephemeral GitHub Actions job, then verify its output before acting.
- `before-asking-the-operator` — TRIGGER any time you are about to write phrases like "you'll need to", "run this locally", "manually...", "SSH in and", "sudo", "open a terminal", "on the VM, edit", "the operator needs to", "go to the dashboard and create", or any other instruction that attributes work to the operator.
- `credentials-and-vm-mutations` — Invoke BEFORE writing any operator-facing instruction that involves credentials, the live VM's runtime state, or systemd.
- `sprint-format` — Write a sprint log in the canonical format for the ICT bot.

**Live ops & data**

- `diag-data` — Retrieve live runtime state from the production VMs (signals, orders, trades, journal tables, service/heartbeat status, journalctl) without asking the operator.
- `vm-ops` — Inspect and act on the production VMs (live trader + trainer) autonomously through GitHub Actions.
- `vm-migration` — Migrate or decommission a production OCI VM (live trader, trainer, or IB gateway) — provision a candidate, cut over, retire the old box — without leaving loose ends.
- `git-actions` — Dispatch this repo's GitHub Actions workflows from a Claude session and read their results.
- `db-wiring` — Verify every part of the system that produces data is wired into the canonical store so there is one uncompromised single source of truth.
- `db-setup` — Set up, locate, and verify the ICT bot's canonical SQLite stores — trade_journal.db (the money DB the live trader produces) and trainer_store.db (the read-mostly trainer/ML sidecar).

**Building & changing the system**

- `new-strategy` — Wiring checklist + scaffold for adding a new live trading strategy to the ICT bot.
- `new-broker` — Wire a new broker (futures, FX, crypto, prop firm) into the bot's execution path.
- `regime-selectivity` — The binding rules for authoring, gating, and flipping a REGIME OFF-CELL — the (trend, vol) cells in config/regime_policy.yaml that drop a strategy's intents before routing.

**Research, testing & ML**

- `research-driver` — The governance layer for open-ended research/build sessions that don't already map onto a fixed review cadence or a narrower domain skill — how Claude picks what to work on, dispatches to the right existing pipeline before freelancing, keeps moving on other work when a specific item is blocked on a pending Tier-3 decision, pings the operator on a binding hourly cadence, recognizes when a recurring ad hoc pattern should be promoted into its own domain skill, and lands the outcome in the right place in ROADMAP.md's structure.
- `backtesting` — Run and interpret strategy backtests for the ICT bot — the standalone research harnesses (scripts/backtest_squeeze.py, backtest_fade.py, backtest_trend.py, backtest_ict_scalp.py, src/backtest/run_backtest_vwap.py), and the trainer-VM sweep mirror surfaced at /api/bot/backtests/sweeps (the M5 `/test` consumer was REMOVED 2026-08-20).
- `exit-refinement` — The binding, repeatable pipeline for building, validating, and shipping EXIT improvements (trailing-stop geometry, stale-stops, giveback-stops, partial-TP ladders, ML exit heads) for any strategy×symbol leg — data → harness lever sweep → E0/E1/E1.5 exit-head → live parity check → Tier-3 flip → first-decision health check — plus the committed coverage matrix that is M20's done-condition.
- `macro-research` — The repeatable pipeline for MACRO / value / event-study research — the ROADMAP_MACRO family (energy event calendars, surprise-vs-consensus, the M28 value sleeve, M29 system-dynamics, COT/crowding, crypto-funding).
- `model-training` — Trigger, monitor, and analyze ML model training for the ICT bot.
- `drift-remediation` — The standing process for FIXING a drifted / degrading ML model instead of reflexively demoting it.

## Governance & session process

- `operating-protocol.md`: consolidated session-wide operating rules (session shape, three-tier merge authority, live-mode invariant, ping-PR pattern).
- `decomposition-rules.md`: normative milestone → sprint → checkpoint contract.
- `sprint-planning.md`: sprint planning policy.
- `session-workflow.md`: start/middle/end checklist.
- `session-handoff.md`: bounded sprint-continuation routine (pairs with `.github/workflows/continue-work.yml`).
- `checkpoint-workflow.md`: resume/stop rules + handoff format.
- `checkpoints/CHECKPOINT_LOG.md`: append-only log of session handoffs; `checkpoints/HANDOFF_TEMPLATE.md` is the per-session template.

## Architecture & runtime reference

- `repo-map.md`: high-level structure, the 9-unit Coordinator, key file locations.
- `comms-architecture.md`: Claude ↔ Telegram operator channel.
- `trading-mode-flags.md`: the runtime mode/feature flags.
- `pipeline-health-check.md`: the in-process health-check suite.
- `exchange-truth-attribution.md`: exchange-truth P&L attribution.
- `closed-flat-invariant.md`: the closed → exchange-flat reconciler.
- `prop-account-state.md`: prop-account configuration & gating.
- `../integrations/prop-accounts-architecture-DESIGN.md`: **scalable prop-trading architecture** — account→ruleset binding, mandatory per-account compatibility matrix, multi-account ticket + discrepancy banner, Telegram-ping execution. **Reference before building any prop-account logic.**
- `ui-processor-audit.md`: which Telegram handlers read DB/env/Coordinator directly (webapp-UI migration order).

## VM ops, access & automation

- `vm-operator-mode.md`: the **live trader VM** trust contract.
- `trainer-vm-mode.md`: the autonomous-Claude **trainer VM** charter.
- `system-actions.md`: tiered production-mutation bridge (allowlist + tiers).
- `diag-relay.md`: PM-side VM diag relay (issue → workflow → JSON comment).
- `vm-resource-management.md`: cross-session resource optimization — free GH runners default, the scarce trainer VM's FIFO board lane (running-never-preempted), GPU-burst budget, and the loud-failure flag.
- `coordination-board.md`: the live cross-session comms board (GitHub issue #6927) — START/QUESTION/DONE, the merge-slot claim, and the VM-lane claim.
- `deployment-ops.md`: Oracle/live bot operations.
- `telegram-pings.md`: what triggers which Telegram ping, and where the wiring lives.
- `web-automations.md`: Claude Code on the Web — recurring automations.
- `auto-task-daily-trade-audit.md`: the daily one-trade lifecycle audit auto-task.

## Data, ML & external compute

- `ml-training-policy.md`: ML training boundaries.
- `training-improvement-workflow.md`: 4-stage autonomous "improve a strategy/model" cycle.
- `huggingface-workflows.md`: datasets/models/Spaces patterns.
- `colab-workflows.md`: Colab notebook patterns.
- `external-delegation.md`: what Claude should delegate off-VM.

## Quality, hygiene & security

- `testing-policy.md`: local vs remote checks.
- `ci-status-checks.md`: the required CI checks.
- `cleanup-policy.md`: safe deletion rules; `cleanup-report.md`: current cleanup backlog.
- `security-secrets.md`: credential rules; `api-key-inventory.md`: where keys live.
- `git-workflow.md`: branch/commit/push rules.
- `debug-memory.md`: recurring bugs and known fixes.
- `bug-log.md`: running bug record; `bug-log-pending/`: staged bug entries.
- `audit-log.md`: standing audit record.
- `health-review-backlog.json`: parking lot for minor issues; drained by `/health-review`.

## Under evaluation / historical (NOT current authority)

- `open-considerations.md`: design questions under evaluation — **not canonical, not directives.** Read before assuming any "we should remove X" is decided (e.g. the Claude comms-bot teardown is UNDECIDED).
- `recurring-sessions.md`: **HISTORICAL** (re-labelled 2026-08-31) — declared a bi-daily/weekly cadence whose trigger functions have zero callers; the skills govern recurring work.
- `milestone-state.md`: **HISTORICAL** (frozen 2026-05-10) — superseded by `ROADMAP.md`; do not use for current status.
- `workplan.md`: **HISTORICAL** (superseded 2026-05-10) — the original operator workplan; canonical docs win over it.
- `architecture-audit-2026-05-02.md`: a point-in-time compliance audit.
- `next-session-prompt.md`: a dated post-sprint handoff prompt (2026-05-14).
- `comms-timer-assessment.md`: one-off assessment of 1-minute comms polling.
- `closed-flat-invariant-phase2-wiring.md`, `env-gate-purge-phase2-annotations.md`: operator-applied patch docs from the S-067 Phase-2 close-out.
- `janitor-2026-05-07-{deadfiles,missing-tests,ui-consolidation}.md`: S-046 Janitor audit records.
