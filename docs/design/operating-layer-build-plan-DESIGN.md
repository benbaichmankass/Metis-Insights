# Operating Layer — Build Plan

> **Status: PROPOSED 2026-09-01, revised the same day on operator direction.** Fourth and
> final pass in the operating-model series, after
> [`operating-model-DESIGN.md`](./operating-model-DESIGN.md) (structure + the 24 functions),
> [`operating-layer-schema-and-state-DESIGN.md`](./operating-layer-schema-and-state-DESIGN.md)
> (schema, state home, access posture) and
> [`operating-layer-function-derivation-DESIGN.md`](./operating-layer-function-derivation-DESIGN.md)
> (5 work · 14 partial · 5 missing).
>
> Everything here is derived from the derivation's inventory. Approving it means the
> ORDER is agreed; each phase still lands as its own PR under the normal tier gates.

## Two ordering criteria, and the first one is new

**1 · The plan must survive the session that wrote it.** *(Operator-directed, 2026-09-01.)*

This series is four documents in `docs/design/` that **nothing points at**. A fresh session
reads `CLAUDE.md` and `docs/claude/OPEN-ITEMS.json`; neither mentions the operating model.
So absent a change, the next session does exactly what every prior session did — and the
plan is lost to the precise disease it diagnoses. That is not a hypothetical: it is the
measured condition (a ~403,000-token mandatory read that no session has ever held) applied
to this document.

**So the first thing built is the thing that carries the plan forward**, and it is built
before this session closes. Everything else is downstream of that.

**2 · Every later phase must change how the next session works, on its own.**

August ran **45 governance / hardening / observability sprints against 2 deployments**. A
plan whose value arrives in phase four would reproduce that ratio inside the project meant
to cure it. So each phase names what a session can do at the end of it that it could not do
at the start, and no phase depends on a later one to be worth having.

⚠️ **An honesty caveat about this plan's own justification.** The model says **E2 capability
build is pulled by a held-up stage, never self-started** — and this plan IS capability work.
It is not pulled by a constraint diagnosis, because **E1 does not exist yet**; it is pulled
by an operator directive. That is a legitimate source (A2 is operator-set by design) but it
is a *different* one, and recording it as a constraint finding would be the unprovenanced
conclusion this repo keeps paying for. The measured constraint is **DECISION** — 256 of 370
research units superseded unread, 1 of 117 dispositions actioned — and only Phase F acts on
it directly.

---

## The split: what is built now, and what continues later

| | Phase | When |
|---|---|---|
| **A** | **Survival** — the plan carries itself forward | **This session, before it closes** |
| B | Visibility — the work view and the digest | Next session |
| C | Migration, the ceiling, the priority that reaches a session | |
| D | The constraint, computed rather than judged | |
| E | No work is lost; rules verified at exit | |
| F | The repairs — the phase that touches the measured constraint | |
| G | The forcing function — retirement, and the pull rule | |
| H | The control half — decisions from the UI, and the read gate | |

**A is the only phase that must happen before this session ends.** Everything from B on is
a fresh session's work, and B onward are ordered so that each is a legitimate stopping
point rather than a step in a march.

---

## Phase A — survival *(this session)*

**Builds:** the work-object store, seeded with **this plan's own phases as its first
objects** · the register row that makes it visible to every session · the Telegram
notification on a phase landing.

**The store's first content is the plan to build the store.** That is deliberate, not
cute: it means the mechanism is exercised by real content from its first commit, and the
build's own progress is the first thing it is asked to track. One file per object, per the
schema pass (the concurrency lesson `backlog_append.py` was written for), following the
existing `research/queue/*.yaml` precedent.

**⚠️ The delivery channel already exists and works, and needs no new plumbing — this is the
finding that makes Phase A small.** `scripts/ops/render_session_brief.py` renders
`OPEN-ITEMS.json` **into `CLAUDE.md`**, which is *the only surface that reaches a session
before its first tool call* (project hooks do not run on Claude Code on the web — verified
2026-08-26; CI guards fire at merge, which is after the wrong work is already built). So
the operating-layer state rides an existing, working renderer as a register row. A row with
`loud: true` **must be reported on in a session's closing summary** — the forcing function
is already built and already binding.

