# S-M20-EXIT-COVERAGE-MATRIX-2026-08-12

## Date Range

- **Start:** 2026-08-12 ~21:50 UTC
- **End:** 2026-08-13 (in flight at the time of writing — see § Gaps not yet verified)

## Objective

**Primary:** advance M20's done-condition — the per-leg exit-coverage matrix
(`docs/research/exit-refinement-coverage.json`) — by working the largest open
block, `exit_head_ml`, through the `exit-refinement` skill's pipeline.

**Secondary:**

- Re-sweep the pullback-family stale/giveback cells under live-parity geometry.
- Merge PR #8814 on green (housekeeping carried in from the prior session).
- Keep Tier-3 decisions **queued for the operator**, not enacted (operator asleep;
  explicit standing instruction for this session).

## Tier

**Tier 1.** Research tooling, tests, a research data file, and one CI workflow.
No `src/`, no `config/`, no registry, no order path, no live env var. The two
findings that *would* be Tier-3 (a live lever change on `trend_donchian_avax_4h`
and on `gld_pullback_1h`) are **proposed and queued**, not applied — § Risks.

## Starting Context

- **Active roadmap item:** M20 exit refinement. Done-condition per the
  `exit-refinement` skill: *no `pending`/`blocked` rows on live legs*.
- **Prior sprint:** PR #8712 was the last write to the matrix.
- **The continuation prompt stated coverage as `304/376 = 80.9%` across 47 live
  legs**, with `exit_head_ml` the largest open block (31 of 57 pending cells).
- **Known risks carried in:** an un-run cell is `pending`, never a negative;
  `beats()` has no minimum-n; Path B's two thresholds are deliberately UNSET;
  `m20-sweep-corpus.jsonl` records no regime state.
- **Concurrent session** working the tick-chain / PR #8815. Scope agreed with the
  operator: leave those files to them, review their work, report gaps via the
  board — never edit their files. Honoured; one finding reported (§ Contradictions).

## Repo State Checked

- Branch `claude/m20-exit-coverage-matrix-8d3he7`, 12 commits ahead of `main`
  (`2e7250f` … `d25acbc`), PR **#8825** open.
- `main` verified clean **before** attributing any guard failure to my diff —
  this mattered twice (§ Validation).
- Trainer VM at `f2ca1fb7` (2026-08-12T23:19Z), `ict-trainer-git-sync.timer` armed.
- Canonical docs read: `CLAUDE.md` (§ Diagnostic provenance, § Collapsed states),
  `docs/CLAUDE-RULES-CANONICAL.md`, the `exit-refinement` skill, `ROADMAP.md` M20.

## Files and Systems Inspected

**Code**

- `scripts/research/m20_coverage_rollup.py` — **new**, 472 lines.
- `scripts/research/m20_fleet_exit_sweep.py` — read for `MIN_OOS_TRADES`, `classify`.
- `scripts/ml/train_exit_head.py` — `eval_split`, the fold loop.
- `scripts/ml/build_exit_head_dataset.py` — `load_harness_trades`, `load_live_trades`.
- `scripts/backtest_ict_scalp.py` — the emit dict (lines ~560-590).
- `scripts/research/m20_exit_head_round.py`, `scripts/ci/run_guards.py`.

**Config** — `config/strategies.yaml` (read-only: resolving the 47 live leg names).

**Data** — `docs/research/exit-refinement-coverage.json`,
`docs/research/m20-sweep-corpus.jsonl` (680 rows).

**Workflows** — `.github/workflows/m20-exit-lever-sweep.yml` (edited),
`.github/workflows/claude-pr-automerge.yml` (read for its trigger contract).

**Services** — trainer `ict-trainer-git-sync.timer`; live journal copies at
`/home/ubuntu/ict-trading-bot/{,data/}trade_journal.db`.

**Relays** — trainer-vm-diag issues #8822, #8824, #8827, #8832, #8833, #8837,
#8840, #8841, #8842, #8843, #8844, #8846, #8847, #8848.

