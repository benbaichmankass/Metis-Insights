# Sprint Log: S-M20-PULLBACK-STALE-BACKLOG-2026-08-17

## Date Range

2026-08-17 02:45Z – 03:30Z (overnight autonomous session, operator asleep).
Second workstream of the night; the first is
[`S-M20-CORPUS-UNION-2026-08-17.md`](./S-M20-CORPUS-UNION-2026-08-17.md).

## Objective

Work the M20 pullback-family stale/giveback re-sweep backlog down.

The framing changed one step in, and that change is the sprint. The named task
was "re-sweep the pullback family". Before dispatching anything I read the
corpus — and found the matrix was already behind measurements the corpus held.
So most of this sprint is a **read-back**, not a re-measurement, and only one
leg actually needed a fresh run.

## Tier

**Tier 1** throughout. Two JSON artifacts (`docs/research/exit-refinement-coverage.json`,
`docs/claude/health-review-backlog.json`), one corpus data file, one sweep
dispatch on free runners. No `src/`, no `config/`, no live path, **nothing
shipped**. Every disposition that would change a live lever is recorded as
QUEUED for the operator.

## Starting Context

Stale DECISIONS stood at 3, done-condition at 25 cells (3 pending + 22 blocked).
The corpus had just been unified and proven end-to-end (#9823, #9827, #9830), so
for the first time the corpus on `main` was complete — which is precisely what
made this sprint possible: a matrix can only be shown to lag a corpus that is
not itself lagging.

## Repo State Checked

- `docs/research/exit-refinement-coverage.json` — 376 live cells, 8 lever columns.
- `docs/research/m20-sweep-corpus.jsonl` — 1364 rows on `main` at start, 1376 after.
- `scripts/ci/check_matrix_corpus_agreement.py` — read the source, specifically
  `NEGATIVE_STATUSES` (line 105) and the early return at line 242.
- `scripts/research/m20_fleet_exit_sweep.py:572-597` — `MIN_OOS_TRADES = 25` and
  `DEFAULT_SPLIT_TARGET_OOS = 50`, plus `git log -S` on the latter to date it.
- `.github/workflows/m20-exit-lever-sweep.yml` — inputs, incl. the stale
  `split_target_oos` description.
- `tests/test_m20_tp_geometry_guard.py` — read after it failed, not before.

## Files and Systems Inspected

| What | Why |
|---|---|
| corpus rows for `mhg_pullback_1d`, `htf_pullback_trend_2h` | the two legs carrying pullback stale decisions |
| all 376 live cells × 8 levers | to generalise the first finding rather than fix one cell |
| `wf_folds` on every cell I was about to quote | the degeneracy defect filed earlier tonight makes a bare `N/M` unquotable |
| `scripts/ci/guard_selftests.py:162` | after a stray probe file surfaced mid-run |

## Work Completed

**Four matrix cells corrected, one leg re-swept, three defects filed.**

1. **`mhg_pullback_1d / giveback_stop`: `blocked:insufficient_base` → `honest_negative`.**
   The cell's own ref said "Unblocks at >=25 OOS trades" against a measured OOS
   of 7. A 2026-08-17 live-parity run had already reached OOS=36. Both cells
   lost — `gb1R_afterMFE1R` IS +6.5222 / OOS −3.4918, `gb1R_afterMFE2R` negative
   on both sides.

2. **`mhg_pullback_1d / stale_stop`: `tp_geometry` `no_take_profit` → `live_parity`,
   status held.** The pass reproduces under the geometry production places
   (`path_b_wf_pass`, wf 4/6). Status stays `passed_unshipped` deliberately:
   renewing evidence does not make a lever shippable, and the `stale12`+`trail3`
   combo is still untested.

3. **`iwm_trend_long_1d / vol_trail`: blocked → `honest_negative`.** All three
   cells `is_oos_fail` outright at live parity, OOS=34. **No walk-forward ran**,
   which is exactly why this one is gradeable and its `splg` sibling is not.

4. **`splg_trend_long_1d / vol_trail`: status HELD, ref corrected.** See
   § Contradictions.

5. **`htf_pullback_trend_2h / trail_geometry` re-swept** (run `31989495640`, at the
   sanctioned default target). IS=357 / OOS=49. Shipped `trail_mult 4.0` survives
   — `trail3` `is_oos_fail` (IS −9.33 / OOS −11.68), `trail5` `is_oos_fail`
   (IS +7.74 / OOS −4.44). `tp_geometry` → `live_parity`, status held at `shipped`.

6. **`mes_trend_long_1d / trail_geometry` annotated, nothing else touched.** Now the
   only remaining stale DECISION, and unreachable by re-running.

## Validation Performed

- **Every `wf_summary` was opened before being quoted.** `mhg/stale8_lt0R`'s 4/6
  carries six non-zero `d_net_r` folds (+1.2617, −0.1798, +0.4569, +0.4786,
  −0.4418, +0.0926) — not the degenerate shape. Recorded honestly that the 2026
  win is thin (+0.0926), so it is four real wins of which one is marginal.
- **The split target was dated, not assumed.** `git log -S 'DEFAULT_SPLIT_TARGET_OOS = 50'`
  → `e0f0761e`, 2026-08-16, an operator Tier-3 call.
- **The whole blocked column was swept**, not just the cell that surfaced the gap.
- **`scripts/ci/run_guards.py` re-run after every commit**, because the runner
  refuses to call an uncommitted tree clean and says so.
- **The corpus-branch merge conflict was resolved by the union tool**, and the
  result checked with `cmp` against the branch file rather than assumed.

## Documentation Updated

- `docs/research/exit-refinement-coverage.json` — five cells, `updated_at`, and
  the `_unstamped_ceiling` ratchet 210 → 208.
- `docs/claude/health-review-backlog.json` — three new items, two updates on one.
- `docs/research/m20-sweep-corpus.jsonl` — 1364 → 1376 rows (#9833).

## What checking changed that reasoning would have got wrong

Recording these because in both cases the wrong answer was the more natural one.

1. **I was about to discount the key measurement as configured-to-pass.** The run
   carried `split_target_oos=50`, which looked like a deliberate override chosen
   to clear the floor. It is the **script default**, set by an operator Tier-3
   decision the day before. Had I trusted the workflow's input description
   instead of the constant, I would have been confirmed in the error — that
   description still says empty defaults to `MIN_OOS_TRADES`. Filed.

2. **I was about to propose a guard fix with a 67% false-positive rate.** The
   obvious predicate is "does a corpus verdict exist for this blocked cell?".
   Sweeping all 376 cells: 6 blocked cells match, and only **3** are genuine —
   the other 4 are 2026-08-10 rows emitted on base OOS of 3–6, before
   `MIN_OOS_TRADES` enforcement existed. A guard shipping at 4 false positives
   out of 6 findings is the desensitized alarm the P1 rule exists to prevent.

## Contradictions or Drift Found

- **`matrix-corpus-agreement` cannot see the class it most needs to.** Its
  `NEGATIVE_STATUSES` gate returns early for any `blocked:`/`pending` cell, so a
  block the corpus has already answered persists while the guard prints OK. It
  did exactly that over three live instances. The status whose meaning is
  *provisional* is the one status exempt from re-checking. Filed as
  `BL-20260817-MATRIX-CORPUS-GUARD-NEVER-CHECKS-BLOCKED-CELLS`.

- **A state the matrix enum cannot express.** `splg_trend_long_1d/vol_trail` is
  measured above the floor at live parity — so its block reason is factually
  stale — but its only passing cell passes on `wf_summary: 6/6` with **all six
  folds at `d_net_r == 0.0`**. `honest_negative` would assert a negative the run
  did not establish; the pass would ship a lever on six no-ops. *"Measured, but
  the grader is under question"* is neither. Status held, ref corrected to stop
  claiming we could not look. **Operator call, not mine.**

- **The tp-geometry ratchet caught my own change, correctly.** Stamping two cells
  without lowering `_unstamped_ceiling` left 2 cells of slack, so un-stamping one
  no longer breached and both planted-omission tests failed with an *empty*
  problem list — the probe going quiet, which is the exact failure that file
  exists to catch. Tightened 210 → 208.

- **A near-miss worth recording as drift, not just a nuisance.**
  `guard_selftests.py:162` plants its probe into the real working tree. One
  surfaced in a guard run immediately before a `git add -A`. That commit was
  clean — verified by reading `git show --stat`, not by assuming — but the
  hazard is real and is filed.

## Risks and Follow-Ups

- **`trend_donchian/trail_geometry` is the largest open item and it is Tier-3.**
  The corpus rows landed in #9833 surface a live-parity Path-B pass (`trail6`,
  wf 4/6, IS=300/OOS=49, folds checked and non-degenerate) against a cell
  recorded `honest_negative`. It passes Path B, not Path A: `gate_reason_OOS =
  maxdd_worse` with `d_net_r` OOS +1.7851 — better net R, worse drawdown, exactly
  the trade-off Path B exists to surface. This is the census's highest-value leg
  (316R left at the 1R rung, negative R-weighted capture). **Recorded as
  counter-evidence, status untouched, queued.**
- The guard fix needs the two-part predicate above **plus** a planted control;
  a quiet pass would prove nothing, and this guard already passed quietly.

## Deferred Items

- Fixing (rather than filing) the three defects.
- `vol_trail` grading generally, which is blocked on the `wf_summary` degeneracy
  defect rather than on data.

## Next Recommended Sprint

**Fix `matrix-corpus-agreement`'s blocked-cell blind spot**, with the predicate
established here (live-parity measurement AND `base_trades_OOS >= floor`) and the
control set this sprint measured: 3 cells that must flag, 4 that must not. That
control set is the reason to do it next while it is fresh — it is a planted
control that already exists in the data rather than one that has to be invented.

Note the sequencing: `splg` will be a legitimate flag once the guard can see
blocked cells, so the acknowledgement mechanism has to extend to blocked cells
too, or the fix will red `main` on a cell that was deliberately held.

## Wrap-Up Check

- Guards: `scripts/ci/run_guards.py` green after each commit.
- `tests/test_m20_tp_geometry_guard.py`: 8 passed.
- Stale DECISIONS 3 → 1; done-condition 25 → 23; headline unchanged at
  373/376 = 99.2% — correct, since `blocked` already counts as closed there.
- Telegram pings sent 02:55Z and 03:25Z, both confirmed by reading the
  workflow's reply comment rather than the issue's closed state.
- Nothing shipped. No live lever flipped. Tier-3 items queued for the operator.