**⚠️ And that channel is itself UNPROVEN, which Phase A must not paper over.**
`OI-20260826-SESSION-BRIEF-NEVER-READ-BY-A-FRESH-SESSION` is open and says exactly this:
the brief was shipped and *no fresh session has ever been observed acting on it*. Phase A
therefore rides a mechanism whose own verification is outstanding. That is acceptable —
it is the best channel available and the alternative is no channel — but it means Phase A's
done-condition is an **observation, not a deploy**.

**Done when:** a session that arrives cold, pointed at nothing, states which phase the
operating-layer build is on and what the next step is — and does so citing the brief.
**A passing test is not that observation.**

**What changes:** the plan stops being four documents nobody opens.

## Phase B — visibility *(moved up from last)*

**Builds:** a read-only work view in the SPA · the daily digest.

**Moved from the end to second, on operator direction, and the reasoning holds
independently:** the model calls the dashboard *"the enforcement mechanism the system has
never had"* — every register today is invisible in practice, which is why they rot. Putting
it last asked Phases A–G to be held accountable by pings alone. Putting the **read** half
early is what makes the build's own progress visible while it is happening, which is the
same argument as Phase A one level up.

**⚠️ Only the READ half moves. The control half stays in Phase H**, and the split is what
makes this cheap: answering decisions from the UI needs the write path, the read gate, and
the archiving of the other two consumers. Rendering work objects needs none of that.

**The route is well-precedented rather than new.** `/api/bot/roadmap`
(`src/web/api/routers/roadmap.py`, 475 lines) already parses repo files — `ROADMAP.md` plus
every sprint log — into a milestone/sprint view, file-backed, Tier 1, read-only, cached on
mtime, degrading to an empty envelope rather than a 5xx. A work-object route is the same
shape over a different directory. That precedent also **decouples the view from the store's
internals**, which matters because moving visibility earlier means rendering a store that
has not stabilised — the honest cost of this reorder, and the mitigation for it.

**The digest** is the model's notification contract's second half: events on state changes
only, **plus one rolled-up daily summary of everything autonomous**. F6 already works as an
alert path (`outcomes.py` → `pending-pings` → `send_ping.py` → Telegram, with severity
levels); what is missing is the roll-up and the *activity vs state-change* split.

⚠️ **F6 has a designed failure mode and it is measured, not theoretical.** 202 of 376
CRITICAL/ERROR rows in one window were a single un-latched alarm, which trained the operator
past the one channel reserved for an unprotected position. So the digest pings on **state
changes and decisions** — a verdict written, a decision recorded, a deployment made, an
incident fixed, a parent authored, a WIP ceiling hit — and **never on activity** (a sweep
started, a file edited, a session opened).

**Done when:** the operator can see what is in flight without opening the repo, and gets one
daily roll-up.

## Phase C — migration, the ceiling, and the priority that reaches a session

**Builds:** the carried rows migrated · A5 WIP control · A3 priority propagation.

**Migration and the ceiling ship together, deliberately.** The ceiling is meaningless
without real rows, and the rows are dangerous without the ceiling: 572 not-closed backlog
items arriving unbounded would render as 572 things in flight, which is the condition the
redesign exists to end. So they land as one change. Carried rows arrive **dormant or
accepted by default**; a row becomes in-flight only when given an owner, a dependency edge
and a place under an intent. ⚠️ **Carrying everything is not the same as everything being
open** — the registry may hold hundreds of objects while at most 8 are being worked.

**A5** is a hard cap of **8 work objects in flight**, enforced rather than advisory;
exceeding it produces a justification that becomes an operator decision.
`scripts/ci/check_open_items.py` deliberately sets `MAX_ITEMS = None` and **that stays**:
the register is uncapped, the *in-flight set* is capped. Different populations — conflating
them would re-introduce the eviction rule the operator reversed on 2026-08-26.