## Work Completed

### 1. The M20 headline was never computed anywhere

Three sessions quoted three different figures for a file that had not changed:
**319** (PR #8712), **304** (the continuation prompt), **311** (a fresh
hand-count the same day). The 304 was **self-inconsistent with its own next
sentence** — the same prompt said "57 pending cells", and 376 − 57 = 319.

The divergence is not arithmetic: "closed" has three defensible cuts and no
session said which it used. `m20_coverage_rollup.py` now computes and **names**
all three, and reproduces all three historical figures.

**The headline and the done-condition are different questions.** The headline
counts `blocked` as closed; the skill's done-condition does not. So M20 needed
**61** cells resolved, not the 57 the prompt implied — a session reading only
the pending count under-scopes by four and never revisits the blocked ones.

### 2. `train_exit_head.py` graded per family; the matrix's unit is the leg

`eval_split` pools every symbol in the E0 dir — right to **train** on, wrong to
record a **verdict** from. Writing one pooled verdict into each of a family's
leg rows is `BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS` reappearing a
layer up. Each fold's test set is now cut by leg, each leg's denominator stated.
On the test fixture, one pooled family of 146 OOS trades resolves to **three
different verdicts**.

`MIN_OOS_TRADES` is **imported** from `m20_fleet_exit_sweep`, not mirrored, so
one matrix is never governed by two floors. An unimportable floor yields a third
state (`ungraded_no_floor`) and withholds verdicts rather than inventing a local
default; `insufficient_base` stays distinct from `honest_negative`.

### 3. A `status: null` had sat in the matrix since 2026-08-09

Not a legend value, so nothing could grade the cell. Set to `pending` per the
exploder's own documented rule, not a new inference.

### 4. The `ict_scalp` exit-head round had never been runnable

`backtest_ict_scalp.py` emitted no top-level `exit_time`, and
`load_harness_trades` drops any row without it — so 1170 emitted trades became
**0** E0 rows under the message `no trades loaded`. Also never emitted `symbol`,
and hardcoded `strategy: "ict_scalp_5m"` on every leg. Fixed; the builder now
names the **missing field** and states the population it read.

### 5. Seven of 47 "live" matrix legs do not exist in `config/strategies.yaml`

Surfaced by a sweep dispatch that failed on unknown names. Re-keyed
(`spy_trend_1d` → `spy_trend_long_1d`, and six siblings); `validate()` now fails
CI if any live leg cannot be resolved against config.

### 6. Evidence-vintage caveat, scoped rather than blanket

