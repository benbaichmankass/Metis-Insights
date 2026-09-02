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

## Decisions the operator can answer from the UI (Phase H)

A work object may declare **`decision_requests[]`** — a question with named,
selectable options — and pair it with a `blocked_on` edge of
`kind: operator_decision` naming the request's `id`. The SPA renders these at
the top of the Work section and the operator answers them there, which is what
the operating model means by *"the operator is not the bottleneck on their own
decisions"*.

```yaml
decision_requests:
  - id: DEC-20260901-EXAMPLE          # required; a request without one is DROPPED and counted
    question: >-
      What should we do about X?
    urgency: blocking                  # routine | blocking
    asked_on: 2026-09-01
    context: >-
      What a reader needs in order to answer it without opening the repo.
    options:
      - key: a                         # required; this is what a submission names
        label: Do the first thing
        implication: >-
          What choosing this actually commits us to.
      - key: b
        label: Do the second thing
        implication: ...
    allows_free_text: true             # an explicit `false` closes free text
    # `answer:` is written by the COMMITTER, never by hand-editing the transit
    # log. Its PRESENCE is what makes the decision true.
```

**The round-trip, and the one rule that shapes it:**

| | |
|---|---|
| 1 | The question lives **here, in the repo** |
| 2 | The operator answers in the SPA → `POST /api/bot/work/decision` appends ONE row to `runtime_logs/work_decision_transit.jsonl` on the live VM. **Nothing is decided.** |
| 3 | `GET /api/bot/work/decisions` grades it `in_transit` — an **open window**, listable with its age |
| 4 | `scripts/ops/commit_work_decisions.py --transit <file> --apply` writes the `answer` block into the object file here, and it lands as a normal PR |
| 5 | Only now does the request grade **`committed`** |

⚠️ **`committed` is read from THIS DIRECTORY, never from the transit log** — and
that is the transit contract, not an implementation detail. **Transit fails
BACK, never forward:** an answer that does not reach the repo leaves its
question **unanswered**, never "answered" and never ambiguous. *A question
wrongly shown as answered is a decision nobody made.*

⚠️ **A `blocked_on` edge of `kind: operator_decision` with NO matching request
is surfaced separately, as an `unanswerableOperatorEdge`** — a question the
operator is blocking on that they *cannot answer from the UI, because nobody
wrote it down*. Folding it in with the answerable ones would hide exactly that
gap.

⚠️ **Measured 2026-09-01, before this shipped: of 584 objects, ZERO declared a
`decision_requests` block and ZERO carried an `operator_decision` edge.** The
inbox was empty by construction. `WO-20260901-PHASE-H` therefore carries the
first real question as its own content — the Phase A precedent, *the store's
first content is the plan to build the store*: a mechanism is exercised by real
content from its first commit, or it is deployed and unproven.

## Spawning a sub-session, and proving none was lost

`SESSIONS.json` is the **only** thing a manager arriving COLD can read to pick up
the sub-sessions its predecessor spawned. A session it does not name is, to that
successor, a session that does not exist.

⚠️ **THIS HAS FAILED TWICE, AND THE SECOND TIME WAS WORSE.**
`MI-15-SESSIONS-REGISTRY-INCOMPLETE` recorded **3 of 6** spawned sessions absent
on 2026-09-01 and applied the remedy *"remember to register"*. On
**2026-09-02T05:56Z it was 6 of 9, five of them LIVE** — including all three
sessions carrying the cycle's highest-priority work — while the MI-15 row was
still sitting at `landed_unproven`. **The moment a manager spawns a session is
exactly the moment it is least likely to stop and write a record**, so the
remedy cannot be another reminder.

### Spawn through the registry, not around it

```bash
python3 scripts/ops/session_registry.py register \
    --title "..." --why "..." --spawned-by "$CLAUDE_SESSION_ID" \
    --branch "Metis-Insights:claude/..." --checklist-item MI-nn
# -> appends the row AND prints the spawn prompt to paste into create_session
# -> then: ... confirm --registry-key <key> --session-id <the new id>
```

⚠️ **THIS IS A SOFT COUPLING AND SAYING SO IS THE POINT.** The repo does **not
own the spawn** — a sub-session is created by the `create_session` MCP tool, and
nothing here sits on that call path, so no code can make the registry write
happen *as part of* the spawn. What `register` does instead is put the row on
the path to **the spawn prompt**, which a manager needs anyway: the cheapest
correct route now goes *through* the registry rather than around it. It is still
bypassable by writing a prompt by hand, which is why the two detectors below
exist and why the coupling alone was never going to be enough.

