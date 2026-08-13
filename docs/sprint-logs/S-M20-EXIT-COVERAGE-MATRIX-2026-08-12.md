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

### 7a. The `vol_trail` wave-2 block: 2 closed, 1 graded at a stated non-standard split, 7 blocked

The recovered wave-2 sweep (§ 8) graded all 10 legs. The result splits
**perfectly by timeframe**, which is what makes it structural rather than
per-leg luck:

| timeframe | legs | OOS n | outcome |
|---|---|---|---|
| 1h | slv, uso | 40, 27 | clear the 25 floor → **honest_negative** (both) |
| 1d | the other 8 | 3–8 | miss the floor → `insufficient_base` |

`slv_trend_1h` is a clean graded refutation — all three cells are the IS-only
overfit shape (net_R up in-sample, down out). `uso_trend_1h` has one cell at
`path_b_wf_pass`, but that verdict is **not** `rate ok`: its drawdown exchange
rate holds on OOS only (headroom −1.494 IS / +5.134 OOS), so nothing is
shippable; the Path-B row is named in the ref so a future threshold session
finds it rather than re-deriving it.

**A second sweep tested whether the split, not the data, was binding.** At
2023-01-01 only `tqqq` crossed (OOS 8 → 27, still `is_oos_fail`); the other
seven went to 12–21 while IS shrank. `tqqq` is recorded as `honest_negative`
**with the non-standard split stated in the ref** — safe only because the
answer is a refutation at both windows, i.e. moving the window made the cell
gradeable without manufacturing a pass. The seven are `blocked`, not
`honest_negative`: the cell terminated at "we had too few trades to look",
which is the opposite claim from "we looked and it failed".

### 7b. Why those seven can't be graded — a gate-ordering finding

`m20_fleet_exit_sweep.py:1442-1451`:

```
if _thin:            -> verdict=insufficient_base   (walk-forward SKIPPED)
elif candidate:      -> walkforward(...)
elif is_path_b_...:  -> walkforward(...)
```

The skip is deliberate; its comment reasons that the walk-forward "would be
measuring the same too-thin book". **It would not.** `_thin` is computed from
the post-split OOS window; the walk-forward's folds span the **full history** —
the one cell that reached it here (`uso_trend_1h/vt_cold10_t2`) ran six folds,
2021 through 2026. For a 1h leg the two denominators roughly agree; for a 1d
leg they diverge by an order of magnitude. So a 60–79-trade leg is refused a
six-fold test because its 3–8-trade window is thin.

Filed as `BL-20260813-THIN-OOS-BLOCKS-THE-WALKFORWARD-IT-COULD-PASS`. **Not
fixed here** — it changes the evidentiary standard by which a live exit lever
is judged, which is the operator's call, not a research-tooling edit.

Coverage after 7a: **344 / 376 = 91.5%**; done-condition 45 cells (32 pending
+ 13 blocked — blocked rose 6 → 13 on this finding, deliberately visible).

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
- A `grep` for `exit_time` in the scalp harness returned five hits, every one an
  unrelated pre-existing use, while the emit dict still lacked the field. Reading
  the **dict** rather than grepping for the name is what caught it; the later
  relaunch was **gated** on that check so an hour of trainer time could not be
  spent rediscovering 0 rows.
- I claimed the daily legs were "structurally unreachable" and should be blocked.
  The arithmetic refuted it (7 of 8 carry 57–79 lifetime trades). I then over-
  corrected to "the split is the binding constraint" — a second sweep refuted
  *that* too: only 1 of 8 crossed at an earlier split. Both readings were stated
  and both were tested; the recorded disposition is the measured one.
- **I preserved the wrong artifact.** I kept the eth/sol 15m E1 reports rather
  than recomputing, reasoning "only their live arm was missing". That is self-
  defeating — the missing live arm is exactly what the corrected `--db` fixes, so
  preserving them preserved the defect. Confirmed still `live: 0` in #8852; a
  re-run is queued behind the running round (#8853).
- I pushed one commit while a guard was failing (`artifact-validity-guard`,
  missing `resolution_criteria`), because a shell `&&` chain masked the exit
  code. Caught on the next CI wake and fixed in the following commit — but the
  push should not have happened.
- `run_guards --base main` reported PASS while `check_backlog_criteria --base
  main` exited 1 — the same flag, the same word, opposite answers. Rather than
  take the friendly reading, I traced it: `run_guards` **prepends `origin/`** to
  its base, so `--base main` there resolves to `origin/main`; the standalone
  script does not, so it used my **local** `main`, which had drifted to a commit
  sharing **no merge base** with HEAD, degenerating the diff to "everything" and
  tripping on a pre-existing row outside my change. Fixed by repointing the local
  ref (`git branch -f main origin/main`), after which both agree at exit 0. Worth
  recording because the failure mode is a guard reporting on a population nobody
  asked about while naming the same flag as the one that scoped it correctly.

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
