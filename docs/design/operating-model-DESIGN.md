# Operating-Model Design — Metis-Insights

> **Status: AGREED (operator-approved 2026-09-01). Scope: STRUCTURE and FUNCTIONS only.**
>
> This document deliberately contains **no artifacts, no file changes, and no build
> order**. It answers *how the operator and Claude should work together so this system
> makes money*, and derives the functions that support it. What gets built follows later,
> from a separate derivation.
>
> **The next stage is a single design pass covering the work-object schema and where
> coordination state lives** (§ "What is settled, and what comes next"). Nothing else
> starts before it, because the coordination layer, the decision round-trip, the
> dashboard and the constraint readout all sit on top of that decision.
>
> An earlier draft of this document jumped straight to a build order and was rejected
> for exactly that reason. Do not re-introduce one here.

## Context

**The ask.** Not a repo cleanup. The question is how the operator and Claude should work together so that this system makes money — and only then, what functions support that way of working, and only after *that*, what gets built.

**The operator's frame** (stated across this session):

| | |
|---|---|
| Purpose | Make money now, at current scale. |
| Operator's involvement | **Actively involved, often in several concurrent sessions.** As much autonomy as is genuinely possible — but not send-and-forget, and on-demand work must fit the framework rather than break it. |
| Binding constraints | Operator attention; and no proven edge yet. |
| Priority rule | **The operator decides each cycle** — priority may legitimately be research, capability, integrity or deployment depending on where things are held up. |
| Context model | **Work object + role pack**, both. |
| Work unit | **A step, with a mandatory parent.** |
| Rule binding | **All four mechanisms**: attached to work type · executable checks · a short constitution · verified at session exit. |
| Live book meanwhile | Leave it; fix the gate first. |
| Machinery | Rebuild the operating layer; port pieces back on demand. |

### The measured diagnosis

All figures measured this session against the working tree; populations stated.

1. **The instruction surface exceeds a context window twice over.** Mandatory session-start reading ≈ **403,000 tokens** (`CLAUDE.md` ~105k, of which 69% is API/env reference tables · `ROADMAP.md` ~178k · architecture ~46k · rules ~27k · OPEN-ITEMS ~42k). No session has ever read the map. Each reads a different fragment and drifts.
2. **Work is created faster than it is absorbed, and accelerating.** Four review backlogs, 1,288 rows, **572 not closed**. Filed vs closed: May +37 · Jun +148 · Jul +125 · **Aug +425** (closed is a lower bound). Reviews file; one skill closes.
3. **Effort goes to the machine.** 196 dated sprint logs (2026-06-03 → 2026-09-01): **9.2% declared Tier-3**, **6.1% deployed a strategy anywhere**, **1.0% onto real money**. Deployment is the only category that *fell* (4→6→2) while throughput tripled. August: 94 sprints — 45 governance/hardening/observability against **2 deployments**. **21 of 32 milestones simultaneously active.**
4. **Evidence is produced and not read.** **256 of 370 research units (69%) superseded before anyone read them.** 117 dispositions written across two days: 64 underpowered, 52 no-action, **1 actioned** — itself a proposal, not a change. M20 corpus 91/1,379 pass (6.6%); E35 corpus **93% ungraded**, its passing and readable rows disjoint.
5. **Nothing is retired.** 6 retirements ever, none in five weeks, against 45 live legs. The M7 kill packet writes to a gitignored path with no cron; Override 5 converts every kill to `tune` pending artifacts that don't exist. 7 cells sit at `shipped_gate_failed` — validation lapsed, held anyway.
6. **The decision record has forked.** `strategy_changelog.json` dead since 2026-07-28. `squeeze_breakout_4h` runs live today against a written record saying it was demoted for a 0% win rate over 60 closes.
7. **The money.** Real money 30d: 27 trades, 22.2% win, −$28.90, PF 0.46. Only broker-verified figure: **−$262.52** on `bybit_2` (1 of 11 accounts). 35 of 41 reports grade caution or worse. Paper's +$172,857 is disowned by the record itself as a provenance artefact at 19% coverage.
8. **The root cause was written a month ago and not acted on.** The backtest cost model is fees-only at flat 7.5bps on a perpetual-futures book that pays funding continuously: *"the validation layer keeps green-lighting strategies the honest live-execution layer then loses money on."* Its top recommendation is implemented in no gate.

