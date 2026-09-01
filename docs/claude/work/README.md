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

⚠️ **Seeded 2026-09-01 with the operating-layer build's own phases and nothing else.** The
carried rows — the ~572 not-closed backlog items, the registers, the live legs — migrate in
**Phase C, together with the WIP ceiling**, deliberately: rows arriving without a cap would
render as hundreds of things in flight, which is the condition the redesign exists to end.
Until then this store is **not** a complete picture of the system's work, and must not be
read as one.
