# Sprint Log: S-CANON-FU-3-branch-protection

## Date Range
- Start: 2026-05-10
- End: 2026-05-10 (PARTIAL — workflow + docs are ready;
  operator-gated PAT setup pending)

## Objective
- Primary goal: make
  `.github/workflows/branch-protection-sync.yml` actually enforce
  the canonical required-checks list on `main`
  (`pytest-collect`, `secret-scan`, `ruff-lint`, `dry-run-guard`).
- Secondary goals: confirm the required-contexts list matches the
  actual job IDs (after the unique-job-id rename in PR #671);
  fix the stale `the-lizardking` owner reference in the verify
  command in `docs/claude/ci-status-checks.md`; capture the
  pending operator steps in this log so the verification can be
  picked up cleanly when the PAT lands.

## Tier
- Tier 2 (configuration changes the gate on `main`).
- Justification: enabling the workflow forces every PR to satisfy
  4 required checks. Operator must create a fine-grained PAT and
  add `BRANCH_PROTECTION_TOKEN` as a repo secret before the
  workflow can mutate protection. Until then, the workflow
  no-ops on a missing secret per the explicit `token_check` step.

## Starting Context
- Active roadmap items: S-CANON-1 audit (PRs #670, #671, #674)
  identified that the workflow is correct but no-ops because the
  PAT secret is unset.
- Prior sprint reference: PR #671 (unique workflow-prefixed job
  IDs that the `REQUIRED_CONTEXTS` list now matches), PR #670
  (label bootstrap fix), PR #674 (per-job timeouts +
  `contents: read`).
- Known risks at start: stale verify command in the CI doc still
  pointed at the legacy `the-lizardking/...` namespace.

## Repo State Checked
- Branch or commit reviewed: `claude/post-canon-followups-3Ykp2`
  off `main` at `39e3c28`.
- Deployment state reviewed: n/a (config-only).
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/github-actions-workflows.md`,
  `docs/claude/ci-status-checks.md`.

## Files and Systems Inspected
- Code files inspected: none.
- Config files inspected: none.
- Deployment files inspected: none.
- Docs inspected: `docs/claude/ci-status-checks.md`,
  `docs/github-actions-workflows.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected:
  - `branch-protection-sync.yml` (token-check guard at lines
    42–58; `REQUIRED_CONTEXTS` at line 67;
    `["pytest-collect","secret-scan","ruff-lint","dry-run-guard"]`).
  - `pytest-collect.yml` (job id `pytest-collect`) ✓
  - `secret-scan.yml` (job id `secret-scan`) ✓
  - `ruff-lint.yml` (job id `ruff-lint`) ✓
  - `dry-run-guard.yml` (job id `dry-run-guard`) ✓
  - Advisory guards confirmed running but not in the required
    list: `repo-inventory.yml`, `silent-empty-guard.yml`,
    `env-gate-guard.yml`.

## Work Completed
- Verified the four `REQUIRED_CONTEXTS` entries match the actual
  job IDs in the corresponding workflow files.
- Fixed stale `gh api repos/the-lizardking/...` line in
  `docs/claude/ci-status-checks.md` § Verify; replaced with
  `benbaichmankass/...`.
- Added a "Status (2026-05-10, post-canon-followups)" subsection
  to `docs/claude/ci-status-checks.md` that captures the
  current readiness state (workflow correct, secret unset,
  protection unchanged) and the expected first-run notice when
  the operator dispatches it.
- Filed this sprint log to anchor the operator-gated steps that
  remain — when the operator lands the PAT, the same log gets
  the verification appended (or a new follow-up log if the
  operator prefers).

## Validation Performed
- Tests run: n/a (config-only).
- Dry-runs or staging checks: n/a — workflow can't run until the
  secret is set.
- Manual code verification: matched each entry in
  `REQUIRED_CONTEXTS` against `jobs.<job-id>:` in the four
  workflow files. Confirmed `branch-protection-sync.yml`
  preserves non-status-checks fields via the GET-then-PUT shape.
- Gaps not yet verified (operator-gated):
  - ~~The fine-grained PAT (Administration: write, scoped to this
    repo only) has not been created yet.~~ **Done 2026-05-10** —
    operator confirmed the PAT exists and `BRANCH_PROTECTION_TOKEN`
    is set as a repo secret.
  - The first `branch-protection-sync` run with the token in place
    will fire automatically when this PR merges to `main` (the
    workflow's `push: branches: [main]` trigger).
  - Test PR confirming merge-button gating still pending — will
    open immediately after this PR's `branch-protection-sync` run
    posts the expected "Required contexts now: [...]" notice.

## Operator runbook (pending action)
1. Create a fine-grained PAT scoped to **only**
   `benbaichmankass/ict-trading-bot` with permission
   "Administration: write". 1-year expiry; calendar reminder to
   rotate.
2. Add the PAT as a repo secret named `BRANCH_PROTECTION_TOKEN`.
3. Trigger one `workflow_dispatch` run of
   `branch-protection-sync`. Final notice should read:
   `Branch protection updated. Required contexts now: ["pytest-collect","secret-scan","ruff-lint","dry-run-guard"]`.
4. Open a trivial-doc-change test PR. Confirm:
   - all 4 required checks appear,
   - the merge button is gated on those 4 passing,
   - `repo-inventory`, `silent-empty-guard`, `env-gate-guard`
     run but are advisory.
5. If anything misbehaves: remove `BRANCH_PROTECTION_TOKEN`; the
   workflow re-no-ops on the next run, leaving protection at its
   prior state.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Roadmap updates: append a row to the Historical Sprint Ledger.
- GitHub Actions doc updates: none (the canonical reference
  already documented the workflow correctly).
- Subsystem doc updates: `docs/claude/ci-status-checks.md`
  (verify command + status subsection).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: `ci-status-checks.md` § Verify still pointed
  at the legacy owner (`the-lizardking`). Resolved.
- Code/doc mismatch: none — workflow file is already correct.

## Risks and Follow-Ups
- Remaining technical risks: when the operator does enable the
  PAT, an existing manual / Colab-applied protection on `main`
  could differ from the spec the workflow PUTs. The workflow
  preserves non-status-checks fields explicitly, but the operator
  should diff the current protection JSON against the workflow's
  spec before the first run.
- Remaining product decisions (Tier 3): none — the four required
  contexts are the canonical list; changes to that list go via
  `REQUIRED_CONTEXTS` edit + commit + push.
- Blockers: operator action on the PAT.

## Deferred Items
- Steps 1–5 of the operator runbook above. Once executed, append
  the verification record (run URL, the actual `Required
  contexts now: [...]` notice, the test-PR URL) to this log or
  to a follow-up log.

## Next Recommended Sprint
- Suggested next sprint: operator executes the runbook above;
  Claude appends the verification record to this log.
- Why next: closes out the third post-canon follow-up.
- Required verification before starting: PAT created + secret
  added.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
