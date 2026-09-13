# MI-235 / PR #11767 — adversarial review of a landing-machinery change

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Review lane** `session_01PkGrU3GSYiWYAUyF99pEQi`, spawned by manager
`session_01HrmZ1RRNM4UnEUaFdrPEjj`. PR **#11767** ("MI-235: tell a blocked lane
when its blocker clears") is green and declares `landing: hold` /
`hold_reason: changes_landing_machinery` — pr-landing **R12**. The manager may
not merge it, because the guard that would catch a mistake in it is the thing
being changed. The operator was offered three options and chose a review lane.

**This report does not merge anything and recommends that the operator does not
merge #11767 as it stands.** One finding is load-bearing: *the mechanism does
not work in the cross-repo case the PR itself cites as its motivating
incident.* It is a one-line fix, verified below.

---

## POPULATION — what was examined, and what was not

**Object reviewed:** PR #11767, head **`5c97a5edbc3a781500dc5fb4c360654ac390bfea`**,
base **`7a5fb24f424419b01fcb71d94db5e097042a9213`**, 11 files, **+1649 / −7**.
Read 2026-09-11. The head matters: MI-235 reported on the board at 11:41Z that
the branch went conflicted and was rebuilt `15d0a882b` → `5c97a5edb`. Everything
below is that head. **A later push invalidates this population, not its method.**

| | |
|---|---|
| **Diffs read in full** (7 of 11 files) | `scripts/ops/session_registry.py` (+139/−0) · `.github/workflows/pr-queue-watch.yml` (98 changed) · `scripts/ci/check_collapsed_states.py` (+41) · `CLAUDE.md` (5) · `docs/DOCUMENT-INDEX.md` (1) · `.github/pr-landing/mi235-blocked-lane-watch.json` (7) · `docs/claude/work/BLOCKED-LANE-WATCH.json` (21) |
| **Read in part** | `scripts/ops/blocked_lane_watch.py` (873 new lines) — imports, `collect_world`'s PR-state/coverage block, `grade_blocker`'s `pull_request` arm, `_latch_key`, `assess`, `_prior_latch`, the receipt builder. **I did NOT read the `branch` / `path_on_main` / `work_object` / `operator_decision` resolver arms line by line.** · `tests/test_blocked_lane_watch.py` (336 lines) — the latch section and the three mutation-test names; **I did not read all 30 tests.** |
| **NOT read** | `docs/claude/blocked-lane-watch.md` (112 lines) · the `docs/claude/OPEN-ITEMS.json` diff as JSON (I read only its rendering in the `CLAUDE.md` brief) |
| **NOT attempted at all** | any live or fleet observation. No VM read, no diag pull, no trainer relay. **No order placed, modified or cancelled on any account.** I did not push to `claude/mi235-blocked-lane-watch`, did not modify the landing machinery, and did not merge anything. |
| **Could not run** | **`pytest` is absent from this container** — verified here (`ModuleNotFoundError: No module named 'pytest'`), not inherited from the board's 2026-09-08 note. So I could not execute the PR's 30 tests or any pytest-invoking guard. Those are *could not look*, never *green*. |

**Ran** (every verdict below is from execution, not from reading):
`scripts/ci/guard_selftests.py pr-landing` · `scripts/ci/check_pr_landing.py
--base origin/main` (twice — detached, then with a real branch context) ·
`scripts/ops/blocked_lane_watch.py --self-test` ·
`scripts/ci/check_collapsed_states.py` · two planted declaration defects against
the real diff · a shell reproduction of the new workflow step · three direct
`blocked_lane_watch.assess()` experiments with a positive control.