### What this adds up to

The failure is not rigour, effort or tooling — all three are unusually strong. It is that **one undifferentiated session type does three incompatible jobs** (operate, build, research), guided by a map too large to read, with **four competing "what next" surfaces and no arbiter**, and **no forcing function that removes anything**. Ops work wins that competition every time because it is urgent, legible and infinitely available.

Two failure modes run at once: **over-siloed at the session level** (a session gets one task, reads a fragment of the instructions, finishes, and never understood what it was part of) and **under-siloed at the governance level** (every session owns filing, doc coherence, backlog hygiene and coordination, so everyone owns everything and nothing completes).

---

## The structure

### One value chain

The system converts capital and operator attention into returns through six stages. Everything that happens is somewhere on it.

**QUESTION** → **EVIDENCE** → **DECISION** → **DEPLOYMENT** → **OBSERVATION** → (new questions)

Two support functions feed the chain rather than sitting on it:

- **CAPABILITY** — infra, tooling, harnesses, automation. Exists *only* to unblock a named stage. "Better backtesting" is not a goal; "EVIDENCE cannot be produced for exits without a cost-correct harness" is.
- **INTEGRITY** — ops, health, provenance, alerting. Keeps the chain *trustworthy*. If the journal lies, every stage above it is fiction — which is why integrity is load-bearing rather than hygiene, and equally why it must be bounded, since it can absorb unlimited effort while the chain moves nowhere.

**Priority across kinds.** Research, capability, integrity and deployment are not comparable quantities and are not ranked against each other. Work is directed at whichever stage is **held up**. The operator makes that call each cycle (their choice), informed by a constraint readout, and bounded by a WIP ceiling — those two are what keep "operator decides" from reproducing 21 simultaneously-active milestones. As measured, the current constraint is **DECISION**: evidence is produced far faster than it is converted into choices, so additional research capability would make things worse.

### Every unit of work is a typed object that carries its own context

The reason a session finishes a small task without understanding what it was part of is that **context lives in the instruction corpus** — 400k tokens it cannot absorb — rather than in the work. Inverting that is the core structural move.

A unit of work declares: its **parent** (the question or commitment it serves) · its **stage** on the chain · its **entry state** · its **exit state** (what must be true to be done) · its **successor** (what becomes possible, and who acts next). A session inherits context from what it was handed rather than reconstructing it from documents.

**The smallest assignable thing is a step with a mandatory parent.** A step is session-sized and parallel-friendly; the required parent is what prevents orphaned tasks. Context reaching a session is **object + role pack**: the object supplies *why and where*, the role pack supplies *how*.

**Rules bind four ways, together:** they arrive attached to the work type (a research step cannot proceed without the rigour rules; a deployment step cannot proceed without the tier rules) · they are enforced by executable checks wherever encodable · a short constitution carries only what cannot be mechanised · and compliance is **verified at session exit** rather than trusted on entry.

### Work arrives three ways

**Cadenced** (a clock) · **Triggered** (an event) · **Directed** (operator or the priority process). This classification is what determines automatability.

**The automatability test.** *Fully automatable*: deterministic trigger, bounded input, checkable output. *Semi*: production is mechanical, judgement is not — the machine prepares, a session or the operator decides. *Not automatable*: commits capital, or requires taste.

### The roadmap is two layers, not a document

**INTENT** — a thin curated layer of directions committed to, expressible before they decompose into work ("we are going after X"). **WORK** — the open questions and commitments beneath each intent, which is where everything actually happens.

The second layer maintains itself: it updates when work objects change state. The first is written by hand and is therefore the one that can drift — which is what produced a 178k-token roadmap with 21 simultaneously-active milestones. **So the binding between the layers is reconciled, not trusted:** an intent carrying no work for N cycles is surfaced as either dormant or abandoned, and a work object with no parent intent is surfaced as unattached. Neither condition is an error; both are things that must be *seen* rather than accumulate silently. The intent layer stays short enough to read in full, or it has stopped being an intent layer.

