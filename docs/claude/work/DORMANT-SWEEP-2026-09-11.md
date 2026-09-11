# The dormant-work-object sweep — MI-243, 2026-09-11

> **Doc status:** `live` · category `evidence` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

**Operator-directed 2026-09-10**, reading the deployed SPA Work page:

> *"there are over 600 dormant work items - we need a specific session in the ops lane (when opens ups) to work through the list and update it - these things either need to closed or qued (for future sessions, it just doesn't make sense that our workflow has this many dormant items) - if some of them are stuck on soaks/observations, then we need a status the describes that"*

This file is the **audit trail** for that sweep. Its purpose is that a later session can
**check the rules rather than inherit an unexplained state change**. Every cohort rule is
stated with the test it applied and the number of rows it moved.

---

## 0. Population — stated once, used throughout

**All 710 `docs/claude/work/objects/*.yaml` at `main` `4e9255acb`, read 2026-09-11.**
All 710 parsed; **0 parse failures**. The operator's page read (708 objects: 6 in flight,
18 ready, 24 waiting, 623 dormant, 37 done) is the same store one day earlier.

| `lifecycle` | before | after | moved by |
|---|---|---|---|
| `dormant`   | **622** | **555** | −21 (Rule 1), −46 (Rule 3) |
| `ready`     | 18 | **60** | +42 (Rule 3) |
| `done`      | 38 | **63** | +21 (Rule 1), +4 (Rule 3) |
| `waiting`   | 25 | 25 | unchanged |
| `in_flight` | 7 | 7 | unchanged |
| `accepted`  | 0 | 0 | unchanged |

⚠️ **READ THE PER-RULE COUNTS, NOT THE DELTA.** A lower dormant number is trivially
achievable by closing rows nobody read, which is the harm this sweep was told to avoid. The
delta is **67**; what matters is that **21** of those closures are checkable against a
backlog row's own terminal status and **46** are individual reads, not a bulk pass.

⚠️ **AND SAY THE UNCOMFORTABLE PART PLAINLY: 555 rows are still `dormant`, and this sweep
did not reduce that number because it could not do so honestly.** 554 of them are one
cohort with one unanswered question — see **Rule 2** and **§ 3**. That question is put to
the operator rather than decided here.

---

## 1. The cohort split, established before any row was touched

**575 of the 622 dormant rows are Phase-C migrants.** The test is exact, not a guess: their
`blocked_on_basis` begins `NOT_ASSESSED — migrated in bulk 2026-09-01`. The remaining **47**
are not migrants.

**Every one of the 575 joins to a live row in one of the four review backlogs** by
`source.row_id` — **0 unmatched**. That total match is the **positive control** that the
join works; a partial match would have meant the probe was broken, not that rows were
missing.

⚠️ **A correction this sweep made to its own first reading, recorded because it changes a
number:** the field is `source.status_at_migration`, **not** `source.status`. A first pass
read the wrong key and got `None` for all 575, which would have made every row look like it
had no recorded prior state. The status-at-migration distribution is
`open` 325 · `kept_open` 229 · and the 21 that have since moved.

---

## 2. Rule 1 — "migrant-terminal" → `done` · **21 rows**

**THE TEST:** the row is a Phase-C migrant **AND** the backlog row it mirrors now carries a
**terminal** status (`resolved` · `wont_fix` · `superseded` · `invalid`), read from the
backlog on 2026-09-11. Non-terminal statuses are `open` and `kept_open`; `kept_open` means a
review triaged the row and deliberately left it open, so it is **not** terminal.

**Movement:** `open → resolved` 12 · `kept_open → resolved` 6 · `kept_open → wont_fix` 3.

**WHAT THE CLOSURE CLAIMS:** only that the finding's own state of record reached a terminal
status. The backlog row is the state of record for the **finding** — this object's own
`source.note` says so. **The sweep did not re-verify any fix on the fleet**, and none of
these 21 rows may be read as a fleet observation. If a backlog row was closed in error, the
error is there; this follows it rather than adding a second independent claim.

---

## 3. Rule 2 — "migrant-live" → **UNCHANGED** · **554 rows**

**THE TEST:** a Phase-C migrant whose backlog original still reads `open` (325) or
`kept_open` (229).

