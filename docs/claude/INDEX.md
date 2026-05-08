# Claude instruction index

Use this directory as Claude Code's task-specific memory. The root `CLAUDE.md` routes here.

**Always start a session by reading `checkpoints/CHECKPOINT_LOG.md` first** —
it tells you exactly where to resume. See `checkpoint-workflow.md` for the rules.

## Update-as-you-go rule

Every session must end by updating the smallest relevant doc when it learns something durable:

- recurring bug → `debug-memory.md`
- cleanup decision → `cleanup-report.md` or `cleanup-policy.md`
- test rule → `testing-policy.md`
- external workflow → `external-delegation.md`, `colab-workflows.md`, or `huggingface-workflows.md`
- deployment lesson → `deployment-ops.md`
- secret or key handling rule → `security-secrets.md`

Remove stale instructions when they waste context.

## Files

### Workflow foundation (M-S0, 2026-05-06)

- `../workplan.md` (top-level): master workplan — goal, priorities, milestone types, merge tiers, VM rules.
- `milestone-state.md`: **read second** (after `CHECKPOINT_LOG.md`). Quick-glance "where the program is right now" — active milestone, queued milestones, open blockers.
- `operating-protocol.md`: consolidated session-wide operating rules (session shape, three-tier merge authority, live-mode invariant, ping-PR pattern).
- `decomposition-rules.md`: normative milestone → sprint → checkpoint contract.

### Session resume

- `checkpoint-workflow.md`: **read first.** Resume rules, stop rules, handoff format.
- `checkpoints/CHECKPOINT_LOG.md`: append-only log of session handoffs (source of truth for "where to resume").
- `checkpoints/HANDOFF_TEMPLATE.md`: template every session copies into the log at the end.
- `session-workflow.md`: start/middle/end checklist.
- `repo-map.md`: high-level structure and entry points. **Updated S-008:** includes 9-unit Coordinator table and key file locations.
- `debug-memory.md`: recurring bugs and known fixes.
- `cleanup-policy.md`: safe deletion rules.
- `cleanup-report.md`: current cleanup backlog.
- `external-delegation.md`: what Claude should delegate.
- `colab-workflows.md`: Colab notebook patterns.
- `huggingface-workflows.md`: datasets/models/Spaces patterns.
- `testing-policy.md`: local vs remote checks.
- `ml-training-policy.md`: ML training boundaries.
- `training-improvement-workflow.md`: 4-stage autonomous "improve a strategy / model" cycle (research → notebook → Colab run → recommendations PR).
- `deployment-ops.md`: Oracle/live bot operations.
- `diag-relay.md`: how a PM-side / web-sandbox session fetches `/api/diag/*` data via the `vm-diag-snapshot` GitHub Actions relay (open a labelled issue → workflow comments JSON back → close). Read this before debugging VM-side state from a sandbox. The session-capabilities matrix that explains *why* the relay exists is in the root `CLAUDE.md` § "PM-side session capabilities".
- `git-workflow.md`: branch, commit, push rules.
- `security-secrets.md`: credential rules.
- `google-drive-master-secrets.md`: SOPS-encrypted master secrets workflow — fill, encrypt, render lean .env files.
- `ui-processor-audit.md`: which Telegram bot handlers read DB / env / Coordinator directly and which need a `src/ui/processor.py` API before a webapp UI can be added without forking logic. Per-handler migration order in § 5.