### Blocked is a dependency edge, not a flag

A work object names what it waits on — another object, an operator decision, an external event, accruing data, or a missing capability. This is what lets **the constraint be computed rather than judged**: where the system is held up falls out of the graph, which is the input the operator's cycle decision depends on. It also gives the dashboard a real blocker view instead of a list of things that merely have not started. It is the most machinery of the available options and it is the one that makes A1 honest.

---

## The functions

24 functions in six groups, derived from the structure above. `Trigger` is how the work arrives; `Autonomy` is the honest result of the test above.

### A · DIRECTION — how work gets chosen

| | Function | Trigger | Autonomy |
|---|---|---|---|
| A1 | **Constraint readout** — where the chain is held up, what is in flight, what is stale, what changed since last cycle | Cadenced | Full |
| A2 | **Priority setting** — the operator chooses the cycle's priority | Cadenced | **None — operator** |
| A3 | **Priority propagation** — the choice is written where every session inherits it | Triggered by A2 | Full |
| A4 | **Registry upkeep** — open questions and commitments update as a byproduct of state change | Triggered | Full |
| A5 | **WIP control** — a hard ceiling on concurrently open parents | Continuous | Full |

### B · DEFINITION — how work becomes assignable

| | Function | Trigger | Autonomy |
|---|---|---|---|
| B1 | **Authoring** — a felt need becomes a well-formed question or commitment with a done-condition | Directed | Semi |
| B2 | **Decomposition** — parent → session-sized steps with entry state, exit state, successor | Directed | Semi |
| B3 | **Dispatch** — a step reaches a session | All three | Full for cadenced/triggered |
| B4 | **Context assembly** — build the object context + role pack a session receives | Triggered | Full |

### C · EXECUTION — the chain itself

| | Function | Trigger | Autonomy |
|---|---|---|---|
| C1 | **Evidence production** — sweeps, backtests, training, event studies | Directed / cadenced | **Full** |
| C2 | **Evidence disposition** — result → verdict with stated population | Triggered by C1 | Semi — **the current constraint** |
| C3 | **Decision preparation** — question, evidence, options, recommendation, consequences | Triggered by C2 | Semi |
| C4 | **Decision recording** — the choice, its basis, its review trigger | Triggered by A2 | Full |
| C5 | **Deployment** — the decision expressed in code and config through the tier gates | Triggered by C4 | Semi |
| C6 | **Post-deployment verification** — did it do what the decision expected | Cadenced | Full to produce, semi to judge |

### D · INTEGRITY — keeps the chain trustworthy

| | Function | Trigger | Autonomy |
|---|---|---|---|
| D1 | **Liveness & incident detection** — is the machine running, is money safe | Cadenced | **Full** |
| D2 | **Incident disposition** — fix / accept / escalate, with severity | Triggered | Semi |
| D3 | **Data trustworthiness** — provenance, coverage, broker reconciliation | Cadenced | Full |
| D4 | **Obligation tracking** — things that must keep being true, re-verified on cadence | Cadenced | Full |

### E · CAPABILITY — unblocks a named stage

| | Function | Trigger | Autonomy |
|---|---|---|---|
| E1 | **Constraint diagnosis** — which stage is held up and why | Cadenced | Semi — feeds A1 |
| E2 | **Capability build** — pulled by a held-up stage, never self-directed | Directed | Semi |
| E3 | **Capability retirement** — machinery that no longer serves a stage comes out | Cadenced | Semi |

### F · CONTINUITY — what makes sessions compose instead of silo

| | Function | Trigger | Autonomy |
|---|---|---|---|
| F1 | **State of record** — the one place a session reads current truth | Continuous | Full |
| F2 | **Session close-out** — exit state, successor, what changed; verified, not trusted | Triggered | Full to verify |
| F3 | **Concurrency coordination** — who is touching what; merge serialization | Continuous | Full |
| F4 | **Lesson capture** — a repeated mistake becomes an executable check, or it is not a lesson | Triggered | Semi |
| F5 | **Rule binding** — rules arrive with the work type; compliance verified at exit | Continuous | Full |
| F6 | **Autonomous-action notification** — the operator is told, by Telegram, whenever something is done without them | Triggered | Full |

