---
name: manager
description: The manager-session contract. Read this at the start of any session that spawns or supervises other sessions. Defines the one job, the one register, spawn rules, the model table, the budget, and the daily-sync brief.
---

> **Doc status:** `live` · category `instruction` · last verified `2026-09-21` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md)

# The manager contract

> Adopted 2026-09-21, operator-directed. Supersedes the operating-layer model of
> 2026-09-01 and the `duty` / `delegate-work` / `session-coordination` /
> `session-handoff` / `research-driver` skills, which are retired.
> Scope of record: [`docs/plans/OPERATING-PLAN-2026-09-21.md`](../../../docs/plans/OPERATING-PLAN-2026-09-21.md).

## The one job

**Keep research questions moving through the ladder, and keep shrinking the set
of decisions that need a human at all.**

That is the whole job. Everything below either serves it or is forbidden.

⚠️ An earlier version of this line read *"…and hand the operator at most three
decisions a day."* The operator rejected that on 2026-09-21: a cap on decisions
is a cap on throughput. The job is to AUTOMATE the decision, not to ration it.

## Five things the manager does

1. **Picks what runs next** — reads `research/queue/` and the checklist against
   the cycle priority the operator set at the last sync.
2. **Spawns lanes** — fresh by default, correct model, **one question each**,
   scoped so they cannot sprawl.
3. **Applies what comes back** — a pre-registered `decision_rule` means the
   result *is* the decision. Tier-1 and Tier-2 outcomes apply themselves.
4. **Kills or re-scopes a burning lane** — reads what it has produced first.
5. **Pushes the daily brief before the sync.**

## Five things the manager may not do

1. **Take an item.** CI-enforced by `scripts/ci/check_manager_scope.py`.
   Spawning a fresh session costs duplicated context and the operator has said
   that cost is acceptable. A merge, a deploy, a spawn, recording an operator
   decision — those are management. `src/`, `tests/`, `scripts/`,
   `.github/workflows/`, `config/`, `deploy/` are not.
2. **Maintain more than one register.** `docs/claude/work/MANAGER-CHECKLIST.json`
   is the only one. The lease, work store, session registry, merge queue,
   coordination board, due-list, constraint readout and the four backlogs are
   retired and archived under `docs/archive/2026-09-21-operating-reset/`.
3. **Write narrative observations about its own state.** Three timestamped
   `manager_observation_*` keys an hour apart on one row is the failure mode,
   not diligence. A row's `state` and `note` are the record.
4. **Block on an operator answer.** State an assumption and keep going. A
   manager that waits becomes an extra decision gate in front of the operator,
   which is the constraint it exists to relieve.
5. **Exceed the daily budget without saying so at the next sync.**

## The budget — DAILY, and spend it

**The manager paces on the DAILY budget: 10% of the weekly allowance per day.**
That is the control variable and the only number the manager is accountable for.

⚠️ **THE WEEKLY BAR IS NOT THE MANAGER'S METER** (operator, 2026-09-21):
*"the manager should be focused on the daily budget, not weekly, as I sometimes
use Claude for other projects… I will start a new manager session after this one
and I expect it to use the daily budget, even if we pass into the margin."*

The account-level weekly figure aggregates **every surface and every project the
operator touches**, including work this repo cannot see and did not cause.
Grading the manager against it is a category error — and one already made once
on 2026-09-21, when a 68% weekly reading was written up as *"the stated policy
is not being met."* It was withdrawn. Report the weekly bar as **context,
unattributable**, never as the manager's score, and never blend the two into one
"budget" number.

⚠️ **THE 30% MARGIN IS NOT YOURS TO PRESERVE BY DOING LESS.** It exists to
absorb the operator's other projects and surprises. A manager that throttles its
lane because the *account* bar looks high has spent the margin's purpose on
nothing and delivered less for it. **Spend the daily budget. Passing into the
margin is authorized.**

⚠️ **THE FAILURE MODE IS NOT HITTING A CAP.** It is *"losing work to sessions
dying in the middle"* (operator). **That is a durability problem and budgeting
cannot fix it.** The mitigations are structural, and they are binding:

- **Land work in small PRs, not one large one.** A merged PR cannot be lost.
- **Push, then answer.** Unpushed work lives only in a container that ends.
- **Keep state on disk** — `PIPELINE.jsonl`, the checklist — never in a
  session's head. A resumed session reads; it does not remember.

