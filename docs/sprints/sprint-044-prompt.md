# Sprint S-044 — M4 step 1: complete the GitHub Actions CI suite

**Sprint type:** Roadmap (M4) | **Risk tier:** Tier 1 (all self-merge)
**Created:** 2026-05-07 | **Branch:** `claude/sprint-044-ci-suite-wQmR4`
**Predecessors:** CP-2026-05-06-15-s043-complete (M3 closed); CP-2026-05-07-01-bug061-spot-tpsl-blocker (most recent merged work)

## 1. Goal

Stop relying on local `pytest` runs and operator-driven `python scripts/secret_scan.py` invocations. After this sprint, every PR opened against `main` runs the lightweight repo-verification suite (collect-only pytest, repo-inventory diff, secret scan, ruff lint) automatically through GitHub Actions, and a green status check gates merges. The result is that the next time someone opens a PR that breaks test collection, leaks a secret, or drifts the repo inventory, the failure surfaces on the PR before review — not after merge. No live-trading code paths touched; only `.github/workflows/`, the existing audit scripts, and a small status-check config doc.

## 2. Dependencies

- **Sprint dependency:** S-043 closed (M3 done) ✅ — confirmed in `docs/claude/milestone-state.md` 2026-05-06.
- **Sprint dependency:** PR #435 merged (BUG-061) ✅ — clean main, no stale CI debt to inherit.
- **Infra dependency:** `scripts/secret_scan.py`, `scripts/repo_inventory.py`, `scripts/check_dry_run_in_diff.py` all already on `main` (verified 2026-05-07).
- **Infra dependency:** Existing workflow `.github/workflows/dry-run-guard.yml` is the template for new workflows (same checkout / diff pattern).
- **External dependency:** None. GitHub Actions runners are public; no secrets needed for any of the new jobs (secret-scan reads tracked files only; pytest is collect-only and uses repo fixtures).
- **Operator hold — do NOT touch:** `src/runtime/orders.py`, `src/units/accounts/*`, `src/main.py`, `config/accounts.yaml`, `deploy/*` — none of M4's CI work belongs there.

## 3. Deliverables

1. `docs/sprints/sprint-044-prompt.md` — this file, committed at T0.
2. `.github/workflows/pytest-collect.yml` — runs `PYTHONPATH=. pytest --collect-only -q tests/` on every PR; fails if collection raises (T1).
3. `.github/workflows/secret-scan.yml` — runs `python scripts/secret_scan.py` on every PR; fails on any tracked-file finding (T2).
4. `.github/workflows/repo-inventory.yml` — runs `python scripts/repo_inventory.py` on every PR and uploads the inventory artifact for visibility (T2).
5. `.github/workflows/ruff-lint.yml` — runs `ruff check .` on every PR; fails on lint errors. Adds `ruff` to a new `requirements-dev.txt` (created at T3).
6. `docs/claude/ci-status-checks.md` — short runbook listing each required status check, what it gates, how to debug a failure, and which checks are *advisory* vs *blocking* (T4).
7. `docs/sprint-summaries/sprint-044-summary.md` — PR list, checkpoint IDs, deliverables table, deferred items (T5).
8. `docs/claude/checkpoints/CHECKPOINT_LOG.md` — kickoff entry `CP-2026-05-07-NN-s044-kickoff` (T0) + closing entry `CP-2026-05-07-NN-s044-complete` (T5).
9. `docs/claude/milestone-state.md` — M4 status updated to reflect CI suite progress at T5.

## 4. Checkpoints

| # | Checkpoint title | What completes by then | Risk class | Wall-clock | Gates |
|---|---|---|---|---|---|
| T0 | Kickoff — sprint prompt + CP | `sprint-044-prompt.md` committed; `CP-2026-05-07-NN-s044-kickoff` prepended to CHECKPOINT_LOG; PR self-merged | infra (docs-only) | ≤ 20 min | T1, T2, T3 |
| T1 | `pytest-collect.yml` workflow | Workflow added; tested by triggering it on a noop branch; passes on current main; PR self-merged | infra | ≤ 30 min | T4 |
| T2 | `secret-scan.yml` + `repo-inventory.yml` workflows | Both workflows added; both pass on current main; repo-inventory artifact attaches; PR self-merged | infra | ≤ 30 min | T4 |
| T3 | `ruff-lint.yml` workflow + `requirements-dev.txt` | Workflow added; `ruff` pinned in `requirements-dev.txt`; runs against current main and either passes or only flags pre-existing issues with a single follow-up issue link in the PR body; PR self-merged | infra | ≤ 45 min | T4 |
| T4 | `ci-status-checks.md` runbook | Short runbook documents each workflow, its trigger, what it gates, debug steps; PR self-merged | docs-only | ≤ 30 min | T5 |
| T5 | Sprint close | `sprint-044-summary.md` written; `milestone-state.md` updated (M4 partial → progressing); `CP-2026-05-07-NN-s044-complete` prepended to CHECKPOINT_LOG; PR self-merged | docs-only | ≤ 20 min | — |

### 4b. Unit boundary declaration

