# How the manager/sub-session operating model actually behaves — measured, 2026-09-08

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> `session_012cVryifpv359yAWWVkrmnk` · parent `session_01HrmZ1RRNM4UnEUaFdrPEjj`

Measurement of the operating model **as it behaves**, not as it is designed, from
evidence this system produced in a single day. Research only: every finding is
**FILED, not fixed**.

## Populations, stated once

| population | what it is | complete? |
|---|---|---|
| **P-REG** | all 164 rows of `docs/claude/work/SESSIONS.json` at `origin/main` a0ec22c | **complete** |
| **P-LIVE** | 200 sessions from `list_sessions(mine=true)`, 2 pages of 100, read 2026-09-08T08:19Z | **a PAGE, not a population** — `has_more: True` on both pages |
| **P-JOIN** | P-REG joined to P-LIVE by session id | **complete: 164/164 registry rows resolved** |
| **P-TODAY** | the 21 sessions in P-LIVE created on 2026-09-08 | page-bounded |

Every number below names which one it came from. Where a quantity is a proxy
rather than a direct reading it is labelled **ESTIMATED** with the direction of
its error.

---

## Class 1 + Class 2 are ONE defect, not two

**This is the finding.** The "two of six containers silently cannot push" class
and the "`create_session` without `source_url` attaches the wrong repo" class
have been carried as separate rows since 2026-09-05. They are the same defect.

### The correlation (P-LIVE, n=3 of 3)

Every session known to have been push-denied carried a wrong or absent repo in
`session_context.sources`:

| session | class | evidence it was push-denied |
|---|---|---|
| `session_015iukpShDdwvP5C` (MI-185) | `the-lizardking/ict-trading-bot` | brief: ~201k tokens spent, could not land |
| `session_0173g82hw2Rf3Hjn` (MI-183) | **no `sources` key at all** | brief: ~160k tokens spent, could not land |
| `session_013r8Joo877hnkYo` (1B, 09-06) | **no `sources` key at all** | its own successor's TITLE reads *"previous run stranded: no repo attached, push denied"* |

**3 / 3.** The mechanism is not mysterious: a container whose attached repo is
`the-lizardking/ict-trading-bot` is pushing at a repo this session is not scoped
to, and a container with no `sources` key has no repo to push at all. The refusal
was never a permissions classifier being capricious — it was the correct answer to
a push nobody should have been able to make.

⚠️ **n = 3, and it is 3 of 3 KNOWN cases, not 3 of 3 cases.** The population of
push-denied sessions is not enumerable from any surface I can read; it is
knowable only from what a session reported before dying. So this is a perfect
correlation on a small, self-selected sample. It is enough to act on and not
enough to call a proof.

### What was NOT obtained: the verbatim refusal

The unit asked for the refusal text character for character. **I could not get
it.** `list_sessions` / `get_session` return the session RECORD (status, context,
task_summary) and not its transcript events; there is no tool on this surface
that returns another session's tool output. A fourth session paraphrasing would
add nothing. **Recorded as not-measurable-and-why, not as absent.**

What this measurement supplies instead is better than the refusal text: a
**predictor that costs one tool call and runs before any budget is spent.**

### The predictor

`get_session` with no argument describes the calling session and returns
`session_context.sources`. Verified live on this session:

```
"session_context":{"sources":[{"git_repository":{
    "url":"https://github.com/benbaichmankass/metis-insights","revision":"main"}}]}
```

A session can therefore establish, in its first tool call, whether it will be
able to land its work — no git, no commit, no push, no budget. The push probe
now in every spawn prompt detects the same condition but costs a branch, a
commit and a network round-trip, and it cannot say *why*. This reads the cause.

### Filing did not teach anything — measured

15 instances of the wrong/absent-repo class in P-LIVE, across 5 distinct days:

```
2026-09-02  MALFORMED_HOST          MI-36    <- url read "github.comute.com/..."
2026-09-02  NO_SOURCES_KEY          MI-37 (retry)
2026-09-02  NO_SOURCES_KEY          MI-73
2026-09-02  WRONG_REPO lizardking   MI-75
2026-09-02  WRONG_REPO lizardking   MI-76
2026-09-03  NO_SOURCES_KEY          MI-101
2026-09-03  WRONG_REPO lizardking   MI-101 respawn
2026-09-03  WRONG_REPO lizardking   MI-109
2026-09-04  WRONG_REPO lizardking   MI-109 respawn
--- FILED 2026-09-06: BL-20260906-A-SUBSESSION-SPAWNED-WITHOUT-SOURCE-URL-GETS-NO-REPO-AND-ITS-WORK-DIES-UNPUSHED
2026-09-06  NO_SOURCES_KEY          1B
2026-09-08  WRONG_REPO lizardking   MI-179
2026-09-08  NO_SOURCES_KEY          MI-183
2026-09-08  WRONG_REPO lizardking   MI-185
2026-09-08  WRONG_REPO lizardking   MI-188
2026-09-08  NO_SOURCES_KEY          MI-189
```

