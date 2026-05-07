# Sprint S-046 — M4 step 3: Janitor audits (close M4)

**Sprint type:** Roadmap (M4) | **Risk tier:** Tier 1 (all self-merge except T4 ping-PR)
**Created:** 2026-05-07 | **Branch:** `claude/sprint-planning-status-ZMePk`
**Predecessors:** `CP-2026-05-07-06-s045-followup-auto-sync` (PR #439 + #440 merged). S-045 closed M4 step 2; the post-S-045 follow-up landed the auto-sync branch-protection workflow.

## 1. Goal

S-044 + S-045 + the post-S-045 follow-up shipped the M4 CI suite, blocked test-collection on green, expanded ruff to the default rule set, and wired branch protection to auto-sync from the workflow file. M4 still has one piece of work outstanding from the workplan: the **Janitor audits**. Three audits are open per `docs/claude/workplan.md` § "Janitor Mode" and the 2026-05-02 architecture audit:

1. **Dead-file audit.** Use the `repo-inventory.yml` artifact from recent PRs (now ≥ 5) to find scripts/modules with no inbound references and no recent edits. Delete the safe ones; document the rest.
2. **Duplicate-module audit.** `src/ui/` and `src/units/ui/` both exist on disk (3 files vs 4 files). Per architecture rule (4) the canonical location is `src/units/ui/`. Consolidate to one location and update imports.
3. **Missing-test audit.** Walk `src/units/*` and list every module without a matching `tests/test_<unit>_*.py`. File a stub test (import smoke + one trivial assertion) for each, so future changes can't ship completely untested.

T4 also files the operator-hold lint residuals ping-PR carried over from S-045 — 15 mechanical ruff hits suppressed via `[lint.per-file-ignores]` in `ruff.toml`, blocked behind operator-hold paths (`src/runtime/pipeline.py`, `src/units/accounts/*`).

After this sprint, **M4 is formally closed**. Next active milestone becomes **M5 — Strategy testing workflow**.

## 2. Dependencies

- **Sprint dependency:** S-045 closed (`CP-2026-05-07-05-s045-complete`) ✅; PR #438 merged at `903191c`. Post-S-045 follow-up (PR #439, #440) merged.
- **Infra dependency:** `repo-inventory.yml` workflow on `main` ✅ (S-044). Artifact retention is 14 days; ≥ 5 PRs have run since the workflow shipped (PRs #437..#441), so we have inventory snapshots to diff.
- **Infra dependency:** `pytest-collect` blocking, `ruff check .` clean on `main` ✅ (S-045). Both must stay green through every commit on this branch.
- **Operator hold — do NOT touch:** `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, `src/units/accounts/*`, `src/main.py`, `config/accounts.yaml`, `deploy/*`. Any deletion or move that would touch these paths stops the sprint and routes through a ping-PR.

## 3. Deliverables

1. `docs/sprints/sprint-046-prompt.md` — this file (T0).
2. **Dead-file audit report** (T1). `docs/claude/janitor-2026-05-07-deadfiles.md` listing every candidate dead file with: path, last-modified date, inbound-reference count, decision (delete / keep / defer-with-reason). PR removing the safe deletions in one mechanical commit per category (scripts, fixtures, notebooks, helpers).
3. **Duplicate-module consolidation** (T2). One canonical location for the UI unit. Architecture rule (4) names `src/units/ui/` as canonical, so the 3 files in `src/ui/` are either consolidated into `src/units/ui/` (if they hold unique content) or deleted (if `src/units/ui/` already supersedes them). All callers updated; `from src.ui import …` becomes `from src.units.ui import …` everywhere. Brief diff report in `docs/claude/janitor-2026-05-07-ui-consolidation.md`.
4. **Missing-test audit + stubs** (T3). `docs/claude/janitor-2026-05-07-missing-tests.md` with one row per `src/units/*` module that lacks a `tests/test_<unit>_*.py`. New stub tests filed under `tests/` with at least one importable assertion each. Stubs are not coverage — they're presence guards.
5. **Operator-hold lint residuals ping-PR** (T4). Separate branch `claude/ping-s046-ruff-residuals` carrying *only* the proposed mechanical fixes for the 15 operator-hold ruff hits, plus the `ruff.toml` ignore-table prune. Opened as `(PM REVIEW): S-046 — apply 15 mechanical ruff fixes in operator-hold paths`. Per CLAUDE.md § "Ping-PR vs work-PR separation", a separate ≤ 5-line ping-PR also lands on `claude/ping-s046-ruff-residuals-ping` whose only payload is an entry in `pending-pings.jsonl` linking to the work-PR. The work-PR stays draft until operator approves; the ping-PR self-merges immediately to fire the Telegram notification.
6. `docs/sprint-summaries/sprint-046-summary.md` (T5).
7. `docs/claude/checkpoints/CHECKPOINT_LOG.md` — `CP-2026-05-07-NN-s046-kickoff` (T0) + `CP-2026-05-07-NN-s046-complete` (T5).
8. `docs/claude/milestone-state.md` — **M4 → CLOSED**, M5 → active.

## 4. Checkpoints

| # | Checkpoint title | What completes by then | Risk class | Wall-clock |
|---|---|---|---|---|
| T0 | Kickoff — sprint prompt + CP + milestone-state + sprint-start ping | This file committed; kickoff CP prepended to CHECKPOINT_LOG; milestone-state notes S-046 active; sprint-start ping in pending-pings.jsonl; draft PR opened | docs-only | ≤ 20 min |
| T1 | Dead-file audit | Report filed; safe deletions PR'd; deferred entries documented with reason | infra | ≤ 60 min |
| T2 | UI consolidation | `src/ui/` removed (or content folded into `src/units/ui/`); imports updated repo-wide; report filed; `pytest --collect-only` and `ruff check .` both green | infra | ≤ 60 min |
| T3 | Missing-test audit + stubs | Report filed; stub tests for every uncovered `src/units/*` module; collection green | tests | ≤ 60 min |
| T4 | Operator-hold ping-PR | Work-PR opened as draft on `claude/ping-s046-ruff-residuals`; companion ping-PR self-merged on `claude/ping-s046-ruff-residuals-ping` | docs / PM | ≤ 30 min |
| T5 | Sprint close | `sprint-046-summary.md`; `milestone-state.md` flips M4 to CLOSED, M5 to active; final CP; sprint-complete ping | docs-only | ≤ 30 min |

### 4b. Unit boundary declaration

| Unit | Role in this sprint |
|---|---|
| `src/units/strategies/` | **reads** (missing-test audit) |
| `src/units/accounts/` | **reads** (missing-test audit only — no source edits; ping-PR T4 proposes mechanical fixes here for operator review, not in this branch) |
| `src/units/dashboards/` | **reads** (missing-test audit) |
| `src/units/db/` | **reads** (missing-test audit) |
| `src/units/trading_school/` | **reads** (missing-test audit) |
| `src/units/ui/` | **owns** (T2 consolidation target) |
| `src/ui/` | **owns** (T2 — removed or folded into `src/units/ui/`) |
| `src/runtime/` | **untouched** (operator hold for `orders.py` / `pipeline.py` / `trading_mode.py`; the rest is read-only) |
| `src/bot/` | **reads** only (telegram bot is a thin shell — it imports the UI unit; T2 may rewrite `from src.ui import` to `from src.units.ui import`) |
| `src/core/coordinator.py` | **reads** (no changes expected) |

No new cross-unit imports added. T2 *removes* a cross-unit boundary leak by consolidating UI into one folder.

## 5. Live-mode invariant check (per CLAUDE.md, every PR)

- `config/accounts.yaml` untouched ✅
- `src/runtime/orders.py` / `pipeline.py` / `trading_mode.py` untouched ✅
- `src/units/accounts/*` untouched in *this* branch ✅ (T4 proposes touching `src/units/accounts/` in a separate ping-PR; that PR is held for operator review)
- `scripts/check_dry_run_in_diff.py` clean against main on every commit ✅
- Any Janitor deletion that would touch the above stops the sprint and routes through a ping-PR.

## 6. Success criteria

- ✅ `docs/claude/janitor-2026-05-07-deadfiles.md`, `…-ui-consolidation.md`, `…-missing-tests.md` all exist and list per-file decisions.
- ✅ `src/ui/` no longer exists on disk (or carries only a deprecation shim with a removal date documented in the report — preferred outcome is a clean delete).
- ✅ `grep -r "from src.ui" src/ tests/ scripts/` returns 0 results.
- ✅ Every `src/units/<unit>/` directory has at least one `tests/test_<unit>_*.py` file.
- ✅ `pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py` returns 0 errors.
- ✅ `ruff check .` (no `--select`) clean.
- ✅ `python scripts/secret_scan.py` clean.
- ✅ Operator-hold ping-PR open on `claude/ping-s046-ruff-residuals` (DRAFT — do not self-merge); companion ping-PR on `claude/ping-s046-ruff-residuals-ping` self-merged.
- ✅ `docs/claude/milestone-state.md` shows M4 → CLOSED and M5 → active.
- ❌ No `src/runtime/orders.py` / `src/runtime/pipeline.py` / `src/runtime/trading_mode.py` / `src/units/accounts/*` / `config/accounts.yaml` / `deploy/*` edits in *this* branch. (T4 proposes them in a separate branch held for operator approval.)
- ❌ No new feature work piggybacked on the Janitor sprint. Only deletions, moves, stub tests, and the ping-PR.

## 7. Hard guardrails

- No `--no-verify`, no `--no-gpg-sign`, no force-push to `main`.
- One audit per checkpoint; one PR per audit. Don't bundle dead-file deletions with the UI consolidation — they have different blast radii.
- If a deletion candidate has any inbound reference outside the file itself, **do not delete** — document the references in the report and defer.
- If T2 reveals that `src/ui/` and `src/units/ui/` have diverged content (rather than one being a stale copy), STOP and write the analysis up before touching either path. The architecture audit assumption is "stale shim"; verify before deleting.
- Stub tests at T3 must pass. A stub that raises `ImportError` is worse than no stub — it actively breaks `pytest-collect`.

## 8. Hand-off

After S-046 closes, **M4 is done**. Next sprint (**S-047**) opens against M5 — Strategy testing workflow:
1. Telegram-triggered `/test <strategy_name>` command writing a structured request to the repo.
2. Validation logging (signals + decisions + outcomes per workplan § Required logs).
3. Backtest workflow docs (`docs/claude/backtest-workflow.md`) per workplan § Backtesting sessions.

If the operator prefers to advance the M2 dashboard backend or M9 model registry instead, the workplan permits either. M4 closure here unblocks the M0..M10 sequence; only M6 stays blocked behind the S-015 operator hold.