⚠️ `--session-id` is optional because of an ordering fact that cannot be wished
away: the platform mints the id when `create_session` **returns**, after the
prompt was needed. A row written first carries `state: spawn_pending` and a
`registry_key`. **A pending row is a WEAKER record** — it names the work but
cannot be polled — so `handoff_check` refuses to grade a handoff `ready` while
one is unconfirmed.

### Two detectors, with deliberately different reach

| | Reach | Where it runs |
|---|---|---|
| **Offline** — `session_registry.py status --strict` | the manager also writes a session id into `MANAGER-CHECKLIST.json::items[].owner`; an owner absent from the registry is a lost session, found from **two file reads** | **CI, every PR** (`session-registry-guard`) |
| **Live** — `session_registry.py reconcile --live-sessions <list_sessions output>` | what is **actually running** | only a session holding the `list_sessions` MCP tool can produce the observation. **CI holds no MCP tools.** |

⚠️ **The offline detector is PARTIAL BY CONSTRUCTION** — a session written into
neither file is invisible to it too. It catches the *overlap* of two incomplete
records, which is strictly more than the zero either caught alone.

⚠️ **Enforcement is scoped to `in_flight` checklist items**, where losing a
session costs LIVE work. Owners absent on other states are **censused and
printed on every run**, so the narrow enforcement can never hide the wider
number.

### Before handing over: `handoff_check.py`

```bash
python3 scripts/ops/handoff_check.py --session-id "$CLAUDE_SESSION_ID" \
    --live-sessions <(the list_sessions output)
```

The **lease is deliberately not a handoff** — its own docstring says takeover is
TIME-BASED *because a session that dies cannot hand over*. That is right for a
manager that DIED; a manager that is alive and stepping down is the other case,
and everything the successor needs to inherit was, until now, "remember to."
This is the check that turns it into a verdict. It refuses on: an observed live
session absent from the registry · a checklist owner absent from it · a lease
you do not hold · manager state that never reached `origin` · an unconfirmed
`spawn_pending` row.

**Three states, never collapsed** — `ready` · `not_ready` · **`unknown` (we
could not look)**. ⚠️ **`ready` is UNOBTAINABLE without a live observation, and
that is the enforcement rather than an inconvenience.** There is deliberately no
flag that asserts the registry is fine: *asserting it is what failed twice.*
Exit codes 0 / 3 / 4 keep the three apart, so a caller cannot treat "we could
not look" as a pass.

## The other half of a handoff: OPEN PRS

`SESSIONS.json` says which sub-sessions a successor inherits. **`OPEN-PRS.json`
says which PRs it inherits, and what the operator already said about them** —
ownership, intent and decisions, which GitHub does not carry.

⚠️ **THE DANGEROUS CASE IS A FORGOTTEN CONDITION, NOT A FORGOTTEN PR.** `#10746`
carries a Tier-2 approval that is *conditional*: stage on `bybit_1` (demo) only,
explicitly not a fleet-wide flip, with the operator having accepted that
real-money `bybit_2` stays exposed during the soak.

- A successor knowing **nothing** about that approval stalls and re-asks —
  wasteful, and **safe**.
- A successor knowing **"approved"** but not the **condition** could merge it
  fleet-wide onto a **real-money account**.

**Only the half-informed case is dangerous.** So a row recording a verdict
*without* its condition is **worse than a missing row** — it reads as complete.

### `operator_decision` is a typed object, not a string

```json
"operator_decision": {
  "verdict": "approved_with_conditions",   // closed vocabulary
  "condition": "must ship behind an account allowlist …",
  "scope": "bybit_1 (demo) ONLY. NOT a fleet-wide flip …",
  "decided_on": "2026-09-02",
  "text": "<the operator's original wording, VERBATIM>"
}
```

`verdict` ∈ `approved` · `approved_with_conditions` · `not_required` ·
`pending` · `none_recorded`. `python3 scripts/ops/open_pr_record.py --strict`
**fails** a row whose verdict is `approved_with_conditions` while recording
neither `condition` nor `scope`, and it runs in CI on every PR.

⚠️ **A plain `approved` is deliberately NOT forced to carry a condition** —
failing it would push authors to invent one to satisfy the guard, which is worse
than the gap it closes.

