# Sprint Log: S-M20-WRITTEN-AND-NEVER-READ-2026-08-17

## Date Range
- Start: 2026-08-17 ~07:45Z (continues [`S-M20-GUARD-WIRING-AND-INERT-FOLDS-2026-08-17.md`](S-M20-GUARD-WIRING-AND-INERT-FOLDS-2026-08-17.md), same overnight session, operator asleep)
- End: 2026-08-17 ~10:30Z

## Objective
- Primary goal: continue M20, and close the defects this session's own tooling turned up. Every one landed in the same class as the first half of the night — **a value that is computed, declared, and never read.**
- Secondary goals: keep the hourly operator ping; queue every Tier-3 call; leave no probe or stray file behind.

## Tier
- **Tier 1** throughout. Research/reporting scripts, one new pure-function module, tests, backlog + matrix records. No `src/runtime`, no `src/units`, no `config/`, no order path, no service/timer, no live lever, no account-mode change.
- Every finding that would alter live behaviour was QUEUED (see Risks).

## Starting Context
- Active roadmap item: M20 exit-refinement coverage.
- Prior sprint reference: `S-M20-GUARD-WIRING-AND-INERT-FOLDS-2026-08-17.md` (same session, first half).
- Known risks at start: the coverage **headline** does not move when a block clears — the known misquote trap; `exit_head_ml`'s 141 NOT CHECKED cells are a closed negative and must not be reopened.

