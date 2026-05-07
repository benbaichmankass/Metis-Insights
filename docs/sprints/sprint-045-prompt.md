# Sprint S-045 — M4 step 2: conftest cleanup, promote `pytest-collect` to blocking, ruff rule expansion

**Sprint type:** Roadmap (M4) | **Risk tier:** Tier 1 (all self-merge)
**Created:** 2026-05-07 | **Branch:** `claude/sprint-045-conftest-ruff-cleanup`
**Predecessors:** `CP-2026-05-07-03-s044-complete` (S-044 closed; CI suite shipped). PR #437 merged at `bb83914`.

## 1. Goal

S-044 shipped the GitHub Actions CI suite, but `pytest-collect` had to ship **advisory** because `tests/conftest.py` stubs `telegram` / `telegram.ext` as `MagicMock` without exposing `telegram.error`, and `src/bot/comms_handler.py` does `from telegram.error import TelegramError`. ~45 test files transitively fail collection as a result. Until that's fixed, `pytest-collect` cannot gate PRs and the M4 CI story isn't actually complete. S-044 also pinned `ruff-lint` to a narrow `E9,F63,F7` rule set because current `main` carries 286 hits across the broader rule set; until those are cleaned, the lint gate isn't doing the work it should.

This sprint closes both gaps:
1. Fix the `tests/conftest.py` telegram-stub bug (or install `python-telegram-bot` and remove the stub) so the test suite collects cleanly.
2. Drop the `|| true` shim from `pytest-collect.yml` and flip the workflow to blocking.
3. Tighten `ruff-lint.yml` rule by rule, fixing pre-existing hits in scoped passes (`F541` → `E401` → `F811` → … in priority order). End the sprint with the broadest rule set we can pin without leaving fixable hits behind.
4. Update branch protection on `main` to require `pytest-collect`, `secret-scan`, `ruff-lint`, and `dry-run-guard`.

After this sprint, M4 step 2 is done. M4 step 3 (Janitor audits — dead files, duplicate `src/ui/` vs `src/units/ui/`, missing tests) becomes S-046.

## 2. Dependencies

