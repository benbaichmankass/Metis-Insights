# WS10 — Architecture-doc enforcement

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** ✅ DONE 2026-05-11 (S-AI-WS10 + S-AI-WS10-FU + S-AI-WS10-CLOSEOUT)

## Objective

Make documentation maintenance mandatory so the architecture doc does not
drift away from the codebase.

## What shipped

### S-AI-WS10 (2026-05-10) — Scaffold

- [`docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`](../../architecture/ARCHITECTURE-CHANGE-CHECKLIST.md) — defines what counts as an architecture change and what to update when one ships.
- [`.github/PULL_REQUEST_TEMPLATE.md`](../../../.github/PULL_REQUEST_TEMPLATE.md) — "Architecture impact" checkboxes (Not applicable / Architecture docs updated).
- [`.github/workflows/arch-doc-guard.yml`](../../../.github/workflows/arch-doc-guard.yml) + [`scripts/arch_doc_guard.py`](../../../scripts/arch_doc_guard.py) — advisory CI guard that emits `::warning` when high-impact paths change without an arch-doc touch. Always exits 0 by design.
- [`docs/ARCHITECTURE-CANONICAL.md`](../../ARCHITECTURE-CANONICAL.md) gained dedicated **Change log** and **Known gaps** sections at the bottom.

### S-AI-WS10-FU (2026-05-10) — Follow-ups

- [`scripts/git-hooks/pre-commit`](../../../scripts/git-hooks/pre-commit) + [`scripts/install-hooks.sh`](../../../scripts/install-hooks.sh) — opt-in local pre-commit hook wrapping the same `arch_doc_guard.py`. Operators who install it get blocking behaviour locally; CI stays advisory.
- [`.github/workflows/doc-audit-weekly.yml`](../../../.github/workflows/doc-audit-weekly.yml) + [`scripts/ops/audit_verification_checklist.py`](../../../scripts/ops/audit_verification_checklist.py) — weekly cron that audits paths in ARCHITECTURE-CANONICAL.md's Verification Checklist, files a `doc-drift` issue when paths break.

### S-AI-WS10-CLOSEOUT (2026-05-11) — Drift refresh + roadmap close

- Refreshed the **Change log** in `docs/ARCHITECTURE-CANONICAL.md` with seven missing entries (S-AI-WS8-PART-2/3, S-AI-WS7-FU, S-AI-WS9-FU, S-AI-WS10-FU, plus the 2026-05-11 work: S-AUTH-SPLIT, S-AI-WS9-AUTORETRY, S-AI-WS5-BOOTSTRAP, S-AI-WS8-DASHBOARD, and this close-out).
- Refreshed the **Known gaps** section: dropped four already-resolved entries (dashboard endpoint, drift detector, audit-log rotation, missing trainer service body), updated three entries to reflect today's state (baseline promotion bootstrap, trainer-VM provisioning autoretry, YAML-wiring boundary), added two new entries (no Change-log row validation, no roadmap-consistency audit).
- Marked the WS10 + WS8 rows DONE in [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md).

The close-out itself was the test: a Claude session inspecting the docs caught a 24-hour drift in the Change log + Known Gaps and fixed it in the same PR. That's exactly the loop WS10 is supposed to enable.

## Acceptance — verified

- [x] Repo has an enforceable architecture-update workflow.
  - The guard is advisory by deliberate design choice — see the Known Gap entry "`arch-doc-guard` is advisory, not blocking" in `docs/ARCHITECTURE-CANONICAL.md`. Upgrade to blocking is filed for a future workstream once the team has demonstrated fluent use without bypass.
- [x] A reviewer can see architecture impact directly in the PR or linked docs.
  - The PR template surfaces the checklist link inline; reviewers tick "Not applicable" or "Architecture docs updated" before merge.

## Filed for follow-ups

These are intentional deferrals captured in the Known Gaps section of `docs/ARCHITECTURE-CANONICAL.md`:

1. **Upgrade `arch-doc-guard` to blocking.** Revisit after ~20 successful PR cycles without anyone tripping the bypass path. Tracking is the Known Gap entry of the same name.
2. **`arch_doc_guard.py` validates a Change-log row was added.** Currently the guard only checks "did any arch-doc path get touched"; it doesn't enforce "a new Change-log row appended". Easy to add but premature without (1).
3. **Audit the roadmap for consistency.** The weekly doc-audit currently checks the Verification Checklist for broken paths; it doesn't check roadmap rows for consistency (e.g. a workstream marked DONE in the roadmap but in-progress in a sprint plan, or sprint logs that reference non-existent sprint IDs).
