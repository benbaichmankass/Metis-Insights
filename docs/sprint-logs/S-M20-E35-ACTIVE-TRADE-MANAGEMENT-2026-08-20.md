# Sprint Log: S-M20-E35-ACTIVE-TRADE-MANAGEMENT-2026-08-20

## Date Range
- Start: 2026-08-20 ~14:30Z
- End: 2026-08-20 ~19:10Z

## Objective
**Primary:** continue M20 E3 after #10060's honest negative — run the
bracket-geometry sweep (the one exit parameter set never swept) and the
operator-pre-approved `rr_floor` walk-forward, both net of fees.

**Secondary (operator-directed mid-session, and it became the larger outcome):**
record the reframe that this milestone is **ACTIVE TRADE MANAGEMENT**, not exit
refinement — a bracket must carry a predictive expectation at entry, revisable in
*either* direction — and name an ML track rather than leaving it to whoever gets
to it.

## Tier
**Tier 1.** Research tooling, docs, backlog. No `src/` order path, no
`config/strategies.yaml`, no unit file, no VM action, no trainer VM. Every
bracket/lever parameter change this evidences remains **Tier-3**.

## Starting Context
- Active: M20, post-#10060 (E3's licence did not survive its own precondition;
  the joint lever grid bought +0.000 R).
- Prior sprint: `S-M20-E3-EXIT-MECHANISM-2026-08-20`.
- Known risks carried in: `BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY`,
  `BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT`, and the standing
  caution that a probe keyed on a non-existent field returns the comfortable answer.

## Repo State Checked
- Branch `claude/m20-e3-exit-mechanism-cont-plkj6q`, reset to `origin/main` at
  `da8713f`; merged `origin/main` at `f76eb0f` mid-session; ended at `bca3121`.
- PR **#10068** (draft), 7 commits, **4/4 CI green on `bca3121`**, 0 review comments.
- Canonical docs read at start: root `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`
  (RULE ONE, population, collapsed states, multi-session coordination),
  `ROADMAP.md` M20 row, `docs/design/exit-mechanism-construction-PROCESS.md`
  §§ E2/E3/E3.5, coordination board #6927 (▶️ START posted 14:44Z).

## Files and Systems Inspected
- **Harnesses:** `scripts/backtest_trend.py` (`tp_price` at :483-488 fixed at
  entry, maxDD at :836 over NET), `scripts/backtest_pullback.py`,
  `scripts/backtest_squeeze.py` (maxDD at :378 over GROSS; no `--rr-floor`; no
  `tp_r_effective`).
- **Sweep machinery:** `scripts/research/m20_fleet_exit_sweep.py`
  (`run_cell`, `resolve_split`, `winner_mfe_p80`, `run_census`, `beats`,
  `walkforward`, `cells_for`, `inert_rr_floor_reason`, `base_args`),
  `m20_split_dispersion.py`, `e3_joint_lever_sweep.py`, `e3_barrier_decomposition.py`.
- **Panel/label:** `src/research/triple_barrier.py`,
  `scripts/research/build_intrabar_exit_panel.py`, `analyze_exit_head.py`.
- **Guards:** `scripts/check_silent_empty_in_diff.py`,
  `scripts/check_harness_lever_coupling.py`, `scripts/ops/check_research_index.py`,
  `scripts/ops/check_backlog_refs.py`, `scripts/check_impossibility_claims.py`.
- **Delegation:** `.github/workflows/llm-delegate.yml`, `scripts/llm/scope_guard.py`.
- **Config:** `config/strategies.yaml` (read-only).

## Work Completed
1. **Fetch pass, free lane, trainer VM NOT used.** 12 candle files from
   `data.binance.vision` via `scripts/ops/fetch_backtest_candles.py`
   (BTC/ETH/SOL/XRP/ADA/AVAX at 1h/2h/4h), span 2021-08-16 → 2026-08-19. Took
   the runnable-leg count from 0 usable to **19**.
