# Sprint Log: S-M20-WF-EFFECTIVE-FOLDS-2026-08-17

## Date Range
- Start: 2026-08-17 (overnight autonomous session, continuing from 2026-08-16)
- End: 2026-08-17 14:2xZ (operator awake; Tier-3 items handed over, not decided)

## Objective
- **Primary goal:** close out `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`
  — the sweep's walk-forward counts a fold in which the lever never fired as a
  *win* — by finding and fixing every consumer that grades on the raw count,
  not just the one that surfaced it.
- **Secondary goals:** convert the one remaining `squeeze_breakout_4h`/`vol_trail`
  projection into a measured verdict; audit the remaining raw-`wf_wins` consumers
  and report the result honestly whichever way it came out; keep the M20 coverage
  roll-up moving by MEASUREMENT rather than relabelling.

## Tier
- **Tier 1** throughout. Research tooling, tests, a matrix `ref`, and backlog rows.
- Justification: nothing in this sprint touched `src/`, `config/`, a unit file, or
  a matrix `status` on a live leg. Every disposition question that WOULD have been
  Tier-3 was queued for the operator instead of decided — see § Risks and Follow-Ups.

## Starting Context
- Active roadmap items: M20 (exit refinement). Coverage headline at session start
  372/376; done-condition 23 cells.
- Prior sprint reference: `docs/sprint-logs/S-M20-EXIT-LOOP-DECOUPLE-2026-08-12.md`
  (the decouple half of M20); `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`
  was opened the same night this sprint started.