**A3** writes the cycle's priority where Phase A already renders. Today nothing writes one,
so a decision taken with the operator reaches the next session only if a human retypes it.

**Done when:** a session knows this cycle's priority without being told, and the ceiling
refuses a ninth parent.

## Phase D — the constraint, computed rather than judged

**Builds:** E1 constraint diagnosis · A1 rebuilt on it. **Retires:** the due-list.

E1 walks the typed `blocked_on` edges and names the held-up stage. Nothing in the repo does
this today — verified by search — which is why A1 cannot be honest: `render_due_list.py` is
746 lines and says what is DUE, never where the chain is stuck.

A1 becomes the four-item readout: where the chain is held up with its evidence · the book
and the money with population and coverage · what is in flight against the ceiling and what
has stopped moving · decisions waiting on the operator.

**Done when:** the cycle's priority has a computed readout behind it, and the due-list is
**deleted** rather than left running beside it.

> **STATUS 2026-09-01 — BUILT, and the diagnosis REFUSES. Read both halves.**
> `scripts/ops/constraint_readout.py` ships E1 and A1; the four-item readout renders to
> [`docs/claude/READOUT.md`](../claude/READOUT.md) + `CONSTRAINT.json` and its headline
> reaches a session through the `CLAUDE.md` brief, under the priority.
>
> ⚠️ **IT NAMES NO STAGE, and that is the correct output rather than a shortfall.**
> Measured over all 584 objects (0 parse failures): **6 carry an ASSESSED `blocked_on`
> basis — 1.0%** — while 578 carry an empty list stating `NOT_ASSESSED`. Below the
> declared 50% coverage floor the verdict is `insufficient_basis` and no stage is named.
> **The constraint is not computable yet because the edges have not been written**, which
> is a finding about the store, not about the machinery. A stage named over that graph
> would be the fabricated-answer-wearing-a-computed-label class this plan warns about
> three paragraphs above its own justification.
>
> ⚠️ **Two things this plan asserted that the build had to correct.**
> (a) *"E1 walks the typed `blocked_on` edges and names the held-up stage"* assumes the
> stage histogram is informative. It is not: the store holds `INTEGRITY` 498 ·
> `EVIDENCE` 78 · `CAPABILITY` 8 and **zero** objects on QUESTION, DECISION, DEPLOYMENT
> or OBSERVATION, because migration's source was three review backlogs — registers of
> defects. A histogram over it describes the migration. `chain_coverage` publishes the
> empty stages so a consumer cannot miss it.
> (b) **The due-list was NOT deleted**, and the reason is measured: `render_due_list.py`
> also draws on `PROBES.json`, monitoring cadences, the recurrence ledger, red scheduled
> crons and unlanded automation PRs — **four source classes with no counterpart in the
> readout**. Deleting it would drop live signals. The overlap is exactly one class
> (operator-owed items). Retiring it needs those four carried first, or an operator
> decision to drop them; both are recorded on `WO-20260901-PHASE-D`.

## Phase E — no work is lost, and rules are verified at exit

**Builds:** F2 verified close-out + lease + reaper · F5 exit verification. **Retires:**
`continue-work.yml`.

**These are one hook, not two.** Both fire at session exit through the same mechanism, and
both ask about the same session: *did it record where it left the work*, and *did it follow
the rules its work type required*. Building them separately would put two verifiers on one
event.

`close_session.py` is 354 lines and does the right things — but it is **session-invoked**,
so a session that dies never runs it, and nothing verifies close-out or reaps an abandoned
one. Three mechanisms: incremental progress written as work happens · a lease per session ·
a reaper that records what a dead session actually did.

This phase needs the live layer and therefore the write token (`DASHBOARD_API_TOKEN`,
operator-decided 2026-09-01 — propagation only, no new secret). ⚠️ **The transit contract
is part of the done-condition, not a refinement of it:** three states, never collapsed
(`not_submitted` · `in_transit` · `committed`), and **transit fails BACK, never forward** —
an answer that does not commit leaves its question *unanswered*, never "answered".