2. **`scripts/research/e35_barrier_race.py`** (new, 39/39 self-test) — the
   entry-time barrier race.
3. **`scripts/research/e35_bracket_geometry_sweep.py`** (new, 44/44) —
   `tp_r × atr_stop_mult × timeout_bars` through the existing m20 gate by import.
4. **`scripts/research/e35_shard_plan.py`** (new, 20/20) — one-leg-per-job CI
   matrix; empty matrix is an ERROR, not an empty success.
5. **Four defects found and dispositioned** (§ Contradictions).
6. **Docs:** `docs/research/e35-bracket-is-not-a-decision-2026-08-20.md`,
   `e35-rr-floor-walkforward-2026-08-20.md`,
   `e35-bracket-geometry-sweep-2026-08-20.md`; `PROCESS.md` §§ E3.6 + E-ML;
   M20 renamed in `ROADMAP.md`; `RESEARCH-CAPABILITY-INDEX.md` +2 rows;
   `llm-delegate` SKILL.md re-grade.

## Validation Performed
- **Self-tests:** 39/39 · 44/44 · 20/20. One real bug caught by its own test
  (`--gate-top 0` still gated the joint argmax).
- **CI:** `run_guards.py --all` **49/49** at each commit; 4/4 CI green on
  `bca3121`; `pytest` targeted suites 87 passed.
- **Positive controls, run BEFORE trusting any negative:** each swept axis
  provably moves the harness; `run_cell` parallel distinctness 5/5 with the
  pre-fix collision as the negative control; the corpus collision detector shown
  to fire on an injected positive before its 0/2,756 negative was accepted; the
  dispersion tool's own `harness_agreement` (0.0008 R) gating every band quoted.
- **Cross-checks:** `tp_R` medians **18/18 EXACT (worst |diff| 0.0)** against the
  harnesses' independently-computed `tp_r_effective_median`; backlog union
  byte-presence asserted in BOTH directions (0 missing either side).
- **Gaps not yet verified:**
  - The `eth_pullback_2h` joint cell is **n = 1 leg** and is the **argmax of 199**;
    the multiple-comparisons cost is **unpriced** (no shuffled-label or random-cell
    control was run). It is NOT a Tier-3 candidate on this evidence.
  - The `path_b_wf_pass` drawdown rates were **re-derived** from `results.jsonl`,
    not read from `verdicts.json` (which records none).
  - `BL-20260820-SQUEEZE-MAXDD-IS-GROSS-WHILE-EVERY-SIBLING-IS-NET` is **filed, not fixed**; recorded
    squeeze maxDD figures elsewhere were NOT re-measured.
  - The delegate's line citations were re-graded at n=25; **the pre-fix
    comparison is historical**, not re-run.

## Documentation Updated
- Rules/skills: `.claude/skills/llm-delegate/SKILL.md` (measured re-grade).
- Design: `docs/design/exit-mechanism-construction-PROCESS.md` §§ E3.6, E-ML.
- Roadmap: M20 renamed **Active Trade Management** + dated entry.
- Research: 3 new docs (above).
- Index: `docs/research/RESEARCH-CAPABILITY-INDEX.md` (+2 rows; guard now 86/86).
- **Coverage matrix: `docs/research/exit-refinement-coverage.json` gained a ninth column,
  `bracket_geometry`** (added at session close, during the doc-freshness decision-landing pass —
  it had been missed). Statuses DERIVED from the per-leg `gate` blocks in the artifacts, not from
  the research doc's prose: 13 `honest_negative` / 2 `passed_unshipped` / 12 `pending` /
  25 `blocked`, over 52 rows of which **19 legs were measured and 33 were not**. The three
  not-measured reasons are kept apart (no free-lane feed · no dispersion band · dispersion
  refused) so a missing feed is never read as a negative result. Research doc gained a § 6
  recording the same.
- Backlog: `docs/claude/health-review-backlog.json` +3 rows (753 total).
- TRADE-PIPELINE.md: **not touched — no pipeline stage changed** (Tier-1 research only).

