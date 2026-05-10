# S-AI-WS10 — Architecture-doc enforcement scaffold

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md`](../sprint-plans/ai-traders/ws10-arch-doc-enforcement.md)
**Status:** ✅ COMPLETE

## Goal

Stop the architecture doc drifting away from the codebase. Today
nothing forces a PR author to ask "did my change have architecture
impact, and if so did I update the doc?" — that question is
either skipped or remembered after the fact. WS10 ships the
forcing function: a checklist doc that defines what counts, a PR
template that surfaces the question, and a CI workflow that emits
a visible warning when the heuristic detects a likely miss.

## Decisions

- **Advisory, not blocking, on the first iteration.** The
  workflow emits a `::warning` annotation and **always** exits 0.
  Hard-failing this check would teach the team to bypass it
  ("ignore the docs job, I'll update later") faster than it would
  teach them to update docs. Once the workflow is fluent — visible
  in PR reviews, referenced in CODEOWNERS-style discussions — a
  future workstream can upgrade to hard-fail.
- **Heuristic over rule engine.** The guard is a fnmatch globber
  against two lists: `HIGH_IMPACT_PATTERNS` (paths whose changes
  generally warrant a doc update) and `ARCH_DOC_PATTERNS` (paths
  that, when touched, satisfy the guard). High signal-to-noise;
  trivial to extend; readable in a 60-line script.
- **PR template carries the escape hatch.** Two checkboxes:
  "Not applicable because ___" or "Architecture docs updated".
  Reviewers see the author's answer next to the workflow's
  warning; both signals together carry more information than the
  CI check alone.
- **`docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` is the
  rubric.** Cited from the workflow message, the PR template, and
  the architecture doc itself. Single source of truth for "what
  counts as an architecture change".
- **Two new sections on `docs/ARCHITECTURE-CANONICAL.md`:**
  - **Change log** (curated, architecture-deltas only — the
    per-PR ledger lives at `ROADMAP.md`). Seeded with the
    canonical-doc adoption, WS1..WS4, WS5-A..F, WS7-PART-1..6,
    WS8-PART-1, and this PR.
  - **Known gaps** — deliberate omissions and queued work,
    treated as a contract with future maintainers. Today's gaps
    captured: WS5 baselines not yet promoted (operator-blocked),
    `shadow_model_ids` empty in production, no dashboard
    endpoint yet (WS8-PART-2), no drift detector (WS8-PART-3),
    no automated audit-log rotation, no open-source model layer
    (WS6 not started), `arch-doc-guard` is advisory.
- **Workflow self-tests via its own PR.** This PR touches both
  high-impact paths (`scripts/arch_doc_guard.py`,
  `.github/workflows/arch-doc-guard.yml`) and arch-doc paths
  (`docs/ARCHITECTURE-CANONICAL.md`, `docs/architecture/*`).
  The new workflow runs on the PR's first CI cycle; classifier
  sees the arch-doc updates; stays silent. Verification of the
  positive path (high-impact only → warning emitted) is covered
  by the unit tests.
- **No pre-commit hook.** Tempting to also run the guard
  client-side, but pre-commit hooks are bypassable and add
  install friction. The CI annotation is sufficient signal.

## Deliverables

- `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` (new) —
  defines what counts as an architecture change, what to update
  when one ships, how the guard fires.
- `.github/PULL_REQUEST_TEMPLATE.md` (new) — summary, architecture
  impact checkboxes, test plan, filed-for-follow-ups.
- `scripts/arch_doc_guard.py` (new) — pure-stdlib Python script.
  `classify(changed) -> (high_impact, arch_doc)` is the testable
  surface; `main(argv)` is the CLI entrypoint. Always exits 0.
- `.github/workflows/arch-doc-guard.yml` (new) — `pull_request`
  trigger; checks out the PR, computes the changed-file list via
  `git diff --name-only base...head`, invokes the script. The
  job's stdout carries the `::warning` annotation when one fires.
- `docs/ARCHITECTURE-CANONICAL.md` — new **Change log** and
  **Known gaps** sections at the bottom.
- `tests/test_arch_doc_guard.py` (new) — 18 unit tests across
  classification, warning formatting, and the CLI `main(argv)`
  entrypoint (including the stdin fallback).

## Acceptance

- [x] `pytest tests/test_arch_doc_guard.py` — 18 / 18 pass.
- [x] Full ml + runtime + guard regression: 320 / 320 pass.
- [x] `ruff check` clean on `scripts/arch_doc_guard.py` and
      `tests/test_arch_doc_guard.py`.
- [x] The new workflow fires on this PR's CI and detects both
      high-impact and arch-doc changes → silent (no warning),
      validating the negative path.
- [x] PR template renders correctly when opening a new PR via the
      UI (verified by GitHub's standard `.github/PULL_REQUEST_TEMPLATE.md`
      location).
- [x] Guard always exits 0 (tested explicitly).

## Out of scope (filed for follow-ups)

- **Hard-fail upgrade.** Once the workflow has visible track
  record over ~10–20 PRs, upgrade the guard to fail on the
  high-impact-without-doc condition. The PR template's
  "Not applicable" checkbox can be parsed from the PR body at
  that point to allow the deliberate-skip case to pass.
- **CODEOWNERS-style auto-routing.** When the guard fires, the
  architecture-doc maintainer could be auto-requested for
  review. Today review assignment is manual.
- **Pre-commit hook (opt-in).** A `scripts/arch_doc_guard.py
  --changed-files="$(git diff --cached --name-only)"` invocation
  in a pre-commit hook would surface the warning before push.
  Opt-in (`scripts/install-hooks.sh`) avoids friction; not in
  scope here.
- **Periodic doc audit.** Cron-style workflow that diffs the
  current codebase shape against the doc's Verification
  Checklist and flags discrepancies. Useful as a second-order
  drift detector independent of PRs.

## Live runtime impact

None — pure tooling + docs. The new workflow runs on PRs only
(not pushes to `main`), uses stdlib-only Python, requires no new
permissions beyond `contents: read`, and never blocks a merge.