**Done when:** killing a session mid-work loses nothing, **demonstrated by killing one**.

## Phase F — the repairs, and the one that touches the measured constraint

**Builds:** C3 decision preparation · C4 decision recording.

⚠️ **C3 and C4 are being delivered separately, and C4 is DEFERRED with a reason.** The obvious first move for C4 — a guard comparing the changelog's execution verdict against `config/strategies.yaml` — cannot be built honestly today: the 53 changelog entries carry only `{date, ref, summary}`, so the verdict exists **only as prose**. Pattern-matching English for it is sub-class **A** of the diagnostic-provenance defect (*the label names a quantity the accessor does not return*), and a guard that is confidently wrong on the entries it misses is worse than none. The honest path is a **structured field on new entries plus a reported denominator of un-structured ones** — a design decision, not a wire. A live instance is filed meanwhile (`BL-20260901-DECISION-RECORD-SAYS-SHADOW-WHILE-CONFIG-SAYS-LIVE-SQUEEZE-BREAKOUT-4H`).

**C3 was 1,518 lines that had never produced a durable artifact.**
`strategy_review_packet.py` emits a real action badge with reasons and an SLA, and wrote to
a **gitignored path with no cron**. This is the phase that acts on the measured constraint:
the generator that turns evidence into a decision packet exists and was simply not running.