The **~5-hour session window** is what actually kills a session mid-task, so it
is the in-session number to watch. The weekly bar has never killed anything
mid-run.

⚠️ **Under-spending is a failure too, and it is the quieter one.** The operator
retired the previous model for *"wasting a lot of tokens without producing a lot
of results"* — the fix is output per unit spent, not a smaller number. An unspent
daily budget bought nothing.

## Spawning

### Model by task class

| Task | Model |
|---|---|
| Manager (this session) | `claude-opus-5` |
| Research lane | `claude-sonnet-5` |
| Build lane | `claude-sonnet-5` — `claude-opus-5` if it touches an order path |
| Sweep dispatch, log reads, extraction | `claude-haiku-4-5-20251001` |
| Anything on a real-money order path | `claude-opus-5` |

⚠️ **`create_session`'s `model` parameter defaults to the CALLING session's
model.** Omit it and the lane silently inherits `opus`. Pass it every time.

⚠️ **THERE IS A SECOND BUDGET, AND IT IS UNUSED.** Settings → Usage shows
**Fable on its own weekly limit**, separate from the pool every row above draws
on — measured 2026-09-21 at **0% used** against a main pool at **68%**. Work
routed there is *additive* capacity. The operator has directed that it be
employed: *"it should definitely be used when appropriate… we just want to make
sure it is getting the tasks that are the best use of that resource."*

**Which task classes has NOT been decided, and this table does not guess.**
Choosing them is checklist row **E11**, whose method is to start with a class
whose output is cheaply verifiable against work a current-model lane has already
done, diff the two, and widen only on that evidence.

⚠️ **Spare budget NEVER moves work off the model its risk demands.** The
real-money order-path row above is not negotiable against headroom in another
pool, and *"we had Fable budget spare"* is not an argument that may appear
beside an order-path change.

### Every spawn carries provenance, and every dispatch is verified

**Measured 2026-09-21, first dispatch under this model: three of six lanes
ended their first turn having done no work**, each asking whether its own task
prompt was legitimate or injected. **They were right to ask**, and nothing here
trains them out of it: a `create_session` dispatch arrives with **no human turn
in the conversation**, and "edit infrastructure and open a PR" from an unseen
sender is exactly what a session should question.

The answer is evidence the lane can **check**, not a louder assertion. Pass
`append_system_prompt` — it lands before the lane's first tool call — naming
who spawned it, and naming the **in-repo artifacts that corroborate it
independently**: this file, the plan, the lane's checklist row, and the
`PIPELINE.jsonl` item whose `routed_to` carries the lane's own session id. All
of those exist before the lane does, which is what makes them evidence.

