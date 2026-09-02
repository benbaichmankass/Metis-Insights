# Operating Layer — Function Derivation: what exists, what is broken, what is missing

> **Status: MEASURED 2026-09-01.** Third pass in the operating-model series, after
> [`operating-model-DESIGN.md`](./operating-model-DESIGN.md) (structure + the 24 functions)
> and [`operating-layer-schema-and-state-DESIGN.md`](./operating-layer-schema-and-state-DESIGN.md)
> (work-object schema, state home, access posture).
>
> Grades each of the 24 functions against what the repo actually contains. **Still no
> build order** — this is the inventory a build plan would be derived from.

> ⚠️ **CORRECTION 2026-09-02 — THIS DOCUMENT IS A DATED SNAPSHOT AND ITS HEADLINE IS NOW STALE
> IN THE UNDERSTATING DIRECTION. The grades below are left exactly as measured on 2026-09-01;
> read this note beside them rather than reasoning from the table alone.**
>
> **All five functions graded MISSING have since shipped**, between that pass and 2026-09-02:
> **A3** priority propagation (`CYCLE-PRIORITY.json` rendered into `CLAUDE.md`) · **A5** WIP
> control (`scripts/ci/check_wip_ceiling.py`, enforced in CI) · **E1** constraint diagnosis
> (`scripts/ops/constraint_readout.py`) · **E2** as a governed function
> (`scripts/ci/check_capability_pull.py`, advisory) · **E3** capability retirement
> (`sunset-pass.yml` + `SUNSET-DISPOSITIONS.json`).
>
> **Shipped is not working, and two of the five have a measured gap:**
> - **E1/A1 is graded here as trigger `Cadenced` / autonomy `Full`. It has NEITHER.** No workflow
>   invokes `constraint_readout.py`; CI runs `--self-test` only, and that registration's own note
>   reads *"the readout itself is generated on demand, not in CI"*. Its sibling
>   `session-brief-guard` DOES run a `--check`, so the session brief cannot go stale and the
>   readout can. `BL-20260902-CONSTRAINT-READOUT-IS-CADENCED-BY-DESIGN-AND-HAS-NO-CRON-AND-NO-FRESHNESS-CHECK`.
> - **E3's machinery is referenced by ZERO of the 32 skills**, so no role pack sends a session to
>   it while 10 retirement candidates sit undispositioned.
>   `BL-20260902-E3-SUNSET-MACHINERY-IS-REFERENCED-BY-ZERO-SKILLS-SO-NO-SESSION-IS-EVER-SENT-TO-IT`.
>
> Also corrected: the E3 row below says *"nothing has ever retired a skill, register, workflow or
> guard."* That is **refuted for workflows** — 35 deleted since 2026-06-13 — and **holds for
> skills, guards and registers**, all at zero. Working:
> [`docs/audits/operating-layer-skills-workflows-inventory-2026-09-02.md`](../audits/operating-layer-skills-workflows-inventory-2026-09-02.md).

## The finding

**The mechanical layer is largely already built. What is missing is concentrated almost
entirely in DIRECTION and CAPABILITY-GOVERNANCE.**

Of 24 functions: **5 work**, **14 are partial** (present but broken, unread, unfired, or
voluntary), and **5 are missing outright**. The five missing ones are
**A3 priority propagation · A5 WIP control · E1 constraint diagnosis · E2 as a *governed*
function · E3 capability retirement** — which is precisely the arbiter and the forcing
function the structure identified as absent.

That is the diagnosis confirmed from the other direction: this system built **execution**
and **integrity** to a high standard and never built the **steering**. It is also good
news for cost — the build is small, and it is not where the effort has been going.

---

## A · DIRECTION — how work gets chosen

