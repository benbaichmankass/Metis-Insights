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

## Next Recommended Sprint
Read the four arms when they land, run
`scripts/research/m20_consolidate_dispersion_arms.py` + `m20_dispersion_rate.py`
over the extended corpus, and update the dispersion headline **from the script,
not by hand**. Required verification: the `per_leg` denominator moves 3 → 6 and
the pre-registered comparison is read *as pre-registered* — note that its own
data already invalidated the original power calculation, so **no outcome of that
comparison reaches p < 0.05**.

## Wrap-Up Check
- [x] Code inspected directly (not inferred from docs)
- [x] Canonical docs reviewed and updated
- [ ] TRADE-PIPELINE updated — **N/A**, no pipeline stage changed
- [x] Roadmap checked (M20, 373/376 unchanged by this sprint)
- [x] Contradictions recorded, including my own
- [x] Unknowns stated rather than smoothed over
