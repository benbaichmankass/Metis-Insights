# Sprint Log: S-CANON-FU-1-workplan-superseded

## Date Range
- Start: 2026-05-10
- End: 2026-05-10

## Objective
- Primary goal: mark `docs/claude/workplan.md` and `docs/workplan.md`
  as superseded by the S-CANON-1 canonical doc set so future
  sessions don't pick them up as authoritative.
- Secondary goals: update dependent docs that previously cited the
  workplan as the decider, without rewriting their substantive
  content.

## Tier
- Tier 1 (docs only; no code or workflow changes).
- Justification: pure documentation update; preserves historical
  text intact under a new "Status: Superseded" banner.

## Starting Context
- Active roadmap items: S-CANON-1 closed in PR #662; the canonical
  doc set is now `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`, current sprint log.
- Prior sprint reference: `docs/sprint-logs/S-CANON-1.md`.
- Known risks at start: future sessions might still treat
  `docs/claude/workplan.md` as the decider unless its top-of-file
  banner explicitly says otherwise.

## Repo State Checked
- Branch or commit reviewed: `claude/post-canon-followups-3Ykp2`
  off `main` at `39e3c28`.
- Deployment state reviewed: n/a (docs-only).
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md`,
  `ROADMAP.md`, `docs/sprint-logs/S-CANON-1.md`.

## Files and Systems Inspected
- Code files inspected: none.
- Config files inspected: none.
- Deployment files inspected: none.
- Docs inspected: `docs/claude/workplan.md`, `docs/workplan.md`,
  `docs/claude/milestone-state.md`, `docs/claude/ci-status-checks.md`,
  `docs/claude/next-session-prompt.md`,
  `docs/claude/bug-log-pending/README.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: none.

## Work Completed
- Added "Superseded 2026-05-10" banner to top of
  `docs/claude/workplan.md` and `docs/workplan.md`. Body left intact.
- Replaced "workplan.md is the decider" authority block in
  `docs/claude/milestone-state.md` and
  `docs/claude/ci-status-checks.md` with a pointer to the canonical
  doc set.
- Updated `docs/claude/next-session-prompt.md` Hard-constraints bullet
  and `docs/claude/bug-log-pending/README.md` fold-in workflow step 5
  to cite `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers
  instead of the workplan.

## Validation Performed
- Tests run: n/a (docs only).
- Dry-runs or staging checks: n/a.
- Manual code verification: `grep -rn 'workplan.md is the decider\|workplan.md.*authoritative\|workplan.md.*authority\|workplan.md.*canonical'`
  before and after to confirm only sprint summaries / historical
  sprint prompts retain the legacy phrasing (skipped intentionally).
- Gaps not yet verified: none.

## Documentation Updated
- Rules doc updates: none (canonical rules already authoritative).
- Architecture doc updates: none.
- Roadmap updates: append a row to the Historical Sprint Ledger.
- GitHub Actions doc updates: none.
- Subsystem doc updates: `ci-status-checks.md`, `milestone-state.md`,
  `next-session-prompt.md`, `bug-log-pending/README.md`.
- Historical docs marked superseded: `docs/claude/workplan.md`,
  `docs/workplan.md`.

## Contradictions or Drift Found
- Contradiction 1: pre-edit, two docs (`milestone-state.md`,
  `ci-status-checks.md`) named `workplan.md` as the decider while
  `ROADMAP.md` and `CLAUDE.md` already pointed to the canonical set.
  Resolved by editing the two outliers.
- Code/doc mismatch: none.

## Risks and Follow-Ups
- Remaining technical risks: none.
- Remaining product decisions (Tier 3): none.
- Blockers: none.

## Deferred Items
- Historical sprint prompts under `docs/sprints/*-prompt.md` and
  sprint summaries under `docs/sprint-summaries/*` still reference
  the workplan; these are kept untouched per task instruction
  (historical record).

## Next Recommended Sprint
- Suggested next sprint: S-CANON-FU-2-cfi-wiring (already shipped
  alongside this task on the same branch; see
  `S-CANON-FU-2-cfi-wiring.md`).
- Why next: surfaces the closed-flat invariant on the live tick loop
  behind an env gate, requested in the same follow-up batch.
- Required verification before starting: none — independent task.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
