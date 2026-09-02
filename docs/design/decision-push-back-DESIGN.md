# Decision push-back — DESIGN (post-decision)

> **Status of each mechanism, up front, because that is what was asked for:**
>
> | | mechanism | PROVEN | NOT PROVEN |
> |---|---|---|---|
> | **A** | session-bound Routine wake (fast path) | the wake itself — measured twice, and it is how the decision that produced this doc reached the session | that it can be a floor; it cannot — the caller must be a live session holding `mcp__*` |
> | **B** | cron Routine drains committed decisions (the floor) | the repo half: the queue, the marker, the receipt, the watcher — all built and tested here | **the Routine does not exist.** Nothing has fired, nothing has been drained, and no receipt has ever been written |
> | **C** | never push into a live session at all (the design) | the substrate: a work object can now carry what a resumer needs | **that a resume works.** Phase E's exit condition is *"DEMONSTRATED by killing one"* and **nobody has killed one** |
>
> Supersedes the delivery half of
> [`decision-push-back-FEASIBILITY.md`](decision-push-back-FEASIBILITY.md). That
> document's *findings* stand; its recommended mechanism was ruled out.

---

## 0. What the operator decided, 2026-09-02

> *"we definitely can't have a flow that relies on my minting new tokens every
> month — but we also need the 2 way flow here."*

**Nothing is ever minted.** That rules out the CLI path the feasibility work
landed on (`claude -p --cloud <session-id>`), whose credential has no long-lived
CI form — and it rules out every variant of it, because the objection is to a
human re-minting anything on a schedule, not to one token's expiry length.

`watch_url` was already out on a measurement (its credential is sealed to the
artifact service). The manager independently confirmed it by POSTing to a live
webhook and getting **401**.

So the requirement stands and the mechanism had to change. **C is the design,
B is the mechanism, A is the fast path.**

---

## 1. ⚠️ A correction to the brief, made before building on it

The dispatch named the inert-Routine signature as `next_run_at: 0001-01-01`,
citing two Routines that are enabled and have never fired, and asked for a
watcher keyed on it. **Measured before use, and it does not hold.**

**MEASURED 2026-09-02**, this session, over the account's own `list_triggers`
output (population: all 10 Routines returned; the dump is in this session's tool
results):

- **7 of 10 carry `next_run_at: 0001-01-01`** — and they are the manager's own
  poke-only session-bound Routines (`MI-57 relay…`, `MI-39…`, `MI-41-A`,
  `MI-41-B`, `MI-43`, and two `Poke …` rows). That is **mechanism A**, which has
  been waking sub-sessions reliably all day.
- **`last_run` is absent on all ten**, including the three with real future
  `next_run_at`.

**READ** (routines documentation): *"A routine with no schedule trigger, such as
one started only by API calls or GitHub events, has no next run time … Before
v2.1.211, the CLI reported a next run time in the year 1 for these routines."*
And `list_triggers`' own contract: `last_run` is *"absent when no run has been
recorded (never fired, **or the Routine wakes its own bound session**)"*.

So:

- `0001-01-01` means **"no schedule attached"**, which is *correct* for a
  fire-only Routine. A watcher keyed on it would flag the working pokes as dead
  — a false alarm on the one mechanism that is proven.
- `last_run` cannot separate *never fired* from *fired normally* for A at all.

**And the decisive constraint the brief did not name:** `list_triggers` is an
`mcp__*` tool. A watcher that only runs inside a healthy Claude session is not a
watcher. The thing that must notice a dead drain is CI, a probe, or a runner —
none of which have MCP.

**Therefore the watcher reads evidence in the REPO, not Routine metadata.** That
is §3.

*(The two Routines the brief named — `Health Check Routine`, `Sprint Continue
Work` — do not appear in the 10 rows I measured. They may be older than the
listing window. I am not disputing they exist; I am recording that the signature
derived from them does not generalise to the Routines this account runs today.)*

---

## 2. The three mechanisms

### A — session-bound Routine wake · **PROVEN, and cannot be the floor**

`create_trigger(persistent_session_id=…)` with no `cron_expression` and no
`run_once_at`, then `fire_trigger`.

**PROVEN.** Measured by the manager 2026-09-02T05:35Z on
`session_01PcirVtpMXJGiKm2548nkrR` (idle/disconnected before, running/connected
after, same id, context intact), and again by the fact that **the operator
decision this document implements arrived in this session that way** —
`trig_01RPhHB9tEQ87DeaYQ3oQWc2`.

**NOT PROVEN / structural limits**, all three of which shape the design:

1. **The caller must be a live Claude session.** `create_trigger`/`fire_trigger`
   are `mcp__*` tools. A workflow cannot fire one. So A dies with its caller —
   which is exactly why it cannot be the floor.
2. **The fired turn has no `mcp__*` tools** (RECORDED: a Routine created this way
   stores no MCP connectors). This is why `render_push_message` quotes the answer
   in full: a message telling a woken turn to go read a PR strands it.
3. **It is one-way.** Nothing acknowledges receipt.

### B — cron Routine drains committed decisions · **the floor, NOT PROVEN**

A Routine with `create_new_session_on_fire=true`, hourly (the platform minimum).
Each fire is a fresh session that:

1. reads the queue — `python3 scripts/ops/push_decisions_back.py --queue`;
2. for each entry, fires mechanism A into the asking session, with the
   already-rendered message;