| | Function | Grade | Evidence |
|---|---|---|---|
| A1 | Constraint readout | **partial** | `render_due_list.py` (746 ln) → `DUE.json`/`DUE.md`, six declared sources with a `read`/`could_not_read` verdict per source. But it is an **owner-assignment list, not a constraint readout** — it says what is DUE, never where the chain is held up; its only reader is the `duty` skill; and it depends on E1, which does not exist |
| A2 | Priority setting | **missing (as a mechanism)** | The operator decides ad hoc. `ROADMAP.md` § "Next" and § "Cross-session priority order" exist as prose; **21 of 32 milestones are simultaneously active**, which is the measurement that says the mechanism is not working |
| A3 | Priority propagation | **MISSING** | Verified by search: nothing writes a current-cycle priority that a session reads. A decision taken with the operator reaches the next session only if a human retypes it |
| A4 | Registry upkeep | **partial** | `render_session_brief.py` (382 ln) renders OPEN-ITEMS + RECURRENCE-LEDGER into `CLAUDE.md` automatically — the one genuinely automated piece, and correctly placed (the only channel reaching a session before its first tool call). The registries it renders are maintained **by hand** |
| A5 | WIP control | **MISSING** | `scripts/ci/check_open_items.py` sets `MAX_ITEMS = None` deliberately. No cap on open parents exists anywhere |

## B · DEFINITION — how work becomes assignable

| | Function | Grade | Evidence |
|---|---|---|---|
| B1 | Authoring | **partial** | `backlog_append.py` is genuinely good — byte-exact round-trip, refuses a row restating an existing one, prints candidates. But it authors **defects**, not work objects; `research/queue/*.yaml` authors jobs (**5 ever**) |
| B2 | Decomposition | **doc-only** | `decomposition-rules.md` (276 ln) defines milestone → sprint → checkpoint, but points at `milestone-state.md`, which it declares frozen; `sprint-planning.md` declares itself subordinate to a workplan doc. No executable decomposition |
| B3 | Dispatch | **partial** | `dispatch_queue.py` (327 ln) + `research-queue-dispatch.yml` (cron `20 6 * * *`) dispatch **compute**. Dispatching a **session** works only through Routines, outside the repo. `continue-work.yml` explicitly does not run Claude |
| B4 | Context assembly | **partial** | The SESSION-BRIEF block works. Role packs exist as 32 skills. **No per-object context** — which is the half the anti-silo fix depends on |

## C · EXECUTION — the chain itself

| | Function | Grade | Evidence |
|---|---|---|---|
| C1 | Evidence production | **WORKS** | 13 `backtest_*.py`, ~100 `scripts/research/`, ~60 `scripts/ml/`, runner/trainer/GPU lanes, 9,744 measured cells across three corpora. The most mature thing in the system |
| C2 | Evidence disposition | **partial** | `research_disposition.py` exists and `survey()` works. The repo's own OI states it: **370 units, 103 dispositioned, 11 unread, 256 superseded-unread** — *"the machinery is healthy and the reading is in debt"* |
| C3 | Decision preparation | **partial** | `strategy_review_packet.py` is **1,518 lines** and emits a real action badge with reasons and an SLA — a genuine decision-packet generator. **No packet has ever been committed**: it writes to a gitignored path with no cron |
| C4 | Decision recording | **broken** | `strategy_changelog.json` last written **2026-07-28**; August's Tier-3 approvals live in coverage matrices, corpora and OPEN-ITEMS instead. `squeeze_breakout_4h` runs live against its own record saying it was demoted |
| C5 | Deployment | **WORKS** | PR + branch protection (3 required checks) + `system-actions` allowlist + the tier gates |
| C6 | Post-deployment verification | **partial** | OPEN-ITEMS `monitoring` rows with `check_every_days` + `clears_when`, plus the probe layer. **9 of 14 probes read `could_not_run`** |

## D · INTEGRITY — keeps the chain trustworthy

| | Function | Grade | Evidence |
|---|---|---|---|
| D1 | Liveness & incident detection | **WORKS** | Liveness watchdog, IB-gateway watchdog, account/trainer reachability, silent-refusal, prop-fills staleness, naked-position sweeps across three venues. Genuinely strong |
| D2 | Incident disposition | **partial** | `duty` + `health-review` skills dispose. No enforced severity band, and the backlog is the measurement: **filed vs closed +425 in August** |
| D3 | Data trustworthiness | **WORKS** | `src/runtime/provenance.py` as single owner, `broker_truth.py`, coverage on `/stats` and `/performance`, and a guard family enforcing it. The system's strongest layer |
| D4 | Obligation tracking | **partial** | OPEN-ITEMS `monitoring` rows are a real re-observation contract. The probe layer that should automate it is 9/14 `could_not_run`, and **`probes.yml` + `due-list.yml` have zero schedule runs since landing 2026-08-31T14:20Z** |

