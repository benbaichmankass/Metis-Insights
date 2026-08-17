# Sprint Log: S-M20-GUARD-WIRING-AND-INERT-FOLDS-2026-08-17

## Date Range
- Start: 2026-08-17 ~04:00Z (overnight autonomous session, operator asleep)
- End: 2026-08-17 ~07:45Z

## Objective
- Primary goal: close the M20 defects filed overnight, all of one class — **a control or signal that exists and is never read**.
- Secondary goals: keep the hourly operator ping cadence; queue every Tier-3 call rather than acting on it; leave no probe, stray file or unfiled observation behind.

## Tier
- **Tier 1** throughout.
- Justification: CI guards, research tooling, workflow input *descriptions*, and backlog/doc records only. No `src/`, no `config/*.yaml`, no order path, no service or timer, no live lever flipped, no account-mode change. Every finding that would alter live behaviour was QUEUED for the operator (see Risks).

## Starting Context
- Active roadmap items: M20 exit-refinement coverage.
- Prior sprint reference: [`S-M20-PULLBACK-STALE-BACKLOG-2026-08-17.md`](S-M20-PULLBACK-STALE-BACKLOG-2026-08-17.md).
- Known risks at start: the coverage **headline** does not move when a block clears, so it is the known misquote trap; `exit_head_ml`'s 141 NOT CHECKED cells are a deliberate closed negative and must not be reopened.