**F6 is the condition on which autonomy was granted, and it has a designed failure mode.** The repo's own record: 202 of 376 CRITICAL/ERROR rows in one window were a single un-latched alarm, which trained the operator past the one channel reserved for an unprotected position. So F6 pings on **state changes and decisions** — a verdict written, a decision recorded, a deployment made, an incident fixed, a parent authored, a WIP ceiling hit, a checkpoint reached — and never on **activity** (a sweep started, a file edited, a session opened). Rate-limited per event class, digested rather than streamed. A notification channel that fires constantly delivers less visibility than one that fires rarely, which is precisely why granting autonomy without this design would remove the oversight it was conditioned on.

### The load-bearing dependencies

- **F2 → B4 → every session.** Close-out is what makes the next session's context assembly possible. This is the anti-silo mechanism in full: a session situates itself because the *previous* session recorded where it left the work — not because it read the corpus.
- **D3 gates C2 and C3.** Evidence cannot be honestly dispositioned, nor a decision honestly prepared, from a journal whose trustworthiness has not been established. Integrity is a precondition of two chain stages, not a parallel concern.
- **C1 outruns C2, and that is the disease.** Evidence production is the most automated function present; disposition is the least. Anything that raises C1 without raising C2 makes the system worse. This is the measured 256-of-370 unread and 1-of-117 actioned.
- **A5 makes A2 survivable.** Operator-set priority without a WIP ceiling is what produced 21 simultaneously-active milestones.
- **E2 is pulled by E1, never self-started.** This single rule is what redirects the measured 45-governance-sprints-to-2-deployments ratio.
- **A1 depends on E1 and F1.** The operator's cycle decision is only as good as the readout behind it, and the readout is only as good as the state of record.

---

## Session types

Five work types plus the cycle. **The split of PRODUCE from DISPOSE is the load-bearing one:** a session that runs a sweep currently counts itself finished when the sweep lands, which is exactly how 256 of 370 units went unread. Separating them makes reading the result somebody's actual job, with its own trigger and its own definition of done.

| Type | Owns | Operator |
|---|---|---|
| **CYCLE** | A1 consumed · A2 · A3 · A4 reviewed · C4 recorded | **Present** |
| **PRODUCE** | C1 · compute dispatch | Absent |
| **DISPOSE** | C2 → feeds C3 | Absent |
| **DECIDE** | C3 prepared autonomously; the choice itself is made at a cycle or checkpoint | Present for the choice only |
| **BUILD** | C5 · E2 — pulled only, never self-started | Absent, except tier gates |
| **OPERATE** | D1–D4 — bounded; may not open work beyond its severity band | Absent |

F1–F6 are cross-cutting: every session pays a verified close-out, and every autonomous action notifies.

## Rhythm

**Continuous flow with checkpoints, plus two cadences.** Work runs continuously; the operator is pulled in at defined checkpoints rather than by a clock alone. A **light daily** confirms or redirects in minutes. A **deep weekly** revisits the constraint, the registry, kills, and capital.

A checkpoint pulls the operator in when: a decision is ready · the constraint has moved · WIP is full and something wants in · a tier-gated action needs approval · an incident exceeds its severity band · a verdict changes what is on the registry.

## How on-demand work enters

**It must become a work object first.** An ad-hoc request is authored into a question or commitment — usually one line, usually seconds — before any session starts on it. Nothing is worked that has no parent. This is what keeps urgent requests composable instead of orphaned, and it is the mechanism by which "on-demand work fits the framework rather than breaking it."

## The autonomy grant

Granted to run with no operator involvement: **C2** evidence disposition · **D2** incident fix within a severity bound · **E2** capability build once a stage is diagnosed as held up · **B1/B2** authoring and decomposition. All conditioned on **F6**.

**Honest caveat on B1/B2.** Autonomous authoring is the channel through which scope sprawls — it is how 21 milestones came to be simultaneously active. It is safe only while A5 (WIP ceiling) is real and enforced, and while the cycle actually reviews the registry rather than rubber-stamping it. If either weakens, this is the grant to withdraw first.

## Feasibility — measured, not assumed

