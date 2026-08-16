# Sprint Log: S-M20-DISPERSION-ISOLATION-AND-QUEUE-2026-08-15

> **WRITTEN MID-SESSION, DELIBERATELY** — recorded before the screen finished
> because the session had already been through one context compaction and the
> durable record must not depend on reaching the end.
>
> ✅ **The screen has since COMPLETED** (4 arms × ~74 min, 16:13:48Z →
> 21:10:52Z; ETA was ~21:15Z, so it landed within 4 minutes of the estimate).
> § "Screen result" is **closed** — see item 9 below and
> `docs/research/m20-fold-dispersion-2026-08-15.md` for the result: **1 of 3
> legs moved**, the flip was **pre-registered** 44 minutes ahead, and AUC
> dispersion proved **anti-correlated** with verdict stability.

## Date Range
- Start: 2026-08-15 (overnight, operator asleep; hourly-ping mandate active)
- End: open (this log covers 14:40Z–17:00Z; earlier work is in
  `S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13.md` and the fold-dispersion
  research doc)

## Objective
- **Primary goal:** get the M20 5m fold-dispersion screen to produce four
  comparable arms, after two 73-minute arms were voided mid-run by the trainer's
  code being reset under them.
- **Secondary goals:** make the evidence rows self-describing (the study's
  independent variable was not on them); close the trainer heavy-job-queue
  bypass; correct, everywhere it was published, a root cause that turned out to
  be wrong.

## Tier
- **Tier 1** throughout.
- Justification: research tooling, tests, CI guards, docs and backlog only. No
  file under `src/`, `config/`, or any live-VM unit was touched; no live lever
  was flipped. The 7 Tier-3 items and the PR #9257 merge call remain **queued
  for the operator**, untouched, per the overnight mandate.

## Starting Context
- Active roadmap item: **M20** exit-refinement coverage. Matrix at
  **373/376 = 99.2%**, re-verified this session by
  `scripts/research/m20_coverage_rollup.py`, not by hand.
- Prior sprint: `S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13.md`.
- Known risks at start: `--fold-offset` exists **only** on the unmerged branch
  (`BL-20260815-FOLD-DISPERSION-EVIDENCE-RUNS-ON-AN-UNMERGED-BRANCH`), so every
  arm races the trainer's code-sync.

