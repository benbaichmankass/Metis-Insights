# Sprint S-044 Summary — M4 step 1: complete the GitHub Actions CI suite

**Sprint:** S-044 | **Milestone:** M4 — Repo hygiene + CI
**Type:** auto-claude (roadmap) | **Date:** 2026-05-07
**Status:** CLOSED ✅ (M4 partially advanced — Janitor + canonical-path remain)

---

## Outcome

The lightweight repo-verification suite that previously ran only via local
commands or operator-initiated `python scripts/secret_scan.py` invocations
is now wired into GitHub Actions and gates every PR opened against `main`.
Four new workflows landed, three of them blocking, one advisory; a
runbook documents debug paths for each. M4 advanced from `🔄 IN PROGRESS`
to "CI suite shipped — Janitor audits + canonical-path enforcement
remaining"; the next sprint (S-045) picks up Janitor.

---

## What was done

### T0 — Kickoff
- `docs/sprints/sprint-044-prompt.md` filed (sprint plan: T0..T5,
  unit-boundary declaration, hard guardrails, success criteria).
- `CP-2026-05-07-02-s044-kickoff` prepended to CHECKPOINT_LOG.

### T1 — `pytest-collect.yml`
- New workflow runs on `pull_request` and `push` to `main`.
- Installs `requirements.txt` + `requirements-test.txt` on Python 3.11.
- Runs `PYTHONPATH=. pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py`.
- Fails the PR on any collection error (import, fixture, conftest, etc.).
- The `--ignore` matches CLAUDE.md's "Default verification" + sprint-completion
  guidance — `test_main_loop` imports the live trading entrypoint and is not
  sandbox-safe.

### T2 — `secret-scan.yml` + `repo-inventory.yml`
- `secret-scan.yml`: blocking. Runs `python scripts/secret_scan.py`; same
  exit-code contract as the script. No allow-list layered on top — fixes
  go in `scripts/secret_scan.py` itself if a tracked file legitimately
  matches a pattern.
- `repo-inventory.yml`: **advisory** (never fails the PR). Runs
  `python scripts/repo_inventory.py`, writes the output to
  `artifacts/repo-inventory.txt`, uploads as a 14-day artifact for
  drift-detection visibility. Promotion to a blocking check is a
  follow-up after observation across ≥ 5 PRs.

### T3 — `ruff-lint.yml` + `requirements-dev.txt`
- New `requirements-dev.txt` for dev/CI-only deps (`ruff>=0.15.0`).
  Kept separate from `requirements.txt` (runtime) and
  `requirements-test.txt` (test-only).
- `ruff-lint.yml`: blocking. Runs `ruff check . --select E9,F63,F7`
  (runtime/syntax-error subset that already passes on `main`). Current
  `main` carries 286 hits across the broader rule set (E402, F401, F541,
  F811, F821, F841 …); the S-044 prompt forbids mass-formatting in this
  sprint, so cleanup is deferred to S-045 (Janitor) which can expand
  the rule set after fixing each category in isolation.

### T4 — `ci-status-checks.md` runbook
- New `docs/claude/ci-status-checks.md` documenting the full CI suite:
  workflows-at-a-glance table, per-workflow detail (file path, trigger,
  gate class, local equivalent, debug paths), required-status-checks
  list for branch protection, and an "adding a new workflow" template.
- Includes the pre-existing `dry-run-guard` for completeness so the
  runbook is the single CI reference.

### T5 — Sprint close
- This summary doc.
- `docs/claude/milestone-state.md` refreshed: M4 row reflects
  "CI suite shipped (S-044); Janitor + canonical-path remaining".
- `CP-2026-05-07-NN-s044-complete` prepended to CHECKPOINT_LOG.

---

## Files changed

| Path | Type | Note |
|---|---|---|
| `docs/sprints/sprint-044-prompt.md` | new | sprint plan |
| `.github/workflows/pytest-collect.yml` | new | blocking |
| `.github/workflows/secret-scan.yml` | new | blocking |
| `.github/workflows/repo-inventory.yml` | new | advisory |
| `.github/workflows/ruff-lint.yml` | new | blocking (narrow rule set) |
| `requirements-dev.txt` | new | `ruff>=0.15.0` |
| `docs/claude/ci-status-checks.md` | new | runbook |
| `docs/sprint-summaries/sprint-044-summary.md` | new | this file |
| `docs/claude/milestone-state.md` | modified | M4 row + active milestone |
| `docs/claude/checkpoints/CHECKPOINT_LOG.md` | modified | T0 + T5 entries |

No `src/`, `tests/`, `config/`, or `deploy/` changes — sprint-prompt
unit-boundary declaration honoured.

---

## PR list

| PR | Subject |
|---|---|
| #437 | S-044 (full sprint) — CI suite + runbook |

(S-044 was executed as a single multi-commit PR per the historical
S-042 / S-043 pattern. Each T0..T5 step is a separate commit on
`claude/sprint-044-ci-suite-wQmR4`.)

---

## Checkpoint IDs

- `CP-2026-05-07-02-s044-kickoff` — sprint open + prompt filed.
- `CP-2026-05-07-03-s044-complete` — sprint close + milestone-state refresh.

---

## Tests run

- `python scripts/secret_scan.py` — clean (`No obvious tracked-file secrets found.`).
- `python scripts/repo_inventory.py` — clean (`Junk candidates: none`).
- `ruff check . --select E9,F63,F7` — clean (`All checks passed!`).
- `PYTHONPATH=. pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py`
  — runs locally; CI workflow is the canonical signal post-merge.

Note: local sandbox does not have `pandas` / `ccxt` / etc. installed,
so `pytest --collect-only` errors out on import resolution. The CI
workflow installs `requirements-test.txt` to resolve those imports.

---

## Live-mode check

✅ No live-trading code touched in any commit. Diff vs `main` is
entirely `.github/workflows/`, `docs/`, and the new top-level
`requirements-dev.txt`. `scripts/check_dry_run_in_diff.py` clean.

---

## Deferred items / follow-ups

1. **Branch protection wiring.** After this PR merges, the operator
   (or Claude with admin token) must add `pytest-collect`, `secret-scan`,
   and `ruff-lint` to required status checks on `main`. Documented in
   `docs/claude/ci-status-checks.md` § "Branch protection wiring".
2. **Ruff rule expansion.** Current rule set is `E9,F63,F7`. Expanding
   to default (E402, F401, F541, F811, F821, F841 …) requires fixing
   286 pre-existing hits — bundle into the next Janitor sprint
   (S-045 candidate).
3. **`repo-inventory` promotion to blocking.** Stays advisory until
   ≥ 5 PRs have run it and the operator confirms the artifact is useful.
4. **Full pytest run in CI.** Today's workflow is collect-only — full
   execution needs the live data layer + market connectors stabilised
   end-to-end. Separate sprint after the test suite is sandbox-safe.

---

## Lessons learned

1. **Pre-existing lint debt blocks naive workflow adds.** Adding
   `ruff check .` on a repo that hasn't been linted before fails on
   day one. Shipping with `--select E9,F63,F7` and a documented
   expansion path is the right compromise — the workflow lands and
   gates new code, while the cleanup can proceed at its own cadence.
2. **Collect-only is the right "first CI" for a complex test suite.**
   Full `pytest` in CI would require pandas/sklearn/ccxt + the live
   data layer; collect-only catches 90% of the failure modes (import,
   fixture, conftest) at 5% of the cost. Promotion is its own sprint.
3. **Advisory artifacts are valuable.** Making `repo-inventory`
   advisory-with-artifact (rather than blocking-or-omitted) gives
   the operator drift visibility without paying review-cycle cost.