**Autonomous session initiation works and is already in use.** Claude Code Remote Routines fire a prompt into either a fresh session or a persistent one, on a cron or one-shot, each carrying its own repo sources, allowed-tools and outcome branches. Three routines exist on the account; one fires today (`alpaca_live` first-trade check, one-shot 2026-09-01T14:15Z, bound to a persistent session).

**But the standing automation is inert.** `Health Check Routine` and `Sprint Continue Work` are both `enabled: true` with `next_run_at: 0001-01-01` — **no schedule; they have never fired on their own.** Both were created in May, both still name `the-lizardking/ict-trading-bot` (renamed July), both target branches that no longer exist. Same pathology as `probes.yml` and `due-list.yml` having zero scheduled runs: mechanisms that look armed and are not.

**No GitHub workflow in the repo invokes Claude.** `continue-work.yml` states it in its own header — it validates a handoff file and surfaces it "so the next session or a human operator can pick up the work with one click." So today every session in this system starts with an operator click; autonomy lives entirely in Routines, outside the repo, mostly dead.

**Constraints to design around:** routine minimum interval is normally hourly · a routine delivers a *prompt*, so its usefulness is entirely a function of the state it can read on arrival · a running session has no inbox and cannot receive an answer mid-run without polling or being re-fired.

## Where each function runs

| | |
|---|---|
| **Mechanical — repo / VM / Actions, no Claude (17 of 24)** | A1 readout render · A3 propagation · A4 registry upkeep · A5 WIP enforcement · B3 dispatch · B4 context assembly · C1 evidence production · C4 decision recording · C6 verification *production* · D1 detection · D3 data-trust measurement · D4 obligation tracking · F1 state of record · F2 close-out *verification* · F3 concurrency · F5 rule binding · F6 notification delivery |
| **Requires Claude judgement** | B1 authoring · B2 decomposition · C2 disposition · C3 decision prep · C5 deployment · D2 incident fix · E1 constraint diagnosis · E2 capability build · E3 retirement · F4 lesson capture · *judging* C6 |
| **Requires the operator** | A2 priority · tier-gated approvals · the decisions themselves |

**Automation principle:** prefer a GitHub trigger wherever it is the best answer to the need — it is versioned, auditable and in-repo. Routines are for what a GitHub trigger cannot do today, which is *start a Claude session*. Dead routines get deleted rather than left claiming a cadence they do not have.

## The coordination layer

Two expectations: **no work is lost even if a session stops mid-way**, and **there is always full visibility of everything in flight**.

**Close-out alone cannot satisfy the first.** A session that dies cannot run its own close-out — which is precisely why `continue-work.yml` does not achieve this today: it depends on the session voluntarily finishing. Three mechanisms are required instead:

- **Incremental progress.** State is written to the work object *as work happens*, not at the end.
- **A lease per session.** A session holds a heartbeat on the objects it is working.
- **A reaper.** An expired lease is detected, and what was actually done is recorded against the object — by something that is not the dead session.

**The decision round-trip runs by urgency, declared on the work object.** A routine question queues for the next cycle. A question marked *blocking* ends the session cleanly and re-fires a fresh one when the answer lands — reloading context from the work object rather than holding it open.

## The control dashboard

A new section in the **Svelte SPA** — already HTTPS, browser-direct, deep-linkable, and where the Telegram pings already point. This is not a nice-to-have: **visibility is the enforcement mechanism the system has never had.** Every register today is invisible in practice, which is why they rot.

It shows: **anything waiting on the operator, at the top** · what is being worked on and by whom · how each piece fits the larger plan · what is next · what is blocked and on what. Decisions are answerable **from the UI** — including multiple-choice — so the operator is not the bottleneck on their own decisions.

Architecturally this is well-precedented: `POST /api/bot/prop/report` and `POST /api/bot/learning/progress` are already operator-write endpoints on the same FastAPI, and the SPA already reaches it browser-direct through Caddy. The decision-write path is the same shape as things that already work.

## Open architecture question — needs its own design pass

