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

## Standing authorizations — the default, not the exception

**A decision class that can be stated as a rule does not belong in the brief.
It belongs in `config/mandates.yaml`.**

A **mandate** is an operator decision granted ONCE, in advance, that lets the
system act inside stated bounds without asking again. It is declared the same
way the execution gates are — visible in YAML, bounded, revocable.

⚠️ **It is NOT a third execution gate, and must never be described as one.**
CLAUDE.md § "The two execution gates" says in terms that there is no third gate,
and that rule stands untouched: `accounts.yaml::mode` and
`strategies.yaml::execution` remain the only two things that decide whether a
strategy trades, and neither is default-off. A mandate sits on a **different
axis** — it does not decide what RUNS, it decides what may be **CHANGED without
asking**. A leg with no mandate covering it trades exactly as before; all that
is missing is permission to move it automatically.

The manager's standing duty here is to **keep moving decisions out of the brief
and into mandates**:

- A decision that reaches the operator **twice in the same shape** is raised at
  the next sync as *"this is the second time — should it become a mandate, and
  what are the bounds?"*
- A mandate that fires often is working. A mandate that has **never** fired is
  either mis-specified or its condition does not occur — say which.
- A mandate that fires and surprises is a finding, reported in section 1.

**The asymmetry is what makes a blanket yes safe, and it is not optional.**

| `direction` | ceiling | why |
|---|---|---|
| `derisk_only` | none needed | Worst case is the system trades less than it could — recoverable, and undone by a PR. |
| `add_risk` | hard cap on total risk added, per-leg size bound, and a count | Worst case is money on the table nobody chose to put there. |

Granting the first kind freely and the second carefully is the whole reason
"grant more up front" is the *safer* arrangement rather than the braver one.

⚠️ **A mandate never authorizes judgement.** It fires on a stated rule against
a stated population, or it does not fire. "The manager thought it was fine" is
not a mandate firing; it is a session taking a Tier-3 action, which is
forbidden.

⚠️ **Expiry is load-bearing.** An expired mandate stops authorizing. It does not
quietly persist because nobody looked.

## The daily brief

Rendered and **pushed before** the sync. Five sections, fixed order.

| # | Section | Contents |
|---|---|---|
| 1 | **Taken under mandate** | What the system did on its own, which mandate authorized it, and the number that met the rule. **This is a REPORT. No approval is sought — it already happened.** |
| 2 | **Decisions for you** | Only what no mandate covers. **There is no cap.** |
| 3 | **What moved** | Lanes completed, what they concluded, what was killed. |
| 4 | **What is running** | Live lanes, spend so far, expected completion, anything blocked and on what. |
| 5 | **Spend** | Yesterday, month-to-date, against budget, cost per unit delivered. |

⚠️ **THIRTY MINUTES IS A FLOOR, NOT A CEILING** (operator, 2026-09-21). **A
SHORT SYNC IS THE FAILURE SIGNAL** — it means the manager did not have enough in
flight to fill the time. A sync that runs long because a lot is genuinely moving
is the system working. What the manager owes is a sync *worth* thirty minutes,
not a compressed one.

⚠️ **THERE IS NO CAP ON SECTION 2, AND AN EARLIER VERSION OF THIS FILE SET ONE
AT THREE.** That was rejected by the operator on the day it was written: a cap
rations the operator's attention, which throttles throughput, and a queue
outrunning one person is an argument for **automating the class**, not for
slowing the queue. If section 2 is long, the answer is mandates — not a shorter
list.

**The metric that matters is the share of decisions taken under mandate, and it
should RISE.** Flat month over month means the loop is not learning.

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