**9 before the row was filed, 6 after.** The row named the mechanism correctly
and the class continued at the same rate. Two respawns (MI-101, MI-109)
reproduced the defect because the respawn omitted the same parameter the
original omitted — the remedy was known and was not carried into the retry.

One instance is worth naming separately: `MI-36` attached
`https://github.comute.com/benbaichmankass/Metis-Insights` — a **corrupted
hostname**, `github.com` with `ute.com` spliced in. The clone did not fail
loudly enough for anyone to notice at the time.

### The severity multiplier

MI-188 ran ~4.5 minutes against `the-lizardking/ict-trading-bot` and reported
*"analyzing trades journal (1000 newest); zero trailing/partial found yet"* — a
confident, quotable trading number **computed from the wrong codebase**. The
failure is not that the work is lost. The failure is that the work is *produced,
reported, and plausible*. A container with no repo fails safe; a container with
the WRONG repo fails silent, and the pre-rename repo is real, has a real trades
journal, and answers real questions wrongly.

---

## Class 3 — registers go false by time passing: the decay curve

### The claim-vs-truth table (P-JOIN, n=164, complete)

| registry `state` | live status | n |
|---|---|---|
| idle | IDLE | 94 |
| archived | ARCHIVED | 32 |
| completed | IDLE | 10 |
| *(no `state` field)* | IDLE | 8 |
| **working** | **IDLE** | **7** |
| idle_delivered | IDLE | 4 |
| blocked | IDLE | 3 |
| stalled | IDLE | 2 |
| idle_delivered | ARCHIVED | 1 |
| done / failed | IDLE | 2 |
| **working** | **RUNNING** | **1** |

**8 rows claim an active state; 1 is actually RUNNING (12.5%).** The registry has
been partially reconciled since MI-84's 69%-wrong reading — 94 rows now correctly
say `idle` — so the wrongness is concentrated in exactly the rows that matter:
the ones a manager would act on.

### The decay curve (P-JOIN minus 1 right-censored, n=163)

How long is a `working` row, written at spawn, actually true?

*lifetime = `live.updated_at` − `live.created_at`.* **ESTIMATED, not MEASURED:**
`updated_at` is the record's last write, which is activity for an idle session but
is also bumped by archival and tag edits. **Direction of error: over-states life**,
so the true curve is at or below this one.

```
min 1.1m | p10 21.8m | p25 40.4m | MEDIAN 61.3m | p75 121.6m | p90 718.9m | max 7502.3m

dead within  10m:    7/163   4.3%
dead within  20m:   15/163   9.2%
dead within  30m:   24/163  14.7%
dead within  42m:   44/163  27.0%   <- the "42 minutes" anecdote is the 27th percentile
dead within  60m:   78/163  47.9%
dead within 120m:  122/163  74.8%
dead within 240m:  133/163  81.6%
```

**A `working` row is a coin-flip after one hour.** The 42-minute case that
motivated this unit is not an outlier — 27% of rows die sooner. The long tail is
real (p90 = 12h), which is exactly why the median is the number to design against
and the maximum is not.

**What this bounds:** any decay mechanism whose refresh interval exceeds ~60
minutes is, by construction, wrong about half its active rows. That is the
number the existing rows (`BL-20260902-SESSIONS-REGISTRY-HAS-A-SPAWN-WRITER-AND-NO-CLOSE-WRITER-SO-EVERY-ROW-READS-WORKING`,
`BL-20260905-THE-SESSION-REGISTRY-WAS-WRONG-ON-69-PERCENT-OF-STATES-AND-58-ROWS-HAD-NEVER-BEEN-OBSERVED-ONCE`) name the defect without supplying.

### The eight active-claiming rows, individually