⚠️ **THIS PARAGRAPH SAID THE REPAIR IS "a cron and a committed path" AND THAT IS NOW STALE
IN THE DIRECTION THAT INVITES DUPLICATE WORK — do not re-quote it as outstanding.** Both
shipped on 2026-09-01 (PR #10649, repaired by #10653) and were verified independently
rather than taken from this document: `.github/workflows/strategy-review-packets.yml`
(`cron: "40 4 * * *"` + `workflow_dispatch` + issue-driven) and
`comms/strategy_reviews/2026-09-01/` on main. A later session reading the old wording would
rebuild a shipped mechanism — `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`, whose cheap
preventer is exactly the existence check that produced this correction.

**What was left, and is the third part the original repair did not name: a READER.**
Measured 2026-09-01 by grepping `*.py`/`*.ts`/`*.svelte`/`*.yml` for
`comms/strategy_reviews`, the committed record had **zero consumers** — the writer and the
docs and nothing else. A record written and never read is the shape
`provenance-consumer-guard` exists to catch, and here it is the C3 failure one level up: the
packet becomes durable and still reaches no decision. Closed by
`GET /api/bot/strategy-reviews`.

⚠️ **THE CRON IS DEPLOYED, NOT OBSERVED, AND THE TWO MUST NOT BE CONFLATED.** The workflow
landed at 11:58Z on 2026-09-01 and its cron is 04:40 UTC, so **its first scheduled
opportunity had not yet arrived** — a third state, distinct from *fired and worked* and from
*fired and failed*. Both runs to date (#10652, #10656) were dispatch-driven.
`OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` is a live
counter-example in this repo, so correct cron syntax is not evidence of firing.

⚠️ **AND THE PACKET STILL PROPOSES NOTHING — the repair is necessary and is not yet
sufficient.** Population: the committed 2026-09-01 index, all **52** enabled strategies,
window **7 days**. `graded: 52 · actionable: 0 · by_action {"hold": 52}`. The cause is
structural, not a fleet in good health: `n_closed` was **0 for 34 legs, 1–4 for 14, 5–19 for
4, and never exceeded 8**, against the generator's own `MIN_CLOSED_FOR_ACTION = 20` floor
(PB-20260630-004) — so **52/52 were under the floor and no leg could produce a KILL/DEMOTE
whatever its PnL**, including **13 losing legs** carrying **−$35,446** of
provenance-trusted PnL between them. At a 7-day window a leg needs ~3 closes a day to be
gradeable while the whole fleet closed **50** trades that week, so this repeats every run
until the window and the floor are made compatible. **That is a decision, not a wire** —
widening the window changes what evidence a KILL badge rests on — so it is written up in the
PR body and filed, not flipped.

**C4 is one decision record instead of four forked surfaces.** `strategy_changelog.json` has
been dead since **2026-07-28**. The cost is not tidiness: `squeeze_breakout_4h` runs live
today against a written record saying it was demoted for a 0% win rate over 60 closes.

## Phase G — the forcing function

**Builds:** E3 capability retirement · E2 as a governed function.

**E3** is a sunset pass with a forcing function, for machinery as much as strategies — 6
strategy retirements ever, none in five weeks against 45 live legs, and nothing has ever
retired a skill, register, workflow or guard. **Complexity is monotonic by construction**

⚠️ **THE SENTENCE ABOVE IS REFUTED FOR WORKFLOWS, and correcting it matters because Phase G's
done-condition depends on it.** Measured 2026-09-02 — population: files deleted on `origin/main`
between 2026-06-13 (the oldest commit in a shallow clone's fetched history) and 2026-09-02, so
these are **lower bounds, not lifetime totals**; positive control, 137 files were deleted across
the tree, so the search finds deletions:

| machinery | deleted | the claim |
|---|---:|---|
| **workflows** | **35** (30 of them one commit — the 2026-08-07 per-guard consolidation; plus `grade-order-packages.yml`, retired by operator directive 2026-06-24, and two verify-before-build probes deleted the day they answered their question) | **REFUTED** |
| `scripts/ops/` scripts | 7 | refuted |
| **skills** (`SKILL.md`) | **0** | **holds** |
| **CI guards** (`scripts/ci/`) | **0** | **holds** |
| **registers** (`docs/claude/*.json`) | **0** | **holds** |

So Phase G's *"something has actually been removed"* was **already met before Phase G opened**,
repeatedly. The forcing function is absent for **skills, guards and registers** — where the count
is a clean zero — and present for workflows. A plan aimed at *"nothing is ever removed"* aims at
the wrong target. Full working: [`docs/audits/operating-layer-skills-workflows-inventory-2026-09-02.md`](../audits/operating-layer-skills-workflows-inventory-2026-09-02.md) § 4.2.
until something removes.

**E2's missing half is a rule, not a mechanism.** Capability build happens constantly. What
is absent is the **pull rule**: nothing requires a capability to be justified by a held-up
stage — which is only enforceable once E1 can name the stage, hence its position here.

**Done when:** something has actually been removed, and a capability PR carries the stage it
unblocks.

## Phase H — the control half

**Builds:** decisions answerable from the UI · `require_session` attached to the routes that
never got it. **Preconditions:** Android + Streamlit retired from the live feed
(`BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED`) and the DB explorer narrowed
(`BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN`) — both
already filed.

The gate is **attaching an existing, enforced, tested mechanism**: `require_session` is
genuinely default-deny and exactly two routes attach it today. Archiving the other two
consumers is what makes that tractable — there is nothing else left to keep working.
`/api/auth/login` was built for this and **no consumer has ever called it**.

---

## What is NOT built

Stated explicitly, because a build plan that only adds is how this got here.

**Leave entirely alone:** the **64 CI guards** (F4/F5's working half — lessons that became
code) and the **provenance layer** (`provenance.py`, `broker_truth.py`, and the guard family
enforcing them). Load-bearing, working, and already encoding lessons that would otherwise be
re-learned.

**Retire as part of the build:**

| Retired | Replaced by | Phase |
|---|---|---|
| The two dead Routines (`enabled`, no schedule, naming the pre-rename repo and dead branches) | ✅ **GONE as of 2026-09-02** — see the correction below. (Was: *deleted outright — OPERATOR-ONLY, attempted and refused 2026-09-01*) | A |
| `DUE.json` / `DUE.md` as an owner-assignment list | A1 as a constraint readout | D |
| `continue-work.yml` (validates a handoff; cannot start a session) | F2's close-out + the reaper | E |
| The register sprawl — 13 surfaces a session is expected to read | One state of record | A, then G |

⚠️ **One of those four cannot be done by a session, and Phase A found that out by trying.**
`Health Check Routine` (`trig_015diTGEy9jATecSPMFmPyNF`) and `Sprint Continue Work`
(`trig_01SvpLKYiTKgXkeLgV7Q3pEr`) were created **via `http_api`**, and the control plane
permits an agent to delete or disable only routines it created itself — **both `delete_trigger`
and `update_trigger(enabled=false)` were attempted on 2026-09-01 and refused**. There is no
runner path either: this is the Claude Code Remote control plane, not a VM action, so the
`before-asking-the-operator` test genuinely resolves to *ask*. **This is a real operator
hand-off**, in the same class as originating a secret value. Until it happens the register
keeps two routines that read `enabled: true` and can never fire — the state this repo calls a
mechanism that looks armed and is not. ⚠️ `Health Check Routine` additionally carries a stored
OAuth token (`sk-ant-oat01-…`, created 2026-05-10) that has never been used; deleting the
routine retires that credential with it.

⚠️ **CORRECTION 2026-09-02 — THE TWO DEAD ROUTINES ARE GONE, so stop asking the operator for
them.** `list_triggers(enabled=true)` returns exactly **2** routines and neither is
`Health Check Routine` nor `Sprint Continue Work`; positive control — the call did return two live
routines, so the probe finds positives. ⚠️ **What is established is their ABSENCE from the register
as a session can read it, NOT who removed them or when** — do not record this as "the operator
actioned the hand-off". ⚠️ **And the shape is back:** a poke-only routine created 2026-09-02T05:35Z
reads `enabled: true`, no `cron_expression`, `next_run_at: 0001-01-01` — byte-identical in register
shape to the two dead ones, and deliberate. The register has no field separating *fires only when
poked, on purpose* from *claims a cadence it can never have*, which is why those two survived four
months in plain sight (`BL-20260902-ROUTINE-REGISTER-CANNOT-DISTINGUISH-A-DELIBERATE-POKE-ONLY-ROUTINE-FROM-A-DEAD-ONE`).

⚠️ **A retirement is done when the old thing is GONE, not when the replacement ships.** The
failure mode is well evidenced: the new surface lands, the old one is left "for now", and a
session must now read both.

## Size, honestly

No hour estimates — this document has no basis for them, and inventing one is the
fabricated-number pattern the repo has a guard family for. What *is* measurable is how much
of each phase already exists:

| Phase | New | Reuses |
|---|---|---|
| **A** | The store; the plan as its first objects | **`render_session_brief.py` → `CLAUDE.md`, working today** · the `loud: true` forcing function · F6's Telegram path |
| B | The SPA section | **`roadmap.py` (475 ln) as the route's exact shape** · `send_ping.py` |
| C | The cap; the priority write; the migration | `backlog_append.py`'s round-trip discipline |
| D | E1 (genuinely new) | `render_due_list.py`'s source-reading, then deleted |
| E | Lease, reaper, exit verification | `close_session.py` (354 ln) · `pending-pings.jsonl`'s transit pattern · the 64 guards |
| F | A cron and a committed path | **`strategy_review_packet.py` (1,518 ln), complete and unrun** |
| G | The pull rule; the sunset pass | The tier gates |
| H | The decision round-trip | **`require_session`, default-deny and tested** · `/api/auth/login`, built and never called |

**Five functions are genuinely missing and all five are steering.** Everything else is a
wire, a cron, a deletion, or an attachment. That is why this plan is short.

## Calibrations carried forward

Settled in the prior passes, restated only because a phase would otherwise re-litigate them:
WIP ceiling **8**, counting work in flight — not intents, not carried rows · caps are hard
and exceptions are decisions · notification is **events plus a daily digest**, on state
changes and never on activity · incidents by severity band, and an OPERATE session may not
open work below P1 · ad-hoc work enters only as a work object.

## What this plan does not decide

The dashboard's information design · which of the 32 skills, 124 workflows and 13 registers
map onto which function (Phase G's inventory, not a prerequisite) · and the per-phase PR
decomposition, which each phase produces when it starts.
