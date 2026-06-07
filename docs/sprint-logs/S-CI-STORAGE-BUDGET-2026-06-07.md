# Sprint Log: S-CI-STORAGE-BUDGET-2026-06-07

## Date Range
- Start: 2026-06-07
- End: 2026-06-07

## Objective
- Primary goal: Stop GitHub Actions storage from hitting / sitting at the **100 %** of the 0.5 GB free quota that the operator was alerted on, and keep the repo inside the free budget going forward.
- Secondary goals: Restore the autonomy contract for one-shot recoveries (issue-driven dispatch, not operator-clicks-in-UI), and fix the cancelled-state silent-hang in the purge workflow.

## Tier
- Tier 1 (CI / tooling / observability only — no order-path, runtime, or config-yaml impact).
- Justification: every change in this sprint touches `.github/workflows/*.yml`, `docs/*`, and a sprint log. No production code, no DB writeback, no order-path code, no strategy/risk config.

## Starting Context
- Active roadmap items: none directly related. This was an unplanned, reactive sprint triggered by the GitHub email "You have used 100 % of the Actions storage included for the benbaichmankass account."
- Prior sprint reference: independent of the M14 / S-MLOPT-S15a work landing in parallel (#2919, #2928, #2930).
- Known risks at start:
  - If the operator had a $0 Actions budget set, Actions could already be blocked.
  - Storage at 100 % means even the cleanup workflow needs to fit inside whatever capacity remains.

## Repo State Checked
- Branch or commit reviewed: `main` at `864ea533…` (the live tip when the sprint opened); plus the three feature branches we shipped during the sprint.
- Deployment state reviewed: not applicable — no runtime / VM / deploy changes.
- Canonical docs reviewed: `CLAUDE.md` § PM-side session capabilities (workflow-dispatch precedents), `docs/github-actions-workflows.md` § Repo / branch admin.

## Files and Systems Inspected
- Code files inspected: none (no `src/` changes).
- Config files inspected: none (no `config/` changes).
- Deployment files inspected: none.
- Docs inspected: `CLAUDE.md`, `docs/github-actions-workflows.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected (all of `.github/workflows/`):
  - **Edited (PR #2922)** — `purge-artifacts.yml` (new); `system-actions.yml`, `rotate-account-keys.yml`, `continue-work.yml`, `health-snapshot.yml`, `trainer-vm-diag.yml`, `vm-web-api-recover.yml`, `vm-ib-gateway-recover.yml`, `vm-ib-gateway-stop.yml`, `vwap-backtest.yml`, `ict-scalp-backtest.yml`, `repo-inventory.yml`, `training-rerun-5m.yml`, `sync-vm-secrets.yml` (retention caps).
  - **Edited (PR #2924)** — `purge-artifacts.yml` (issue trigger); `bootstrap-labels.yml` (label).
  - **Edited (PR #2929)** — `purge-artifacts.yml` (timeout + parallelism + reply-on-cancel).
  - Inspected and **left alone** because they either had no `upload-artifact` step or already gated uploads to `workflow_dispatch` only: `vm-diag-snapshot.yml` (already gated), `get-diag-token.yml` (1-day retention, fine), every CI-guard workflow (no artifact uploads).

## Work Completed
- **PR #2922 — `ci: cap workflow artifact retention at 7d + add purge-artifacts.yml`** (merged): capped every artifact-uploading workflow at ≤ 7 days retention (`repo-inventory` at 3 d, `get-diag-token` at 1 d unchanged) and shipped a new `purge-artifacts.yml` with `schedule` (daily 03:00 UTC) + `workflow_dispatch` triggers.
- **PR #2924 — `ci(purge-artifacts): issue-driven trigger so Claude can dispatch autonomously`** (merged): added `issues.opened` trigger gated to label `purge-artifacts-now`, optional issue-body overrides for `older_than_days` / `dry_run`, reply-to-issue + close-on-complete steps, and the label itself in `bootstrap-labels.yml`. Restored the autonomy contract (the v1 workflow forced the operator to click "Run workflow" in the UI, which CLAUDE.md explicitly forbids).
- **PR #2929 — `fix(purge-artifacts): 60-min timeout + parallel deletes + reply on cancel`** (merged): bumped `timeout-minutes 10 → 60`; parallelised the DELETE loop in chunks of `CONCURRENCY=10` via `Promise.all`; switched the reply step to `if: always()` so cancelled runs post the partial summary (the v2 workflow's first one-shot purge cancelled at 10 min mid-delete and silently left issue #2927 open with no comment — indistinguishable from a wedge).
- **One-shot recovery purges run autonomously**:
  - Issue #2927 fired the v2 workflow; cancelled at the 10-min timeout after clearing ~2 400 artifacts.
  - Issue #2931 fired the v3 workflow after PR #2929 merged; cleared the remaining **668 artifacts (4.5 MiB)** in 25 s.
  - Issue #2927 manually closed with a stale-explainer comment.
- **Doc updates this PR** — `docs/github-actions-workflows.md` (catalogue entry now lists the issue trigger, 60-min timeout, parallel deletes, reply-on-cancel; summary table marks Pattern A + label) and `CLAUDE.md` (added `purge-artifacts.yml` to the workflow-dispatch precedents list).

## Validation Performed
- Tests run: `pytest-collect` + `pytest-run` green on every shipped PR (#2922, #2924, #2929).
- Dry-runs or staging checks: none ran the workflow in dry-run mode (`dry_run=true`); the issue-driven path was validated by the two recovery purges actually running (real DELETEs).
- Manual code verification:
  - YAML linted (`python -c "import yaml; yaml.safe_load(...)"`) before each push.
  - Workflow logs read directly after each dispatch (issue #2927 run + issue #2931 run) to confirm behaviour matched the script.
  - Issue #2931 closed with comment `artifacts found: 668, deleted: 668, failed: 0` confirms the parallel + reply-on-always paths both work.
- Gaps not yet verified:
  - The GitHub billing UI's storage meter has a lag; the operator should eyeball https://github.com/settings/billing once it refreshes to confirm the cap is clear of the 100 % alert state.
  - The daily 03:00 UTC cron hasn't fired yet; first scheduled run on 2026-06-08 will be the steady-state validation.

## Documentation Updated
- Rules doc updates: `CLAUDE.md` — added `purge-artifacts.yml` to the precedents list for the workflow-dispatch contract in § PM-side session capabilities → Workarounds shipped.
- Architecture doc updates: none (no architecture impact).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none (no pipeline impact).
- Roadmap updates: none. Pure CI / tooling sprint; not tracked on ROADMAP.md.
- GitHub Actions doc updates: `docs/github-actions-workflows.md` — added Pattern A to the summary-table row for `purge-artifacts.yml`; rewrote the catalogue entry to cover the issue trigger, the 60-min timeout, the parallel-chunk implementation, the reply-on-cancel behaviour.
- Subsystem doc updates: none.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: the first revision of `purge-artifacts.yml` (PR #2922) shipped with only `schedule` + `workflow_dispatch` triggers, which forced an operator-click-in-UI step that contradicted CLAUDE.md § "Access & autonomy" ("The operator's role is to approve tier-gated actions and set direction — not to fetch logs, SSH into a VM, or run commands on your behalf"). Caught by the operator in chat; remediated in PR #2924.
- Contradiction 2: the second revision of the workflow's reply step used `if: success()` / `if: failure()`, which both skip on `cancelled`. When the v2 workflow's first one-shot purge cancelled at the 10-min timeout, issue #2927 stayed open with no comment — looking identical to a wedged workflow. The doc-versus-reality contradiction was "the workflow always reports back" vs "the workflow silently dropped a terminal state." Remediated in PR #2929 with `if: always()` + `job.status`-driven icon/state_reason.
- Code/doc mismatch: none surviving after this PR. The `docs/github-actions-workflows.md` entry now matches the on-disk YAML.

## Risks and Follow-Ups
- Remaining technical risks:
  - The 7-day cap on `health-snapshot` artifacts shortens the operator-accessible history window from 30 d → 7 d. Nothing in code reads these artifacts (the dashboard reads `artifacts/health/*` from the live VM filesystem, not from GitHub Actions artifacts), but an operator pulling the bundle for an out-of-band health review now has a 7-day rather than 30-day backstop. Acceptable for the storage budget; flag if a future review process needs longer.
  - The parallel-chunk DELETE loop assumes GitHub's `deleteArtifact` REST endpoint won't trip secondary rate limits at CONCURRENCY=10. The two real recovery runs (issues #2927 + #2931) hit zero rate-limit errors, but if a future ~10 000-artifact recovery hits 429s, drop CONCURRENCY or add jittered retry.
- Remaining product decisions (Tier 3): none.
- Blockers: none.

## Deferred Items
- Deferred item 1: no public dashboard / observability for the actual current Actions storage usage from within Claude sessions. The GitHub REST API has `/repos/{owner}/{repo}/actions/cache/usage` for caches but no equivalent simple endpoint for artifact storage; the only signal is "list all artifacts and sum `size_in_bytes`" which is what `purge-artifacts.yml` already does. A future enhancement could publish that totals row to a daily Telegram ping; not worth the wiring today.
- Deferred item 2: `training-rerun-5m.yml`'s pre-existing `pyyaml`-missing bug (the `ModuleNotFoundError: No module named 'yaml'` failure that retripped on every push touching that workflow file). Out of scope for this sprint; fixed independently in PR #2928 by the parallel S-MLOPT-S15a session.

## Next Recommended Sprint
- Suggested next sprint: none directly. The storage problem is solved and steady-state.
- Why next: pure-CI sprints don't seed follow-on work the way a strategy or runtime change does.
- Required verification before starting: operator eyeballs GitHub billing UI after the meter refreshes (~15 min lag) to confirm the 100 % alert has cleared.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified.  *(N/A — no pipeline stage touched.)*
- [x] Roadmap status was checked.  *(N/A — no roadmap item moved.)*
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