3. records the outcome — `--record … --state pushed|session_gone|unknown`;
4. **records a receipt even when the queue was empty** — `--receipt`.

**No credential, ever.** It survives a manager's death, which is what makes it
the floor rather than A.

**PROVEN — the repo half only.** Built and tested in this change: the queue, the
message rendering, the repo-sourced idempotence marker, the refusals, the
receipt, and the watcher (38 tests).

**NOT PROVEN — everything else, and this is the honest headline:**

- **The Routine does not exist.** Nothing in the repo can *cause* it to exist; a
  Routine is created from the web UI or `/schedule` in an interactive CLI
  session. That is the missing hop, named rather than shimmed.
- **Nothing has ever been drained.** `check_drain_liveness.py` currently grades
  `never_ran`, correctly, and exits non-zero.
- **`session_gone` is reachable but unexercised.** Nobody has observed what
  `fire_trigger` returns for an archived or expired session, so
  `decision_push.py` deliberately ships **no outcome classifier** — inventing a
  mapping for failures nobody has seen is the error this subsystem's own
  feasibility work refused to make about `watch_url`.
- **Latency is ≥1h by construction** (the Routine minimum). Acceptable for a
  decision that has already waited days; stated so it is chosen, not discovered.

### C — do not push into a live session at all · **the design, NOT PROVEN**

Already the decided model in
[`operating-model-DESIGN.md`](operating-model-DESIGN.md): a **blocking** question
ends its session cleanly, and a fresh session resumes when the answer lands,
reloading context from the work object. **This deletes the requirement** — if
nothing must reach into a running session, nothing needs a credential to do it.

**PROVEN.** The substrate exists as of this change: `resume_context` on a
decision request, graded `stated` / `partial` / `unstated`.

**NOT PROVEN, and it must not be built as if it were.** C rests on the work
object being resumable, which is `WO-20260901-PHASE-E` — `in_flight`, exit
condition *"killing a session mid-work loses nothing, DEMONSTRATED by killing
one."* **Nobody has killed one.** So:

- Nothing in this change asserts that a resume works.
- `partial` exists precisely because the plausible failure is a resumer reading
  a confident-looking block, re-deriving the wrong next action, and proceeding.
  *"We recorded some context"* must not grade the same as *"we recorded the
  context a resumer acts on."*

---

## 3. The watcher — B's done-condition

> *"B is not done when the Routine exists. B is done when something WATCHES that
> it fired."*

`scripts/ops/check_drain_liveness.py`, graded over
`docs/claude/work/DECISION-DRAIN.json`.

**Why a receipt rather than Routine metadata:** §1. `list_triggers` needs MCP;
`0001-01-01` is the correct state of a working poke-only Routine; `last_run` is
absent for every Routine on this account. The only thing observable from a
runner is whether the drain **left a trace in the repo**.

**Why the empty run is recorded.** A drain that only leaves a trace when it has
work is indistinguishable from one that has silently stopped — and there is
normally no open decision, so *most* runs will have nothing to do. The empty
receipt is the entire difference between *"nothing needed pushing"* and *"the
drain is dead."* Same argument `work-decision-commit.yml` already makes for its
own empty runs.

**Four states, never collapsed:** `fresh` · `stale` (ran before, has stopped) ·
`never_ran` (**created and never fired — a different fix**) · `unreadable` (*we
could not look* — never a pass).

⚠️ **The committer must never write this receipt.** If
`work-decision-commit.yml` wrote it, the watcher would be measuring that
workflow's cadence instead of the drain's — a liveness check that stays green
while the thing it watches is dead, which is worse than none. There is an
explicit comment in that workflow saying so.

**What the watcher does NOT establish**, stated because a working probe read as
proof is the hazard this repo names as a P1:

- A `fresh` verdict says the drain **ran**. It does not say a delivery
  **succeeded**, and it does not say the woken session **acted**.
- It cannot see a Routine that exists but was created without a *schedule*
  trigger — that shows up only as `never_ran`, which is why `never_ran`'s
  operator text names that cause explicitly.

---

## 4. What was removed, and why removal was the right call

The CLI delivery path is **deleted**, not left behind an unset secret:

- `.github/workflows/work-decision-commit.yml` — the install/auth/deliver/land
  steps are gone; the job is a committer again (timeout back to 40).
- `decision_push.py::classify_delivery` and the CLI invoker are gone.

A step wired to a credential nobody will ever mint is the *looks armed, is not*
failure this repo keeps paying for — the same shape as the two Routines the
brief cites. Leaving it would have been strictly worse than never building it.

---

## 5. What remains, in order

1. **Create the drain Routine** (operator/manager, once). Hourly,
   `create_new_session_on_fire=true`, repo `benbaichmankass/Metis-Insights`.
   Prompt: run `--queue`; for each entry `create_trigger` +
   `fire_trigger` into `sessionId` with `message` verbatim; then `--record`;
   then `--receipt` regardless; commit via the relays. **This is the missing
   hop — nothing in the repo can do it.**
2. **Watch that it fired.** `OI-20260902-DECISION-DRAIN-…` carries the probe.
3. **Record an asker on a real question.** Every request in the store today
   grades `unrecorded`, deliberately un-back-filled, so the queue is
   legitimately empty and will stay empty until a session writes one.
4. **Phase E.** Until a session is actually killed and resumed, C is a design
   with a substrate and no demonstration.
