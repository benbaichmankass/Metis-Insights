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

- `checkpoint-workflow.md`: **read first.** Resume rules, stop rules, handoff format.
- `checkpoints/CHECKPOINT_LOG.md`: append-only log of session handoffs (source of truth for "where to resume").
- `checkpoints/HANDOFF_TEMPLATE.md`: template every session copies into the log at the end.
- `session-workflow.md`: start/middle/end checklist.
- `repo-map.md`: high-level structure and entry points.
- `debug-memory.md`: recurring bugs and known fixes.
- `cleanup-policy.md`: safe deletion rules.
- `cleanup-report.md`: current cleanup backlog.
- `external-delegation.md`: what Claude should delegate.
- `colab-workflows.md`: Colab notebook patterns.
- `huggingface-workflows.md`: datasets/models/Spaces patterns.
- `testing-policy.md`: local vs remote checks.
- `ml-training-policy.md`: ML training boundaries.
- `deployment-ops.md`: Oracle/live bot operations.
- `git-workflow.md`: branch, commit, push rules.
- `security-secrets.md`: credential rules.
- `google-drive-master-secrets.md`: SOPS-encrypted master secrets workflow — fill, encrypt, render lean .env files.
