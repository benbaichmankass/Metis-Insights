# S-AI-WS10-FU — Periodic architecture-doc audit

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS10.md`](S-AI-WS10.md)
**Status:** ✅ COMPLETE — weekly schedule + workflow_dispatch + issue-trigger.

## Goal

Catch architecture-doc drift that the per-PR `arch-doc-guard`
misses: paths in the Verification Checklist that quietly stopped
existing because a file moved or was deleted in a refactor.
Per-PR enforcement can't see drift that accumulates across
many small PRs; a periodic full-doc audit does.

## Decisions

- **Verification-Checklist-only.** The audit parses the
  `## Verification Checklist (current state)` section of
  `docs/ARCHITECTURE-CANONICAL.md`, walks every `[x]` line, and
  resolves each backtick-quoted path against the repo. Other
  sections (Change log, Known gaps) aren't auditable in this way
  — they're free-text history, not present-tense assertions.
- **First backtick path per line is authoritative.** A line like
  `Comms directory: \`comms/\` with \`requests/\`, \`archive/\`,
  \`schema/\`` has multiple backtick spans. The first
  (`comms/`) is the primary assertion; the rest are sub-paths or
  commentary. Auditing all four would produce false positives
  whenever a sub-path is relative-to-parent rather than
  relative-to-repo-root.
- **Brace expansion.** `deploy/ict-*.{service,timer}` is a pattern
  that needs the brace expanded into `deploy/ict-*.service` and
  `deploy/ict-*.timer`, then each glob-resolved. Matched if EITHER
  alternative exists.
- **Three triggers.** Weekly cron (Mondays 12:00 UTC),
  `workflow_dispatch` for ad-hoc, and `issues.opened` labelled
  `doc-audit-now` so Claude sessions can fire an audit
  autonomously.
- **Idempotent issue filing.** When drift is detected, the
  workflow searches for an open issue with the day-stamped title
  before creating; re-runs on the same day don't pile up
  duplicate issues. Day-stamping means we DO get a fresh issue
  per day if drift persists — operator visibility wins over
  noise reduction here.
- **Always exits 0.** The audit script never fails. Empty drift
  is the success case; non-empty drift gets filed as an issue but
  the script and workflow both still complete cleanly. Same posture
  as the rest of the operational scripts (rotate_shadow_log,
  arch_doc_guard).
- **`doc-drift` label tags the filed issue** so the operator can
  filter the issue list by label.

## Deliverables

- `scripts/ops/audit_verification_checklist.py` (new) — ~120 LOC.
  `parse_checklist(text)`, `_path_exists(repo, candidate)` with
  brace expansion, `audit(repo, doc)` returning a JSON-serialisable
  report, `main(argv)` CLI entrypoint.
- `.github/workflows/doc-audit-weekly.yml` (new) — three-trigger
  workflow. On drift, posts a `doc-drift`-labelled issue via
  `actions/github-script`. On issue-trigger path, also comments
  on the triggering issue + closes it.
- `.github/workflows/bootstrap-labels.yml` — new `doc-audit-now`
  + `doc-drift` labels.
- `tests/test_audit_verification_checklist.py` (new) — 12 tests
  across `parse_checklist`, `_path_exists` (simple / missing /
  brace / directory), `audit` (clean / missing / first-path-only
  heuristic / missing doc), `main(argv)`, and live-repo smoke
  tests that the parser handles the real doc.

## Acceptance

- [x] `pytest tests/test_audit_verification_checklist.py` —
      12 / 12 pass.
- [x] `ruff check` clean on script + tests.
- [x] Workflow YAML parses.
- [x] Live-repo audit currently clean (`missing: []`).
- [x] First-backtick-path-per-line heuristic correctly handles
      the "Comms directory: `comms/` with `requests/`, ..." line
      (verified by `test_only_first_backtick_path_per_line_checked`).
- [x] Brace patterns (`{service,timer}`) resolve correctly.
- [x] Issue creation is idempotent on the same day (verified
      by the workflow's `search.issuesAndPullRequests` lookup
      before create).

## Out of scope (filed for follow-ups)

- **Audit Change-log freshness.** A row in the Change log table
  should match a real PR; auditing this would catch stale or
  invented entries. Filed.
- **Cross-doc audit.** `docs/CLAUDE.md`, `docs/CLAUDE-RULES-
  CANONICAL.md`, and `docs/architecture/ai-model-platform.md`
  also reference repo paths. A future audit could walk those
  docs too, with per-doc verification-section conventions.
- **Auto-PR-to-fix.** When drift is detected, the workflow could
  open a PR that comments out the stale line + suggests the
  replacement. Reduces operator work but adds complexity; filed.
- **Slack / Telegram alert** on drift in addition to the GitHub
  issue.

## Live runtime impact

None — workflow-only. Runs on the GitHub-hosted runner; touches
no VM. The audit script lives at `scripts/ops/` and is also
runnable locally (`python scripts/ops/audit_verification_checklist.py`)
for fast iteration when updating the architecture doc.

## Operator usage

```
# Ad-hoc audit:
# Actions → doc-audit-weekly → Run workflow

# Or via issue (Claude sessions):
# Open new issue with label `doc-audit-now` — workflow runs, posts
# summary as issue comment, closes the issue.

# Read drift issues:
# Issues filter → label:doc-drift

# Local check (e.g. before editing the architecture doc):
python scripts/ops/audit_verification_checklist.py
```
