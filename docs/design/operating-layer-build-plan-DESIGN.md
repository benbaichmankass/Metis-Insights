# Operating Layer — Build Plan

> **Status: PROPOSED 2026-09-01.** Fourth and final pass in the operating-model series,
> after [`operating-model-DESIGN.md`](./operating-model-DESIGN.md) (structure + the 24
> functions), [`operating-layer-schema-and-state-DESIGN.md`](./operating-layer-schema-and-state-DESIGN.md)
> (work-object schema, state home, access posture) and
> [`operating-layer-function-derivation-DESIGN.md`](./operating-layer-function-derivation-DESIGN.md)
> (what exists, what is broken, what is missing).
>
> **This is the first document in the series that proposes work.** Everything in it is
> derived from the derivation's inventory — nothing new is introduced here that the three
> prior passes did not already settle. Approving it means the ORDER is agreed; each phase
> still lands as its own PR under the normal tier gates.

## The ordering criterion

**Every phase must change how the next session works, on its own.**

This is the one rule the plan is built around, and it is not a style preference — it is
the direct lesson of the measured diagnosis. August ran **45 governance / hardening /
observability sprints against 2 deployments**. A build plan whose first three phases are
substrate and whose value arrives in phase four would reproduce exactly that ratio, in
the project meant to cure it. So each phase below names what a session can do at the end
of it that it could not do at the start, and no phase depends on a later one to be worth
having.

⚠️ **An honesty caveat about this plan's own justification.** The operating model says
**E2 capability build is pulled by a held-up stage, never self-started** — and this plan
IS capability work. It is not pulled by a constraint diagnosis, because **E1 does not
exist yet**; it is pulled by an operator directive. That is a legitimate source (A2 is
operator-set by design) but it is a *different* source, and recording it as a constraint
finding would be the unprovenanced-conclusion error this repo keeps paying for. The
measured constraint is **DECISION** — evidence outruns disposition, 256 of 370 units
superseded unread, 1 of 117 dispositions actioned — and only Phase 4 acts on it directly.

---

## What is NOT built

Stated first, because a build plan that only adds is how the thing being fixed got here.

**Leave entirely alone:** the **64 CI guards** (F4/F5's working half — lessons that became
code) and the **provenance layer** (`src/runtime/provenance.py`, `broker_truth.py`, and
the guard family enforcing them). Both are load-bearing, both work, and both already
encode lessons that would otherwise have to be re-learned.

**Retire as part of the build, not after it** (this is E3 applied to the operating layer
itself, and it is what keeps the plan from being purely additive):

| Retired | Replaced by | Phase |
|---|---|---|
| `DUE.json` / `DUE.md` as an owner-assignment list | A1 as a constraint readout | 2 |
| The register sprawl — 13 surfaces a session is expected to read | One state of record (F1) | 0, then 5 |
| `continue-work.yml` (validates a handoff, cannot start a session) | F2's verified close-out + the reaper | 3 |
| The two dead Routines (`Health Check Routine`, `Sprint Continue Work` — no schedule, `next_run_at: 0001-01-01`, naming a repo renamed in July and branches that no longer exist) | Deleted outright; nothing replaces them | 0 |

⚠️ **A retirement is not done when the replacement ships — it is done when the old thing
is GONE.** Six strategy retirements have ever happened and nothing has ever retired a
skill, register, workflow or guard, so the failure mode here is well evidenced: the new
surface lands, the old one is left "for now", and a session must now read both.

---

## Phase 0 — one place that says what is in flight

**Builds:** F1 state of record · the work-object store · the migration of carried rows.

The store is the schema pass's model as files: one file per object (the concurrency
lesson `backlog_append.py` was written for — a naive read-append-write on a shared JSON
reformats the file and buries a one-row change in a 47,000-line diff), following the
existing `research/queue/*.yaml` precedent. Three levels, INTENT → WORK OBJECT → STEP,
with `lifecycle` and the typed `blocked_on` edges intact — the edges are what Phase 2
computes the constraint from, so they are not optional decoration here.

**Migration is part of this phase, not a follow-on.** An empty store is not a state of
record. All existing rows carry across — the 572 not-closed backlog items, the registers,
the live legs — arriving **dormant or accepted by default**. ⚠️ **Carrying everything is
not the same as everything being open**, and the distinction is the whole thing: the
registry may hold hundreds of objects while at most 8 are in flight. A row becomes
in-flight only when it is given an owner, a dependency edge and a place under an intent.