## Repo State Checked
- Branch: `claude/m20-exit-coverage-matrix-8d3he7` (PR #9257, open, draft).
- Commits added this sprint: `8a15fa7c`, `9ad32e94`, `38004dbc`.
- Trainer state read live via the `trainer-vm-diag` relay (#9492–#9496).
- Canonical docs reviewed: `CLAUDE.md` § Diagnostic provenance / Collapsed
  states; `docs/claude/trainer-resource-protocol.md` § Rule 1.

## Files and Systems Inspected
- Code: `scripts/research/m20_exit_head_round.py`, `scripts/ml/train_exit_head.py`,
  `scripts/ml/train_entry_head.py`, `scripts/ml/build_exit_head_dataset.py`,
  `scripts/ml/spike_a_pooled_labels.py`, `scripts/ml/exit_head_replay.py`,
  `scripts/ml/export_exit_head.py`, `src/utils/trainer_heavy_lock.py`,
  `scripts/ops/trainer_git_sync.sh`, `scripts/ops/run_training_cycle.sh`,
  `scripts/research/m20_fleet_exit_sweep.py::resolve_data`.
- Services/timers: `ict-trainer-git-sync.timer` (`OnUnitActiveSec=15min`),
  `ict-trainer.timer`.
- Workflows: `trainer-vm-diag.yml` (relay), `guards.yml` / `scripts/ci/run_guards.py`,
  `pytest-run.yml` (relevance filter).
- Docs: `docs/research/m20-fold-dispersion-2026-08-15.md`,
  `docs/research/RESEARCH-CAPABILITY-INDEX.md`,
  `docs/claude/trainer-resource-protocol.md`.

## Work Completed

### 1. 🔴 Corrected a root cause I had published and never measured

I asserted — in `m20_exit_head_round.py`, a test docstring, the research doc,
two backlog rows, the coordination board, and two Telegram pings — that holding
the trainer heavy-job lock **prevents** the ~15-min `Reset to origin/main` that
voided two arms. The next arm measured it **false**.

- **Evidence:** arm `off0` ran its full 74 min under a held lock
  (`{"status": "heavy_lock_acquired"}` in its own log) and its AFTER hashes were
  `6f6458ac22d8` / `08541341e093` — byte-identical to `origin/main`'s copies.
  The reset landed **with the lock held**. (`off0` survived only because
  `if a.fold_offset:` treats `0` as falsy, so the control arm never forwards the
  branch-only flag.)
- **Cause:** there are **two** reset paths and I enumerated one.
  `run_training_cycle.sh:138` is inside the lock (~daily);
  `scripts/ops/trainer_git_sync.sh` via `ict-trainer-git-sync.timer`
  (`OnUnitActiveSec=15min`) is **deliberately lock-free** — its header says so,
  and that design exists because gating sync behind the lock once left the
  trainer **495 commits behind** (`BL-20260718-TRAINER-GITSYNC-STALE`).
- **The tell I already held:** I wrote "~15-min reset" repeatedly while
  attributing it to a **daily** job and never reconciled the two numbers.
- **The reasoning error:** `grep -c heavy_lock` over three scripts is a true
  fact about *callers*; I turned it into a claim about the *reset* without
  enumerating the reset's producers. "A search returning nothing is not proof of
  absence" has a mirror image — **a search returning one thing is not proof
  there is only one.**

### 2. Git-worktree isolation (the actual fix), proven not assumed

The screen now runs its **code** from a linked worktree at `/tmp/m20_screen_wt`
pinned to a detached HEAD, which `git checkout -B main` in the main worktree
cannot reach. Deliberately **not** masking the timer — that would re-introduce
the worse failure it exists to prevent.

- **Isolation proven by forcing a sync** while the worktree stood: main moved to
  `6f6458ac22d8`, the worktree held `43eec43d0c83` / `6412613984a3`.
- **Push-safety measured, not asserted:** 15 pushes to the branch during a
  running arm left both executed files byte-identical.
- **Still true mid-run** (relay #9496, 16:35:41Z): main at `e998198b`, worktree
  at `02d5139d`, both file hashes unchanged.

Two traps found on the way, **both of which first read as a completed run**:
- a worktree checks out **tracked files only**, so the untracked candle CSVs
  were absent and all three legs skipped `data_missing` in 4 seconds. The driver
  now passes the main clone's data dir explicitly and **refuses to launch** below
  3 CSVs;
- an orphaned child (`backtest_ict_scalp.py`, pid 1550816) survived a stop
  holding the **inherited** flock fd, so the queue read HELD with nothing running.

### 3. `fold_offset` on the evidence rows (`BL-…-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`, resolved)

Rows carried `total_sort` and `block_unit` — the axes the study holds **fixed** —
and not `fold_offset`, the axis it **varies**. Now stamped from the same
`a.fold_offset` `_round_meta` reads, plus `fold_offset_basis`
(`argv` / `predates_flag_<commit>` / `unavailable`) so a backfilled 0 and a
measured 0 are not the same claim.

All 33 committed corpus rows backfilled by
`scripts/research/m20_backfill_corpus_fold_offset.py` (committed, idempotent —
a throwaway producer makes its artifact unreproducible). **Offset 0 is
established, not defaulted:** `--fold-offset` landed in `43820a32` @
2026-08-14T23:49:18Z and every row predates it, so each was produced by a driver
whose argparse would have *rejected* the flag. 28 rows dated from the round-dir
stamp; the 5 naming only a relay were dated by **reading the GitHub API**
(#9156 @ 05:20:00Z, #9206 @ 08:44:23Z), not inferred from issue numbering.

### 4. Trainer heavy-job queue (`BL-…-RESEARCH-TRAINERS-BYPASS-THE-HEAVY-JOB-QUEUE`, resolved)

Locked at the **entrypoint**, not the callers: `train_exit_head.py` alone has
five in-repo callers plus every ad-hoc relay invocation, and the direct
`python scripts/ml/train_exit_head.py …` path is how most relays run it. One
module owns it (`scripts/ml/_heavy_queue.py`), resolving the repo root itself
rather than trusting CWD — the screen already runs from a worktree elsewhere.

New CI guard `trainer-heavy-lock-guard`
(`scripts/ci/check_trainer_heavy_lock.py`, registered in `run_guards.py`)
resolves an **AST call node** — a comment or string naming the helper fails,
per the `new-table-wiring-guard` lesson. **Its drift pass immediately found a
script my hand enumeration had missed** (`spike_a_pooled_labels.py`, three
`lgb.train` arms), which is the whole argument for the completeness pass
existing.

`docs/claude/trainer-resource-protocol.md` § Rule 1 now carries the three-path
table, and its overclaiming sentence — *"the backstop so nothing can slip
past"* — is corrected in place. That sentence is why the gap went unnoticed.

### 5. 🔴 The worktree fix silently opted the screen OUT of the queue

Found by reading `_lock_file()` while trying to close § "Gaps"' own
"lock unexercised on the trainer" item — i.e. by chasing my own stated gap.

`trainer_heavy_lock._lock_file()` resolves `<repo_root>/runtime_logs/trainer/
.heavy.lock` from `parents[2]` of the **running** module. Correct for the
canonical clone; a trap for any other checkout. So § 2's worktree — the right
fix for the code-reset problem — moved the lock file too, and the screen took a
**private mutex**.

**Measured on the box (#9497), screen mid-arm:**

| | |
|---|---|
| `/tmp/m20_screen_wt/runtime_logs/trainer/.heavy.lock` | **HELD** |
| `/home/ubuntu/ict-trading-bot/runtime_logs/trainer/.heavy.lock` | **FREE** |
| probe launched from the canonical clone | **acquired immediately, rc=0** |

A training cycle or drift-retrain was free to start into the 6 GB box beside a
running screen — the exact contention the queue exists to prevent. **Two
individually-correct fixes; their interaction was the defect** — the seam shape
`CLAUDE.md` § Number provenance already names.

**Why it survived review:** every log line said the right thing. The job prints
`{"status": "heavy_lock_acquired"}`, which is **true** — it acquired *a* lock —
and nothing in the output distinguishes "joined the shared queue" from "created
a private one". *The lock message is not evidence the queue was joined.*

Fixed the additive half only: `_heavy_queue.canonical_lock_file()` points a
non-canonical checkout at the canonical clone's lock, and the round driver —
which imported `acquire_heavy_lock` **directly**, bypassing the helper — is
routed through it. `tests/test_heavy_queue_shared_across_checkouts.py` asserts
it with **real processes and a real flock**, because a path-string comparison
would have passed against the broken version too: it resolved a perfectly valid
path, just not a shared one.

**Deliberately not done:** the default lock path in `trainer_heavy_lock.py` and
the shell wrapper is unchanged. They agree with each other today, and moving the
mutex has a transition window in which an in-flight job on the old path does not
serialize against a new job on the new one. That is a live-behaviour change to
the trainer's scheduled-job serialization — **queued for the operator**.
`BL-20260815-WORKTREE-RUN-TAKES-A-PRIVATE-HEAVY-LOCK`.

**The running screen is not retrofitted**, and the reasoning is recorded rather
than left implicit: contention costs time and carries an OOM risk but **not
verdict correctness** (an E1 verdict does not depend on wall-clock), the box had
5,251 MB available with no other heavy job, and a restart would burn ~35 min of
arm `off0`.

Two smaller things, both mine: my relay probes in #9492/#9493/#9496 tested
`runtime_logs/.trainer_heavy.lock`, a path **neither implementation uses**, so
those "flock FREE" readings said nothing — and `flock -n` *created* that file,
so I removed it (#9498, guarded on empty-and-unheld). And the canonical
`heavy_lock_holder.json` still names a dead `drift_retrain` pid: documented
stale-holder behaviour, so read it through `read_holder()` and never `cat`.

### 6. The `exit_head_ml` column held TWO INCOMPATIBLE ARITHMETICS

Found while checking whether the mes/mgc/mhg cells were reachable — a gap an
earlier turn of this session had recorded as open and which relay #9195 had in
fact already closed. **Verifying my own stated gap is what surfaced this.**

The seven equity 1d cells reason correctly from lifetime trades (*"48 lifetime
harness trades; a 50-trade test block needs >=100 for one fold"*). The three
futures cells reason from the **LEVER sweep's** `MIN_OOS_TRADES=25` and its date
split, and then name an escape route — *"an EARLIER split could arithmetically
reach 25 OOS"* — that this lever has **no date split to take**.

Computed against `train_exit_head.fold_blocks`'s actual loop rather than from
prose: folds are fixed blocks of `b=50` over the sorted trade list starting one
block in, so `u = len(range(b, N-b+1, b))`. **u ≥ 2 needs N ≥ 150; even ONE fold
needs N ≥ 100.** MES 33, MGC 74, MHG 80 → **u = 0 at any split.**

The statuses were right; the stated way out was not. It matters because that same
ref exists partly to stop a session burning time on a dead route — it records one
doing exactly that a day earlier — and then named a second one.

### 7. Derived the block instead of asserting it (the class fix)

**Not what my own backlog row first proposed.** That said "check the
blocked-reason vocabulary"; it would not have caught this, because both
arithmetics wrote the *same* status string and the divergence was in ref prose.
A keyword guard over prose would also be brittle and cheap to lie to — which
this repo already rules out.

The real defect: the trade counts lived **only in prose**, so nothing could
recompute the bound.

- `lifetime_trades` promoted to a **field** on all 11 blocked cells.
- `m20_coverage_rollup.py` **derives** `u` from it, mirroring the trainer's own
  `range(...)` rather than a closed form, so a change to the fold construction
  shows up as a diff instead of a stale formula. A cell missing the field is
  reported **ungraded**, never skipped.
- The **done-condition now splits**: 25 remaining = **14 actionable + 11
  arithmetic**. Pooling them invited "keep sweeping and it converges"; those 11
  close only if the leg *trades* more (a strategy question) or the E1 protocol
  changes.

Producer and consumer landed together — a field written and never read is worse
than a missing one. Statuses untouched (asserted by diffing every cell
old-vs-new), headline unchanged, all three matrix guards green.

### 8. Audited the 3 remaining PENDING cells — genuine work, and one has no driver

With the arithmetic split landed, only **3 cells on live legs are `pending`**, all
on the two prop donchian legs that graduated shadow→live on 2026-08-13. I audited
whether they are real work or mislabels. **Three hypotheses were tested and
REFUTED** — recorded so nobody re-runs them:

| # | hypothesis | how it died |
|---|---|---|
| 1 | duplicate matrix rows hide a status | zero duplicate `strategy` keys |
| 2 | the row's `execution` is stale vs `strategies.yaml` | zero disagreements — my first pass compared against `_declared_legs()` (**55 declared**) instead of the **47 effective-live** legs, which manufactured a 4-row "defect" |
| 3 | `exit_ladder` is `n/a` for legs that declare no ladder | refuted by convention: **45 of 52** rows grade it `honest_negative`, every donchian sibling included |

Hypothesis 2 is the one worth remembering: the module holds **two** definitions of
"live" — `_declared_legs()` (all declared) and `rollup()`'s `execution == "live"`
(the denominator). Reading the wrong one turns a correct file into a confident
finding. I nearly published it.

**What the audit did find**, measured rather than inferred:

    bank-flag hits per harness
      backtest_ict_scalp.py  32     backtest_squeeze.py    0
      backtest_trend.py      16     backtest_fvg_range.py  0
      backtest_pullback.py   17

`exit_ladder` has **zero non-comment occurrences** in `m20_fleet_exit_sweep.py`
(its `--levers` is `choices=sorted(LEVER_DECLARED_KEYS)` — four keys, not
including it), and the only driver constructing ladder cells is
`m27/ict_scalp_exit_sweep.py::ladder_cells()`, whose `_HARNESS` is **hardcoded**
to the scalp harness. So donchian/pullback have the **capability and no driver**:
their 45 verdicts rest on the 2026-07-12 memo-era pass, and nothing runnable
today reproduces the column for a leg added since.

The **coherence check** that this read is right: the two cells already carrying
`blocked:no_harness_levers` are `squeeze_breakout_4h` and `fvg_range_15m` —
exactly the two harnesses measuring **0** bank flags. The vocabulary is applied
correctly; the gap is one level up.

**Deliberately did NOT re-label the cells.** `blocked:no_harness_levers` would be
factually wrong (the harness *has* the levers), and minting a status to close them
is the cosmetic-cell anti-pattern (`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`).
They stay `pending` because they are genuinely open. Filed as
`BL-20260815-EXIT-LADDER-HAS-NO-DRIVER-FOR-THE-DONCHIAN-PULLBACK-FAMILIES` with
the port + a reproduction check as its resolution criteria — the fix routes the
column onto **free runners**, so it costs the trainer nothing.

### 9. Completed the 4-arm screen; dispatched the pullback re-sweep on evidence VINTAGE

The screen finished 21:10:52Z. Result + the pre-registered flip are written up in
`docs/research/m20-fold-dispersion-2026-08-15.md`; the short version is **1 of 3
legs moved**, the flip was predicted 44 min ahead in `fc46889c`, and AUC
dispersion turned out **anti-correlated** with verdict stability on this screen.

**Then a scoping correction worth recording.** The named next thread was "the
pullback-family stale/giveback re-sweep". Checked against the matrix, that
thread is nearly *closed* on the axis I first looked at — 18 of 19 pullback rows
are `honest_negative` on BOTH levers, and the only re-sweepable cell
(`mhg_pullback_1d/giveback_stop`) is `blocked:insufficient_base`, which
re-running cannot fix. Reporting "no actionable work" there would have been
true and useless.

The actionable population is on a different axis — **evidence vintage**:

| pullback stale/giveback cells | count |
|---|---|
| `tp_geometry` recorded | 1 (`no_take_profit`) |
| **unrecorded, newest ref PRE-2026-08-10 cutover** | **32** |
| unrecorded, newest ref post-cutover | 5 |

Those 32 are `honest_negative` verdicts measured on a book with **no TP
modelled**, which is not the book production runs. The census precedent says
this is not a formality: `tqqq_trend_long_1d` went 32 → 75 trades and median
capture +0.40 → +1.05 once real geometry was modelled — so a no-TP
`honest_negative` can be wrong in the *conservative* direction, leaving a real
improvement unshipped.

**Dispatched** `m20-exit-lever-sweep.yml` (run `31911850455`, `in_progress`,
22:20:02Z) over the **17 LIVE** pullback legs carrying pre-cutover cells,
`levers=stale_stop,giveback_stop`, `tp_cap_pct=0.099` (live parity),
`split_target_oos=35` — the workflow's own input doc notes the default targets
`MIN_OOS_TRADES` exactly, so a one-trade shortfall lands below the floor and
returns `insufficient_base`; 35 leaves margin. **Free runners**, so the idle
trainer stays free.

The 204 was verified against the run list rather than trusted — a dispatch
acknowledgement is not evidence a job started.

### 10. The re-sweep landed and produced counter-evidence against two live cells

Run `31911850455` completed at ~22:25Z: 17 legs, 68 new corpus rows, 4
superseded. `matrix-corpus-agreement` then **failed CI on my own head**, which
is the guard doing exactly its job — two cells now PASS where the matrix records
`honest_negative`:

| leg / lever | cell | verdict | wf | base n OOS |
|---|---|---|---|--:|
| `sol_pullback_2h` / giveback_stop | `gb1R_afterMFE1R` | **PASS** | 5/6 | 34 |
| `sol_pullback_2h` / giveback_stop | `gb1R_afterMFE2R` | **PASS** | 6/6 | 34 |
| `slv_pullback_1d` / stale_stop | `stale12_lt0R` | `path_b_wf_pass` | 5/6 | 33 |

**The statuses are NOT flipped** — a passing cell is not a passing lever
disposition and a live-leg status change is Tier-3, so both are **queued for the
operator**. The guard's prescribed remedy was taken instead: the evidence is
appended to each cell's `ref` so a reader meeting the status also meets the
measurement that disagrees with it (`f804b12b`).

Two caveats went into the refs because the headline numbers overstate the case,
and both were **measured from the corpus rows, not inferred**:

- **A win total is not a count of wins.** An all-zero walk-forward fold (the
  lever never fired) still counts `ok`. `slv`'s 5/6 is really **2 real wins
  against 1 real loss with three inert folds** (2021, 2024, 2026); `sol`'s 6/6
  is 5 real wins plus one no-op. On that basis `gb1R_afterMFE1R` — **zero**
  inert folds, 5 real wins — is the stronger cell despite the lower headline,
  and the ref says to read them in that order rather than by `wf_summary`.
- **`slv` is economically nothing.** Path-B, `is_oos_pass:false`,
  `rate_ok_OOS:false` (`maxdd_worse`), OOS net_R gain **+0.001**.

One correction to my own first pass, recorded because it nearly shipped: I
diffed the corpus on `(leg, lever, cell)` and got "147 verdict changes" over a
commit that added 68 rows. That key is **not unique** (218 duplicated keys,
multiplicity up to 5) — the dict silently collapsed rows and I was comparing
whichever survived. The arithmetic is what caught it, not re-reading the list.

### 11. Every leg's sweep comment claimed a split 16 of 17 legs had not used

Found while checking the above against the corpus rather than the PR comments.
The banner prints ``IS/OOS split `${SPLIT}` `` unqualified, but under the
default `--split-mode oos-trades` that value is only the **fallback** — the real
boundary is resolved per leg by `resolve_split()`, inside the sweep, which the
workflow cannot see. All 17 comments said `2025-07-01`; **sixteen legs had run
at a different derived boundary** (`sol_pullback_2h` 2025-08-23,
`slv_pullback_1d` 2022-11-29, `ief_pullback_1d` 2017-01-20, …). The one leg that
genuinely used it, `iaum_pullback_1d`, did so **because its derivation failed
and fell back** — the single true reading was true by failure.

This matters because comparing legs is what a fleet sweep is *for*, and the
banner promised a shared cut that did not exist. It is diagnostic-provenance
sub-class A on the **same banner** whose `geometry` line was added to kill the
identical defect five days earlier, and this file's docstring was corrected for
the same drift on 2026-08-13 while the emitted line was not — so the fix went
into the output, not into more prose about it (`198a822d`).

Both halves mirror how geometry was already solved: the SUMMARY carries a
per-leg `- split (leg): …` line emitted by the sweep (three states, never
collapsed — DERIVED / FELL BACK / unknown, and the requested date is **never**
substituted for an unresolved one), and the banner states the request + names
`split_mode`, pointing at the per-leg line. Extracted as a pure
`summary_split_line()` for the reason its sibling `insufficient_base_reason`
documents, and pinned by `tests/test_m20_summary_split_line.py` — **plant-proven**:
reverting to a date-only renderer fails 6 of its 9 tests, including both
controls. The first draft of one test was itself wrong (it banned the bare form
outright, which is *correct* under `split_mode=date`) and the suite caught it.

✅ **LIVE-VERIFIED at 22:48Z** on the next sweep's real output — and it landed on
the sharpest case available. `iaum_pullback_1d` is the one leg that genuinely
*did* run at 2025-07-01, so the old renderer would have printed that date and
looked right. The new output instead reads:

> `- split (iaum_pullback_1d): **2025-07-01** — FELL BACK to the --split date
> (leg_too_thin, lifetime=36 trades); the oos-trades derivation could not be
> satisfied, so this boundary was NOT chosen for this leg`

with the banner above it now saying `IS/OOS split DERIVED per leg … 2025-07-01
is only the FALLBACK date — legs do NOT share a boundary`. Same date as before
the fix, opposite meaning, and the difference is now on the page. That is
exactly the case `test_a_fallback_is_never_dressed_as_a_derivation` pins, so
the test and production agree on a real run rather than only on a fixture.

### 12. Declared the re-sweep's geometry, moving evidence vintage 62.8% → 52.4%

The re-sweep's whole point was the **vintage** axis, and finishing it meant the
matrix had to cite it: 34 live cells (17 pullback legs × `stale_stop`,
`giveback_stop`) still pointed at pre-cutover evidence while the measurement
that clears them sat committed in the corpus. Evidence no consumer reads is the
defect the corpus exists to end, one level up.

`tp_geometry` is a **declared measurement, not a marker**, so the stamp rests on
three checks made against the data — deliberately not against the run's PR
comment, which § 11 had just shown is not evidence:

1. `pullback` ∈ `m20_fleet_exit_sweep.LIVE_TP_CAPPED_FAMILIES`;
2. all 68 rows carry `tp_cap_pct == 0.099`, the `LIVE_TP_CAP` the agreement
   guard keys on; and
3. all 68 carry a **measured** live-TP-reach distribution (**0 missing**) —
   which the sweep emits *only* where the cap actually applies. That is the
   APPLIED-vs-REQUESTED distinction, i.e. exactly what the banner got wrong.

Statuses untouched. **32** cells record that the re-sweep *reproduces* the
negative under the geometry production actually places; the other **2** say the
opposite in as many words and point at their counter-evidence segment, so no
cell claims a reproduction it did not get.

**Vintage 181/288 (62.8%) → 151/288 (52.4%).** The drop reconciles exactly:
34 stamped − 4 already non-stale by date (the four 1h `giveback` cells dated
2026-08-12, after the 2026-08-10 cutover) = **30** = the observed 181−151. I
checked that rather than assuming it, because "34 stamped, 30 moved" is the
shape a silent partial failure also has.

**Headline coverage is unchanged at 373/376.** This moves what the closed cells
are *conditioned on*, not how many are closed — and those are different claims.

### 13. Sized and dispatched the next vintage tranche (82 cells, 31 legs)

With the pullback stale/giveback cells cleared, `--stale-corpus-state` says of
the remaining **151** stale cells: **144** have *no* newer live-parity row —
"a re-run IS the remedy" — and **7** have one that *contradicts* the status.

**The 7 are not a finding.** I checked each ref: all 7 already carry the
`LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS` acknowledgement from an
earlier session. The roll-up asks *"does newer evidence disagree?"* and the
agreement guard asks *"is the disagreement acknowledged?"* — different
questions, both answered correctly, and it would have been easy to file the
gap between them as a defect.

For the re-runnable set I first re-derived the population myself and got **119**
cells across 16 legs — **wrong**, because my derivation omitted the family
filter and swept in the 8 `ict_scalp` legs, whose harness *does* model a real
target and which are therefore not in the stale population at all. Using the
roll-up's own `stale_cells` instead: **82 cells across 31 legs** in
lever-sweep-addressable columns, dominated by `trail_geometry` (28),
`vol_trail` (23) and `trail_decay` (12). That is the second time this session a
hand re-derivation disagreed with the tool that owns the definition, and both
times the tool was right.

**Dispatched** run `31913017711` (22:46:39Z) over those 31 legs,
`levers=trail_geometry,vol_trail,trail_decay`, `tp_cap_pct=0.099`,
`split_target_oos=35` — the same shape as the run that just worked. Verified
**31 sweep jobs actually queued/in-progress**, not merely that the dispatch was
acknowledged.

Not addressable by this sweep, and stated so it is not mistaken for pending
work: `regime_flip_exit` (38 stale cells) has its own driver and a cutover of
**NEVER**, and `exit_ladder` (29) has no harness levers.

**I re-verified the `NEVER` rather than inheriting it**, because it holds 38
cells stale and "a previous session checked it" is not verification. It is
**correct and current**: `m20_flip_replay_sweep.py:65` calls
`base_args(name, cfg, fam, data, resample)` — five positional args — while
`base_args`'s sixth parameter is `tp_cap_pct: float = 0.0`, so that driver
builds no-take-profit books by construction; `grep` for `tp_cap` / `tp_geometry`
in that file returns nothing, so there is not even a field to check. A clean
result, recorded with its date so the next session knows when it was last true.

### 14. The tranche sweep landed; acknowledgements are now GENERATED, not written

31 runs, 293 rows, and **14** live cells where live-parity evidence contradicts
an `honest_negative`. Hand-writing two refs was fine; fourteen within the hour
was not, so `scripts/research/m20_ack_corpus_disagreements.py` now drafts them.
It **imports the agreement guard's own `find_disagreements`** rather than
reimplementing the predicate — a second definition would be free to drift from
the one CI fails on — writes `ref` prose only (never `status`, never
`tp_geometry`), and is dry-run by default with the dry-run asserted **on bytes**,
not on the log line claiming it.

**It paid for itself on the first batch.** `mhg_pullback_1d / vol_trail` reads
`wf=5/6` and is really **ONE** real win: four of six folds are inert — the lever
never fired — and an inert fold still counts `ok`. Hand-written, that cell
enters the matrix as a solid pass. Two more carry the same shape. **8 of 14 are
Path B**, and exactly **two** are the strong shape (Path-A PASS, no inert fold):
`sol_pullback_2h/trail_decay` and `ada_pullback_2h/trail_decay`. Saying "14
cells now pass" would be true and misleading, so the split is what got recorded.

### 15. Second vintage pass — and the cell I refused to stamp

67 more cells declared live-parity (`trail_geometry`, `vol_trail`,
`trail_decay`), same three checks as § 12. **`squeeze_breakout_4h /
trail_geometry` was skipped**: it requested the cap and its family carries one —
two of three checks pass — but both its rows carry **no measured live-TP-reach
distribution**, which is the check that separates APPLIED from REQUESTED. A
criterion that never excludes anything is not a criterion.

Vintage **153 → 106** (47), against 67 stamped − 20 already-fresh = 47. It
reconciles exactly, **but only once the UNDATED bucket is counted** — read
against `pre_cutover` alone it looks 2 short, which is what I first computed.

### 16. The screen's rows landed, and the pre-registered test RESOLVED

The completed 4-arm screen was still trainer-side only — the committed record
held `off0` and nothing else for its three legs, i.e. this sprint reproducing
the "22 % machine-readable" defect the record exists to end. Pulled via relay
#9522 (consolidator fetched from the branch, not re-implemented), transfer
sha256-verified, cross-check clean. Record **234 → 246**.

Re-derived **from the script**: `per_leg` denominator **3 → 6** — the
pre-registered required check. One mover (`ict_scalp_sol_5m`, off12), the
machine-readable form of the flip pre-registered 44 minutes ahead.

**The comparison resolved: `per_leg` 2/6 = 33.3 % vs `family_pooled` 9/27 =
33.3 %, Fisher p = 1.0000.** Identical point estimates — and the honest reading
is the one written down first: at n = 6 no reachable outcome could reach
p < 0.05, so this settles nothing, and quoting the agreement as a result would
be the overclaim the pre-registration exists to prevent. Re-checking
reachability also caught a gap in **my own** table: it omits **5/6** (p = 0.062,
equally unreachable). Corrected.

⚠️ The ANY-screen headline reads 33.3 % before *and* after (9/27 → 11/33
coincides), so the EVERY-screen rate (26.7 % → 27.3 %) is pinned beside it in
the test — otherwise a future merge adding nothing would pass.

### 17. The last hand-pooled cells re-measured — and the pooling flattered them

Two `exit_head_ml` cells (`slv_trend_1h`, `uso_trend_1h`) were `honest_negative`
from a 2026-08-13 run whose **own ref warned about how it was produced**: the two
legs' rows were *concatenated by hand* into one family dir and trained once,
because "alone this leg clears ZERO folds". A verdict that exists only under a
manual step is one the pipeline cannot reproduce.

Re-run per leg through the shipped driver (relay #9532; verdicts read from
`e1_report.json` in **#9533**, not from the log tail — the relay had truncated
`uso`'s verdict line, and a truncated tail is not a measurement):

| leg | n_oos | folds | beats_actual | beats_hard | mean_auc | verdict |
|---|---|---|---|---|---|---|
| `slv_trend_1h` | 250 | 5 | 1/5 | 1/5 | 0.5452 | `honest_negative` |
| `uso_trend_1h` | 150 | 3 | 1/3 | 1/3 | 0.5421 | `honest_negative` |

**Verdicts unchanged**, so no status moved and no Tier-3 question arose — but two
claims in the existing refs are now measurably false and were corrected in place:

1. *"alone this leg clears ZERO folds"* — under trade-blocking each leg alone
   clears **5** and **3** usable folds of 50 trades. Pooling was a workaround for
   *calendar-year* folds, never a property of the legs.
2. **The pooled run's per-leg numbers flattered BOTH legs** — `slv` 4/7 → 1/5,
   `uso` 6/7 → 1/3 `beats_actual`. Both moved the same way, which is the only
   reason it is worth stating. ⚠️ Recorded as a **direction, not a paired test**:
   the runs cut different partitions (7 calendar folds vs 5/3 trade blocks) over
   different n (249/168 vs 250/150), so no fold is comparable across them. The
   transferable claim is that *a per-leg verdict attributed out of a pooled model
   is not the same measurement as one trained per leg* — and here it was the more
   generous of the two.

Swept the matrix for other cells of this class before moving on, since a
generous-attribution defect matters far more on a **decision** cell than on a
negative: **exactly 2 cells** carry `CONCATENATED BY HAND`, both of them these.
The 27 other refs mentioning pooling are ordinary family grouping — the three
`ict_scalp_*_15m` cells (including the two `passed_unshipped`) were already
per-leg trade-blocked runs at distinct `n_oos` (350/300/300). **Class closed.**

Vintage: these were the last two `exit_head_ml` cells predating the per-lever
`2026-08-14` cutover — stale share **36.8 % → 36.1 %** (104 of 288), exactly −2.

**A driver defect surfaced first, and is fixed.** The initial retry (#9531)
skipped both legs and reported *"could not determine whether `<harness>` supports
`--strategy-name` … fix the harness probe"*, then *"no emitted trades — nothing to
build"*. Neither sentence was true. The probe had died on `ModuleNotFoundError:
pandas` because **I** launched the round with bare `python3` instead of the
`.venv/bin/python3` its own docstring specifies; since every harness runs under
`sys.executable`, no leg could ever have run, and nothing had been measured.
Sub-classes **A** (a message naming an untested cause) and **C** (an empty result
reading as a clean negative) of the diagnostic-provenance rule, one level apart.
Fixed by branching on the failure **stage**, not by rewording either label:
`interpreter_defect()` reads the missing module out of stderr (anchored, so a
traceback merely passing *through* `pandas/` is not a missing pandas), and
`empty_round_reason()` separates *no leg reached a harness* from *every harness
failed* from the one real empty result. The refusals themselves were **correct**
and are unchanged — an unattributable row is still worse than a missing leg.
9 tests, plant-proven twice (neutering `interpreter_defect` fails 3; a naive
substring match plus the collapsed empty message fails 7).

### 18. The largest stale block was pointed at a remedy that could not fix it

With the `exit_head_ml` column closed, the remaining backlog is **99 stale cells
with no fresher corpus row**, which the roll-up labels *"nothing newer; a re-run
IS the remedy"*. Broke that down before dispatching anything, and the label was
wrong for the largest single block in it.

**38 of the 99 are `regime_flip_exit`** — a lever pinned at
`GEOMETRY_CUTOVER_NEVER`, meaning its harness never modelled the live TP. A
re-run of such a cell writes *another* stale row. Every one of those 38 was
labelled with hours of trainer time that could not have cleared it. **The
backlog a sweep can actually address is 61, not 99.**

**Root cause, and it is one line.** `m20_flip_replay_sweep.py` called
`base_args(name, cfg, fam, data, resample)` **positionally**, so `tp_cap_pct`
defaulted to `0.0` and neither `--tp-cap-pct` nor `--tp-r` was ever appended —
and the driver stamped no geometry at all, so *"there is not even a field to
check"*. Fixed: `--tp-cap-pct` **defaulting to 0.099** (a cap you must remember
to pass is a cap that will be forgotten), forwarded to `base_args`, with
`tp_geometry` stamped per leg **and** run-level, always, including the `0` case.

**Proved the fix is material before spending the sweep** (relay #9534, one leg,
A/B from a worktree pinned to the branch — `main` at `5d5bbb67`, worktree at
`4d08b3ad`):

| `trend_donchian_eth_4h` | capped (fixed) | uncapped (old) |
|---|---|---|
| trades | **196** | 157 |
| `actual_net_r` | **26.73** | 49.251 |
| walk-forward | 2/6 | 3/6 |
| `tp_geometry` | `live_parity_capped` | `NO_TAKE_PROFIT` |

The populations differ (+24.8 % trades — a capped TP closes trades that
otherwise run on, so it is a *different book*, not the same trades re-scored),
and the no-take-profit baseline was **1.84×** more profitable than the geometry
production actually places. ⚠️ **One leg, and the verdict did not flip** (`fail`
both ways) — this establishes the baseline moves, **not** that any of the 38
statuses change. The full re-sweep is dispatched (#9535); all 38 cells are
`honest_negative`, so no live decision rides on the current values.

**The derivation now has one home.** `tp_geometry_for` moved unchanged into
`m20_fleet_exit_sweep` — it was the *only* producer, living inline in
`m20_exit_head_round`, and a second copy is exactly how the 2026-08-10
fleet-sweep fix failed to reach that sibling. Added the state the inline version
lacked: an empty family set is **`UNOBSERVED`**, not a parity claim (unreachable
from the old caller, reachable from this one, which writes `verdicts.json` even
if every leg fails). An existing test caught the extraction by grepping the
driver for the label strings; it was **pointed at the new home, not weakened**,
and gained a test that no other file re-derives the label.

**`m20_coverage_rollup` gained the state it was missing**:
`harness_never_modelled_the_tp`, checked *before* `no_live_parity_row` since both
mean "nothing newer exists" and only one is answerable by sweeping. Derived from
`cutover_for(lever)`, never a lever list, so it **self-clears** — deleting a
lever's `LEVER_GEOMETRY_CUTOVER` entry (how a harness fix is marked) returns its
cells to the re-runnable bucket with no edit to the state logic. That property is
itself pinned by a test that simulates the fix and restores the map.

⚠️ **Deliberately did NOT remove the `regime_flip_exit` NEVER entry.** The
harness can now produce a live-parity book; no committed cell was measured on one
yet. A landed re-sweep is what marks it fixed.

**The same defect reaches M21, where it is worse** — filed, not fixed
(`BL-20260816-M21-ENTRY-SWEEPS-STILL-CALL-BASE-ARGS-POSITIONALLY`).
`m21_entry_sweep.py:133` and `m21_entry_head_round.py:125` still call `base_args`
positionally; they are the remaining two of BL-20260814's "three sibling sweeps".
M20's column is 38 cells, **all** `honest_negative`, so its stale evidence costs
knowledge. `entry-refinement-coverage.json` carries **23 `shipped` + 4
`passed_unshipped`** — decision cells — and M21 has **no geometry tracking at
all**, so nothing there reports the condition. ⚠️ The filing carries an explicit
`not_established`: a draft of it claimed 25 of those sit on capped families,
which was **wrong** — it ran `classify()` over the matrix's `strategy` labels,
5 of which are *composite* rows covering many legs and mixing both families.
Recorded so the figure is not re-quoted.

### 19. The re-sweep's very first leg returned a PASS that measured nothing

Caught **mid-sweep**, in the 45-second liveness probe of #9535 — which is the
argument for the probe:

```
trend_donchian (BTCUSDT 1h): PASS wf=6/6 flip%=0.0 net 37.3918 -> 37.3918
```

A perfect six-of-six walk-forward over a lever that **never once acted**, on a
leg whose matrix cell is `honest_negative`. It is a **tautology by
construction**: `m20_regime_flip_replay.replay` sets `flip_r = actual_r` on a
`no_flip` row, so a fold with zero flips holds the two series identical and its
`beats` test — `flip_net >= actual_net and flip_dd <= actual_dd` — passes with
equality. **Every inert fold was a free win**, and nothing in the output
separated that from a lever that genuinely helped six times.

Verified as mechanism, not inferred: the `no_flip` branch and its `r = actual_r`
were read at `m20_regime_flip_replay.py:116`, and a test asserts the equality
directly rather than trusting the comment.

It is the same class as the inert walk-forward folds the acknowledgement drafter
reports (item 14), one level worse: there inertness was 3 of 6 folds; here it
can be the **entire verdict**.

⚠️ **Correction to my own justification, published in the commit for this fix
and in an earlier draft of this item.** I wrote that an inert PASS *"lands in
the corpus as a floor-clearing PASS contradicting a live cell — precisely what
`matrix-corpus-agreement` escalates."* **That path does not exist.** Measured
after the fact: `m20-sweep-corpus.jsonl` holds **1,264 rows across exactly the
five levers the fleet sweep emits** (`stale_stop` 222, `giveback_stop` 182,
`trail_geometry` 170, `trail_decay` 411, `vol_trail` 278, +1 null) and **zero**
`regime_flip_exit`, `exit_ladder` or `exit_head_ml` rows. Nothing routes
flip-sweep verdicts into the corpus, so the guard never sees them.

The fix stands on its own merits — a tautological PASS is wrong wherever it is
read, and the reader it actually misled was **me**, for the minute between the
liveness probe and checking `flip_pct`. But the mechanism I claimed was not
verified before I asserted it, and a future session could act on the belief that
the guard covers this column. It does not.

- **Three verdicts, never two** — `INERT_NEVER_FLIPPED` when `flipped == 0`,
  checked **before** the win ratio (or a 6/6 of free wins reaches `PASS` first).
  *"the lever fired and lost"* and *"the lever never fired"* are opposite
  findings and only one is evidence about the lever.
- **`PASS` now decided on `real_wins`**, a win that is not inert.
- Per-fold `flips` + `inert`, and `walkforward_real` / `inert_folds` beside the
  legacy string — so the correction is one field away from anyone who only ever
  sees the summary line.
- `wins` / `walkforward` keep their exact old values, so an existing consumer
  reads the same number.
- `INERT_NEVER_FLIPPED` is **not** in `check_matrix_corpus_agreement.PASS_VERDICTS`,
  so it cannot be read as counter-evidence — asserted in a test that checks
  `PASS` **is** in the set in the same breath, so the probe is shown able to find
  a positive.

⚠️ **The #9535 sweep predates this fix.** Its *books* are correct (the TP-cap fix
was in the worktree it ran from, `0b10227b`) and only the verdict LABEL is
affected — but the old code records no per-fold flip count, so partial inertness
is not recoverable from its output. Re-run on the fixed commit rather than
patched.

✅ **Live-verified on the very leg that produced it** (#9536, worktree
`70bb3316`) — same leg, same numbers, corrected label:

```
before:  trend_donchian (BTCUSDT 1h): PASS                wf=6/6 flip%=0.0 net 37.3918 -> 37.3918
after:   trend_donchian (BTCUSDT 1h): INERT_NEVER_FLIPPED wf=6/6 flip%=0.0 net 37.3918 -> 37.3918
```

…and the same sweep then produced a far sharper instance than the one that
prompted the fix:

```
scha_trend_long_1d (SCHA 1d): INERT_NEVER_FLIPPED wf=17/17 flip%=0.0 net 7.4277 -> 7.4277
```

**Seventeen of seventeen.** Under the old two-state verdict that is the most
impressive walk-forward anywhere in the sweep, and the lever never fired once.
A reader ranking legs by `walkforward` would have put it first.

The v1 sweep also completed cleanly at `tp_geometry: live_parity_capped` — the
first `regime_flip_exit` evidence ever produced on the geometry the live units
place. Its pullback legs are **real** fails, not inert ones: `ada_pullback_2h`
flip% 100.0 taking net_R 30.13 → −0.54, `avax_pullback_2h` 98.9 % taking
16.82 → −2.99, `htf_pullback_trend_2h` 99.5 % taking 19.20 → −9.69. A lever that
fires on essentially every trade and destroys the book is a finding the old
no-take-profit evidence agreed with; what changes is that it is now measured
against the right baseline.

### 20. Chasing that correction found a worse defect one file over

Checking whether flip verdicts reach the corpus (they do not — § 19) meant
reading the extractor, and **`m20_corpus_extract.py` accepts a flip-sweep file**.

Both sweeps write a file named **`verdicts.json`**, both under `runtime_logs/`,
and `find_verdicts` is an `rglob("verdicts.json")` — so `--in runtime_logs/`
reaches both. The schemas are unrelated: fleet writes per-cell measurements
(`cells`, `d_net_r_IS`, `wf_folds`), flip writes per-leg results (`flip_pct`,
`walkforward`, `actual_net_r`).

**Measured, on the real #9534 payload, before the guard existed** — it did not
error. It emitted a row asserting:

```
leg_status: "no_levers"      # "this leg has no levers to sweep"
```

about `trend_donchian_eth_4h`, whose own source file records a lever that fired
on **43.9 %** of trades and returned `fail`. A confident row stating the opposite
of its input, keyed into the durable corpus that both `matrix-corpus-agreement`
and the coverage roll-up read.

Now refused, and the refusal names what it saw and who owns the schema. Three
choices worth stating:

- **The discriminator is the FLEET marker, not the flip one.** Asking *"does
  this look like a flip file?"* would wave through any third schema that appears
  later — the unasserted-denominator shape.
- **The run FAILS; the file is not skipped.** Skipping would extract the others
  and report success over a population quietly missing one, and the operator's
  next move (narrow `--in`) depends on knowing the file was there.
- **A degenerate doc does not raise.** An empty run is a real, separate state.

Verified end to end: the CLI exits 1, names `m20_flip_replay_sweep`, and leaves
the corpus **byte-identical**. 6 tests, plant-proven (removing the assertion
fails 3), including a positive control — a fleet-shaped doc still extracts, and
a `leg_status`-only entry is still recognised as the legitimate no-cells fleet
row.

### 21. The re-sweep landed: 42 legs, zero PASS — and it caught 12 false positives

The full `regime_flip_exit` re-sweep completed (#9536 → read via #9540; the
first two reads died on the relay's indentation trap — see below). **A complete
population: 42 legs, zero `data_missing` / `harness_error` / timeout rows**, at
`tp_geometry: live_parity_capped`, `tp_cap_pct: 0.099`, families
`['donchian', 'pullback']`.

| verdict | legs |
|---|---|
| `fail` | 30 |
| `INERT_NEVER_FLIPPED` | **12** |
| `PASS` | **0** |

**No new adjudications.** All 38 live `regime_flip_exit` cells stay
`honest_negative` — now for the first time on the geometry production actually
places. The operator's morning queue does **not** grow.

**And the § 19 fix earned itself in one run.** Every one of those 12 inert legs
would have been a `PASS` under the old two-state verdict, at these walk-forwards:

```
qqq_trend_long_1d   20/20     spy_trend_long_1d   17/17     mes_trend_long_1d   11/11
iwm_trend_long_1d   17/17     splg_trend_long_1d  17/17     qld_trend_long_1d   11/11
scha_trend_long_1d  17/17     uso_trend_1h        10/10     tqqq_trend_long_1d  11/11
trend_donchian       6/6      trend_donchian_sol   6/6      trend_donchian_sol_prop 6/6
```

That is **12 spurious floor-clearing PASSes**, each contradicting a live
`honest_negative` cell, each carrying the most persuasive-looking walk-forward
in the run. Caught by a 45-second liveness probe on the *previous* sweep.

**The 30 real fails are emphatic, not marginal** — the lever fires on 95–99 % of
trades and destroys the book:

```
qqq_pullback_1h  dR −95.15  flip% 97.5   net  75.43 → −19.72
spy_pullback_1h  dR −84.33  flip% 96.7   net  70.74 → −13.59
gld_pullback_1h  dR −81.88  flip% 98.0   net  61.04 → −20.84
tlt_pullback_1h  dR −72.81  flip% 95.8   net  15.37 → −57.44
```

⚠️ **The 12 inert legs are a COVERAGE finding, not a result.** For those legs the
frozen-label flip condition is never met at all, so the sweep has said nothing
about whether the lever would help them — their `honest_negative` rests on
*"never tested"*, which is a different claim from *"tested and lost"*. Notably
they are **every** `*_trend_long_1d` equity leg plus three donchians; a policy
that never fires across a whole family is worth a look on its own terms, and
it is now visible rather than hidden behind a 20/20.

⚠️ **Three relay round-trips were burned on a documented trap.**
`docs/claude/diag-relay.md` § *"Any non-trivial `cmd:` script MUST be base64'd"*
describes this exactly — the block's first line is dedented and the rest keep
their indent, so a heredoc terminator never terminates and a multi-line
`python3 -c` dies on `IndentationError`. A 2026-08-13 session hit it four times;
I hit it twice more (#9538 heredoc, #9539 `-c`) before following the doc. The
doc needed no change — **I did**. Recorded because "the doc says so" is not the
same as having read it.

### 22. Stamped, and the sentinel cleared — vintage 36.1 % → 23.3 %

With a complete live-parity population in hand, the column was stamped from the
artifact (relay #9541, machine-readable dump — not retyped from a summary):

- **41 cells** gained the measurement in `ref` **plus an explicit
  `tp_geometry: live_parity`**. **No status moved**, and the writer asserts that
  before saving. Because 0 of 42 legs returned `PASS`, every `honest_negative`
  is **confirmed**, not merely retained.
- The **12 inert** cells say so distinctly — their `honest_negative` rests on
  *never tested*, and each ref records that the old verdict would have scored it
  `PASS` at a walk-forward up to **20/20**.
- **`regime_flip_exit` removed from `LEVER_GEOMETRY_CUTOVER`.** That entry's own
  comment says removal — not a date edit — is how a harness fix is marked, and
  both conditions are now met *and checkable*: the harness passes the cap and
  stamps geometry, **and** a sweep landed on it. This is exactly why the
  sentinel was not dropped in the same commit as the harness fix: a fixed
  harness with no evidence behind it still leaves every cell on the old book.

**Evidence vintage 36.1 % → 23.3 %** (104 → 67 stale cells), and the
`harness_never_modelled_the_tp` bucket **self-cleared 38 → 0 with no edit to the
state logic** — the property § 18 built it to have, now demonstrated rather than
asserted. The remaining 67: **33** re-runnable · **29** with no producer · **5**
already-adjudicated disagreements.

**Two honest residuals, both stated rather than smoothed over:**

- **`squeeze_breakout_4h`** is the one `regime_flip_exit` cell the re-sweep did
  not cover *and a re-run will not cover*: `POLICY_KEY` maps only `donchian` and
  `pullback`, so a `squeeze` leg is skipped silently. It now reads
  `no_live_parity_row` — the closest available state and still not exact.
  Deliberately **not** given a fourth state for a population of **one**; the
  reason is written into that cell's own `ref`, where a reader meets it.
- **The sweep was dropping the correction fields.** `walkforward_real` and
  `inert_folds` were computed by the replay and present in each
  `<leg>_flip.json`, but `verdicts.json` — the file every reader and every
  downstream tool opens — returned `null` for all 42 legs, because the per-leg
  dict copied a fixed field list that predated them. A wholly-inert leg survives
  that (its verdict says so); a **partially** inert one does not. Now propagated
  via `.get`, so a pre-fix report degrades to null rather than crashing.

**Two of my own tests failed on this correct change**, and the failure was
right: they asserted that *some* lever IS pinned at `NEVER`, which made them a
function of production state, so removing the entry broke them with a message
about a missing positive case rather than about the logic. They now **inject** a
`NEVER` lever and restore it — plant-proven still to catch a disabled branch —
and one had to stop demanding the cleared cells land in `no_live_parity_row`,
which holds only for a lever that *has* a producer. It asserts conservation of
the whole distribution instead.

## Validation Performed
- **Tests:** 10,861 passed. The 34 failures in the full run were checked, not
  assumed: **32 are pre-existing sandbox dependency gaps** — proven by running
  the same 7 modules in a throwaway worktree at `origin/main` and getting an
  **identical 27 failed / 66 passed** — and the 2 that were mine
  (`test_check_research_index`, `test_guards_uncommitted_work`) are fixed.
- **Guards:** CI reported `PASS 38 · FAIL 1` on `9ad32e94`; the one failure was
  `artifact-validity-guard` (my new research script unindexed), fixed in
  `38004dbc`. The new `trainer-heavy-lock-guard` passed in that same CI run.
- **Lock behaviour exercised off-box, all four states:** inert without the role
  marker (returns None) · acquires under `TRAINER_HEAVY_LOCK_FORCE` · re-entrant
  skip when `TRAINER_HEAVY_LOCK_HELD` is set (**the no-deadlock property**) · a
  child *without* the flag times out against a held parent with **rc=75**.
- **Every new guard/test proven load-bearing by planting its defect.** Two worth
  recording:
  - the `fold_offset` test: plant 1 (field removed) fired; **plant 2 initially
    PASSED**, and that was my *plant* being wrong — `.replace(..., 1)` hit the
    identical string in `_round_meta` instead of the row. A plant that does not
    land is indistinguishable from a guard that does not work unless you check
    *which* occurrence you edited.
  - the heavy-lock guard's `rc` first read as 0 under a planted regression —
    `head -3` had eaten the pipeline's exit status. Re-measured: **rc=1** planted,
    **rc=0** restored.
- **Corpus rewrite proven purely additive:** the file round-trips byte-identically
  through `json` before the edit, all 33 changed lines are a pure suffix append
  of the two new keys, and every pre-existing field compares equal across all rows.

### Gaps not yet verified
- **The screen result itself.** Four arms, ETA ~21:15Z. Nothing about the
  dispersion finding is updated by this sprint.
- ~~The lock is unexercised on the trainer.~~ **Closed by § 5** — chasing this
  gap is what found the private-lock defect. `on_trainer_vm()` returns True on
  the box and both lock files were observed in real held/free states (#9497).
  What remains unexercised is the **new entrypoint** locking specifically: the
  running screen is pinned to the pre-fix worktree commit, so `train_exit_head`'s
  own `take_heavy_queue` has not yet run on the trainer.
- `replay_pregate*` remains outside the queue by design — filed as
  `BL-20260815-HEAVY-IS-UNDEFINED-FOR-REPLAY-AND-EVAL-JOBS` rather than patched,
  because adding four filenames would pass today's scan while leaving the next
  4 GB eval job invisible.

## Documentation Updated
- `docs/claude/trainer-resource-protocol.md` — § Rule 1 three-path table; the
  overclaiming sentence corrected.
- `docs/research/m20-fold-dispersion-2026-08-15.md` — a **second** correction
  block; the first correction is kept verbatim as the record of what was wrong.
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — the backfill script routed.
- `docs/claude/health-review-backlog.json` — 2 rows resolved, 1 filed, 2
  corrected. The wrong paragraphs are **kept verbatim** with corrections
  appended rather than rewritten.

## Contradictions or Drift Found
- **Mine:** the heavy-lock/reset claim (§ 1), published in six places before
  being measured. Corrected in all six.
- The protocol doc's *"nothing can slip past"* described two enforcement halves
  while a third family of jobs was exempt.
- The `fold_offset` backlog row's own `action_taken` ("not fixed mid-screen —
  the trainer re-checks out the branch per arm") was **made obsolete by the
  worktree** during the same session; annotated rather than silently dropped.
- My hand enumeration "7 scripts in scope" was incomplete — the guard found the
  8th.
- **Mine:** I diffed the sweep corpus on `(leg, lever, cell)` and reported 147
  verdict changes over a commit that added 68 rows. The key is not unique; the
  arithmetic caught it before it was published (§ 10).
- The sweep banner asserted one shared IS/OOS split across legs that had each
  been cut at a different derived boundary — 16 of 17 comments wrong, on the
  same banner and the same defect class as the `geometry` line five days
  earlier (§ 11). Fixed, not logged.
- The sprint log's own header still said the 5m screen was running after it had
  finished; corrected in `ec632c3d`.
- **Mine, and the worst of the session: EIGHT consecutive hourly status pings
  delivered the single character `|`** (#9495 → #9521, 16:17Z → 23:18Z). The
  `send-ping` issue-body parser is `grep -m1 '^message:'` — one line — so a
  `message: |` block truncates to the marker, and the action **exits 0** and
  closes "completed". The pre-existing non-empty check cannot catch it because
  `|` is not empty. Roughly seven hours in which the operator's only overnight
  visibility channel was dead while every available signal said it worked,
  during a session explicitly instructed to ping hourly. Caught only by checking
  the action's own log line rather than trusting the ✅. Guard shipped refusing
  bare block markers; `BL-20260815-SEND-PING-BLOCK-SCALAR-DELIVERS-ONE-CHARACTER`
  stays **open** because that guard is enumerative and the durable fix is to
  assert a plausible length or echo the queued body back.
- **Not mine:** `artifact-validity-guard` began failing **every** PR at 00:00Z
  on 08-16 when a `KNOWN_VACUOUS` grandfather expired (`until: 2026-08-15`) — by
  design; the list forbids permanent grandfathering. Its owner row was already
  `resolved`, so only the entry was left behind. Measured before acting (1 of 19
  captures zero-row, 18 non-empty since), pruned the dead capture, removed the
  entry. Did **not** extend the date: that turns a guard into a snooze button.

## Risks and Follow-Ups
- **Trainer disk at 90% (4.8 G free)** across every relay this session. Not
  investigated; flagged here because a screen writing datasets is a consumer.
- The `--fold-offset` machinery is still branch-only, so **none of this screen's
  evidence is reproducible from `main`** until PR #9257 merges.

## Deferred Items
- Three paper-routed stale decisions in the coverage matrix.
- ~~The pullback-family stale/giveback re-sweep.~~ **DONE** — dispatched on the
  evidence-vintage axis and landed (§ 9, § 10). It is *not* closed as a
  question, though: it produced two Tier-3 decisions for the operator rather
  than settling them.

## Queued for the operator (Tier-3 — added by this sprint)
- `sol_pullback_2h` / **giveback_stop**: both cells PASS at live parity
  (`gb1R_afterMFE1R` wf 5/6 with zero inert folds — the strongest evidence in
  the run; `gb1R_afterMFE2R` wf 6/6 = 5 real wins + 1 no-op). Ship, or leave the
  `honest_negative` standing on the older partition?
- `slv_pullback_1d` / **stale_stop**: `path_b_wf_pass` — my read is **do not
  ship**: Path-B only, 3 of 6 folds inert, OOS net_R gain +0.001. Recorded as a
  decision because the matrix now carries the counter-evidence either way.

**Nothing in §§ 17–19 is Tier-3** — all of it is research tooling and
re-measurement, and **no cell status was flipped**. Two consequences for the
morning, though, both about what the queue is *about to* look like:

- **The `regime_flip_exit` re-sweep may add new adjudications.** All 38 of that
  column's live cells are `honest_negative` today, and they are being re-measured
  on a live-parity book for the first time (§ 18). Any cell that now returns a
  floor-clearing `PASS` becomes a corpus disagreement to adjudicate, exactly like
  the 16 already queued. It cannot *lose* anything: a lever that was never
  shipped cannot be un-shipped by better evidence.
- **Read the new verdicts against `walkforward_real`, not `walkforward`** (§ 19).
  A `PASS` at `flip_pct: 0.0` was a tautology and is now `INERT_NEVER_FLIPPED`;
  a run mixing inert and real folds still reports both numbers, and only the
  `_real` one bears on whether the lever works.

## Next Recommended Sprint

~~Read the four arms when they land, run the consolidator + rate script over the
extended corpus, and update the dispersion headline **from the script**.
Required verification: the `per_leg` denominator moves 3 → 6…~~
**✅ COMPLETED IN THIS SESSION** (§ 16). The arms landed, the record went
234 → 246, the denominator moved **3 → 6** as required, and the pre-registered
comparison resolved at **p = 1.0000** — which is one of the outcomes the
pre-registration had already declared unable to reach significance. Left struck
through rather than deleted, because "the recommendation was carried out" and
"the recommendation is pending" are different states and a reader needs to see
which.

**The actual next sprint is an OPERATOR-DECISION sprint, not a measurement one.**
This session's measuring is done; what is left needs judgement I am not
authorised to apply:

1. **Adjudicate the 16 acknowledged corpus disagreements** (§ 10, § 14). Every
   one is a live cell whose recorded `honest_negative` is contradicted by
   live-parity evidence. **Read the split, not the count:** exactly **two** are
   the strong shape (Path-A PASS, zero inert folds) — `sol_pullback_2h /
   trail_decay` and `ada_pullback_2h / trail_decay`; **8 of 14** in the second
   batch are Path B; and three carry inert folds, with
   `mhg_pullback_1d / vol_trail` reading `wf=5/6` while being **one** real win.
   A flip is Tier-3 per cell.
2. **`sol_pullback_2h` is the one leg to look at first** — it now passes across
   **three** levers (giveback, trail_decay, vol_trail) with drawdown improving
   on both windows, which no other leg does.
3. **One weekday `/api/diag/tick_cost` read at n ≥ 40**
   (`BL-20260816-TICK-MEAN-DOUBLED-SINCE-0814`). Tick mean is 72.3 s → 140.0 s
   like-for-like; the venue-closure explanation is well-supported but
   **confounded and unsettled**, and one weekday sample discriminates.

**Required verification for whatever ships:** any lever flipped out of this must
be checked against the *first* live decision it produces, not merely deployed —
and the `grant%` figures above 100 % (`tlt_pullback_1h/trail4` 179 %,
`tlt_pullback_1d/decay_stall6_t2.5` 199 %) must stay excluded from candidacy
regardless of their verdict strings.

## Wrap-Up Check
- [x] Code inspected directly (not inferred from docs)
- [x] Canonical docs reviewed and updated
- [ ] TRADE-PIPELINE updated — **N/A**, no pipeline stage changed
- [x] Roadmap checked (M20, 373/376 unchanged by this sprint)
- [x] Contradictions recorded, including my own
- [x] Unknowns stated rather than smoothed over