## E · CAPABILITY — unblocks a named stage

| | Function | Grade | Evidence |
|---|---|---|---|
| E1 | Constraint diagnosis | **MISSING** | Verified by search: nothing in the repo names a bottleneck stage. A1 cannot be honest without it |
| E2 | Capability build | **missing as a GOVERNED function** | It happens constantly — it is most of what the sprint record contains. What is missing is the **pull rule**: nothing requires a capability to be justified by a held-up stage, which is why 45 of 94 August sprints were governance/hardening/observability against 2 deployments |
| E3 | Capability retirement | **MISSING** | No sunset mechanism for machinery. 6 strategy retirements ever, none in five weeks; nothing has ever retired a skill, register, workflow or guard. Complexity is monotonic by construction |

## F · CONTINUITY — what makes sessions compose

| | Function | Grade | Evidence |
|---|---|---|---|
| F1 | State of record | **partial** | 13 registers; OPEN-ITEMS is the designated session-start read precisely *because* the backlogs are too large. No single state of record |
| F2 | Session close-out | **partial / voluntary** | `close_session.py` (354 ln) commits a handoff, pushes and dispatches — but it is **session-invoked**, so a session that dies never runs it. **Nothing verifies close-out and nothing reaps an abandoned one** |
| F3 | Concurrency coordination | **partial** | `session-board.json` + GitHub issue #6927, explicitly honour-system and advisory. The board holds 1 `active_sessions` row whose status is `done` |
| F4 | Lesson capture | **partial** | `RECURRENCE-LEDGER.json` (4 classes / 18 occurrences) each requiring an executable prevention — the right shape. The **64 CI guards are the strong half**: lessons that became code |
| F5 | Rule binding | **partial** | 64 guards bind mechanically. Skill-first lookup is prose-binding only. **No exit verification** — nothing checks a session followed the rules its work type required |
| F6 | Autonomous-action notification | **WORKS (as an alert path)** | `outcomes.py` → `pending-pings` → `send_ping.py` → Telegram, with severity levels. **No digest**, and no distinction between pinging on *activity* and on *state change* — the split F6 requires |

---

## What this implies for the build

**Five genuinely new things**, all small, all in steering:

1. **E1 constraint diagnosis** — compute which stage is held up from the dependency graph.
2. **A1 rebuilt on it** — the readout becomes a constraint readout rather than a due-list.
3. **A3 priority propagation** — write the cycle's priority where B4 puts it in front of every session.
4. **A5 WIP control** — a cap that is enforced, with exceptions surfaced as operator decisions.
5. **E3 retirement** — a sunset pass with a forcing function, for machinery as well as strategies.

**Three repairs of things that already exist and stopped short:**

- **C3** — cron the review packet and commit it to a non-gitignored path. The generator is 1,518 lines and has never produced a durable artifact.
- **C4** — one decision record again, instead of four forked surfaces.
- **F2** — make close-out verified rather than voluntary, and add the reaper. This is the one that needs the lease, and it is the mechanism behind *"no work is lost."*

**Two things to leave alone:** the guard suite (F4/F5's working half) and the provenance
layer (D3). Both are load-bearing, both work, and both already encode lessons that would
otherwise have to be re-learned.

⚠️ **A correction this pass produced.** `operating-model-DESIGN.md` calls the
`probes.yml` / `due-list.yml` non-firing *"the same pathology"* as the two dead Routines.
**It is not the same failure.** The Routines have **no schedule at all** and cannot fire;
these have a schedule (`20 5 * * *`, `50 5 * * *`) and missed a window one day after
landing, against `health-snapshot`'s 438 successful schedule runs. Both deserve attention;
conflating *cannot fire* with *has not yet fired* is the collapsed-state error this repo
has a guard for.
