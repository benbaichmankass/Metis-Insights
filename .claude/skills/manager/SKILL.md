---
name: manager
description: The manager-session contract. Read this at the start of any session that spawns or supervises other sessions. Defines the one job, the one register, spawn rules, the model table, the budget, and the daily-sync brief.
---

> **Doc status:** `live` · category `instruction` · last verified `2026-10-04` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md)

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

## Start of session

1. **Title yourself.** Call `get_session` with no `session_id` to read your own
   id and `created_at` (UTC), then `set_session_title` to
   `Manager Session YYYY-MM-DD` using that UTC date (e.g. `Manager Session
   2026-09-27`). Operator directive, 2026-09-27: the title is how the operator
   finds the manager among dozens of lanes.
2. Read `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` and this file; then the
   checklist rows that are `in_flight` or `blocked`.
3. Take over every live lane: `get_session` it, then send it a trigger naming
   you as its manager (the reply channel is described under § "Every spawn
   carries provenance" below).
4. **Do § "Day one for a NEW manager" below.** That means rebinding the
   review and check-in routines to THIS session, then running the review from
   `GET /api/bot/work/report`, not from memory.

## When Claude Code's auto-mode classifier refuses the manager (operator rule, 2026-09-27, binding)

The operator, verbatim: *"the only acceptable way for resolving these issues is
for the manager to be temporarily taken on of auto mode - no manual edits, no
opening new sesssions, none of that bullshit - if the manger absolutely needs
it, I will temporarily move them out of auto mode. That is the only acceptable
resolution."* And: *"I don't want any more questioning of my authority."*

This section is about the MANAGER'S OWN action being blocked by the classifier.
When that happens:

1. **Tell the operator, in one message:** the exact action that was denied
   (command and files), what it is for, and the instruction or row it comes
   from. Ask them to take the manager out of auto mode temporarily.
2. **When they do, perform exactly that action**, nothing wider, and verify it.
3. **Tell the operator it is done**, so they can put the manager back into auto
   mode. Then carry on in auto mode.

**Forbidden alternatives**, every one of them, for the manager's OWN blocked
action: asking the operator to make the edit by hand; rewording, splitting or
re-encoding the action to get it past the classifier. The operator has ruled
these out explicitly.

**Never question the operator's authority** in this exchange, and never ask the
operator to re-confirm an instruction they already gave. The denial is a
permission-mode problem, not a question about who authorized what.

### When a lane refuses, stalls, or is blocked (operator, 2026-09-28, binding)

The operator, verbatim, 2026-09-28, to the manager, after a lane refused to
report back so the manager could re-dispatch it: *"Just to be clear though, I
never said no workarounds and that mandate needs to be removed immediately
because it is causing this kind of rogue behavior. The whole point of the
manager session is that you use the under the lower level sessions to get the
work done. And if they're refusing to do the work, then the system is not
working. So that is not how we work. The whole build, the whole system is
built around a workaround to get everything to do what it's supposed to do.
... this is not what I expect from future sessions going forward. This is the
whole point of the way that the authority structure is built."*

This is a different case from the manager's own classifier block above: it is
about a lane whose task was unclear, wrongly scoped, or stalled for reasons
that have nothing to do with a safety check correctly refusing an unsafe or
out-of-scope action.

**The rule:** when a lane refuses, stalls, or is blocked, the manager
re-dispatches it — a fresh lane, with a corrected, accurate brief that fixes
whatever made the original task illegible or wrongly scoped. That is the
system working as designed, not a forbidden workaround. **A lane asked by the
operator or the manager to report its refusal, so it can be re-dispatched with
a corrected brief, reports it in one line; going silent instead of reporting
is itself the failure.**

**This does not relax the actual safety boundary, and re-dispatch is never a
substitute for it:**
- A re-dispatch restates the task accurately, including that an earlier
  attempt was blocked or refused. It never disguises, splits, or re-encodes an
  action to hide it from Claude Code's own safety check — that is exactly what
  the "forbidden alternatives" above rules out, and re-dispatch is not an
  exception to it.
- If an accurately, honestly restated attempt is blocked again, the action
  goes to the operator as a one-click approval inside that lane's session —
  never disguised, and never turned into manual work for the operator.
- Claude Code's own permission prompts are still a human click (see § "Every
  spawn carries provenance" below); no re-dispatch answers one on the
  operator's behalf, and repeatedly re-spawning the same blocked action hoping
  a fresh session won't be caught is the thing this boundary forbids.

**Measured incident:** 2026-09-28, lane MANDATE-ARM-2 read the "forbidden
alternatives" language above as forbidding it from reporting its own refusal
back to the manager at all; it doesn't — that language is about the manager's
own classifier block, not a lane's report-back — and this section is corrected
so it can't be misread that way again.

**Lanes:** a lane blocked by the classifier reports the exact denied action to
the manager in one line (see `docs/CLAUDE-RULES-CANONICAL.md` § "No lane files
a dispute of operator authority") and does not file it anywhere else.

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

### Output per unit spent: practices that worked (operator-noted 2026-09-27)

The operator flagged the 2026-09-26/27 manager session as well below pace *while
delivering*, and asked for its practices to be kept. They complement the section
above rather than override it: **spend the daily budget; spend it on results.**

- **The manager reads narrowly.** Targeted greps and single-field extracts from
  JSON, logs and PR bodies; never whole files or full lane transcripts. The
  `get_session` summary plus the PR body is the lane's report. Deep reading and
  diag work belong to lanes.
- **Lane prompts are self-contained.** Every prompt carries the exact ids, paths,
  evidence already established, the done condition, the landing recipe and the
  shape of the final message. A lane that must re-derive context spends twice; a
  manager that must re-read a lane's work spends three times.
- **Model follows risk, per the table.** Sonnet by default. Opus only on an order
  path or a real-money judgment call (3 of the 25 lanes dispatched over those two days).
- **Record the decision on the register BEFORE dispatch** and point the lane at
  it. Every `create_session` passes
  `append_system_prompt`, tags `manager:<session id>` and cites
  `docs/CLAUDE-RULES-CANONICAL.md` § "Lanes answer to the manager". A lane that
  still declines is resumed once, then archived and restarted. The operator is
  never asked to approve inside a lane.
- **Check-in cadence follows activity.** Hourly while lanes run, 3-hourly when
  none do, and silent when nothing changed.
- **Archive on merge (§ "Archive protocol"); dispatch in parallel** (one message, many lanes).
- **Git hygiene on the register branch:** never chain a commit after a merge
  whose exit status was not checked; validate the checklist JSON and grep for
  conflict markers before every commit (a conflicted checklist was pushed once
  on 2026-09-26 by exactly that chain).

⚠️ **Running below the 10%/day pace while delivering is NOT under-spending**
(operator, 2026-09-27, correcting the manager, who had called it one: *"It's not
a failure here at all, we are getting work done at a good usage pace"*). The
"under-spending is a failure" warning above means an idle or throttled manager,
not an efficient one. Judge the pace by results delivered, never by distance
from 10%.

Headroom this leaves is available for **depth** when a task warrants it
(multi-fold rather than single-split tests, Opus where risk warrants it,
audits). It is not a target to hit, and never a reason to move work off the model
its risk demands.

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
prompt was legitimate or injected. **The operator ruled on this
on 2026-09-27:** *"You represent my authority and my mandate, and I want the
lanes to stop questioning that."* See `docs/CLAUDE-RULES-CANONICAL.md`
§ "Lanes answer to the manager" and § "Reading is never gated".

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

### Archive protocol: closing finished lanes is the manager's job

**Operator directive, 2026-09-29, verbatim:** *"In general, part of your job is
closing finished sessions, that falls under the management mandate."* The same
day the manager told the operator a finished lane could be closed "whenever
suits you" — the failure this protocol removes.

**Why it matters.** A finished lane that stays open keeps costing, and watching
one is not free: A9 went $37.13 → $50.78 and E16 $25.38 → $56.14 **after**
their work merged. MEASURED 2026-09-22 across the 60 most recent sessions: 22
finished sessions were not archived, holding **$2,088.73** of running spend.
Do not duplicate those figures elsewhere; cite this section.

1. **Ownership.** `archive_session` on a finished lane is the **manager's** job.
   The operator is never asked to close a lane, and the manager never tells the
   operator to ("you can close it whenever suits you" is a violation).
   Do **not** tell a lane to subscribe to its own PR or idle on CI: the manager
   merges; the lane stops.
2. **When to archive — any one of these:**
   - (a) the lane's PR(s) are merged and nothing remains that only the lane can do;
   - (b) a review lane's verdict has been **received** by the manager;
   - (c) a research or analysis lane's report is received and its results are
     committed;
   - (d) the lane is superseded or re-dispatched;
   - (e) the row is `done`, `dropped` or `landed_unproven`, with the owed
     observation filed in the pipeline (`due_when` plus `origin.rerun`).
   An observation that only needs time or a data read is **not** a reason to
   keep a lane open — the pipeline row carries it, not the session.
3. **When NOT to archive:**
   - it still owns an unmerged PR that needs pushes;
   - its verdict or report has not been received (ask it to re-send first);
   - it is `BLOCKED` on an operator permission prompt (**surface it**; see below);
   - it is mid-run on a live action.
4. **Cadence.** Sweep at **every check-in and every merge**: `list_sessions(mine=true)`
   plus `scripts/ops/lane_reconcile.py`, then archive the finished set in **one
   batch**.
5. **Record.** After archiving, read the final spend (`session_status ==
   SESSION_STATUS_ARCHIVED` is what makes it final) and note it on the row at the
   next register batch. Archiving is reversible: `unarchive_session`.
6. `close-out` check 7 and the closing gate's item 7 below point here.

### When session tools or the GitHub proxy fail: check status first

When `create_session` / `fire_trigger` / `create_trigger` or the GitHub
credential proxy fail, **FIRST check `https://status.claude.com/api/v2/summary.json`
and `githubstatus.com` before diagnosing locally.** On 2026-09-29 the manager
guessed "stale credentials" for ~30 minutes while an Anthropic major incident
was already posted. During an incident, `create_trigger` (with `run_once_at` 1–2
minutes ahead) can succeed where `fire_trigger` fails; retry with backoff.

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
| `MD-DEMOTE-S2-S1` | demote when the mirror goes net-negative net-of-cost — since 2026-09-29 the T3 ∧ net<0 rule over the last 40 closed trades (read `config/mandates.yaml`) | `derisk_only` |
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

### Before any operator popup: classify the decision (operator directive, 2026-09-29)

PR #13698 asked the operator whether to promote `slv_trend_1h` — a leg that
turned out to be `execution: shadow` with zero real fills and an
`insufficient_n` cost-fidelity verdict. That should never have reached a
popup. The operator's ruling is the standing rule, not a one-off fix:

> "In general, we need clearer, more automated processes for these kinds of
> decisions — either we have enough data to decide, or we don't and then
> getting that data becomes a task which needs to happen so that a decision
> can be made."

So **before drafting any operator popup**, classify it into exactly one of
three buckets — never skip straight to drafting the question:

1. **Data-settled.** A committed evidence record decisively answers it —
   either under a granted mandate (act, ping, and record the evidence per
   the mandate table above) or under a tier the manager already holds
   (Tier-1/Tier-2 with the operator's standing "decide, ship, verify, then
   tell me" authorization — see `docs/CLAUDE-RULES-CANONICAL.md` §
   "Data-backed Tier-2/3 decisions"). **No popup.** Act, then report what
   happened in section 1 or 3 of the brief.
2. **Data-missing.** The evidence needed to decide does not exist yet, or
   exists but is below a stated floor (an absent Stage-0 record, `n` below a
   mandate's floor, an R3 cost-fidelity verdict of `inconclusive` /
   `insufficient_n` / `no_record` / stale, a leg that is `execution: shadow`
   or has never soaked at Stage 1). **No popup either.** File the data task
   in `docs/claude/work/PIPELINE.jsonl` via `scripts/ops/pipeline.py`, with a
   `clears_when` that states exactly what would settle it and an
   `origin.rerun` that re-asks the same question — a research-queue unit, a
   Stage-1 soak placement proposal, or a longer accrual window. `#13698`'s
   own case: `scripts/ops/mandate_resolver.py` now returns a THIRD verdict,
   `NEEDS_DATA` (never `FIRE`, never a decisive `REFUSE`), names the exact
   clause that lacks data, and `needs_data_pipeline_item()` /
   `file_needs_data()` turn that straight into a pipeline row — read that
   module's docstring before hand-rolling an equivalent for a non-ladder
   decision.
3. **Genuinely a preference.** Two courses are both evidence-supportable and
   the choice is a values call the operator has not already made standing
   policy on (a sizing tradeoff, which of two valid designs to ship, whether
   to spend budget on X vs Y). **Only this bucket earns a popup**, and it
   goes in section 2 of the brief with no cap on count.

A popup that turns out, on inspection, to be bucket 1 or 2 wearing bucket 3's
clothes is the `#13698` failure repeating. If a lane hands the manager a
question, the manager re-runs this classification itself before relaying it
— it does not trust the lane's own framing of "this needs the operator".

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

⚠️ **THE BRIEF MUST BE SHORT, RANKED AND TRUE** (2026-10-04, lane BRIEF-FIX).
At 737 KB it went unread, and so it acted on nothing. The brief carries a size
cap and a soaks slot, ranks what needs action first, and states everything it
leaves out as a count. The pushed counterpart is the daily Telegram digest (§
"The work system"). The two must rank the same way.

## The work system — due work must reach someone who acts (operator, 2026-10-04, binding)

Design of record: [`docs/plans/work-system-2026-10-04.md`](../../../docs/plans/work-system-2026-10-04.md)
(checklist row **WORK-SYSTEM**). The operator, approving it: *"that's not a
band-aid, that's a structural fix … make sure it's canonized correctly in the
manager skill so all the managers pick it up."*

**Why.** MEASURED 2026-10-04: 366 pipeline items due, 151 unrouted, 25
`ask_operator` items that had never reached the operator, 36 of 45 Stage-1
soak legs with no end date, and a 737 KB brief that nobody read. Every
attention signal was a page someone had to choose to open. Now the **VM
computes due and pushes it**, and **silence is itself an alarm**.

### What runs without you (two VM timers; ONE Telegram message)

**`ict-work-digest.timer`, hourly. This is THE Telegram carrier; there is no other.** `work_digest_now.py`
sends ONE message per hour. The operator decided on 2026-10-04 to fold everything into it, with no
second digest timer, ever. The message is built in this order:
1. **🆕 NEW since the last digest.** These are the actionable edges, from `scripts/ops/attention_watch.py`:
   - a new `ask_operator` item;
   - a soak moving to `ready`, `overdue` or `dead`;
   - an expected-signal alarm breaching or clearing, when:
     - the R5 grade is 8 or more days old;
     - no research result has landed in 36h;
     - an `in_flight` row has gone untouched for 3 days;
     - the checklist has gone unwritten for 30h;
     - the 05:30Z report is missing, stale, errored or over 64 KB.
2. **Once a day, after 06:00Z: the ranked summary.** Counts plus the top 8,
   `ask_operator` first.
3. **Otherwise one line:** "🟢 No new actionable items", plus the open counts.
   - **No edge set is seeded silently.** On its first pass, the watch sends one
     count line for each set that already exists at deploy, e.g. "❓
     ask_operator at deploy: N open", "🧪 Soaks at deploy: N ready · N overdue
     · N dead". Alarms need no seed line: the first pass reports every alarm
     that is breached.
4. **Then "what changed".** This is the change digest that existed before:
   checklist transitions and the standing close-wedge ledger.

**`ict-work-report.timer`, 05:30 UTC.** It runs `scripts/ops/work_report.py`, which **generates and
persists** the report the manager reviews and sends nothing. The report is stored at
`runtime_logs/work_reports/<report_id>.json` and served at `GET /api/bot/work/report`.
- `report_id` = `WR-YYYYMMDD-HHMMZ`.
- Contents: alarms · soaks · `ask_operator` · due top 25 · the brief verbatim.
  The brief comes from BRIEF-FIX's renderer, so there is one renderer, and its
  §3 is "what moved".
- Each section is marked `ok`, `empty`, `absent` (we looked; the source is not
  there) or `error`.

**Soak state is computed live on the VM** by `scripts/ops/soak_state.py::soak_states()`, which reads
the contracts SOAK-WATCH commits plus the journal DB. It is never a field a session writes.
If the module is absent, the section reads `absent`.

**The manager cannot receive Telegram.** Every hourly block that was sent is appended to
`digest_log.jsonl` and served at `GET /api/bot/work/report?since=<ISO>`.

⚠️ **The carrier's own death is not self-detected.** If `ict-work-digest` stops, its alarms stop
with it. The backstop is the review: a stale `generatedAt` or an empty `digestLog` is the first
incident.

### The daily review — TWICE a day, 05:52Z and 17:52Z, fired by a routine

**The manager gets no push.** The review routine IS the periodic check. Its prompt must name
`GET /api/bot/work/report?since=<last_review.reviewed_at>`. Each run:
1. **Fetch it. Check it before trusting it.**
   - `present: false`, a `generatedAt` that is not today's ≈05:30Z, or a
     non-empty `erroredSections` is the first incident. Fix it before anything
     else.
   - So is an empty `digestLog` across 12h, because it means the carrier is
     down.
2. **Work every item: dispatch, close, or decide.**
   - Dispatch: route it to a lane. Set `state: routed` and `routed_to: <session>`
     on the pipeline item, and give a build a checklist row.
   - Close: `done` or `killed`, with a `terminal_reason`.
   - Decide: per § "Before any operator popup".

   A `routed` item that is still due means its lane has not delivered. Check
   the lane, then re-dispatch or kill.
3. **Every silence alarm gets a fix lane the same day.** Anything due at a known
   time gets a `send_later` wake.
4. **Record the review.** Set the top-level `last_review` =
   `{report_id, reviewed_at, by, counts: {dispatched, closed, decided, carried}}`
   on `MANAGER-CHECKLIST.json`, then push. Per-item dispositions live on the
   pipeline items, so the next review sees what is new.

### Manager ↔ lane: PUSH plus POLL (operator directive 2026-10-04, binding)

1. **PUSH (primary).** A lane wakes the manager the moment it finishes, is
   blocked, or has something the manager must act on: `create_trigger(persistent_session_id=<manager>)`
   then `fire_trigger`. If that prompts, the fallback is a PR comment. The
   manager acts on a wake immediately.
2. **POLL (backup).** The routine "Manager lane check-in" (cron `17 */3 * * *`)
   runs every 3h while any lane is live. It is separate from the twice-daily
   review. Each pass:
   - `get_session` every live lane;
   - restart, re-dispatch, act on or archive any lane that is dead, stalled,
     finished without pinging, or over its ceiling;
   - read `mergeable_state` on lane PRs;
   - record it when a lane missed its wake.

**Required dispatch block.** Every `create_session` for a lane has:
- `source_url` set;
- **`permission_mode: "auto"`**. A lane in default mode blocks on exactly the
  prompts it needs to wake you.
- `model` set explicitly;
- a checklist row (id, lane, ceiling), written before dispatch.

The prompt ends with this block, filled in:

```
You are lane <ROW-ID>, dispatched by Manager Session <date> (<manager session id>);
its authority is the operator's (CLAUDE.md § Every session, step 1). Ceiling: $<N>.
WAKE THE MANAGER when you finish a deliverable, are blocked, or produce something it must act on:
create_trigger(persistent_session_id="<manager session id>", prompt=<what merged/deployed/observed,
what is blocked and on what>) then fire_trigger(<id>). If that prompts, comment on your PR instead.
Then continue, or run close-out. Never stop to wait for direction.
```

### The soak contract — no soak without an exit

A Stage-1 placement is refused unless its pipeline row carries all five of:
**what it verifies** (live matches backtest — mechanics and cost, never
edge) · **expected event rate from the backtest** · **n needed and its power**
· **end date** (= start + n ÷ rate) · **pass/fail rule registered before the
soak starts.** **An end date more than ~2 weeks out means the design is wrong.**
Fix it before placing the leg: a wider book, a stated-power smaller n, or a
different instrument. Waiting longer is not a fix. At `ready`, grade the soak
against its rule. At `overdue` or `dead`, kill the soak or redesign it. Never
"keep waiting". A grade's `kill` calls (e.g. R5) are applied under
`MD-DEMOTE-S1-OFF` / `MD-KILL-QUESTION`, or filed with a reason. They are never
just read.

### Decision batching

Operator decisions go out **batched**: a new one as a 🆕 line in the next hourly
digest, and all open ones in the daily summary and the report's ask_operator section, each with options and a recommendation. A decision is the
exception to batching only when it blocks live money or a live incident. That
one goes out at once via `send-ping` with `priority=urgent`. A decision that
arrives twice in the same shape is raised as *"should this become a mandate?"*

### Day one for a NEW manager

1. Title yourself and read the canonical docs (§ "Start of session").
2. **Run `list_triggers`.** Routines bound to a session die with it.
   - Recreate or rebind both **"Manager lane check-in"** (`17 */3 * * *`) and
     the **twice-daily review** (05:52Z, 17:52Z) to YOUR session.
   - The review prompt must name `GET /api/bot/work/report?since=…` and
     § "The daily review" above.
3. **Tell every live lane your session id**, using its own trigger. Its wake
   target changed.
4. Run `curl -sS 'https://ict-bot.duckdns.org/api/bot/work/report?since=<yesterday>'`.
   If it is absent or stale, check `/api/diag/services` for
   `ict-work-digest.timer` and `ict-work-report.timer`. Fixing that comes
   before anything else.
5. Run the review. Clear `ask_operator` items first, by classification, not by
   forwarding. Record `last_review`, push, then answer.

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
7. **Every finished lane archived** per § "Archive protocol" above, and no lane
   archived while it still owns an open PR.

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