⚠️ **WHAT THIS CANNOT DETECT, SAID PLAINLY.** An author who writes
`verdict: approved` where the operator actually attached conditions defeats it,
and **nothing inside the repo can catch that** — knowing a condition was given
means knowing what the operator said, and *this file is that record*. Reading it
out of the old free-text form would mean matching English for a semantic
property, which is diagnostic-provenance sub-class **A** (the repo's own stated
reason for deferring C4). The typed form **narrows** the failure from *"a
condition silently absent from prose nobody parses"* to *"a verdict field a
reader can compare against `text`"*. That is why `text` is mandatory — and it is
a narrowing, **not** a closure. A row still on the free-text form grades
`prose_ungradeable`, which is **`unknown`, never a pass**.

### Staleness, with no wall-clock threshold

The record's own `_doc` says it goes stale the moment a PR merges. So staleness
is detected by **comparing it against a live list of what is open**, not by
ageing `as_of`: a row naming a PR that is no longer open **is** that staleness,
observed rather than guessed. The complementary direction — an open PR with no
row — is the completeness half.

⚠️ **This is NOT a second copy of GitHub.** Nothing here re-derives CI or
mergeability; a JSON mirror of PR state would be free to drift. The live list is
compared and never stored.

⚠️ **The live list cannot be fetched from a sub-session's container on a
Routine-woken turn** — `mcp__github__*` is absent there and `api.github.com`
returns 403 at the sandbox proxy. It has to come from somewhere credentials
exist: an interactive session's `list_pull_requests`, or a workflow. Pass it
with `--open-prs`; without it, completeness grades **`not_observed`**, which is
`unknown` and therefore never `ready`.

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
| **Total migrated (bulk pass)** | **575** | 1,291 |

Store afterwards: **584 objects, 1 `in_flight`.** (575 from the bulk pass + the build's own
8 phases + 1 for a row filed later in the same session — the migration is idempotent and
picks up newly-carried rows on a re-run, skipping every object that already exists so a
hand-added edge or owner survives.)

⚠️ **READ THE `lifecycle`, NOT THE COUNT.** 584 objects is not 584 things in flight, and a
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

## The constraint readout (E1 + A1) — and why it currently REFUSES

`scripts/ops/constraint_readout.py` walks these edges and renders
[`../READOUT.md`](../READOUT.md) + `../CONSTRAINT.json`; its headline reaches a session
through the `CLAUDE.md` brief, under the cycle priority. Re-run it with
`python3 scripts/ops/constraint_readout.py --write` — it is a **dated snapshot, not a
live read**, and the brief prints the date so its age is visible.

⚠️ **It names NO stage today, and that is the honest output.** Measured 2026-09-01 over
all 584 objects: **6 carry an ASSESSED `blocked_on` basis (1.0%)**; 578 carry an empty
list stating `NOT_ASSESSED`. Below the declared 50% coverage floor the verdict is
`insufficient_basis`. **So the single highest-value edit anyone can make to this store is
a TRUE `blocked_on` edge on a row they actually understand** — the diagnosis is short of
assessed edges, not of machinery. Still never invent one: a false blocker is worse than a
missing one, and the readout would report it with total confidence.

Three distinctions the readout keeps and a reader must too:

- **`declared_none` vs `unstated`.** An empty list with a basis that ASSERTS an assessment
  is a claim; an empty list with `NOT_ASSESSED`, or with no basis key at all, is nobody
  having looked. Collapsing them turns 578 unexamined rows into 578 all-clears.
- **The `stage` histogram is not a constraint.** `INTEGRITY` 498 · `EVIDENCE` 78 ·
  `CAPABILITY` 8, and **zero** on QUESTION / DECISION / DEPLOYMENT / OBSERVATION — the
  shape of what got migrated (review-backlog defect rows), not of where the chain is
  stuck. `chain_stages_with_no_objects` publishes the gap explicitly.
- **A hold on a `waiting` target is the weakest hold the graph can express.** `waiting`
  covers both *not delivered* and *delivered, awaiting an observation*, and a dependent
  needs the capability rather than the observation. Reported as
  `holds_on_waiting_targets` with the caveat rather than resolved into a state nobody
  measured. This is not theoretical: `WO-20260901-PHASE-D` carried exactly such a false
  blocker until 2026-09-01.

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