| Unit | Role in this sprint |
|---|---|
| `src/units/strategies/` | untouched |
| `src/units/accounts/` | untouched |
| `src/data_layer/` (DB unit) | untouched |
| `src/ui/` | untouched |
| `src/runtime/` | untouched |
| `src/bot/` | untouched |
| `src/core/coordinator.py` | untouched |

**No `src/`, no `tests/`, no `config/`, no `deploy/`. Only `.github/workflows/`, `docs/`, and a new top-level `requirements-dev.txt`.**

## 5. Risk class & merge model

| PR | Class | Self-merge? |
|---|---|:-:|
| T0 — kickoff (sprint prompt + checkpoint) | docs-only | ✅ |
| T1 — `pytest-collect.yml` | infra | ✅ |
| T2 — `secret-scan.yml` + `repo-inventory.yml` | infra | ✅ |
| T3 — `ruff-lint.yml` + `requirements-dev.txt` | infra | ✅ |
| T4 — `ci-status-checks.md` runbook | docs-only | ✅ |
| T5 — sprint close (summary + final CP + milestone-state) | docs-only | ✅ |

**Live-mode invariant check:** no live-trading code touched in any PR. `scripts/check_dry_run_in_diff.py` clean for all (no `src/units/accounts/*`, no `src/runtime/orders.py`, no `src/runtime/pipeline.py`, no `config/accounts.yaml`). ✅

## 6. Success criteria

- ✅ All four new workflows appear in `.github/workflows/` and execute green on a noop test PR opened from `claude/sprint-044-ci-suite-wQmR4`.
- ✅ `gh api repos/benbaichmankass/ict-trading-bot/branches/main/protection` (or the equivalent Settings → Branches view) lists `pytest-collect`, `secret-scan`, and `ruff-lint` as required status checks. (`repo-inventory` stays advisory — informational artifact only.)
- ✅ `docs/claude/ci-status-checks.md` exists and documents one row per workflow with the trigger, the gate, and the debug command.
- ✅ `docs/claude/milestone-state.md` M4 row reflects "CI suite shipped (S-044); Janitor + canonical-path remaining" at sprint close.
- ✅ `docs/sprint-summaries/sprint-044-summary.md` exists with the PR list, checkpoint IDs, and a 1–3 bullet "lessons learned".
- ❌ No new tests are added under `tests/` — this sprint is CI plumbing, not test coverage.
- ❌ No `requirements.txt` change — runtime deps stay frozen; dev deps live in `requirements-dev.txt`.
- ❌ No reformatting of existing source code to satisfy `ruff` — if `ruff check .` flags pre-existing issues, file a follow-up sprint instead of bundling a mass-format with this CI work.

## 7. Hard guardrails

Inherited from `CLAUDE.md`:
- No edits to `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, `src/units/accounts/*`, `src/main.py`, `config/accounts.yaml`, `deploy/*`. Any drift here triggers Live-mode invariant ping-PR per CLAUDE.md.
- No `parse_mode="Markdown"` anywhere new (none planned; this sprint adds zero Telegram code).
- No new secrets, API keys, or environment variables. The new workflows must run with `secrets.GITHUB_TOKEN` only.
- No `--no-verify`, no `--no-gpg-sign`, no force-push to `main`.
- No mass reformatting. If `ruff` finds pre-existing issues, log them as a follow-up — do not auto-fix in this sprint.

Sprint-specific:
- The `pytest-collect.yml` step **must** stay collect-only. Full pytest needs heavier deps (pandas, sklearn, etc.) and would balloon CI runtime + cost; that's a separate sprint.
- The `secret-scan.yml` workflow must use the same exit-code contract as the script (non-zero on findings) — do not add allow-lists in this sprint.
- The `repo-inventory.yml` workflow stays advisory (uploads artifact, never fails the PR). Making it blocking is a follow-up after the inventory is observed across ≥ 5 PRs.

## 8. Hand-off

Next sprint (S-045) picks up the second M4 slice — **Janitor audits**. Specifically:
1. Dead file audit (scripts unused since N months ago, in light of `repo_inventory.py` output).
2. Duplicate module audit (`src/ui/` vs `src/units/ui/` — the architecture audit flagged this; resolution belongs in M4).
3. Missing test audit (modules in `src/units/` without a `tests/test_<unit>_*.py` counterpart).

The CI artifacts shipped in S-044 will inform S-045: the repo-inventory artifact tells us which files are touched per PR; the pytest-collect run tells us which test files import each module. Janitor work will lean on these signals rather than reading the whole tree by eye.

If the operator wants to defer the Janitor sprint and skip ahead to **M5 — Strategy testing workflow**, that's also fine — M4 and M5 don't have a hard ordering dependency in the workplan, and S-044 alone meaningfully advances M4.

---

**To kick off:** create branch `claude/sprint-044-ci-suite-wQmR4`, file this prompt at `docs/sprints/sprint-044-prompt.md`, append `CP-2026-05-07-NN-s044-kickoff` to `CHECKPOINT_LOG.md`, open PR #N as the T0 PR, self-merge on green (Tier 1, infra/docs-only).