## Repo State Checked
- Branch: `claude/m20-exit-coverage-matrix-8d3he7`. Merged `b8b75cca` (#9856) → `ce8a496e` (#9858); `fc0caf13` open as #9859.
- Deployment state: none touched. Nothing here deploys.
- Canonical docs reviewed: `CLAUDE.md` (§ diagnostic provenance, § collapsed states), `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `docs/claude/coordination-board.md`.

## Files and Systems Inspected
- `scripts/research/m20_coverage_rollup.py`, `scripts/research/m20_fleet_exit_sweep.py`, `scripts/exit_capture.py`,
  `scripts/backtest_squeeze.py`, `scripts/backtest_trend.py`, `scripts/backtest_pullback.py`,
  `scripts/check_claim_basis.py`, `scripts/ops/check_backlog_criteria.py`, `scripts/research/trend_harness_divergence.py`.
- Workflows: `m20-capture-census.yml`, `m20-exit-lever-sweep.yml`.
- Docs: `docs/research/exit-refinement-coverage.json`, `docs/research/RESEARCH-RIGOR-STANDARD.md`, `ROADMAP.md`, the health-review backlog.

## Work Completed
- **Item 1 — the roll-up printed a number that overstated the work (#9856).** `m20_coverage_rollup.py` itself emitted `read the done-condition as {N} actionable + {M} arithmetic`, computed `cells_to_done - len(unreachable)` — subtracting exactly ONE gate and calling every other gate "actionable". It reported **12 actionable** where about 4 were workable. **This was a TOOL defect, not a quoting slip**, which is why it reached a roadmap entry and three operator pings: every consumer was told "12" by the script. Replaced with `gate_partition()`, keyed on the cell's own STATUS string (so it cannot drift from the matrix) and **reusing** the passed-in `fold_reachability` rows rather than re-deriving the bound. An existing test had **pinned** the defect by asserting the literal string; rewritten to the stronger contract, history in its docstring, not deleted.
- **Item 2 — the census SUMMARY table filled 13 of 15 declared columns (#9858).** Found while confirming that `n` meant what I was about to build a finding on. Counting header cells against row cells — arithmetic, not proofreading — gave 13 of 15. Everything from `gb R med` rightward rendered under a **neighbouring column's name**: `tgt hit` displayed `near_miss_r_left_on_table`, an **R-sum under a COUNT's header**. `r_left_median` and `near_miss_r_to_target` are computed by `exit_capture.py` and had **0 references** in the sweep (positive control: siblings 3–4). The ERROR row was separately 7 wide. `CENSUS_COLUMNS` is now the one width source for header/alignment/error-row/cell-count; `census_row_cells()` extracted so the column→accessor MAPPING is testable; a width mismatch now raises.
- **Item 3 — M20's only actionable `harness_gap` cell was shelved on a floor-vs-target conflation (#9859).** The `squeeze_breakout_4h`/`vol_trail` ref projected the derived OOS base into the "25-35 band" by treating the **25 FLOOR** as the **TARGET**. `resolve_split` clamps at `len(stamps) < 2 * target_oos` with `DEFAULT_SPLIT_TARGET_OOS = 50`, so the clamp fires only below lifetime 100. Census run `32015369620` MEASURED the leg at **n=101** → the derivation targets the full 50 OOS trades, 2× the floor. Built the lever, and moved the trail-firing rule to `src/research/trail_levers.py` because it had already been written **twice** and a third copy was about to land.

## Validation Performed
- Tests: `pytest-run` green on all three PRs (**10.1 / 10.3 / 10.4 min** — real runs, judged by DURATION, since an empty changed-file list yields a fast full green).
- `run_guards.py --base main`: **31 → 35 PASS · 0 FAIL** (the 35 includes `exit-coverage-matrix-guard` + `matrix-corpus-agreement`, selected only once the matrix was touched).
- **Failure paths verified by PLANTING BREAKS, never by reading.** The load-bearing one: swapping two accessors while keeping the census row at width 15 fails **only** the mapping test and **passes** the width test — proving a width-only test would have shipped a still-shifted table. Also: the original 13-value row (3 failures), a dropped ladder cell, adding `squeeze` to the decay gate, dropping the `_effective_trail_mult` alias, and re-inlining the tail test as a third copy. All restored green.
- **The `trail_levers` move is byte-identical**, verified on three ARMED paths (levers-off / vol-armed / decay-armed = 137 / 153 / 144 trades, three distinct md5s). The probe was proven **non-blind**: flipping the documented `min` to `max` moved 153 → **137**, exactly the levers-off count.
- **Reachability verified end-to-end, not by flag presence** — each emitted argv runs `rc=0` with a distinct trade count (107 / 117 / 120 vs 100 levers-off).
- Merged content confirmed on `main` by **READING the files**, never the merge SHA.

## Documentation Updated
- Rules docs: none required — no rule changed.
- Architecture doc: **not required, and this was checked rather than assumed** — no new workflow entry; the changes are research scripts and a `src/research/` pure module.
- Roadmap: the M20 row's dated ledger gains a **373 → 372** note (see below). Historical entries left intact.
- Backlogs: 2 new health-review rows, both with `resolution_criteria`.

## Contradictions or Drift Found
- **My own published "12 actionable" was WITHDRAWN**, and the fix had to go in the SCRIPT — prose alone goes stale silently, and this one already had.
- **My own partition labels were wrong once**: 5 accrual, not the 4 I posted (`splg`'s `insufficient_oos_base_at_derived_split` is accrual *by status*; I had labelled it from its ref). Totals reconcile to 23 either way; the status basis is the one a tool can defend.
- **A matrix ref's forward assessment does not survive its own reversal condition** (Item 3). The withdrawn projection is kept in the cell as record so it cannot be re-quoted.
- **`check_claim_basis.py`'s docstring says the basis must be "in the same row"; `_row_text` scans six fields and excludes `evidence`** — where the population most naturally lives. It caught this PR's own row correctly (I had put the denominator only in `evidence`); noting the doc/code gap, not "fixed" by weakening the guard.

## Risks and Follow-Ups
- **The matrix headline FALLS 373/376 = 99.2% → 372/376 = 98.9%** — measured by running the roll-up before and after, not predicted. A removed block stops counting as "closed", so the cell moves `blocked` → `pending` (`harness_gap` 2→1, `never_attempted` 3→4; done-condition still 23). This is the honest direction, and the deliberate contrast with follow-up (5) below, which was left unmade *because* it improves the number.
- **Remaining product decisions (Tier 3) — QUEUED, nothing decided, no live lever touched:**
  1. `trend_donchian_xrp_4h` · `trail_decay` · `decay_arm2R_t2.5` — **SHIPPED on real-money `bybit_2`**; records wf 5/6, **2/6 EFFECTIVE**.
  2. `trend_donchian` · `trail_geometry` · `trail6` — 4/6, verified NOT inert.
  3. The `splg` enum question (a value for *"measured, but the grader is under question"*).
  4. `mhg_pullback_1d` · `stale_stop` shipping — combo untested.
  5. The `pending` → `blocked:no_harness_levers` flip on `trend_donchian_eth_prop` · `regime_flip_exit` — evidence complete, deliberately unmade: it moves the headline **373 → 374**, and a session that just withdrew an over-optimistic figure of its own should not make an unreviewed change that improves the reported number.
  6. **NEW** — whether to ship any squeeze `vol_trail` lever the sweep may find. Ground (1), the negative fleet prior, is now the ONLY ground standing against it, and it is a prior about OTHER legs which the matrix `_doc` forbids inheriting as a verdict.
- Blockers: none.

## Deferred Items
- **The squeeze sweep itself.** The lever is built but ungraded; the sweep must run from `main`, so it is gated on #9859 merging. Expect a real possibility of `honest_negative` — no verdict is claimed anywhere in this work.
- **`backtest_pullback.py` still carries its own inline copy of the vol-tail test.** `trail_levers.py` now has one home and `backtest_trend.py` uses it; converting pullback is a separate change that must repeat the byte-identity verification against ITS baselines, and was not bundled here.

## Next Recommended Sprint
- Merge #9859, add the ROADMAP 373→372 note, then **dispatch the squeeze `vol_trail` sweep** (`legs=squeeze_breakout_4h`, `levers=vol_trail`, `tp_cap_pct=0.099`, `split_target_oos` empty = 50).
- Why next: it is the one cell in the done-condition that a session can now actually move.
- Required verification before starting: read coverage **fresh** and quote BOTH headline and done-condition — the headline alone is the known trap.

## Wrap-Up Check
- [x] Code inspected directly — merged content read off `main`, not inferred from the merge.
- [x] Documentation reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated — **N/A, none touched.**
- [x] Roadmap status checked — headline moves 373 → 372 and the reason is recorded.
- [x] Contradictions recorded — including three of my own.
- [x] Remaining unknowns stated: the squeeze cell has NO verdict, and the fleet prior is negative.
