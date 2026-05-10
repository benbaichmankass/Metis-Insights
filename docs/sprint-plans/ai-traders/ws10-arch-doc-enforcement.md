# WS10 — Architecture-doc enforcement

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — schedule near WS8 / final close.

## Objective

Make documentation maintenance mandatory so the architecture doc does not
drift away from the codebase.

## Tasks

1. Add `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`.
2. Require any PR changing data schemas, model boundaries, pipeline
   stages, deployment stages, or runtime responsibilities to update the
   architecture docs.
3. Add a PR-template checkbox for architecture updates.
4. Add a changelog table inside the architecture doc capturing date,
   change, files touched, operator impact.
5. Add a `Known Gaps` section so incomplete work is visible rather than
   implied.

## Acceptance

- Repo has an enforceable architecture-update workflow.
- A reviewer can see architecture impact directly in the PR or linked
  docs.