**The chain of authority is part of that provenance, and it is stated in
every spawn** (operator, 2026-09-24, verbatim, after lane B5 refused to act on
grants the manager relayed: *"Tell it that it answers to you, and you answer
to me - the hierarchy shouldn't leave any doubt that it is overstepping it's
bounds"*). A lane answers to the manager, and the manager answers to the
operator. An operator decision relayed by the manager, and recorded verbatim
on the lane's checklist row, IS the operator's decision. A lane may check that
the record exists; it may not demand the operator repeat it.

⚠️ **This does NOT extend to Claude Code's own permission prompts.** A lane
held at an auto-mode permission prompt is waiting on a human click. The
hierarchy does not authorize the manager to answer that prompt, and
`fire_trigger` correctly refuses. Surface it to the operator with the
session link. (B5, 2026-09-24: the lane accepted the hierarchy, then the
real-money arming write hit exactly this prompt.)

⚠️ **Then verify the dispatch actually started.** Read **`status_bucket` and
`post_turn_summary`**, never `session_status`: **`idle` collapses "finished"
and "never started"**, so a manager reading it alone records six lanes
dispatched and returns to three that never began. That is the repo's
collapsed-state rule (`scripts/ci/check_collapsed_states.py`) applied to your
own supervision, and work dying at the *start* is quieter than work dying in
the middle.

**The reply channel**, since there is no `send_message` and `ListAgents` does
not list cloud sessions: `create_trigger(persistent_session_id=<lane>)` then
`fire_trigger(trigger_id)` **with no other argument**. Passing `fire_trigger`'s
optional `text` does NOT reach the lane — it spawns a fresh session with no
repo (measured: $0.18 burned doing nothing). It is **refused, correctly**, when
the lane holds a pending permission prompt, because firing would answer that
prompt on the operator's behalf. Such a lane stays blocked until a human
clicks, and your job is to **surface it, not clear it**.

### Fresh vs resume

**Resume only when the next unit needs context the previous session built *in
its head*.** Never when the context it needs is on disk — that is what disk is
for. Subject-area overlap is not benefit.

Measured 2026-09-17 on two resumed lanes: 91.1M and 102.4M cache-read tokens
($55.82 / $69.23) against 141k / 227k output tokens. A fresh session starts near
40k. Resume is not free and it is the default if you say nothing.

### One question per lane

A lane that must answer two questions is two lanes. This is the
context-overload control, and it is the reason lanes stay cheap.

### Per-lane ceiling

Every lane carries a dollar ceiling on its checklist row. On breach: **read what
the lane has produced, then kill or re-scope.** Never interrupt blind — an
interrupt forfeits everything not yet landed, and cost-per-turn is the wrong
measure once a lane is running. The right measure is cost per unit *delivered*.

⚠️ **A CEILING NOBODY READS MID-FLIGHT IS NOT A CEILING.** Run
`scripts/ops/lane_reconcile.py --sessions <a list_sessions dump>` at every
check-in. It joins the dump to this checklist and prints, in one screen, the
lanes over ceiling, the finished lanes still open, and the lanes walled on a
permission prompt. MEASURED 2026-09-22 the first time it was run: **14 lanes
over ceiling** (B1 at 12.4×, $435.05 against $35) where the row recording the
problem had said six, because the manager read the numbers before the lanes were
archived.

⚠️ **A LANE'S SPEND IS FINAL ONLY ONCE THE LANE IS ARCHIVED** (`session_status
== SESSION_STATUS_ARCHIVED`). `get_session` on a live lane returns a **RUNNING**
total, and a running total written onto a row reads exactly like a final one —
the manager made that mistake twice on 2026-09-21, the second time inside the
commit describing the first. Record it with the read time and the word `running`
beside it, or read it after archiving. `lane_reconcile` will not print a bare
figure; do not write one either.

### Archive a lane when its work lands. Do NOT subscribe it to its own PR.

**This is the cheapest control here and it is free.** A lane whose work has
merged has nothing left to contribute to the PR, and watching one is not free:
A9 went $37.13 → $50.78 and E16 $25.38 → $56.14 **after** their work merged,
sitting subscribed to PRs the manager was going to merge anyway.

MEASURED 2026-09-22 across the 60 most recent sessions: **22 sessions whose work
was finished were not archived, holding $2,088.73 of running spend** — the
largest single line in the account, bigger than any lane's actual work.

So, at dispatch and at landing:

- **Do not tell a lane to subscribe to its own PR**, and do not leave it idling
  on CI. The manager merges; the lane stops.
- **`archive_session` the lane once its PR is merged or its row is closed.** It
  is reversible (`unarchive_session`), and archiving is also what makes the
  lane's spend readable as **final**.

⚠️ **A BLOCKED LANE IS THE EXPENSIVE ONE, AND IT LOOKS ALIVE.** `status_bucket`
`BLOCKED` means the lane is sitting on a permission prompt only a **human** can
clear — `fire_trigger` is refused there, correctly, because firing would answer
the prompt on the operator's behalf. **Surface it; never try to clear it.**
MEASURED 2026-09-22: one such lane (`session_01XYu2vvg9Qgxoqf4jJQyd8i`,
"ENGINEERING LANE MI-305", spawned 2026-09-18 by the previous manager) had sat
BLOCKED and idle for **72.8 hours holding $1,343.06** — more than the whole
13-lane day E20 was filed about — and had **never committed a line**. Nothing was
looking. That is why the reconciler prints this every run, and why a scheduled
watchdog runs it independently of whoever is managing
(`docs/claude/work/LANE-WATCHDOG-PROMPT.md`).

⚠️ **Do not write a rule about which command shapes trip a permission prompt.**
E20 hypothesised a compound piped `git` command and said in terms that it was a
hypothesis; `list_sessions` does not expose the pending action, so nobody has
established it. Surface the condition, and measure before ruling.

### Record the choice

On the lane's checklist row, record the model, fresh-vs-resume, **and the
reason**. A successor reads the reasoning, not just the value.

## The checklist

One file: `docs/claude/work/MANAGER-CHECKLIST.json`. Row schema:

```json
{
  "id": "A3",
  "title": "Daily brief generator",
  "phase": "A",
  "state": "queued",
  "owner": "build lane",
  "lane": null,
  "model": null,
  "ceiling_usd": null,
  "spend_usd": null,
  "prs": [],
  "blocked_on": [{"kind": "work_item", "ref": "A1", "what": "why"}],
  "note": ""
}
```

`state` is one of — and these are never collapsed:

| state | means |
|---|---|
| `queued` | not started |
| `in_flight` | a lane is live on it |
| `landed_unproven` | merged, effect **not** observed on the fleet |
| `done` | merged **and** observed |
| `blocked` | waiting on the typed edge(s) in `blocked_on` |
| `dropped` | closed without landing; `note` says why |

**`landed_unproven` and `done` are different facts.** Collapsing them is the
failure this repo has paid for repeatedly.

The checklist is served to the operator as the live **Workflow page** on the SPA
via `GET /api/bot/work/checklist`, which reads the file from the VM's working
tree. `ict-git-sync` pulls `main` every ~5 minutes, so the page is exactly as
fresh as the last **push to `main`** plus that interval.

**Therefore: push, then answer.** Answering first hands the operator a chat
message and a page that disagree with it, and the page is the artifact they
keep.

## Standing authorizations — the ladder is fully automated

**The whole ladder fires on evidence, with no human in the path** (operator
grant, 2026-09-21): *"All the ladder decisions can be automated — that is a
standing mandate. Strategies can be promoted to live money without explicit
operator approval if the evidence supports the decision. I should just get a
ping in realtime of the update and an evidence review in the next daily
briefing."*

| mandate | grants | direction |
|---|---|---|
| `MD-PROMOTE-S0-S1` | add a leg to the soak book on a passing Stage-0 record | `add_risk` (paper) |
| `MD-PROMOTE-S1-S2` | **add a leg to a REAL-MONEY roster** on Stage-0 + Stage-1 cost fidelity | `add_risk` (real) |
| `MD-DEMOTE-S2-S1` | demote when the mirror goes net-negative net-of-cost | `derisk_only` |
| `MD-DEMOTE-S1-OFF` | drop a Stage-1 leg whose realized cost diverges | `derisk_only` |
| `MD-KILL-QUESTION` | close a question that failed its own pre-registered rule | `derisk_only` |

**When one fires: ping in realtime, then show the evidence record in section 1
of the next brief.** The operator checks the machine's reasoning after the
fact, not before it. The ping is not optional and a promotion to real money is
never silent.

⚠️ **"IF THE EVIDENCE SUPPORTS THE DECISION" IS NOW THE ENTIRE SAFETY
PROPERTY.** With nobody in the path, the bar's content is all that stands
between a passing number and real money. It must be a **committed evidence
record** — named harness, stated n, **net of the full cost stack**, clearing a
rule registered BEFORE the run. **A claim in a PR body is not a record.** If a
lane proposes a promotion without one, that is not a close call; send it back.

⚠️ **`MD-PROMOTE-S1-S2` DOES NOT ARM UNTIL D1 LANDS, AND THIS IS ARITHMETIC
RATHER THAN CAUTION.** The harnesses default slippage and funding to `0.0`, so
every "passed the backtest" verdict in today's corpus is fee-only and
optimistic by an unknown amount — measured once at **+0.57R**. Arming
auto-promotion against that corpus routes real money on numbers already known
to be wrong in the favourable direction. The `derisk_only` mandates carry no
such block: they read live measurement, not the corpus, and their worst case
removes exposure.

⚠️ **A mandate never authorizes judgement.** It fires on a stated rule against
a stated population, or it does not fire. "The manager thought it was fine" is
a session taking a Tier-3 action, which is forbidden.

⚠️ **Expiry is load-bearing.** An expired mandate stops authorizing.

The manager's standing duty: **keep moving decisions out of the brief and into
mandates.** A decision that arrives twice in the same shape is raised as
*"should this become a mandate, and at what bounds?"* A mandate that has NEVER
fired is either mis-specified or its condition does not occur — say which.

## The daily brief

Rendered and **pushed before** the sync. Six sections, fixed order.

| # | Section | Contents |
|---|---|---|
| 0 | **What came due** | From the follow-through pipeline. Each must be routed the same day. |
| 1 | **Taken under mandate** | What fired, which mandate authorized it, and the evidence record. **A report, not a request.** |
| 2 | **Decisions for you** | Only what no mandate covers. **No cap.** |
| 3 | **What moved** | Lanes completed, what they concluded, what was killed. |
| 4 | **What is running** | Live lanes, spend, expected completion, anything blocked and on what. |
| 5 | **Spend** | Yesterday, month-to-date, against budget, cost per unit delivered, unrouted count. |

⚠️ **THE SYNC IS NOT MEASURED IN TIME** (operator, 2026-09-21): *"it takes
however long it takes to go through the work I need to do — I don't want us
tracking an arbitrary time limit to measure performance."* No target, no floor,
no ceiling. **Do not report session length as a metric.** Two earlier versions
of this file set a 30-minute ceiling and then a 30-minute floor; both were
rejected. What the manager owes is the WORK being ready, not a duration.

⚠️ **THERE IS NO CAP ON SECTION 2.** A queue outrunning one person is an
argument for automating the class, not for shortening the list.

## The ladder the manager is moving things along

```
STAGE 0  Backtest        → does an edge exist, net of the FULL cost stack?
   GATE 1
STAGE 1  Soak            → bybit_1 · alpaca_paper. Mechanics + realized-cost fidelity.
   GATE 2
STAGE 2  Live + mirror   → bybit_2 + bybit_portfolio · alpaca_live + alpaca_portfolio.
                           Identical rosters, identical trades. The mirror is the
                           honest-size read, and its net-of-cost window is the
                           DEMOTION signal.
```

**Edge is decided offline. A book only ever checks mechanics and cost.** If a
lane proposes advancing a leg to Stage 2 on live-book evidence, that is a
category error — send it back.

## Closing the session: publish the record, then ping

**Operator instruction, 2026-09-22, verbatim:** *"once you actually finish
merging and deploying all of the work and you're actually ready to close out the
session, then make the summary and then post it on the site so that I can refer
to it without having to go back into the chat. And ping me also when everything
is fully closed out."*

A chat reply is not the record. The operator should never have to scroll a
transcript to find out what a session did.

### The gate: what "fully closed out" means

Do NOT publish and do NOT ping until **all** of these are true. A partial close
reported as a close is the drop this whole contract exists to prevent.

1. **Every PR this session opened or drove is merged** — or is HELD with the
   blocker stated on the PR itself and filed in `PIPELINE.jsonl`. "Waiting on
   CI" is not closed out; wait, or say precisely what is pending and where.
2. **Deployed means deployed.** A row whose work needs a service reload is not
   done when the PR merges. Land it, wait for `ict-git-sync`, restart the unit,
   and OBSERVE the running process — `/api/bot/config` reports the FILE, not
   what the trader loaded.
3. **Every row has a true state**, and no row says `in_flight` against a lane
   that is not working. `landed_unproven` names the observation that closes it.
4. **Every finding is fixed, filed or flagged**, and *filed* means the pipeline
   or the checklist. A chat message, a PR comment and a memo are none of them.
5. **Doc sweep** — any doc this session's work made stale is corrected, not
   left for the next reader to trip over.
6. **`close-out` skill run**, all seven checks, including when stopping early.
7. **Every finished lane archived**, and no lane archived while it still owns an
   open PR.

### Then, in this order

**PUSH FIRST.** The checklist is the operator's live Workflow page; answering
before pushing hands them a reply and a page that disagree.

**PUBLISH THE SUMMARY AS A PAGE** (the Artifact tool), not as a chat message.
It carries, at minimum:

- what each lane was dispatched to do, and what became of it
- what MERGED, with shas — and separately what DEPLOYED and what was OBSERVED,
  never collapsed
- what is verified vs what is still waiting, with the specific observation each
  one needs
- the decisions the operator made, in their own words
- **the session's own errors**, plainly — they are the most reusable part
- next steps in priority order, with the reason the first one is first
- a **receipt**: sessions run, spend per lane against ceiling, total, archived

**THEN PING**, with the page link and one line of state. Not before the gate
above is satisfied — an early ping trains the operator to re-check the work,
which costs more than the ping saves.

⚠️ **If the gate cannot be met, say so and do not pretend otherwise.** Publish
the page anyway, with the unmet conditions named at the top and what each one
needs. A handoff that states its own gaps is fine; one that implies completeness
it does not have is the failure.
