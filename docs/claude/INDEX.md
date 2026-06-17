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

Composable workflows — prefer a skill over improvising; chain them.

- `diag-data` — pull live runtime state from the VMs (read-only).
- `vm-ops` — tiered VM inspection + mutation via GitHub Actions.
- `git-actions` — dispatch this repo's workflows + read results.
- `db-wiring` — verify every writer lands in the canonical store.
- `db-setup` — locate/create/verify the canonical SQLite stores.
- `backtesting` — run + interpret strategy backtests.
- `model-training` — drive the trainer VM's ML lifecycle.
- `new-strategy` — wire a new strategy through the execution layer.
- `sprint-format` — write a canonical sprint log.
- `workplan-vs-architecture` — reconcile intent vs design vs reality.
- `health-review` — autonomous layer-2 review of the live bot's runtime.
- `doc-freshness` — session-end canonical-doc contradiction sweep.

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
- `deployment-ops.md`: Oracle/live bot operations.
- `telegram-pings.md`: what triggers which Telegram ping, and where the wiring lives.
- `web-automations.md`: Claude Code on the Web — recurring automations.
- `recurring-sessions.md`: the audit / strategy-improvement / model-training recurring sessions.
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
- `milestone-state.md`: **HISTORICAL** (frozen 2026-05-10) — superseded by `ROADMAP.md`; do not use for current status.
- `workplan.md`: **HISTORICAL** (superseded 2026-05-10) — the original operator workplan; canonical docs win over it.
- `architecture-audit-2026-05-02.md`: a point-in-time compliance audit.
- `next-session-prompt.md`: a dated post-sprint handoff prompt (2026-05-14).
- `comms-timer-assessment.md`: one-off assessment of 1-minute comms polling.
- `closed-flat-invariant-phase2-wiring.md`, `env-gate-purge-phase2-annotations.md`: operator-applied patch docs from the S-067 Phase-2 close-out.
- `janitor-2026-05-07-{deadfiles,missing-tests,ui-consolidation}.md`: S-046 Janitor audit records.
