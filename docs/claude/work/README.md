# The work store — one place that says what is in flight

**This directory is the state of record for WORK** (function F1 of the operating model).
It is the repo half of the design series' answer to *"where does coordination state live"*:
**the repo is the single source of truth; the live layer owns no truth at rest.**

- Design: [`docs/design/operating-model-DESIGN.md`](../../design/operating-model-DESIGN.md) ·
  [`…-schema-and-state-DESIGN.md`](../../design/operating-layer-schema-and-state-DESIGN.md) ·
  [`…-function-derivation-DESIGN.md`](../../design/operating-layer-function-derivation-DESIGN.md) ·
  [`…-build-plan-DESIGN.md`](../../design/operating-layer-build-plan-DESIGN.md)

## Three levels

| Dir | Level | What it is |
|---|---|---|
| `intents/` | **INTENT** | A direction committed to, expressible before it decomposes. Hand-written, few, readable in full. |
| `objects/` | **WORK OBJECT** | A **question** we want answered or a **commitment** we have made. **The WIP ceiling of 8 counts these, in flight.** |
| `steps/` | **STEP** | The smallest assignable thing — one session's worth. **Cannot exist without a parent object.** |

**One file per object.** Two sessions touching different work never conflict. This is the
concurrency lesson `scripts/ops/backlog_append.py` was written for: a naive
read-append-write on a shared JSON reformats the file and buries a one-row change in a
47,000-line diff. `research/queue/*.yaml` is the existing precedent.

## The rules that keep it honest

**`lifecycle` is never collapsed** — `dormant` · `ready` · `in_flight` · `waiting` · `done` ·
`accepted`. *Not started*, *blocked*, *being worked* and *waiting on the operator* are four
different facts, and rendering them identically is the collapsed-state defect this repo has
a CI guard for.

**`blocked_on` is a typed edge, not a flag.** Each entry is `{kind, ref, since}` with `kind`
∈ `object` · `operator_decision` · `external_event` · `data_accrual` · `capability`. This is
what lets the constraint be **computed** rather than judged — where the system is held up
falls out of the graph. ⚠️ **An empty `blocked_on` is a claim that nothing blocks this**, not
an absence of information; if it is unknown, say so in the row rather than leaving it empty.

**A verdict states its population.** Any quantitative claim in a `verdict` or `basis`
carries its population and its basis (MEASURED / INFERRED / DECIDED).

## What is NOT here

**Bugs to fix** still go to the three review backlogs (`docs/claude/*-review-backlog.json`),
filed through `scripts/ops/backlog_append.py::append_row`, never by hand. **What a session
must KNOW before it plans** is still `docs/claude/OPEN-ITEMS.json`. This store is neither —
it is what is being *worked*, and what it depends on.

## The migration — what landed, and what it does NOT mean

**Phase C migrated the carried rows on 2026-09-01, together with the WIP ceiling**, because
rows arriving without a cap would render as hundreds of things in flight — the condition the
redesign exists to end.

Measured at migration (population = rows with `status` in `open`/`kept_open`, across the four
review backlogs; run `python3 scripts/ops/migrate_backlog_to_work_objects.py` to re-measure):

| Source | Carried in | Of total |
|---|---|---|
| `health-review-backlog.json` | 497 | 1,062 |
| `performance-review-backlog.json` | 45 | 111 |
| `ml-review-backlog.json` | 22 | 106 |
| `research-review-backlog.json` | 11 | 12 |
| **Total migrated** | **575** | 1,291 |

Store afterwards: **583 objects, 2 `in_flight`.**

⚠️ **READ THE `lifecycle`, NOT THE COUNT.** 583 objects is not 583 things in flight, and a
view that renders it that way has misread the store. Every migrated row arrived `dormant` —
**carried, not started, and not queued**. A row becomes `in_flight` only when given the three
things migration deliberately does not give it: an **owner**, a real dependency **edge**, and
a place under an **intent**.

⚠️ **`blocked_on: []` on a migrated row is NOT the claim that nothing blocks it.** Each one
carries `blocked_on_basis: NOT_ASSESSED` saying exactly that. No edge was derived in bulk and
none is claimed. **Write a true edge before moving a row out of `dormant`** — an invented edge
is read by the constraint computation as a real blocker, and *a false blocker is worse than a
missing one*: on 2026-09-01 three edges written as `object: WO-PHASE-A`, when the real
dependency was "the store exists", made the graph report two phases blocked that were
available.

**Absences were carried through, not filled in.** Of the 575: 39 have no title, 154 no
`opened_at`, and 51 no `resolution_criteria` — the last of these get a loud `⚠️ UNKNOWN`
done-condition, because an object that cannot say what would end it can never be finished,
only abandoned.

**The backlogs were not rewritten.** The migration reads them and never opens one for writing;
they were byte-identical after the run. New findings are still filed there through
`scripts/ops/backlog_append.py::append_row`, never by hand and never here — the backlog row
stays the state of record for the FINDING, while the object is the state of record for the
WORK of dealing with it.

## The ceiling (A5) — enforced, not advisory

`scripts/ci/check_wip_ceiling.py` **refuses a ninth `in_flight` object.** `waiting` is
deliberately free: a thing blocked on an operator decision or an external event is not
consuming the attention the ceiling rations, and making the ceiling punish honesty about
blockage would defeat the six-state vocabulary.

Exceeding it is possible but not private. It requires a written justification at
`docs/claude/work/wip-ceiling-exception.yaml` naming **the exact object ids** it covers —
- `decision: pending` **still fails.** Filed is not granted; the ceiling's whole function is
  that a ninth parent needs a human to say yes.
- `decision: approved` passes only for the ids it names, and only with `approved_by` +
  `approved_at`. An approval with nobody's name on it is a session approving itself, and a
  blanket exception naming nothing is a permanent cap raise wearing an exception's clothes.

⚠️ **TWO POPULATIONS. THE REGISTER IS UNCAPPED; THE IN-FLIGHT SET IS CAPPED.**
`scripts/ci/check_open_items.py` keeps `MAX_ITEMS = None` and **that stays** — the operator
reversed the old cap on 2026-08-26 (*"we don't want to cap the number of bugs we can track,
we want to ensure that they are actually being tracked, fixed, and learned from"*). Capping a
register of KNOWN PROBLEMS just deletes knowledge. The ceiling guard's self-test asserts
`MAX_ITEMS is None`, so a future change that caps the register believing it is implementing
the ceiling fails loudly instead.