| session | claim written | live | claim was true for |
|---|---|---|---|
| MI-179 | `confirmed_at` 04:45:19Z | IDLE | 56m |
| MI-180 | `state_observed_at` 06:58:00Z | **RUNNING** | 81m+ (still true) |
| MI-182 | `confirmed_at` 04:47:58Z | IDLE | 97m |
| MI-183b | `state_observed_at` 06:58:00Z | IDLE | 29m |
| MI-188 | `confirmed_at` 07:15:25Z | IDLE | **4m** |
| MI-189 | `confirmed_at` 07:15:25Z | IDLE | **1m** |
| MI-188b | `confirmed_at` 07:21:09Z | IDLE | 27m |
| MI-189b | `confirmed_at` 07:21:09Z | IDLE | 32m |

MI-188 and MI-189 are the two wrong-repo spawns. Their rows were true for **4
and 1 minutes** — they were false almost from the instant they were written,
because the sessions they described died on the defect in Class 1.

### The schema cannot support a decay rule

Across 164 rows there are **44 distinct field names**. `state` appears on 156;
`state_observed_at` and `state_basis` — the fields added today precisely so a
claim could be dated — appear on **6**, and on only **2 of the 8** rows currently
claiming an active state. Eight timestamp-ish fields compete to date a claim
(`state_observed_at`, `confirmed_at`, `observed_at`, `last_observed_at`,
`last_observed`, `supervised_at`, `registered_at`, `spawned_at`). Nothing can
decay a row it cannot date under a single agreed key.

---

## Class 4 — guard deadlocks: the class decomposes into two sub-shapes

**Not fixed, not touched.** MI-191 owns the two live instances; `scripts/ci/` was
read only.

### Sub-shape A — PATH POLARITY (the R13 vs R2 instance)

- `check_pr_landing.py` **R13** requires a self-landing branch to write a
  `merge_slot` claim into `docs/claude/session-board.json`.
- `check_manager_scope.py` **R2** grades a manager commit against
  `MANAGER_SURFACE`, which is a strict **allowlist** of 9 globs. A path on
  neither the allowlist nor an active exception **fails**, with the message
  *"not on the manager surface this guard can vouch for"*.
- `docs/claude/session-board.json` is **not on MANAGER_SURFACE**.

So a manager's own self-landing PR must write a file it is forbidden to write.
Each guard is individually correct and their satisfying sets are disjoint.

### Sub-shape B — CONTENT POLARITY (the board-coherence instance)

`check_board_coherence.py` **R3** forbids the literal retired board number on any
**executable** line under `.github/workflows/` or `scripts/` — comments are
deliberately exempt, because the history of why #6927 died is worth keeping.

I reproduced the failure verbatim, on a clean checkout of `origin/main` with none
of my changes applied (`git checkout origin/main` → run the guard):

```
board-coherence: FAILED
  - R3 scripts/ci/check_pr_landing.py:542 hardcodes retired board #6927 on an
    EXECUTABLE line — resolve it through scripts/ci/board_pointer.py instead:
      f"#6927 where that is possible — but #6927 is at GitHub's 2500-"
```

**The offending literal is inside R13's own failure message.** `check_pr_landing.py`
lines 538-543 are the text R13 prints to tell a session how to claim the slot, and
it ends by explaining that the board comment cannot substitute *because #6927 is at
the comment cap*. That is exactly the history R3's comment-exemption was written to
preserve — but it lives in an f-string, not a `#` comment, so R3 correctly calls it
executable and fails.

So the conflict is over **file content**, not a path: A requires content C, B
forbids a pattern matching C. And the specific instance is sharper than "someone
hardcoded a number": **the forbidden literal appears only in prose whose entire
purpose is to explain that the literal is retired.** R3's exemption covers the place
that history is *usually* written and not the place this system actually writes it —
into the failure message a guard hands the session it just stopped.

### Is the class enumerable? PARTIALLY — and the honest answer is *not with the
### guards as they are structured today*

I built the naive static detector for sub-shape A: for each guard, extract the
concrete repo paths it names as module constants; report any not covered by
`MANAGER_SURFACE`.

- **Recall on the known instance: 1/1.** It finds
  `check_pr_landing.py -> docs/claude/session-board.json`.
- **Precision: unknown, and low.** It returns **19 candidates**. I adjudicated
  **1**. The other **18 are not-looked-at — a distinct state from false
  positive**, and I am not reporting them as findings.
- **Recall on sub-shape B: 0/1.** A path detector cannot see a content conflict.