## Repo State Checked
- Branch/commits reviewed: `claude/m20-exit-coverage-matrix-8d3he7`; merged `31b9a4d3` → `6fa9d439` on `main`.
- Deployment state reviewed: none touched. Nothing in this sprint deploys.
- Canonical docs reviewed: `CLAUDE.md`, `docs/ARCHITECTURE-CANONICAL.md` (§ CI workflows), `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `.claude/skills/doc-freshness/SKILL.md`.

## Files and Systems Inspected
- Code files inspected: `scripts/ci/run_guards.py`, `scripts/ci/guard_selftests.py`, `scripts/ci/check_collapsed_states.py`, `scripts/check_diag_unit_allowlist.py`, `scripts/check_diagnostic_provenance.py`, `scripts/research/m20_fleet_exit_sweep.py`, `scripts/research/m20_corpus_extract.py`, `scripts/research/m20_wf_effective.py`.
- Config files inspected: `config/accounts.yaml` (read-only — to establish which legs are real-money).
- Deployment files inspected: `deploy/*.timer|*.service` (enumerated by the allowlist guard; none modified).
- Docs inspected: the three review backlogs, `docs/research/RESEARCH-CAPABILITY-INDEX.md`, `.gitignore`.
- Workflows inspected: `.github/workflows/m20-exit-lever-sweep.yml`, `pytest-run` job logs.

## Work Completed
- **Item 1 — the `wf_summary` degeneracy, closed at both ends.** `ok = d_net >= 0 and (d_dd <= 0 or not require_dd)` is satisfied **by construction** when a lever changed nothing (`0 >= 0`, `0 <= 0`), so an INERT fold counted as a win and a bare `N/M` was unquotable. #9838 shipped the READ side (`m20_wf_effective.py`); #9844 shipped the PRODUCER, so future sweeps stop reproducing the inflated count. `is_inert` is **imported**, not restated, so producer and reader cannot drift; it requires **both** deltas at zero, and an **absent** delta is not inert (absent ≠ recorded-as-zero). `wins`/`ok`/`summary` are deliberately unchanged — see Risks.
- **Item 2 — an unrun guard self-test, then the guard for that class.** `selftest_collapsed_state` was registered in `SELFTESTS` and invoked by nothing, so the guard for the canonical *"can this field say we did not look?"* rule had never demonstrated in CI that it catches a planted break, across all 10 contracts (#9838). Because nothing would have caught the next one, #9847 adds **`selftest-wiring-guard`**: every registered name must resolve to a **verified** covering path — invoked by name, or a `COVERED_BY_CHECKER` declaration whose script must really be run with `--self-test` **and** really declare it in its argparse.
- **Item 3 — guard probes out of the tracked tree** (#9847). `_planted` cleans up on normal exit and on exceptions but cannot on SIGKILL/timeout/cancel. `check_diag_unit_allowlist.py` gained `--deploy-dir` (default `deploy`, CI byte-identical) and its self-test now stages a **faithful copy** in a `TemporaryDirectory`; the `scripts/ml` probe is `.gitignore`d instead, for the measured reason below.
- **Item 4 — sweep-workflow doc drift** (#9847). `split_target_oos` **and** `split_mode` both described the OOS **floor** (25) as the **target**; the target has been 50 since #9748.

## Validation Performed
- Tests run: `pytest-run` **11359 passed, 12 skipped in 541 s** on #9844. `run_guards.py --base main` → **PASS 42 · FAIL 0 · SKIP 0**. `guard_selftests.py --all` clean.
- Dry-runs/staging checks: `check_diag_unit_allowlist.py` default path re-run — **44 units scanned, 0 failures, exit 0** (proving the new argument did not change CI behaviour).
- Manual code verification: merged content on `main` confirmed by **reading the files, not the merge SHA** — `is_inert` imported at `m20_fleet_exit_sweep.py:59`, `grep -c "def is_inert"` = **0**, both call sites persist the split, extractor carries it. The import was additionally confirmed to **resolve at runtime** by loading the merged producer out of a clean extraction of `origin/main` (`is_inert.__module__ == 'm20_wf_effective'`) — it rides a `sys.path.insert`, which would pass every static check and fail only on the next sweep.
- **Failure paths verified by PLANTING A BREAK, not by reading:** neutering `check_diag_unit_allowlist.py`'s `uncovered` computation makes the rewritten self-test exit 1; restoring it returns 0. `check_selftest_wiring.py --self-test` fails on five distinct plants. The new guard was then confirmed **executing in CI** by reading the `guards` job log, not inferred from the total count.
- Gaps not yet verified: #9847's `pytest-run` was still in flight at the time of writing; the PR is a draft pending that.

## Documentation Updated
- Rules doc updates: none required — no rule changed.
- Architecture doc updates: **none required, and this was checked rather than assumed.** `ARCHITECTURE-CANONICAL.md`'s CI list enumerates `.github/workflows/` entries; `selftest-wiring-guard` is a `run_guards.py::GUARDS` entry executed by the existing `guards` job, so the list is not made stale. `canonical-doc-coherence` passed in the 42/42 run.
- Trade pipeline doc updates: **not applicable** — no pipeline stage touched.
- Roadmap updates: none — coverage did not move (see below); this sprint was tooling.
- GitHub Actions doc updates: `m20-exit-lever-sweep.yml` input descriptions corrected (behaviour unchanged).
- Subsystem doc updates: `docs/research/RESEARCH-CAPABILITY-INDEX.md` routes `m20_wf_effective.py` (75/75, 0 unindexed).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Contradiction 1 — my own claim in #9836 was wrong.** I stated that registering a name in `SELFTESTS` is what makes a self-test run in CI. Both halves were false: `run_guards.py` has always run `check_matrix_corpus_agreement.py --self-test` directly, and my registry entry is a manual alias. Cause: I grepped **one of two** wiring paths and read the empty result as absence. Corrected in the backlog, the PR body, and on board #6927; the guard built in Item 2 now enforces the correct model so it cannot be re-derived wrongly.
- **Contradiction 2 — a backlog row's own proposed fix was wrong.** `BL-20260817-GUARD-SELFTESTS-PLANT-PROBE-FILES-IN-THE-LIVE-REPO-TREE` said to point the guard at a temp dir "via the argument it already takes". `check_diag_unit_allowlist.py::main()` had **no argparse at all**; `DEPLOY_GLOBS` was a hardcoded constant. *Field beats comment* — verified by reading `main()`, not by trying the flag. The argument exists now because I added it, and the row records the correction.
- **Code/doc mismatch:** the sweep workflow described the OOS floor as the target (Item 4). Two inputs, not the one the row named — `split_mode` carried the same drift and is the input a dispatcher reads first.
- **A measured figure of mine was wrong and is corrected:** the first degeneracy rate, 79/509 = 15.5%, counted **superseded** runs. Deduped to newest-run-per-cell it is **75/386 = 19.4%**.

## Risks and Follow-Ups
- Remaining technical risks: **existing corpus rows still carry only the recorded figure.** No re-sweep is needed to read them honestly — `m20_wf_effective.py` derives the split from their committed `wf_folds` — but any consumer reading `wf_summary` directly still sees the inflated count.
- **Remaining product decisions (Tier 3) — QUEUED, nothing decided, no lever flipped:**
  1. **`trend_donchian_xrp_4h` · `trail_decay` · `decay_arm2R_t2.5` — SHIPPED on real-money `bybit_2`.** Records `wf 5/6`; **2/6 effective** (three folds where the lever changed nothing). Whether it still clears its bar is the operator's call. Reproduce: `python3 scripts/research/m20_wf_effective.py --shipped-only`.
  2. `trend_donchian` · `trail_geometry` · `trail6` — live-parity Path-B pass at 4/6, **verified NOT inert**, so a genuine 4/6.
  3. The `splg` enum question — should the matrix gain a value for *"measured, but the grader is under question"*? Its `vt_hot80_t2` is one of **5 fully-degenerate** rows, which independently corroborates holding the cell rather than grading it.
  4. `mhg_pullback_1d` · `stale_stop` shipping — combo untested.
- Blockers: none.

## Deferred Items
- **Deferred item 1 — `BL-20260817-SELFTESTS-DECLARED-BUT-NEVER-RUN-IN-CI`.** Measured rather than assumed empty: **14** scripts declare `--self-test`, `run_guards.py` runs **8**, **6** it never does. Those 6 are research/ops tooling whose correctness is not a repo invariant, so conscripting them wholesale would add CI runtime and failure surface — the desensitised-alarm shape. Wants per-script triage; `ops/get_env.py` is the most likely genuine candidate.
- **Deferred item 2 — the 11 arithmetically-unreachable `exit_head_ml` cells.** `N < 150` lifetime trades cannot form a single fold, so no sweep can clear them. Not a gap; do **not** dispatch runs against them.

## Next Recommended Sprint
- Suggested next sprint: merge #9847, then work the remaining M20 done-condition — **12 actionable cells** of the 23 (23 = 3 pending + 20 blocked, of which 11 are arithmetic).
- Why next: the grading tooling is now honest at both ends, so cell verdicts read from it can be trusted for the first time; the Tier-3 queue above is the operator's first decision point.
- Required verification before starting: re-read coverage **fresh** via `python3 scripts/research/m20_coverage_rollup.py` and quote **both** headline and done-condition — the headline alone is the known trap and does **not** move when a block clears.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — merged content read off `main`, and the runtime import resolution tested rather than assumed.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated — **N/A, no pipeline stage touched.**
- [x] Roadmap status was checked — coverage unchanged at headline **373/376 = 99.2%**, done-condition **23 cells**. Correctly unchanged: this sprint was tooling, not matrix movement.
- [x] Contradictions were recorded — including two of my own, above.
- [x] Remaining unknowns were stated clearly.