## Contradictions or Drift Found
1. **`BL-20260820-RUN-CELL-SHARES-A-FIXED-TEMP-PATH`** (high, **resolved**) —
   `m20_fleet_exit_sweep` wrote to **four** process-shared `/tmp` literals incl.
   `resolve_split` (the IS/OOS boundary). Measured: 3 legs on 3 symbols returned
   an identical base `net_R` of −9.6113. Fixed (`mkstemp`), pinned, corpus scanned
   clean (0 of 2,756 across 51 legs).
2. **`BL-20260820-SWEEP-EMITS-CELLS-FOR-FLAGS-THE-HARNESS-DOES-NOT-IMPLEMENT`**
   (medium, **resolved**) — rr_floor cells emitted to `backtest_squeeze.py`, which
   has no `--rr-floor`; 3 of 57 rows graded `error`, a collapsed state. Fixed by
   reading the harness source, three-state, pinned with an opposite-defect control.
3. **`BL-20260820-SQUEEZE-HARNESS-EMITS-NO-TP-R-EFFECTIVE`** (low, open).
4. **`BL-20260820-SQUEEZE-MAXDD-IS-GROSS-WHILE-EVERY-SIBLING-IS-NET`** (medium,
   open) — `backtest_squeeze.py:378` vs `backtest_trend.py:836`. Found *because*
   `m20_split_dispersion` REFUSED (delta 0.7986 R vs 0.001 tolerance). Two
   hypotheses refuted first (row ordering; emit-schema divergence).
   **Cross-family maxDD comparisons are gross-vs-net.**
- **Not caused here but observed:** four squeeze/sibling divergences in one day
  suggests a harness-convergence guard is the durable fix, not three point patches.
- **Own errors, corrected in-session:** a stop-axis refusal rule that was
  backwards (refused cells better in R *and* cheaper in leverage); a tool that
  ignored the `_prop` legs' `tp_r: 6.0` ceiling (23.8% of the population); a
  `grep -c` verification that matched the pre-patch line and so could not
  distinguish applied from not-applied.

## Risks and Follow-Ups
- **Technical:** the sandbox recycled 3+ times (16:40 / 17:48 / 18:43Z) killing
  long serial runs. Diagnosed as container recycle, **not** resource exhaustion
  (30 GB free, 799 MB of 16 GB used, no OOM). Mitigation shipped:
  `e35_shard_plan.py` + per-leg resume. **Not a production-VM issue.**
- **Tier-3 decisions owed (none proposed here):** any `tp_at_r` /
  `atr_stop_mult` / `timeout_bars` / `rr_floor` declare.
- **Blockers:** ML-1 is gated on
  `BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY`, and that
  row cannot be closed by passing a different number — `triple_barrier_forward`
  takes a scalar `tp_r` with no cap parameter, and the emitted per-row record
  carries **no field naming the barrier it was labelled at** (verified by key
  extraction).

## Deferred Items
- Fixing the squeeze maxDD basis and re-measuring recorded figures.
- Pricing the argmax-of-199 multiple-comparisons cost.
- A second independent leg for the joint-cell result.
- The TP-extension harness lever (`tp_price` is immutable in the bar loop).
- Extending `check_harness_lever_coupling.py` to the cell→harness direction.

## Next Recommended Sprint
**A second leg for the joint bracket cell, and the E2 barrier fix that unblocks ML-1.**
Why: the joint-cell result is the first thing in M20 to survive IS/OOS + effective
walk-forward + dispersion, and its only disqualifier is that it rests on one leg
and an unpriced search. Required verification before any Tier-3 conversation:
a second, independent leg passing at `pass_fraction` 1.0, plus a control that
prices the argmax-of-199.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed → `docs/TRADE-PIPELINE.md` correctly untouched.
- [x] Roadmap status was checked and updated (M20 renamed + dated entry).
- [x] Contradictions were recorded (4 filed, 2 resolved in-session).
- [x] Remaining unknowns were stated clearly (§ Gaps not yet verified).