The reason precision is poor is structural and is itself the finding: *"guard G
names path P"* is not the same claim as *"guard G requires ACTOR A to write P"*,
and **no guard declares the second**. The requirement lives in imperative code
and prose. Until a guard declares its satisfying set — which actor, which path,
which content, required or forbidden — disjointness cannot be computed from the
guards' own declarations, only discovered by hitting it. Both live instances were
found by hitting them.

`checked: scripts/ci/check_pr_landing.py` (R13's requirement is imperative code
inside `_slot_held_by_branch`, with the path in a module constant and the
*actor* nowhere) and `checked: scripts/ci/check_manager_scope.py`
(`MANAGER_SURFACE` is a 9-glob allowlist; who it binds is derived at runtime
from the git history of `MANAGER-LEASE.json`, not declared). Scanning all 52
`scripts/ci/check_*.py` for a machine-readable satisfying-set declaration
returned none — the detector above had to *infer* one from module-constant path
literals, which is why its precision is poor.

An empirical alternative exists and I did not run it: cross-run every guard's own
PASS fixtures against every other guard, and any fixture that is green under its
author and red under a sibling is a candidate. Most guards ship self-tests
(`guard_selftests.py`, `check_selftest_wiring.py`). **Not-looked-at**, recorded so
the next session does not re-derive the idea.

---

## This session's own run, as data

Studying the substrate means recording my own trace.

| observable | value |
|---|---|
| Step-0 push probe | **rc = 0, succeeded** — a positive control for Class 1 |
| my `session_context.sources` | `benbaichmankass/metis-insights` — **correct**, consistent with the probe passing |
| GitHub MCP write (board START) | **succeeded** — no 403; this container has full write capability |
| my registry row | **does not exist** — see below |
| my work object `WO-20260908-MEASURE-HOW-ACTIVE-MANAGEMENT-ACTUALLY-BEHAVES-FROM` | **does not exist on `origin/main`** |

**I was dispatched to own a work object that was never created, and I am not in
`SESSIONS.json`.** Measured: `git ls-tree origin/main docs/claude/work/objects/`
returns no match for `MEASURE-HOW`, and my session id appears in no registry row.
This is the same completeness gap `MI-15` recorded twice (3-of-6 absent, then
6-of-9 absent) — the moment a manager spawns is the moment it is least likely to
stop and write the record. It reproduced on the session sent to measure it.

Consistent with the Class 1 finding, the two sessions in this wave that got a
correct repo (this one, MI-194) both pushed; both wrong-repo sessions before us
did not.

---

## What was measured, what was not

| class | verdict |
|---|---|
| **1 — push-denied containers** | **MEASURED as to mechanism** (3/3 correlation with wrong/absent repo) and a one-call predictor is verified live. **NOT MEASURED: the verbatim refusal text** — no tool on this surface returns another session's transcript events. |
| **2 — wrong-repo spawns** | **MEASURED.** 15 instances / 5 days in P-LIVE; 9 before the backlog row, 6 after; today 5 of 21. Wrong-repo and no-repo have **different consequences** (silent-wrong vs fail-safe) though a common trigger. |
| **3 — register decay** | **MEASURED**, complete denominator (n=164 joined, n=163 for the curve). Median truth-lifetime **61.3 min**, 47.9% dead within an hour. Lifetime is ESTIMATED via `updated_at`, over-stating life. |
| **4 — guard deadlocks** | **PARTIALLY MEASURED.** Both instances' mechanisms established by reading the guards; class decomposed into two sub-shapes; a detector demonstrated with recall 1/1 on A, 0/1 on B, precision unknown (18 of 19 candidates un-adjudicated). Enumerability verdict: **not with the current guard structure**, and the reason is stated. |

## Filed

- `BL-20260908-PUSH-DENIED-AND-WRONG-REPO-ARE-ONE-DEFECT-3-OF-3-AND-A-ONE-CALL-PREDICTOR-EXISTS`
- `BL-20260908-A-WORKING-REGISTRY-ROW-HAS-A-MEASURED-HALF-LIFE-OF-61-MINUTES-SO-ANY-DECAY-RULE-SLOWER-THAN-THAT-IS-WRONG-ABOUT-HALF-ITS-ROWS`
- `BL-20260908-NO-GUARD-DECLARES-ITS-SATISFYING-SET-SO-A-DEADLOCKED-PAIR-CAN-ONLY-BE-FOUND-BY-HITTING-IT`