239 of 254 closed cells on the 38 legs whose harness modelled **no take-profit**
predate the 2026-08-10 TP-parity cutover (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`); **0 of 396** matrix refs mentioned it. Rather than
flagging the whole fleet, each live unit was read against its harness — `scalp`
and `fvg` place a real target their harness models and are **clean**. The
roll-up now prints the caveat with its own denominator.

### 7. 13 cells closed by a live-parity re-sweep

Coverage moved **319 → 334 / 376 (88.8%)**.

### 8. The sweep corpus could never reach `main` — and had silently discarded 4 runs

The `corpus` job pushes directly to the dispatch ref. Dispatched on `main`, that
is declined by branch protection (`GH006 … 3 of 3 required status checks are
expected`) — **structural, not a race**: a bare commit cannot satisfy a required
check. Four wave-2 runs (31647462929, 31648068353, 31648088122, 31648666524) went
**11 of 12 jobs green**, swept all ten legs, uploaded every artifact — and threw
every row away at that one step. Worse, the four-attempt backoff loop reported it
as a flaky push, which is how the real cause stayed hidden across four runs.

Fixed three ways: a default-branch dispatch is **retargeted** onto a corpus
branch that opts into `claude-pr-automerge.yml` (so the corpus reaches `main`
through CI rather than by weakening the protection that refused it); a
protected-branch decline now **fails fast and names the fix** instead of
retrying; and a rebase conflict on the append-only corpus **re-derives the
union** from the other side's copy instead of dropping this run's rows.

### 9. Documented the trainer's ≤15-minute worktree lifetime

`ict-trainer-git-sync` hard-resets to `origin/main` every ~15 min. A round
launched against `git checkout`-ed files loses them mid-run: `ict_scalp_eth_15m`
and `ict_scalp_sol_15m` completed with the fixed script; `ict_scalp_xrp_15m`,
invoked from the same loop seconds later, died on `unrecognized arguments:
--strategy-name`. Recorded as `docs/claude/trainer-vm-mode.md` § 9.a.1 with the
reflog evidence, so the next session merges rather than checks out.

## Validation Performed

**Tests** — 14 new tests in `tests/test_exit_head_per_leg.py`. **Each was
verified against a planted defect in the real source**: the named test failed
when the floor check, the maxDD clause, the hard-rule comparison, the no-floor
state, or the usable-fold count was removed, and passed again on restore. A test
that survives a broken implementation is worse than none.

**The roll-up was verified able to FAIL**, not just to pass: it reported the
`status: null` before the fix and `OK` after.

**Reproduction** — all three historical figures (319 / 315 / 311) reproduced
from the one script, so the claim about the divergence is measured, not asserted.

**The corpus-push fix was simulated with `git` stubbed**, four cases: dispatch on
`main` retargets and pushes; a still-declined push fails in **0 s** rather than
burning the 30 s backoff; a `claude/**` dispatch does **not** retarget and does
**not** create the request file; and a rebase conflict re-derives the union
**with the automerge opt-in surviving the hard reset** — that last case caught a
real bug I had just written (`reset --hard` deletes a staged-but-new file, so the
branch would have been pushed with no PR opened, which looks like success).

**Guards** — diff-scoped `dry-run`, `env-gate`, `silent-empty`,
`new-table-wiring`, `strategy-risk`, `writer-conformance`,
`diagnostic-provenance` clean. New `exit-coverage-matrix-guard` registered.

**Self-corrections during the session, each caught by re-probing rather than by
review:**

- A sqlite probe printed `total trades: 0` and then died on `no such function:
  chr`. I refused to treat that `0` as established and re-probed; the re-probe
  found the real cause.
- I recorded `live_trades: 0` as "the arm was not evaluated". It was **my wrong
  `--db`** — I took the first `find` hit (an 8.2 MB stub, mtime Aug 2) over the
  declared data dir (767 MB, synced Aug 12, 4585 trades). That is sub-class **B
  implicit input selection**, the class this repo guards for, committed by me.
  Corrected in `d25acbc`; `load_live_trades` now reports its population.
- A probe reported every corpus row's strategy as `?`. The rows key on `leg`,
  not `strategy` — my probe was wrong, the corpus was fine. Verified by dumping
  the actual keys before concluding.

### Gaps not yet verified

- **The `ict_scalp` E1 round has NOT been re-run against the correct journal.**
  The two 15m verdicts on record were produced with `live_trades: 0`, so the
  E1→E2 gate's *"live validation set agrees in sign"* arm — which the program doc
  calls a hard stop — is **unevaluated**, not passed. The matrix refs say so.
- The 5m round (4 legs) has not produced usable output at all; it must re-run
  after PR #8825 reaches the trainer via git-sync.
- `ict_scalp_xrp_15m` never completed.
- **The 10 wave-2 `vol_trail` verdicts exist only as workflow artifacts and job
  logs.** They swept successfully but never reached the corpus, so those 10 cells
  remain `pending` in the matrix — correctly, since an un-run-*into-the-corpus*
  cell is `pending`, never an inherited verdict.
- The corpus-push fix is verified by simulation, **not yet by a live run**.
- PR #8825 was still awaiting `pytest-run` at the time of writing.

## Documentation Updated

- `docs/claude/trainer-vm-mode.md` — new § 9.a.1 (worktree lifetime).
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — routing row for the roll-up.
- `docs/research/exit-refinement-coverage.json` — 13 sweep cells, 7 leg re-keys,
  1 null fixed, 2 `exit_head_ml` cells + a `CORRECTED 2026-08-12` note.
- `docs/claude/health-review-backlog.json` — `BL-20260812-SWEEP-CORPUS-CANNOT-PUSH-TO-MAIN`
  filed with a fix suggestion.
- **This log.**
- ROADMAP.md — **not yet updated** (§ Deferred).

## Contradictions or Drift Found

1. **Three live figures for one unchanged file** (§ Work 1) — resolved by
   single-homing the computation.
2. **Seven matrix legs naming strategies config does not declare** (§ Work 5) —
   the matrix and config had drifted; config wins.
3. **The whole closed-cell population is conditioned on a geometry production
   does not run** for 38 of 47 legs, and no ref said so (§ Work 6).
4. **Not mine:** PR #8815 (concurrent session) wraps a `@contextmanager` in a
   non-guarding `try/except` — the guard cannot fire because the exception is
   raised at `__enter__`, not at construction. Verified by repro and reported on
   the coordination board. Their file, not edited.

## Risks and Follow-Ups

**Technical**

- The E1→E2 gate is still a `gate_note` string a human reads. The per-leg block
  now states the arithmetic; nothing mechanically enforces it.
- The corpus fix ships unexercised against a real protected-branch dispatch.

**Tier-3 product decisions — QUEUED FOR THE OPERATOR, deliberately not enacted**

1. **`trend_donchian_avax_4h` — `trail_decay`.** Path A **PASS** on 3 cells:
   beats net_R **and** maxDD in **both** IS and OOS, improves drawdown in both,
   OOS n=34 (clears the 25 floor).
2. **`gld_pullback_1h` — `trail_decay`.** Three **Path-B** candidates, `rate ok`=Y
   with positive headroom in both windows. Path B's thresholds are **UNSET by
   design**, so this is a candidate to *judge*, not a pass to apply.

Neither was applied. Both change live exit behaviour on real-money legs.

**Blockers** — the 5m round is blocked on PR #8825 reaching the trainer.

## Deferred Items

- ROADMAP.md M20 status row (pending the wave-2 cells landing, so the figure
  written there is the settled one).
- `doc-freshness` skill pass at session close.
- The `vol_trail` wave-2 re-dispatch (blocked on the corpus fix merging).
- Re-run of `ict_scalp_xrp_15m` and the full 5m round.

## Next Recommended Sprint

**Re-dispatch the wave-2 `vol_trail` sweep and the corrected `ict_scalp` E1/5m
rounds, then close the remaining `exit_head_ml` block.**

*Why:* the machinery that was blocking both is now fixed but not yet exercised —
the sweep's evidence path (corpus push) and the round's data path (emit schema +
correct `--db`). Both fixes are cheap to validate and each unblocks a large
tranche: 10 cells for `vol_trail`, up to 29 for `exit_head_ml`.

*Required verification before trusting the output:* confirm the corpus branch
actually opened a PR (not merely pushed); confirm the E1 report's `live_trades`
is **non-zero** before reading any live-agreement verdict; and re-run
`m20_coverage_rollup.py --validate` before quoting a new headline.

## Wrap-Up Check

- [x] **Code inspected directly** — every file listed in § Files was read, not
      inferred; the emit dict and the corpus keys were read rather than grepped
      for, twice catching a false read.
- [x] **Docs reviewed and updated** — trainer-vm-mode, research index, matrix,
      backlog, this log.
- [x] **TRADE-PIPELINE** — not applicable; no pipeline stage changed.
- [x] **Roadmap checked** — M20 read; the status row is deliberately deferred
      until the in-flight cells settle (§ Deferred).
- [x] **Contradictions recorded** — four, including one not mine (§ Contradictions).
- [x] **Unknowns stated** — § Gaps not yet verified lists six, including that the
      headline figure this session produced rests on cells whose geometry vintage
      the roll-up now prints beside it.
- [x] **Tier-3 items proposed, not enacted** — two, § Risks.