**THESE WERE NOT TOUCHED.** Not closed — the finding is live. Not queued — nobody formed an
intent to work them, and moving 554 rows to `ready` would make the ready list unreadable and
assert an intent nobody formed. No `blocked_on` edge was invented on any of them.

**THE STRUCTURAL FINDING, which is why the dormant count is still 555.** These rows are
**1:1 mirrors of a register that already exists**. The migration's own note draws the line:
*"The backlog row remains the state of record for the FINDING and its updates; this object
is the state of record for the WORK of dealing with it."* For these 554 there **is no
work** — no owner, no intent, nothing scheduled. So the object is the state of record for
work that does not exist, and 554 such objects are what render on the operator's Work page
as the pile they asked about.

**The question that would resolve it is not this session's to answer**, because it reverses
a Phase-C decision and changes what the work store means. It is filed as a
`decision_requests` block on
[`WO-20260911-MI-243-DORMANT-SWEEP.yaml`](objects/WO-20260911-MI-243-DORMANT-SWEEP.yaml).

⚠️ **Whatever is chosen, it must not be described as "closing 554 items."** Nothing about
the findings changes; only whether the work store carries a second copy of them.

---

## 4. Rule 3 — "non-migrant, individually read" → **47 rows**

**THE TEST:** a dormant object that is **not** a Phase-C migrant gets an individual read,
because it has no shared provenance to grade as a group. Graded into exactly one of:

| outcome | n | basis |
|---|---|---|
| `done` | **4** | checkable completion evidence, cited on the row |
| `ready` (queued) | **42** | live, operator-directed work with a clear `done_condition` and no completion evidence |
| left `dormant` | **1** | could not be graded |

**THE ASYMMETRY IS DELIBERATE:** `ready` is the default and `done` needs evidence. A row
wrongly queued is corrected by whoever picks it up; a row wrongly closed is never looked at
again.

**The 4 closed, with the evidence that closed them:**

| row | evidence |
|---|---|
| `WO-20260907-PROPOSAL-A-…-POSITION-TELEMETRY` | `OI-20260907-TELEMETRY-HOOK-…` reads `clears_when: ALREADY CLEARED` — PR merged `115b0c25`, and a real telemetry row **observed** on 2 of 9 legs |
| `WO-20260908-LAND-THE-TWO-OPERATOR-APPROVED-PRS-THE` | #11279 merged 2026-09-08T06:58:07Z, #11312 merged 07:03:59Z, both `merged_by: benbaichmankass` (GitHub API, read 2026-09-11) |
| `WO-20260903-REVIEW-10793-ON-ITS-MERITS-THE-ONE` | `OI-20260903-OPERATOR-COMMANDS-MOVED-OFF-THE-TRADER-BOT-…`: #10904 approved, merged `0cc217ad`, deployed; *"#10793's violation of its own title … is ENDED on the fleet"* |
| `WO-20260906-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` | **a DUPLICATE, not a completion** — named as such in #11279's own body; the work lives on the `-0907-` row |

**The 1 left dormant:** `WO-20260902-NIGHT-SHIFT-2026-09-02-03-FINISH` — a time-boxed plan
whose window elapsed nine days ago. It cannot be satisfied as written and nobody will work
it, but marking it `done` would assert a night's work this sweep did not establish.
***We did not look* is a legitimate outcome and was not laundered into `closed`.**

**⚠️ `ready` here means QUEUED, NOT STARTED, AND NOT ASSESSED FOR BLOCKERS.** No edge was
invented for any of the 42.

---

## 5. Rule 4 — "accrual is not an external event" → the operator's second ask

**THE ANSWER IS THAT NO SEVENTH `lifecycle` VALUE IS NEEDED, and the checklist row was right
to insist `waiting` be examined first.**

**`data_accrual` is ALREADY a declared `blocked_on` kind** — `docs/claude/work/README.md`
lists the vocabulary as `object · operator_decision · external_event · data_accrual ·
capability`, and `scripts/ops/constraint_readout.py:234` names it. **MEASURED over all 710
objects on 2026-09-11: it was used by ZERO of them.** The vocabulary for "stuck on a soak or
an observation" existed and had never once been exercised.