**Where coordination state lives** (work objects, leases, progress, decisions) is unresolved and is the critical path: the coordination layer, the dashboard and the decision round-trip all depend on it. Sessions reach state through git; the browser reaches it over HTTPS; the two want different things from it. The candidate shapes are the bot API + SQLite, versioned repo files projected to the API, or a split by lifetime (durable in the repo, volatile in the API). **This is deliberately not decided here** and gets a dedicated design pass before anything is built on top of it.

The trading system's own architecture is not the problem and does not need re-architecting for this workflow. What is new is the operating layer's architecture — the work-object store, the lease/coordination layer, the decision channel and the dashboard surface — and it should be derived from this structure rather than brainstormed freely.

## Calibration

**WIP ceiling: 8 work objects in flight.** It counts *work*, not intents — intents may be more numerous, most of them dormant. Chosen for comfort: at 8 the ceiling bites only on bad weeks, so it will rarely force a sequencing choice on its own, which puts the real weight on the cycle's registry review. If in-flight work drifts to the ceiling as a matter of course, the ceiling is doing nothing and should come down.

**Caps are hard, and exceptions are decisions.** A cap is enforced rather than advisory; anything that needs to exceed one produces a justification that becomes a decision for the operator. This applies to the WIP ceiling and to the intent cap alike — the point is that exceeding a limit is a visible, argued choice rather than something that happens by drift.

**Intent layer: hard-capped, reconciled each cycle.** Every cycle surfaces intents carrying no work and work carrying no parent intent; each is resolved or explicitly accepted. Exceeding the cap follows the exception rule above.

**Migration: carry everything, re-shape in place.** All existing rows — the 572 not-closed backlog items, the registers, the live legs — become work objects. Nothing is lost and nothing needs re-detecting. ⚠️ **Carrying everything is not the same as everything being open, and the distinction is what keeps this from reproducing the condition the redesign exists to end.** The ceiling counts work *in flight*, not work *in existence*: carried rows arrive dormant or accepted by default, and a row becomes in-flight only when it is given an owner, a dependency edge and a place under an intent. The registry may hold hundreds of objects; at most 8 are being worked.

**Notification contract: events plus a daily digest.** Event pings on decisions and state changes only — verdict written, decision recorded, deployment made, incident fixed, parent authored, WIP full, checkpoint reached — rate-limited per class, never on activity. Plus one rolled-up daily summary of everything autonomous, so nothing is invisible even when individually unpingworthy.

**Incident line: by severity band.** P0 (money at risk) — act immediately and ping. P1 — fix within the pass. P2 and below — accept and log; **an OPERATE session may not open work below P1.** The band decides, not the subject matter.

**Constraint readout contains four things:** where the chain is held up, with its evidence · the book and the money, with population and coverage · what is in flight against the ceiling and what has stopped moving · decisions waiting on the operator. Everything else is available on request rather than pushed.

---

## What is settled, and what comes next

**Settled here:** the value chain and its two support functions · priority by held-up stage, called by the operator each cycle · work as typed objects carrying their own context · the step-with-a-parent unit · object + role pack · four-way rule binding · the roadmap as intent + work, reconciled · blocked as a dependency edge so the constraint is computed · the 24 functions, their dependencies, and where each runs · five session types plus the cycle, with PRODUCE split from DISPOSE · continuous flow with checkpoints, light daily and deep weekly · ad-hoc work entering only as a work object · the autonomy grant and its notification condition · the coordination layer's three mechanisms · the decision round-trip by urgency · the dashboard in the SPA · the calibrations and migration posture above.

**Not settled, and deliberately absent:** the work-object schema and where coordination state lives (the next pass); what gets built, changed or removed; how the existing 32 skills, 124 workflows, 64 guards and 13 registers map onto these functions; and the order of any of it.

**The next stage is a single design pass covering the work-object schema and where coordination state lives** — the operator's call, on the grounds that these are the same problem approached from two sides. What a work object contains determines what the store must hold; where the store lives determines what a session and the dashboard can each do with it. Everything else waits on that: the coordination layer, the decision round-trip, the dashboard and the readout all sit on top of it.

Only after that: derive, function by function, what capability already exists and works, what exists but is broken or unread, and what is genuinely missing — and only from that derivation, a build plan.

Approving this document means the *structure* is agreed, not that implementation begins.