**Done when:** a session can answer *"what is in flight, under which intent, blocked on
what"* from one read, without opening a backlog.

**What a session can do that it could not before:** situate its task. This is the
anti-silo fix's first half — the object carries the context, so the session stops
reconstructing it from a 403,000-token corpus it cannot absorb.

**Also in this phase, because it is one line and the register is a live lie until it is
done:** delete the two dead Routines.

## Phase 1 — the ceiling, and the priority that reaches a session

**Builds:** A5 WIP control · A3 priority propagation.

Both are missing outright, both are small, and both bite immediately on what Phase 0 made
countable.

**A5** is a hard cap of **8 work objects in flight** — enforced, not advisory. Exceeding
it produces a justification that becomes an operator decision. `scripts/ci/check_open_items.py`
deliberately sets `MAX_ITEMS = None` and that stays: the register is uncapped, the
**in-flight set** is capped. Those are different populations and conflating them would
re-introduce the eviction rule the operator reversed on 2026-08-26.

**A3 needs almost no new machinery, which is the useful finding here.** Its delivery
channel already exists and already works: `render_session_brief.py` renders into
`CLAUDE.md`, which is *the only surface that reaches a session before its first tool call*
(project hooks do not run on Claude Code on the web — verified 2026-08-26). It is the one
genuinely automated piece in group A. It has nothing to deliver because nothing writes a
current-cycle priority. So A3 is: write the priority; render it where the brief already
renders.

**Done when:** a session that was told nothing still knows this cycle's priority, and the
ceiling refuses a ninth parent.

**What changes:** a decision taken with the operator stops needing a human to retype it
into the next session. Today that is the only transport.

## Phase 2 — the constraint, computed rather than judged

**Builds:** E1 constraint diagnosis · A1 rebuilt on it. **Retires:** the due-list.

E1 computes which stage is held up by walking the `blocked_on` graph from Phase 0. Nothing
in the repo names a bottleneck stage today — verified by search — which is why A1 cannot
be honest: `render_due_list.py` is 746 lines and says what is DUE, never where the chain
is stuck.

A1 becomes the four-item readout the model settles: where the chain is held up with its
evidence · the book and the money with population and coverage · what is in flight against
the ceiling and what has stopped moving · decisions waiting on the operator.

**Done when:** the cycle's priority decision has a computed readout behind it, and the
due-list is deleted rather than left running beside it.

**What changes:** A2 stops being a judgement call made from memory. This is the phase that
makes "the operator decides each cycle" survivable rather than a synonym for drift.

## Phase 3 — no work is lost, and rules are verified at exit

**Builds:** F2 verified close-out + lease + reaper · F5 exit verification. **Retires:**
`continue-work.yml`.

**These are one hook, not two — that is a real consolidation this plan found.** Both fire
at the same moment (session exit) through the same mechanism, and both answer a question
about the same session: *did it record where it left the work*, and *did it follow the
rules its work type required*. Building them separately would put two verifiers on one
event.

`close_session.py` is 354 lines and does the right things — commits a handoff, pushes,
dispatches — but it is **session-invoked**, so a session that dies never runs it, and
nothing verifies close-out or reaps an abandoned one. The three mechanisms the model
requires: incremental progress written as work happens · a lease per session · a reaper
that records what a dead session actually did.

This is the phase that needs the **live layer** and therefore the write token (§ 3 of the
schema pass — `DASHBOARD_API_TOKEN`, decided 2026-09-01; propagation only, no new secret).
The lease and heartbeat are observations and live there; everything the session *makes*
commits to the repo as it is made.

⚠️ **The transit contract is part of this phase's done-condition, not a refinement of it.**
Three states, never collapsed — `not_submitted` · `in_transit` · `committed` — and
**transit fails BACK, never forward**: an answer that does not commit leaves its question
*unanswered*, never "answered". Open windows must be enumerable and must close observably.

**Done when:** killing a session mid-work loses nothing, and is *demonstrated* by killing
one — not by a passing test.

**What changes:** the anti-silo fix's second half. A session situates itself because the
*previous* session recorded where it left the work.

## Phase 4 — the repairs, and the one that touches the measured constraint

**Builds:** C3 decision preparation · C4 decision recording.