**It is already correctly excluded from the person-blocked count.**
`constraint_readout.operator_block` collects **only** `kind == "operator_decision"`, so a
soak-bound row is **not** counted as blocked on a person — which is exactly the property the
checklist row required of any new value.

**THE ACTUAL DEFECT: `external_event` was carrying two different facts.** Some of its refs
name a genuine external event (a PR merging, an operator deciding, a session acting); others
name **data or an observation accruing on the fleet that no person can schedule**. That is
the collapsed-state defect this repo has a guard family for, one level below `lifecycle`.

**THE TEST APPLIED:** an edge whose `ref` names data or an observation accumulating, needing
nobody to act, is `data_accrual`. Only the `kind:` value was changed — `ref`, `since` and
the note are untouched, so no new claim was introduced.

| row | what changed | justified by |
|---|---|---|
| `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` | first edge re-pointed (its other two are untouched) | its own note: the change reaches the trader *"without anyone doing anything"* and what remains *"is an OBSERVATION"* |
| `WO-20260909-DECISION-INTENT-REDUCE-ORPHANS-…` | its single edge re-pointed | its own note: *"an event NOBODY CAN SCHEDULE"*, *"DO NOT MANUFACTURE THE EVENT"*, *"the wait may be days"* |
| `WO-20260906-ICT-SCALP-5M-TO-SHADOW-…` | **new** edge; basis `NOT_ASSESSED` → `ASSESSED` | #11121 merged 2026-09-06T21:27:22Z, so part (1) now waits on evidence, not work; the edge names the **positive-control denominator** #11121's own body says is required |

Both re-points are justified **from the edges' own notes**, not from this sweep's reading of
a title. `data_accrual` went **0 → 3**.

⚠️ **WHAT THIS DOES NOT YET BUY, stated rather than implied: nothing BRANCHES on
`data_accrual` today.** `grade_edge` treats it exactly as it treats `external_event`
(`not_in_store_by_design`). The re-point makes the **record** true; making it **visible** —
the operator's third criterion — is a **renderer** change, and `/api/bot/work` already ships
`blockedOn[].kind` per object, so the data is there. **That is a renderer ask, not a schema
widening**, and no `lifecycle` change was made.

⚠️ **A conservative call, recorded:** `WO-20260909-DECISION-CLOSED-FLAT-INVARIANT-…` was
**left** `external_event`. Its ref requires a **PLANTED** violation, and planting is an act.

---

## 6. Findings this sweep made and did not fix

1. **The `lifecycle` vocabulary has no terminal value meaning *superseded / duplicate /
   abandoned*.** Forced on two rows: a known duplicate had to be marked `done` (with a
   verdict shouting that the work is NOT complete), and an expired time-boxed plan had to be
   left `dormant` because every other value would have been a false claim.
2. **Five `blocked_on` kinds in live use are outside the declared vocabulary** — `pr` (2),
   `backlog_row` (2), `work_object` (1), `review` (1), `artifact` (1). `work_object` is
   almost certainly meant to be `object`. Measured over all 710 rows, 2026-09-11.
3. **`waiting` is used for rows whose own `blocked_on_basis` says `ASSESSED … nothing blocks
   it`.** Of the 25 `waiting` rows, **10 carry no edge at all**, several of those explicitly
   asserting nothing blocks them — which is the definition of `ready`, not `waiting`. Not
   changed here: it is outside MI-243's dormant population and deserves its own read.
4. **The genuinely accruing register is a different file, and the Work page does not render
   it.** `docs/claude/OPEN-ITEMS.json` holds **57 `monitoring` + 11 `awaiting_verification`**
   rows — soaks running and observations pending. Only **26** work objects name an
   OPEN-ITEMS id at all, and only **2** of those are dormant. **That is why the operator saw
   623 dormant and no "accruing" status: the accruing work was never in the pile they were
   looking at.** The probe found 26 hits across four lifecycles, which is the positive
   control that it was capable of finding them.

---

## 7. What a later session should check

- The 21 Rule-1 closures against the backlog rows they cite — the rule is re-runnable.
- The 42 Rule-3 promotions: each is queued, **not** assessed for blockers. Write a TRUE edge
  before moving one to `in_flight`.
- **The Rule-2 decision.** Until it is answered, 554 dormant rows are the expected state and
  must **not** be re-filed as a defect or swept again on a different rule.
