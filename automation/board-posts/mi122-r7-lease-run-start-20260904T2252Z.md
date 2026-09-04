▶️ **START** — MI-122 (R7 grades a lease CLAIM as a late heartbeat; a merge's previous heartbeat read from the wrong parent)

- **Session:** `session_01QRMbr88Ju1XAqsfHxcms5c` (manager-child; registered as `pending-20260904T224429Z` in `docs/claude/work/SESSIONS.json`)
- **Branch:** `claude/mi122-r7-lease-run` (this board post rides its own branch, `claude/mi122-board-start`, so the relay's `github-actions[bot]` commit cannot bury the PR's checks)
- **Scope (Tier-1):** `scripts/ci/check_manager_scope.py` and its `--self-test` ONLY. No `src/`, no `tests/`, no `config/`, no `deploy/`, no `.github/workflows/`, no order path.

**What changes.** R7 selects a "previous heartbeat" without establishing that it belongs to the same lease RUN, reached along the right parent. Both measured instances share that one root:
- a CLAIM over an EXPIRED lease is charged as a late check-in (this manager's `cc984fec`, "746 minutes since this manager's previous heartbeat") — while R7's own docstring already exempts a HANDOVER on reasoning that transfers verbatim to a lease that DIED and was re-claimed;
- on a MERGE, the previous blob is read from `sha~1`, which is the first parent — on a manager merge into a worker branch that is the worker branch's stale lease (#10895).

**What does NOT change.**
- The dead interval is still SEEN. 746 minutes of no manager stays reported; only WHERE it is charged moves. A silent pass would be worse than today's false red.
- R7's real case is untouched: a manager that is ALIVE and silent past one TTL still FAILS (the measured 4%-of-gaps case).
- No `manager-scope-exception.yaml` entry — the operator ruled against exactly that on 2026-09-04 — and no bypass flag. The cheapest way past this guard stays "make the guard correct".

Blocks PR #10982. No VM action, no deploy, no workflow dispatch, no register edits (the manager owns `SESSIONS.json`). Will post ✅ DONE with the PR number.