**C3 is 1,518 lines that have never produced a durable artifact.**
`strategy_review_packet.py` emits a real action badge with reasons and an SLA — and writes
to a **gitignored path with no cron**, so no packet has ever been committed. The repair is
a cron and a committed path. This is the phase that acts on the measured constraint
(DECISION): the generator that turns evidence into a decision packet exists and is simply
not running.

**C4 is one decision record instead of four forked surfaces.** `strategy_changelog.json`
has been dead since **2026-07-28**; August's Tier-3 approvals live in coverage matrices,
corpora and OPEN-ITEMS instead. The cost is not tidiness: `squeeze_breakout_4h` runs live
today against a written record saying it was demoted for a 0% win rate over 60 closes.

**Done when:** a packet is committed on a cadence and read, and a Tier-3 approval lands in
exactly one place.

**What changes:** the constraint stage stops being starved of prepared decisions.

## Phase 5 — the forcing function

**Builds:** E3 capability retirement · E2 as a governed function.

**E3** is a sunset pass with a forcing function, for machinery as much as strategies —
6 strategy retirements ever, none in five weeks against 45 live legs, and nothing has ever
retired a skill, register, workflow or guard. **Complexity is monotonic by construction**
until something removes.

**E2's missing half is a rule, not a mechanism.** Capability build happens constantly — it
is most of what the sprint record contains. What is absent is the **pull rule**: nothing
requires a capability to be justified by a held-up stage. That rule is only enforceable
once E1 (Phase 2) can name the stage, which is why it sits here rather than earlier.

**Done when:** something has actually been removed, and a capability PR carries the stage
it unblocks.

## Phase 6 — the dashboard, and the read gate

**Builds:** the SPA control section · `require_session` attached to the routes that never
got it. **Preconditions:** Android + Streamlit retired from the live feed
(`BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED`), and the DB explorer
narrowed (`BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN`).
Both are already filed as repo work.

The gate is **attaching an existing, enforced, tested mechanism** — `require_session` is
genuinely default-deny and exactly two routes attach it today. Archiving the other two
consumers is what makes that tractable: there is nothing else left to keep working.

⚠️ **This phase is last, and that is a real trade rather than a free choice.** The model
calls the dashboard *"the enforcement mechanism the system has never had"* — every register
today is invisible in practice, which is why they rot — so putting it last accepts that
Phases 0–5 are held accountable by Telegram (F6 works) plus the CLAUDE.md brief, and not
by a rendered view. The argument for accepting it: the operator is actively involved and
reads the pings; and a dashboard built before there is stable state to render is a
rewrite. **If the registers start rotting again during Phases 1–3, that is the signal to
pull this forward, and it is a real possibility rather than a formality.**

---

## Size, honestly

No hour estimates — this document does not have a basis for them and inventing one would
be the fabricated-number pattern the repo has a guard family for. What *is* measurable is
how much of each phase already exists:

| Phase | New | Reuses |
|---|---|---|
| 0 | The store + migration | `research/queue/*.yaml` shape · `backlog_append.py`'s round-trip discipline |
| 1 | The cap; the priority write | **`render_session_brief.py` → CLAUDE.md, working today** |
| 2 | E1 (genuinely new) | A1's four-item shape; `render_due_list.py`'s source-reading, then deleted |
| 3 | Lease + reaper + exit verification | `close_session.py` (354 ln) · `pending-pings.jsonl`'s transit pattern · the 64 guards |
| 4 | A cron and a committed path | **`strategy_review_packet.py` (1,518 ln), complete and unrun** |
| 5 | The pull rule; the sunset pass | The tier gates |
| 6 | The SPA section | **`require_session`, default-deny and tested** · `/api/auth/login`, built and never called |

**Five functions are genuinely missing and all five are steering.** Everything else on
this list is a wire, a cron, a deletion, or an attachment. That is the finding the
derivation produced and it is why this plan is short.

## Calibrations carried forward

Settled in the prior passes, restated here only because they are what a phase would
otherwise re-litigate: WIP ceiling **8**, counting work in flight, not intents and not
carried rows · caps are hard and exceptions are decisions · notification is **events plus
a daily digest**, on state changes and never on activity · incidents by severity band,
and an OPERATE session may not open work below P1 · ad-hoc work enters only as a work
object.

## What this plan does not decide

The dashboard's information design · which of the 32 skills and 124 workflows map onto
which function (Phase 5's inventory, not a prerequisite) · and the per-phase PR
decomposition, which each phase produces when it starts rather than now.
