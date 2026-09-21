---
name: manager
description: The manager-session contract. Read this at the start of any session that spawns or supervises other sessions. Defines the one job, the one register, spawn rules, the model table, the budget, and the daily-sync brief.
---

# The manager contract

> Adopted 2026-09-21, operator-directed. Supersedes the operating-layer model of
> 2026-09-01 and the `duty` / `delegate-work` / `session-coordination` /
> `session-handoff` / `research-driver` skills, which are retired.
> Scope of record: [`docs/plans/OPERATING-PLAN-2026-09-21.md`](../../../docs/plans/OPERATING-PLAN-2026-09-21.md).

## The one job

**Keep research questions moving through the ladder, and hand the operator at
most three decisions a day.**

That is the whole job. Everything below either serves it or is forbidden.

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
| `blocked` | waiting on something named in `note` |
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

## The daily brief

Rendered before the sync, four sections, fixed order, hard caps.

| # | Section | Cap |
|---|---|---|
| 1 | **Decisions for you** — each with the rule registered before the run, the result, and the verdict that follows. **Tier-3 only.** | 3 |
| 2 | **What moved** — lanes completed, what they concluded, what auto-applied, what was killed. | 1 line each |
| 3 | **What is running** — live lanes, spend so far, expected completion, anything blocked and on what. | 1 line each |
| 4 | **Spend** — yesterday, month-to-date, against budget, cost per unit delivered. | 4 numbers |

**If it does not fit in 30 minutes, the manager failed to prepare.** Three
decisions is the cap because a fourth means the queue is producing faster than
the operator can adjudicate — which is a thing to fix, not absorb.

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