**Dispatch-brief correction, then its own correction (RULE ONE, applied to my
own output).** My brief named work object
`WO-20260911-ADVERSARIALLY-REVIEW-11767-THE-BLOCKED-LANE-NOTIFIER.yaml` on
`claude/manager-tick-1240z-20260911` (PR #11797).

- **At 12:4xZ that file did not exist** — not on that branch (tip `2143bb209`)
  and not on `main`. Positive control: 163 objects were readable on that branch
  and 6 carried the `20260911` date, so the read and the pattern both worked;
  the object was genuinely absent, not unreadable. Per the brief I did **not**
  recreate it, and proceeded from the task spec in the prompt.
- ⚠️ **At 13:1xZ it IS on `main`** — it landed at 13:01 in the manager's
  dispatch PR **#11798** (`0106a54bd`), on a *different* branch
  (`claude/manager-lanes-1248z-20260911`) from the one my brief named. So the
  brief's path was wrong and its existence claim became right while I was
  working.

**Both readings are recorded rather than the first being quietly deleted**, for
the reason this repo already writes down about watched deploys: the
negative-then-positive pair is the evidence that the absence was *measured* and
not assumed, and a session that later finds the object would otherwise read my
first paragraph as simply wrong. Having read it, its `done_condition` is the four
sections below plus the population block above, its `owner` is this session, and
its `blocked_on: []` carries `blocked_on_basis: ASSESSED` with an explicit note
**not** to add an `operator_decision` edge — because the decision this work
serves (whether to merge #11767) is the report's OUTPUT, not its precondition.
This report is written to that condition.

---

## (a) What the diff actually changes about the landing route

**PATHS.** Of the 11 files, exactly **two** are on
`check_pr_landing.py::LANDING_MACHINERY`, and one of those is excluded by the
guard itself:

| path | on LANDING_MACHINERY? | effect |
|---|---|---|
| `.github/pr-landing/mi235-blocked-lane-watch.json` | matches `.github/pr-landing/*.json` | **excluded by the caller** — every branch writes its own declaration, and an excuse every branch satisfies excuses nothing |
| **`scripts/ops/session_registry.py`** | **yes** | **the sole reason R12 fires** |
| the other 9 | no | `pr-queue-watch.yml`, `check_collapsed_states.py`, `blocked_lane_watch.py`, tests, docs, `CLAUDE.md`, `OPEN-ITEMS.json`, `BLOCKED-LANE-WATCH.json`, `DOCUMENT-INDEX.md` |

Not inferred from reading the list by eye — **measured from the guard's own
output**, run against the real branch:

```
$ GITHUB_HEAD_REF=claude/mi235-blocked-lane-watch \
    python3 scripts/ci/check_pr_landing.py --base origin/main
  note: R8 verified — landing machinery in the diff: scripts/ops/session_registry.py
pr-landing: OK — state=declared_hold
```

⚠️ **None of the landing-route DECIDERS is modified.** `git diff --name-only`
over `check_pr_landing.py`, `guard_selftests.py`, `check_automerge_trigger.py`,
`claude-pr-automerge.yml`, `commit-to-main/action.yml` and `claim_merge_slot.py`
returns **empty**. No rule, no path list, no tier table, no arming logic and no
merge call changes.

**BEHAVIOURS.** Two, both additive:

1. **A new `session_registry.py blocked-on` subcommand** (`cmd_blocked_on`, +~110
   lines) that appends a typed `{kind, ref, clears_when, since}` edge to a row in
   `docs/claude/work/SESSIONS.json`, refuses a `kind` with no resolver at write
   time, is idempotent on an identical edge, and offers `--clear`. Purely new:
   no existing function is modified, and the file's diff is **+139/−0**.
2. **A new paragraph in the spawn-prompt template** that every future
   sub-session reads, instructing a blocked lane to run that command and push.

**Why a `session_registry.py` change is a landing-route change at all** — this is
the part that is easy to get wrong, and the PR author initially did. It is not
about code that merges PRs. `check_pr_landing.py`'s own docstring records that
the spawn prompt *"ended, unconditionally and at every tier, with 'Open the PR as
a DRAFT; the manager merges'"*, and that this convention is what left seven
green Tier-1 PRs unlanded on 2026-09-03. **The spawn prompt IS the text that
tells every future lane how to land.** A change to it propagates to every
session spawned afterwards, and no diff-grading guard can read its semantics.

Beyond the machinery list, the diff also changes one existing workflow's
behaviour: `pr-queue-watch.yml` gains a mid-job step, and its receipt-landing
step now passes **two** paths to `commit-to-main` instead of one. (That second
part is safe — `commit-to-main` consumes `${PATHS}` unquoted as a
space-separated pathspec, and default-IFS word-splitting handles the YAML block
scalar's newlines. Checked, because a broken receipt landing would have degraded
an existing escalation channel.)

---

## (b) What R12 protects, and whether this diff can damage it

**R12 in its own words:** a PR that edits the landing machinery *"may not
self-land, because the change and its own approval would be the same act, and a
mistake in it disarms the very check that would have caught the mistake."* What
it protects is therefore not correctness in general — it is the **independence**
of the approving mechanism from the approved change.

**The direct answer: this diff cannot damage the landing guard.** No decider is
touched; R12 and R8 both grade it correctly; the guard's 15 planted defects all
still fail it (see (c)). On the narrow question R12 asks, #11767 is clean.

**But R12's real subject is the second-order case — a change that is CORRECT and
makes a FUTURE mistake harder to catch — and on that question the diff has three
effects, one of which is serious.**

### b1 · The new step can disarm the PR-queue alarm it was placed not to disturb — **SHOULD FIX**

The surrounding workflow has an explicit, deliberate architecture, stated in its
own comments: **self-tests at the top; the assessment never fails its step
("Exit 3 and 4 are NOT step failures here — the receipt must land before the job
reports, or a backed-up queue would also lose the record of itself"); the receipt
lands; the report escalates at the end.**

The new step is inserted *between* the assessment and the receipt landing, and it
carries its own self-test as `python3 scripts/ops/blocked_lane_watch.py
--self-test || exit 2`. Its inline comment asserts **"⚠️ THIS STEP NEVER FAILS
THE JOB."** That is false on that path, and I reproduced the shell semantics
rather than reasoning about them:

```
STEP EXIT CODE = 2   (non-zero => GitHub marks the step FAILED)
GITHUB_OUTPUT contents: []
-> branch "") ::warning:: 'did not run' — a WARNING, not an error
```

Three consequences, all measured from the YAML (only **one** step in the file
carries `if: always()`, and it is the new report step at line 301; steps 207,
235 and 281 carry no `if:` at all):

1. `docs/claude/work/PR-QUEUE-WATCH.json` **does not land** — the artifact
   `check_pr_queue_watch.py` grades for staleness.
2. The existing **"Report the verdict" PR-queue escalation (line 281) is
   skipped entirely** — its `::error::` never prints on that run.
3. The report step's **`2)` branch, written for exactly this case, is
   unreachable**: `exit 2` precedes `echo "rc=$lane_rc"`, so `rc` is empty and
   the weakest of the five branches fires — a `::warning::` saying "did not
   run", not the `::error:: SELF-TEST FAILED` the author intended.

*Field beats comment.* Probability is low (the self-test is stdlib-only and
passed in two environments, and the `import yaml` is guarded), but **R12 exists
for exactly the low-probability, high-consequence case in landing-adjacent
machinery** — and this is that case: a fault in the new grader silently removes
an established alarm. Consequence compounds with a documented reading: `CLAUDE.md`
tells sessions that a red `pr-queue-watch` run means *the queue is unworked*.
After this diff a red run may instead mean a blocked-lane wake, or this. That
guidance is not updated.

**Remedy (matches the existing design exactly):** move
`blocked_lane_watch.py --self-test` into the existing self-test step at line 88,
where every other self-test already lives, and drop `|| exit 2` from the new
step. Then `rc` is always set, no failure path can skip the receipt landing or
the PR-queue report, and the `2)` branch can be deleted as the dead code it is.

### b2 · The spawn prompt contradicts the line directly above it — **SHOULD FIX**

The new paragraph is inserted immediately after this existing text:

> You are registered as `{registry_ref}` in `docs/claude/work/SESSIONS.json`.
> If you learn something that changes your row's scope, say so in your PR body —
> **the manager owns that file.**

…and then instructs every future lane to run a command that **writes that same
file** and to "push it". The PR body itself declines to touch `SESSIONS.json`
because *"editing them here would guarantee a conflict on a shared register"* —
so **the diff's own stated reason for avoiding the file is the hazard it now
instructs every future lane to incur**, at a rate that scales with the number of
lanes. This lands in the landing-route prompt, which is the surface R12 protects.

The mitigation is real but not in force: `SESSIONS.json` is `merge=jsonregister`
in `.gitattributes`, so an **armed** clone auto-resolves. Per MI-235's own board
note at 11:41Z the driver **ships unarmed in every fresh container**
(`BL-20260906`), and merge drivers are client-side — GitHub still grades a PR
`dirty`. **Remedy:** say in the prompt that this is the one exception to "the
manager owns that file", and name `bash scripts/ops/install_merge_driver.sh` in
the same breath. (Or have the lane declare on its own branch and let the manager
land it, which removes the hazard entirely.)

### b3 · A lane can now refresh the manager's supervision record — **NOTE, low severity**

MEASURED. On the base, `session_registry.py` assigns `doc["updated_at"]` at
exactly two sites (lines 794 and 838), inside `register()` and `confirm()` —
**both manager acts**. The diff adds a **third** (line 1134) inside
`cmd_blocked_on`, a **lane** act the new prompt tells every lane to run.

`check_manager_scope.py` **R6** reads `SESSIONS.json::updated_at` at the
manager's heartbeat commit and fails when the gap exceeds the lease's own
`ttl_minutes` (90). So after this lands, the *record* of supervision can be
refreshed by the **supervised party** — R6's fire rate can only fall, against a
calibration of 2 of 50 lease revisions.

⚠️ **Honest qualifier, and it is why this is a note rather than a finding:** R6's
own text already says it *"measures the RECORD of supervision, never supervision
itself"*. This widens a limit the guard declares, rather than breaking a claim it
makes. Minor asymmetry worth one line while someone is in the file: `--clear`
does **not** bump `updated_at` while declaring does, so discharging a blocker
leaves the freshness stamp behind.

### b4 · `rows[0]` with no multiplicity check — **NOTE, cheap fix**

`cmd_blocked_on` does `rows = [r for r in registry_rows(doc) if
r.get("session_id") == a.session_id]` then `row = rows[0]`. This file already
documents being bitten by precisely that: its own `_mint_registry_key` docstring
records that `pending-20260902T133456Z` was shared by THREE rows and
*"`confirm()` matched the first of them, so confirming any one of the three would
have written that session's id onto a DIFFERENT session's row."* The same file
carries `_refuse_duplicate_session_id`, whose docstring states the principle:
*"Uniqueness is a property of the SET, so it is checked against the set."*
**Remedy:** refuse when `len(rows) > 1` rather than taking the first.

---

## (c) Do the guard's own self-tests still fail on a planted defect? — **YES, established by RUNNING them**

```
$ python3 scripts/ci/guard_selftests.py pr-landing
self-test OK — 6 positive controls hold and all 15 planted defects fail the guard
EXIT=0
```

All 15 plants still fail, **including `R12 self-landing a change to the landing
machinery`**, and all 6 positive controls still hold.

⚠️ **That result is necessary and not sufficient, and it would be dishonest to
present it as the verification of this PR.** The guard and its self-test harness
are **not modified by this diff** (measured: empty `git diff --name-only`), so an
identical result on `main` is the expected outcome. It establishes *the guard is
intact*, never *the change was checked by the guard*.

⚠️ **And one of the two mandated commands graded nothing.** On a detached HEAD:

```
pr-landing: SKIPPED — not a PR context, so nothing was graded. This is not a pass.
EXIT=0
```

Exit 0 there is the guard correctly refusing, and it says so itself. Re-running
with `GITHUB_HEAD_REF` is what produced the real verdict quoted in (a).

**So I planted defects myself, against the real diff** — the stronger test:

| plant | result |
|---|---|
| flip `landing: hold` → `self` (the author's own admitted mistake) | **R12 FIRES**, naming `scripts/ops/session_registry.py`; R6 also fires. **EXIT=1** |
| keep `hold`, swap `hold_reason` → `awaiting_evidence` | **passes, exit 0** |

The second is a **pre-existing property of the guard, not a regression in
#11767**: R8 verifies `changes_landing_machinery` when *claimed*, but nothing
requires a PR that *does* touch the machinery to name that reason. The
protection — the hold — is intact either way, so the safety outcome is
unaffected; what is lost is that the machinery fact is then not surfaced to the
human at all (the `R8 verified — landing machinery in the diff: …` note
disappears). One line, someone else's PR.

Also run clean: `blocked_lane_watch.py --self-test` → **OK**;
`check_collapsed_states.py` → **clean (43 contracts)**. The new contract
`blocked_lane_watch.blocker_state` is registered honestly and passes; its
"consumer" is intra-module plus tests (the four constants appear only in the
producer, the guard's own table, and the test file), which matches the
`qty_legalize.venue_max_state` precedent sitting directly below it in the same
table and is declared in its own comment. Not a finding.

### ⛔ c-BLOCKING · The mechanism does not fire in the cross-repo case the PR cites as its motivating incident

Found by reading `_latch_key` against `assess`, then **confirmed by execution
with a positive control.** The latch key is

```python
f"{session_id}|{verdict.get('kind')}|{verdict.get('ref')}|{verdict.get('clears_when')}"
```

— **the STATE is not part of the key**, while `assess` latches *both* `cleared`
and `could_not_look` into that one namespace. So once a blocker has paged in
**either** state, it can never page again — including the transition
`could_not_look` → `cleared`, which is the only transition that matters.

```
RUN 1  (foreign repo listing unreadable)  could_not_look:1   wakes: 1   latch written
RUN 2  (listing now covers it; #215 absent ⇒ genuinely closed)  cleared:1   wakes: 0   ← SUPPRESSED
CTRL   (identical world, empty latch)                          cleared:1   wakes: 1
```

The control proves the suppression is the **latch**, not the grading: the row
grades `cleared` correctly in both runs and pages in only one.

**This is not hypothetical — it is the PR's own worked example.** MI-238, one of
the three incidents in the PR's evidence table, is a cross-repo blocker
(`ict-trader-dashboard#215`) that the PR's own table grades `could_not_look`. Two
independent routes reach that first state: the workflow's dashboard listing is
best-effort (`|| { … rm -f /tmp/dash-prs.json; }`), so a single transient
`gh api` failure on any run burns the latch; and a foreign repo with **zero**
open PRs never enters `pr_states_repos` at all, because `covered` is seeded with
`{this_repo}` only. Either way: **page once about blindness, then silence
forever about the clear.** That is the 3.5-day failure MI-235 was built to end,
reachable through MI-235's own latch.

**It is invisible to the whole test suite, and one test name actively conceals
it.** `test_the_latch_pages_once_and_a_new_clear_still_pages` reads as exactly
the property disproved above; what it asserts is that a **different** session
with a **different** blocker still pages. No test drives a state transition on
the same blocker, and none of the three mutation checks touches latch-key
composition (they target the cross-repo guard, the any-vs-all rule, and grading a
failed read as clear).

**Remedy — one line, and I verified it both ways:**

```python
return (f"{session_id}|{verdict.get('state')}|{verdict.get('kind')}"
        f"|{verdict.get('ref')}|{verdict.get('clears_when')}")
```

```
run1 could_not_look -> wakes=1   (want 1)
run2 cleared        -> wakes=1   (want 1 — THE FIX)
run3 cleared again  -> wakes=0   (want 0 — anti-fatigue property intact)
```

The desensitised-alarm argument is **untouched**: each `(blocker, state)` pair
still pages exactly once. The accepted cost is that a blocker flapping between
the two states could page at most twice — bounded, and on this module's own
declared bias (*"a spurious wake costs one turn and a missed one cost 3.5
days"*) that is the correct trade. It needs a test driving
`could_not_look → cleared` on one blocker, and the existing test renamed to what
it actually asserts.

---

## (d) Recommendation — actionable in one read

**Do not merge #11767 as it stands. Return it to MI-235 (or a fresh lane) for
one short round — not a redesign.** The hold is correct, R12 is working, and the
diff is unusually well evidenced; what is wrong is one substantive bug and one
workflow-placement error, both small and both verified fixable.

| # | item | severity | fix |
|---|---|---|---|
| 1 | **Latch key omits the state**, so a blocker graded `could_not_look` first never pages when it clears — the PR's own MI-238 case | **BLOCKING** | one line in `_latch_key` + a test for the transition + rename the misleading test |
| 2 | Mid-workflow `\|\| exit 2` fails the step, skipping the receipt landing **and** the existing PR-queue escalation; its `2)` report branch is unreachable; its comment says the opposite | **SHOULD FIX** | move the self-test to the existing step at line 88; drop `\|\| exit 2`; delete the dead branch |
| 3 | Spawn prompt tells every lane to write `SESSIONS.json` one line after "the manager owns that file" — the hazard the PR itself avoided | **SHOULD FIX** | name it as the exception and cite `install_merge_driver.sh`, or declare on the lane's own branch |
| 4 | `rows[0]` with no multiplicity check, in a file that records being bitten by exactly that | **NOTE** | refuse `len(rows) > 1`, reusing the file's own precedent |
| 5 | `cmd_blocked_on` is a third, **lane-triggered** writer of the `updated_at` that feeds `check_manager_scope` R6 | **NOTE** | decide knowingly; R6 already declares it measures the record, not supervision. `--clear` not bumping it is a one-line inconsistency |

**What a clean bill would have looked like, and why this is not one:** items 2–5
alone would have been a "merge it, fix on the way past" report. Item 1 is not —
it means the mechanism would have landed, read as working, paged once about its
own blindness, and then silently failed to do the one thing it exists to do, in
the case the PR chose as its headline example. A green suite, a green guard run,
and a correct R12 hold are all compatible with that, which is the whole argument
for R12 and for this lane.

**R12 earned its keep here.** The hold is not bureaucracy: the author's first
declaration said `landing: self`, the guard refused it, and the refusal is what
put a human in front of a diff containing item 1 — which no guard in this repo
could have found. I planted that same mistake back and watched R12 fire.

**Two things this review did not do, stated so nobody reads them as cleared:** I
could not run the PR's 30 tests (`pytest` absent here), and I made **no** live or
fleet observation — so
`OI-20260911-BLOCKED-LANE-WATCH-SHIPPED-AND-NO-LANE-HAS-BEEN-WOKEN-BY-IT` is
untouched by this report and remains entirely owed. Note that item 1 would have
made that row's clause (b) unsatisfiable in the cross-repo case while the row
still read as merely *awaiting* an observation.