- Known risks at start: two other sessions live on the board (#6927) — one on the
  tick-chain / PR #8815, one holding draft #9717. Both scopes were left alone; gaps
  found in them were flagged as board comments only.

## Repo State Checked
- Branch: `claude/m20-exit-coverage-matrix-8d3he7`. `main` walked
  `6acbaa40` → `7c048730` → `6b10dbed` → `4f83a49a` across this sprint's merges,
  with `851e517d` (#9868, another session) landing between two of them.
- Deployment state reviewed: **not** touched. No VM action, no env flip, no
  system-action dispatched. Every merge here is research tooling the trader does
  not import.
- Canonical docs reviewed: `CLAUDE.md` § "Collapsed states" and § "Number
  provenance" (the class this whole item belongs to); `docs/CLAUDE-RULES-CANONICAL.md`
  § "Always state the population" and § "Green is not evidence".

## Files and Systems Inspected
- Code files inspected: `scripts/research/m20_fleet_exit_sweep.py` (the `FOLDS`
  literal at :196, `walkforward()` at ~:1836, the
  `walkforward_effective`/`walkforward_inert_wins` emitters at :2717 and :2748);
  `scripts/research/m20_wf_effective.py` (`is_inert` :64, `grade_folds`,
  `newest_per_cell`); `scripts/research/m20_path_b_floor.py` (`wf_pass`,
  `analyse`); `scripts/research/m20_ack_corpus_disagreements.py`;
  `scripts/ci/check_matrix_corpus_agreement.py` (`newest_floor_clearing_pass`,
  `wf_summary`); `scripts/research/m20_coverage_rollup.py`.
- Config files inspected: none changed. `config/strategies.yaml` was READ for
  routing/arm questions and not written.
- Deployment files inspected: none.
- Docs inspected: `docs/research/exit-refinement-coverage.json`,
  `docs/research/m20-sweep-corpus.jsonl`, `docs/claude/health-review-backlog.json`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: none changed; `guards`/`pytest-run`/
  `pytest-collect` read as CI status only.

## Work Completed

- **#9859 → `6acbaa40` — the floor-vs-target conflation.** `MIN_OOS_TRADES = 25`
  answers *"is this cell gradeable?"*; `DEFAULT_SPLIT_TARGET_OOS = 50` answers
  *"where do we cut?"*. A projection had reasoned from one to the other. The two
  constants now carry the instruction not to be re-coupled.

- **#9866 → `7c048730` — `squeeze_breakout_4h`/`vol_trail` measured.** Ran the
  sweep to completion and rewrote the cell from `pending` to `honest_negative`
  with the measured split recorded (`split_lifetime_trades=101`, target 50,
  boundary 2023-09-06, IS=51 / OOS=49). The Path-B candidate is recorded, not
  buried: `vt_cold10_t1.8` at **+3.2198 OOS net_R** with `d_max_dd_OOS −0.424`
  and wf 4/6 at **zero inert folds**. The ref states plainly that **no ship is
  proposed** — `gate_passed_IS: false`.

- **#9870 → `6b10dbed` — the Path-B floor gate stops counting no-ops.**
  `m20_path_b_floor.wf_pass` graded on the raw `wf_wins`, and it is the pass/fail
  signal the **floor calibration itself** is computed over. It now imports
  `grade_folds` from the sibling reader and grades on `effective = ok − inert`.
  Three bases stay distinguishable and are counted in the report —
  `effective` / `raw_only` ("we could not look at the folds", so fall back rather
  than invent a zero) / `ungraded` (`None`, never `False`). `analyse` publishes
  BOTH rates plus `cells_inflated_by_inert_folds`, because the size of the gap is
  itself the finding. Nine tests in
  `tests/test_path_b_floor_inert_folds.py` pin it, including the load-bearing case
  of an identical recorded 6/6 producing opposite verdicts.

- **#9872 → `4f83a49a` — one definition of "did the lever fire?".** The ack
  drafter held its own inline copy of the inert predicate **twice**. Both removed;
  it now imports `m20_wf_effective` the same way it already imported the agreement
  guard. Verified on `main`: 5 `_wf_effective` references, **0** inline predicates,
  and exactly **one** definition of `is_inert` repo-wide.

- **#9873 (open) — the fold panel is a fixed calendar.** Filed
  `BL-20260817-WF-FOLD-PANEL-IS-A-FIXED-CALENDAR-NOT-AN-OOS-WALKFORWARD`. `FOLDS`
  is a module literal of six calendar years and `walkforward()` never reads the
  cell's own `split`, so **5 cells have OOS years the panel never grades** (all
  five PASSED; largest `tlt_pullback_1d`/`decay_stall6_t2.5` at +8.3159 OOS with
  2019–2020 ungraded) and **14 cells re-grade folds inside their own fitting
  window** (`winner_mfe_p80` derives `decay_p80arm*`/`gb1R_afterMFE*` with
  `--end split`).

- **#9875 → `aa254853` — both findings gained shipped instrumentation, and a
  third finding fell out of building it.** Three things in one PR because the
  qualification specifies the predicate the emitter implements, and shipping the
  spec without the code would leave a window where the row says "use four
  branches" and nothing does.
  1. **Qualified the high-severity row: 102 → 61.** "Declared" is not "in the
     measured baseline" — the lever-OFF arm *removes* a declared lever, and **41**
     rows are real measurements the naive `lever in present` predicate would
     suppress as artifacts (one at `d_net_r_IS` **+19.1782**). That is the mirror
     of the defect itself. Correct partition **61 / 471 / 841**.
  2. **`lever_in_baseline`** emitted per cell row, reproducing that partition
     exactly, sited where the fact was already computed and discarded
     (`base_missing_other_levers` subtracts the row's own lever and emits only the
     complement). Consumer: `m20_ack_corpus_disagreements.caveats_for` branches
     all three ways — that drafter's output *is* a `ref` asserting a measurement,
     so it is the one place that must refuse to.
  3. **Fold coverage** — `fold_years`, `oos_first_year`, `uncovered_oos_years`,
     `pre_split_fold_years` — closing the last untouched half of the fold-panel
     item. Computed in the **extractor, not the sweep**, because all 133
     fold-carrying rows already carry the inputs: every committed row gains
     coverage on the next extract instead of waiting on the hours-long re-run.

- **A defect in `collapsed-state-guard`, found by refusing to trust its green.**
  Registering the new contract, I planted the regression (deleted the consumer) —
  **and the guard still passed.** Deleted the test file too; still passed. Reading
  `main()` explains it: the consumer scan skips only the producer, `scripts/ci/`
  is not in `_SKIP_DIRS`, and every `CONTRACTS` entry names its own states in
  `states` + `why`. **Registering a contract partly self-satisfies the coverage
  check.** Measured across all 12 contracts with the registry excluded: exactly
  **one** really loses coverage — `netting_attribution.anchor_status`, unread
  state `deferred`. Filed as
  `BL-20260817-COLLAPSED-STATE-GUARD-REGISTRY-COUNTS-AS-ITS-OWN-CONSUMER`,
  **not fixed** — the one-line exclusion turns that pre-existing contract red and
  would block every PR, which is how a guard gets switched off.

- **A clean negative, reported as one.** Audited the remaining raw-`wf_wins`
  consumers. `check_matrix_corpus_agreement.newest_floor_clearing_pass` reads
  **zero** `wf_*` fields, and its `wf_summary` (:357) is a display field only —
  no defect. Recorded rather than dressed up as a finding.

## Validation Performed

- **Tests run:** 9 new tests in `tests/test_path_b_floor_inert_folds.py`, green.
  `guards` at 15 PASS / 0 FAIL on the final diff, with `impossibility-claim-guard`
  and `claim-basis-guard` both confirmed to have actually RUN (not skipped) on the
  backlog row that uses the word "unmeasured".

- **Every probe was proven able to fail before its result was believed.** Three
  planted breaks on #9870's tests; a one-sided-predicate plant on #9872's
  byte-identity check (md5 `bf900be5…` identical across all 133 fold-carrying
  rows); and two-directional controls on the fold-coverage arithmetic — planting
  the panel start at 2016/2019/2021/2023/2026 returns 0/2/5/11/125, monotone and
  responsive, so the reported 5 is not a constant the probe prints regardless.

- **Merges verified by READING files off `origin/main`, never by a merge SHA.**
  Content checks after each of the four merges, not a green tick.

- **`pytest-run` judged by DURATION.** ~9–11 minutes is a real run; a sub-minute
  green means an empty changed-file list tested nothing. #9872's run:
  12:28:51 → 12:37:41 = **8.8 min**. `cancelled` rows in the check list are
  superseded concurrency-group runs, not failures.

- **Gaps not yet verified:** no OOS delta was decomposed per year for any of the
  5 uncovered-OOS cells, so the effect size on any individual verdict is
  unmeasured. #9873's CI was still in progress at hand-over.

## Documentation Updated
- Rules doc updates: none needed — this sprint is an instance of rules already
  canonical (§ "Collapsed states", § "Always state the population").
- Architecture doc updates: none (no runtime surface changed).
- Trade pipeline doc updates: none — no pipeline stage was touched.
- Roadmap updates: none required; the M20 headline moved by measurement
  (372 → 373/376) and the roll-up derives it from the matrix, not from prose.
- Subsystem doc updates: `docs/research/exit-refinement-coverage.json` — the
  squeeze `vol_trail` ref rewritten (7447 chars), previous ref kept as record.
- Historical docs marked superseded: none.

## Contradictions or Drift Found

- **My own PR #9859 claim was wrong.** It said the split "derives at the full 50
  OOS trades"; the sweep measured **49**. Corrected on the board and in the matrix
  ref. The refutation of the withdrawn "25–35 band" projection is unaffected.

- **I overstated the `wf_pass` finding publicly** before measuring its effect.
  I claimed it "helps select the floor value the operator is asked to adopt".
  Measured before and after: the verdict is `no_separation` and
  `recommended_floor: None` **both ways** — nothing was mis-selected on this
  corpus. Real effect: an **11.6pp** inflated pass rate (0.6875 recorded vs
  0.5714 effective) and a candidate floor moving 1.4187 → 1.1619. Corrected on
  #9866 and the board; severity downgraded in the backlog.

- **A blind probe returned a reassuring zero.** My first flip measurement read
  `grade_folds(...)["won"]` — a key that function does not return — so every
  comparison was `None` and it printed "0 of 96". The real answer is **17 of 96**.
  This is the sprint's most important lesson and the reason every later probe
  carries a planted control.

- **A near-miss 124-of-133 false alarm.** The fold-panel finding almost shipped
  claiming 124 cells grade in-sample data. Classifying by arm PROVENANCE cut it to
  **14**: for a DECLARED arm a pre-split fold is redundant, not circular. The
  backlog row states the ~9× overstatement explicitly so the number is not
  re-inflated later.

- **A count of a FIELD read as a count of a CONDITION — four times.** The
  124-of-133 near-miss above is one instance; the same shape recurred on
  **102-vs-61** (`declared_levers_present` contains the lever for 102 rows, but
  only 61 are measured against a baseline that runs it — 41 had it dropped), and
  twice more as population conflations on the `wf_pass` rate. Recorded as one
  class rather than four incidents, because the emitter would otherwise have
  hardcoded it.

- **Two mistakes caught while writing the fold-coverage emitter.** The call first
  read `wf_folds` — the CORPUS field name — where the sweep's key is
  `walkforward_folds`; every row would have returned all-`None`, the exact
  write-and-never-read shape the field exists to expose. And
  `int(str(12345)[:4])` is **1234**, so a nonsense split emitted **787**
  "uncovered years" — a confident answer from garbage, found by my own test
  asserting `None` and getting a list. Both are pinned as tests.

- **Populations do not interchange.** One finding produced four correct
  denominators — 17/96 (deduped newest-run-per-cell), 19/133 (all fold-carrying
  rows), 13/112 (the rows `analyse` actually grades — the one the floor depends
  on), and 5/133 for the fold panel. All are recorded in
  `tests/test_path_b_floor_inert_folds.py`'s docstring.

- **A grep of mine made the gate partition look broken** (summed to 21 vs the 23
  quoted) because the pattern omitted `data`. My probe, not the tool.

## Risks and Follow-Ups

- **Remaining technical risks:** the fold panel still cannot say what it did not
  grade — an ungraded OOS year reads identically to a graded-and-won one. That is
  the collapsed-state shape, filed but not fixed.

- **Remaining product decisions (Tier 3) — 8 items, all QUEUED, none decided:**
  1. `trend_donchian_xrp_4h`/`trail_decay`/`decay_arm2R_t2.5` is **SHIPPED on
     real-money `bybit_2`**, records wf 5/6, and is **2/6 EFFECTIVE**. The only
     queued item where money is currently exposed to a number shown to be inflated.
  2. `trend_donchian`/`trail_geometry`/`trail6` at 4/6, verified NOT inert.
  3. The `splg` enum question.
  4. `mhg_pullback_1d`/`stale_stop` shipping.
  5. The `pending` → `blocked:no_harness_levers` flip on
     `trend_donchian_eth_prop`/`regime_flip_exit` — deliberately NOT made, because
     it would IMPROVE the headline (373→374) without measuring anything.
  6. Whether to ship the squeeze cold-tail `vt_cold10_t1.8` (+3.22R OOS, drawdown
     also improved, 4/6 folds zero inert — but FAILS Path A, and Path-B thresholds
     are unset).
  7. `backtest_pullback.py` still holds its own inline copy of the vol-tail test.
  8. Whether any `passed` verdict should be re-graded given the 5 uncovered-OOS
     PASS cells and the 14 IS-armed cells.

- **Blockers:** none for Tier-1 work. **M20 is nearly exhausted for session work:**
  of the **22** done-condition cells (3 pending + 19 blocked, population 47 live
  legs × 8 levers = 376), only **4 are movable by anything a session does** —
  1 `harness_gap` + 3 `never_attempted`. The other 18 (11 arithmetic, 5 accrual,
  2 data) wait on legs trading more or on candles existing.

## Deferred Items
- Deferred item 1: the 4 movable M20 cells (build the squeeze `vol_trail` harness
  flags; run the 3 never-attempted sweeps).
- Deferred item 2: making fold coverage legible in `walkforward()` output — the
  fix for #9873's finding, as opposed to the record of it.

## Next Recommended Sprint
- **Suggested next sprint:** the Tier-3 disposition review of queued item 1 (the
  xrp real-money lever at 2/6 effective), then either the 4 movable cells or a
  pivot off M20.
- **Why next:** item 1 is the only queued item with live real-money exposure to a
  number this sprint proved inflated. Everything else is knowledge, not risk.
- **Required verification before starting:** re-read the xrp cell's `wf_folds`
  through `m20_wf_effective.grade_folds` rather than its stored `wf_summary`, and
  confirm the leg's current routing from `config/accounts.yaml` — do not infer
  real-money exposure from the matrix `routing` column alone.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries. (`FOLDS`,
      `walkforward()`, `newest_floor_clearing_pass` all read in full.)
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update
      and no dashboard verification applies.
- [x] Roadmap status was checked (headline + done-condition re-measured from
      `m20_coverage_rollup.py`, not quoted from memory).
- [x] Contradictions were recorded — including three of my own.
- [x] Remaining unknowns were stated clearly (per-year OOS decomposition
      unmeasured; #9873 CI in flight at hand-over).