- **Sprint dependency:** S-044 closed (`CP-2026-05-07-03-s044-complete`) ✅; PR #437 merged at `bb83914`.
- **Infra dependency:** all four S-044 workflows on `main` (`pytest-collect`, `secret-scan`, `repo-inventory`, `ruff-lint`).
- **Infra dependency:** `requirements-test.txt` (no edits required for stub option B; one-line addition for stub option A — see § 3).
- **Operator hold — do NOT touch:** `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, `src/units/accounts/*`, `src/main.py`, `config/accounts.yaml`, `deploy/*`.

## 3. Deliverables

1. `docs/sprints/sprint-045-prompt.md` — this file (T0).
2. **`tests/conftest.py` fix** (T1). Two acceptable shapes — pick at T1 after a 30-min scan:
   - **(A) Real package.** Install `python-telegram-bot` in `requirements-test.txt`; remove the `telegram` / `telegram.ext` MagicMock stubs from `tests/conftest.py`. Trade-off: heavier test deps, but no stub maintenance.
   - **(B) Better stub.** Keep the stub strategy but expose `telegram.error.TelegramError` (and any other transitively-imported attributes) on the MagicMock module. Trade-off: lean deps, but conftest grows.
   - Sprint owner picks at T1; document the choice in the PR body.
3. **`.github/workflows/pytest-collect.yml` promotion** (T2). Drop `--continue-on-collection-errors` and the `|| true` shim once collection is clean on the rebased branch. Update `docs/claude/ci-status-checks.md` to move `pytest-collect` from advisory to blocking.
4. **Ruff rule expansion** (T3). Fix pre-existing hits in priority order, **one rule per commit**:
   - `F541` (f-strings without placeholders, 23 hits — autofix-safe with `ruff --fix`)
   - `E401` (multiple imports on one line, 9 hits — autofix-safe)
   - `F811` (redefined-while-unused, 9 hits — autofix-safe)
   - `F841` (unused variables, 11 hits — review each before autofix)
   - `F401` (unused imports, 167 hits — autofix-safe; biggest mass-format risk → its own sub-checkpoint)
   - `E402` (imports not at top, 46 hits — manual review per file; many are intentional sys.path setups)
   - `E741` (ambiguous variable names, 13 hits — manual rename)
   - `F821` (undefined names, 4 hits in `scripts/sprint015/data_sources.py` — fix the `from typing import Dict` import)
   - Final `ruff-lint.yml` setting: drop `--select` flag entirely (let ruff use its default rule set).
5. **Branch protection wiring** (T4). Either via `gh api repos/.../branches/main/protection` from the operator's admin token, or as a one-click Colab notebook under `notebooks/operator/` per CLAUDE.md "Always do" rule. Required checks after this sprint:
   - `pytest-collect`
   - `secret-scan`
   - `ruff-lint`
   - `dry-run-guard`
   - `repo-inventory` stays advisory.
6. `docs/sprint-summaries/sprint-045-summary.md` (T5).
7. `docs/claude/checkpoints/CHECKPOINT_LOG.md` — `CP-2026-05-07-NN-s045-kickoff` (T0) + `CP-2026-05-07-NN-s045-complete` (T5).
8. `docs/claude/milestone-state.md` — M4 step 2 done; step 3 (Janitor audits) queued.

## 4. Checkpoints

| # | Checkpoint title | What completes by then | Risk class | Wall-clock |
|---|---|---|---|---|
| T0 | Kickoff — sprint prompt + CP | `sprint-045-prompt.md` committed; `CP-NN-s045-kickoff` prepended to CHECKPOINT_LOG | docs-only | ≤ 20 min |
| T1 | `tests/conftest.py` fix | Pick option A or B; commit; verify `pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py` passes locally with 0 errors | tests | ≤ 60 min |
| T2 | `pytest-collect` → blocking | Drop `\|\| true` + `--continue-on-collection-errors`; update runbook gate column; CI green on PR | infra | ≤ 30 min |
| T3 | Ruff rule expansion | Per-rule sub-checkpoints (T3a..T3h); each rule fixed in its own commit; final `ruff-lint.yml` drops `--select` | mixed | ≤ 3 hr |
| T4 | Branch protection wiring | One-click Colab notebook OR operator-run `gh api` script; documented in PR body | docs / ops | ≤ 30 min |
| T5 | Sprint close | `sprint-045-summary.md`; `milestone-state.md` updated; final CP | docs-only | ≤ 20 min |

### 4b. Unit boundary declaration

| Unit | Role in this sprint |
|---|---|
| `src/units/strategies/` | possibly touched (mechanical ruff fixes only — no behaviour change) |
| `src/units/accounts/` | **untouched** (operator hold) |
| `src/runtime/` | possibly touched (ruff fixes — but NOT `orders.py` / `pipeline.py` / `trading_mode.py`) |
| `src/bot/` | possibly touched (ruff fixes); `comms_handler.py` is the trigger for T1 but is NOT modified — T1 is in `tests/conftest.py` |
| `tests/` | **TOUCHED at T1** (conftest stub fix) and possibly at T3 (ruff fixes in test files) |
| `scripts/` | possibly touched at T3 (e.g. `scripts/sprint015/data_sources.py` F821 fix) |

This is a Janitor sprint by definition — it touches a lot of files for cleanup. The unit-boundary rule is interpreted here as: **no behaviour changes, only mechanical lint fixes + the conftest stub fix**.

## 5. Live-mode invariant check (per CLAUDE.md, every PR)

- `config/accounts.yaml` untouched ✅
- `src/runtime/orders.py` / `pipeline.py` / `trading_mode.py` untouched ✅
- `src/units/accounts/*` untouched ✅
- `scripts/check_dry_run_in_diff.py` clean against main ✅
- Any ruff fix that would touch the above files = STOP, open ping-PR per CLAUDE.md § "Telegram Reporting", do not bundle.

## 6. Success criteria

- ✅ `pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py` passes with **0 collection errors** locally and in CI.
- ✅ `.github/workflows/pytest-collect.yml` no longer carries `--continue-on-collection-errors` or `|| true`; the workflow fails the PR on any collection error.
- ✅ `ruff check .` (no `--select`) passes on `main` post-sprint.
- ✅ `gh api repos/the-lizardking/ict-trading-bot/branches/main/protection | jq '.required_status_checks.contexts'` lists `pytest-collect`, `secret-scan`, `ruff-lint`, `dry-run-guard`.
- ✅ `docs/claude/ci-status-checks.md` reflects all four required, `repo-inventory` advisory.
- ✅ `docs/claude/milestone-state.md` M4 row reflects "CI suite + conftest + ruff cleanup done; Janitor audits remaining → S-046 candidate".
- ❌ No `requirements.txt` change.
- ❌ No `src/runtime/orders.py` / `src/runtime/pipeline.py` / `src/runtime/trading_mode.py` / `src/units/accounts/*` / `config/accounts.yaml` / `deploy/*` edits.
- ❌ No new tests added — this is cleanup, not coverage.
- ❌ No mass refactors piggybacked on the lint cleanup. One ruff rule per commit.

## 7. Hard guardrails

- No `--no-verify`, no `--no-gpg-sign`, no force-push to `main`.
- One ruff rule per commit at T3 — keeps each diff reviewable and reverts cheap.
- If autofix changes runtime behaviour (e.g. an "unused" import has an import side effect), the autofix is WRONG — revert and fix manually with a comment explaining the side effect.
- If a ruff rule's hits exceed budget (~50+ files in one diff), split into per-directory sub-checkpoints under T3.
- Stop the sprint if any T3 fix would touch operator-hold paths.

## 8. Hand-off

Next sprint (**S-046**) closes M4: Janitor audits.
1. **Dead-file audit** — use the `repo-inventory.yml` artifact from the last 5+ PRs to find scripts unused in N months.
2. **Duplicate-module audit** — `src/ui/` vs `src/units/ui/` (post-S-035 back-compat shims may now be removable; the architecture audit flagged this).
3. **Missing-test audit** — modules under `src/units/` without a corresponding `tests/test_<unit>_*.py`.

If the operator prefers to skip ahead to **M5 — Strategy testing workflow**, that's also fine — M4 step 3 (Janitor) is recurring auto-task work and doesn't need to block M5.
