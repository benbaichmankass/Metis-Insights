# Operating plan — decide offline, prove on the book

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> **Supersedes:** the operating-layer model of 2026-09-01 (design phases A–H) in full.
> **Carrier chain:** [`CLAUDE.md`](../../CLAUDE.md) → this file → rows in
> [`docs/claude/work/MANAGER-CHECKLIST.json`](../claude/work/MANAGER-CHECKLIST.json)
> → the Workflow page on the SPA.
> A plan that no checklist row points at is a memo. Every item below has a row.

Companion page (same content, rendered):
<https://claude.ai/artifact/Fki6cnA5dbyHXSsdSKcVLv>

---

## 1. What this is fixing

Four measured facts, each with its population stated.

| figure | what it is |
|---|---|
| **−0.3231R** | lifetime expectancy, real money. `/api/bot/performance` `realMoney` all-window, 2026-09-21: n=431, 27.1% win, totalPnl −$88.64, profitFactor 0.68. 430 of 431 trades crypto; `vwap` alone is 318 of them at −0.4586R. |
| **0** | actionable verdicts from the daily strategy review. `comms/strategy_reviews/2026-09-20/INDEX.json`: 52 legs graded, 71 closes in the window, floor `min_closed_for_action: 20`, `by_action {"hold": 41, "no_offline_evidence": 11}`. Its own `days_to_grade_all_reachable_point: 140.0`, with `unbounded_no_closes: 23`. |
| **7.1 : 1** | commits to `docs/claude/work/` vs commits to `src/`, over 30 days: 1,005 against 141. `config/strategies.yaml` got 9. |
| **1** | research dispositions ever marked `actioned`, of 117. 404 research memos exist, 106 of them this month. None actioned since 2026-08-31. |

**Three things are true at once, and only the third is a process problem.**

1. **The fleet cannot grade itself.** 55 strategies across 11 accounts produce
   ~71 closes a week against a 20-per-leg floor. The review machinery is
   correct and structurally incapable of emitting a decision.
2. **Research terminates in a memo.** The memos are rigorous and honest. They
   end with "PROPOSE ONLY" and wait for a Tier-3 decision that arrives at a
   rate of roughly one per 117.
3. **The manager had nothing decidable to manage.** It was given eight
   registers — work store, lease, checklist, merge queue, session registry,
   coordination board, due-list, constraint readout — and forbidden from doing
   items. Register maintenance was the only output available to it.

### The one mechanical fact that explains the drift

`scripts/check_dry_run_in_diff.py` fails any PR that sets a strategy to
`execution: shadow` or an account to `mode: dry_run` without an inline operator
marker. Across all 78 guards there is **no counterpart**: nothing blocks adding
`execution: live`, and nothing requires evidence before a leg reaches a
real-money roster.

CI makes switching a strategy **off** need the operator's signature and
switching one **on** free. That is why the roster went 36 → 55 while every memo
said cut.

The rule this plan enforces already exists, in
[`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) § "Promotion
evidence — offline edge, live mechanics". It is correct and it keeps losing,
because it is prose and the counter-pressure is CI.

> **Design principle for everything below:** the rules that matter are the ones
> CI enforces, and there must be very few of them.

---

## 2. The ladder

Three stages, two gates, one continuous falsifier. Each stage answers exactly
one question, and a leg moves only when that question is answered on evidence.

```
  STAGE 0   Backtest                     offline harness
            Is there an edge, net of the full cost stack?
            Leaves when — the result clears the rule REGISTERED BEFORE THE RUN,
            at the declared power. Nothing reaches a book without a committed
            evidence record.
                              ▼  GATE 1  ▼
  STAGE 1   Soak                         bybit_1 · alpaca_paper
            Do the mechanics work, and does realized cost match what the
            backtest assumed?
            Leaves when — orders place, fill and reconcile, AND realized cost
            per trade matches the modelled cost.
            Falls back when — they diverge.
            This stage may be WIDE. It is an instrument, not a decision surface.
                              ▼  GATE 2  ▼
  STAGE 2   Live + mirror   bybit_2 + bybit_portfolio · alpaca_live + alpaca_portfolio
            Real money. The mirror carries the IDENTICAL roster and the
            IDENTICAL trades, at honest size.
            Demotes when — the mirror goes net-negative net-of-cost over a
            declared window.
```

**Stage 2 is one stage, not two.** The mirror and the live account carry the
same strategies and take the same trades at all times (operator, 2026-09-21).
The mirror is the honest-size read of the live book, not a separate rung.

**Consequence: R4 becomes a DEMOTION gate, not a promotion gate.** A mirror
that carries identical strategies only ever holds data for legs already live,
so it cannot gate entry. It gates exit. This is the falsifier the system has
never had — nothing currently demotes anything, and 21 retirement candidates
have produced zero retirements.

### Why the cheap gate is cost-fidelity, not profitability

A 1d leg makes 10–30 trades a year. It can never establish expectancy on a
paper book in usable time — which is why 16 of 55 legs are structurally
ungradeable today.

But *"is realized slippage what we assumed?"* converges in a handful of trades,
because it measures a per-trade cost quantity rather than a distribution of
outcomes. That is what makes the ladder traversable for the slow legs.

⚠️ **It only works if Stage 0 models the full cost stack** — see D1.

### Two structural facts about the roster

- **The Bybit mirror invariant exists and is enforced** —
  `tests/test_paper_portfolio_accounts.py::test_bybit_portfolio_mirrors_bybit_2_exactly`
  pins the rosters together. `bybit_2`: 6 strategies, `bybit_portfolio`: 6.
- **The Alpaca mirror is broken.** `alpaca_live` carries 5 strategies,
  `alpaca_portfolio` carries 14. No invariant exists, so that mirror cannot be
  read as the honest-size version of the live book, and Gate 2's demotion
  signal does not work on that side. → **B2**.

---

## 3. The research loop

The fix for "propose only" is to **decide before you measure**. Every queued
question carries its decision rule up front, so the result **is** the decision
and the interpretation step — where everything currently dies — is removed.

```yaml
# research/queue/<id>.yaml — extends the schema that already exists
hypothesis:     one falsifiable sentence
population:     what data, minimum n, how power was computed
instrument:     which harness — and whether it runs the LIVE order path
decision_rule:  IF <result> THEN advance to stage N
                IF <result> THEN kill
cost:           runner-hours
tier:           approval the resulting change needs
```

The dispatcher already exists and fires daily at 06:20 UTC. It has had
**nothing due since 2026-08-31** — the queue holds five units, three on monthly
cadences. The machine is running against an empty input.

**What happens to a result**

- **Advance, Tier-1 or 2** → applies itself. No human in the path.
- **Advance, Tier-3** → arrives at the next daily sync as one line: the rule
  already agreed, and the number that met it.
- **Kill** → closes the question and lands in the ledger. A null is an asset,
  not a memo.

**Shadowing is part of the loop, not a parking lot.** The soak book
(`bybit_1`, `alpaca_paper`) is where legs that have not passed make decisions
on real-time data so we can see the outcomes and learn how to tweak them.
Reviewing those legs is a standing research input (operator, 2026-09-21).

### The queue replaces the roadmap

`ROADMAP.md` is 668 KB, reads 191 ✅ to 24 📋, was last updated 23 days ago and
carries its own header: *"nobody has verified this document's status — do not
act on it as current."* Milestones measure construction. A question queue
measures learning, which is the thing that is not happening.

### Three mechanical faults stop results landing

1. **19 of 23 research workflows produce an artifact and an issue comment and
   nothing else** — no durable record anyone can query later. → **C2**
2. **`replay-pregate-nightly` has never landed a report on `main`, ever** —
   `runtime_logs/**` is not in `TIER1_SURFACE`, so `pr-landing-guard` R5
   rejects the commit. Separately the trainer is swap-thrashing, so 6 of 6
   nightly runs die at 10 of 22 heads and **no non-BTC head has ever been
   graded**. → **C3**
3. **9 of 13 backtest harnesses are wired to no workflow**, and the default
   candle file is a 5,001-row smoke fixture spanning 3.5 days. → **C4**

---

## 4. The manager, restructured

Keep it. The model was not wrong; the job description was.

**What the manager does**

1. Reads the queue and picks what runs next, against the cycle priority the
   operator set at the last sync.
2. Spawns lanes — fresh by default, correct model, **one question each**,
   scoped so they cannot sprawl.
3. Reads what comes back and applies the pre-registered outcome.
4. Kills or re-scopes a lane that is burning without producing.
5. Prepares the daily brief, and **pushes it before the sync**.

**What the manager may not do**

- Take an item. Unchanged, and already CI-enforced by
  `scripts/ci/check_manager_scope.py`.
- Maintain more than one register. **The checklist is the only one that
  survives.** The lease, work store, session registry, merge queue,
  coordination board, due-list and constraint readout are retired.
- Write narrative observations about its own state. Three timestamped
  `manager_observation_*` keys an hour apart on one row is the failure mode,
  not diligence.
- Exceed its daily budget without saying so at the next sync.

### Why one register and not eight

Measured over 30 days: 1,005 commits to `docs/claude/work/`, 697 to one backlog
file, 254 to `OPEN-ITEMS.json` — against 141 to the entire `src/` tree. Roughly
one register write per merged PR, across 2,010 merged PRs.

The build plan for that operating model warned, in writing, that *"August ran
45 governance sprints against 2 deployments"* and that a plan whose value
arrives in phase four would reproduce that ratio. It shipped 1 of its 8 phases
and reproduced it.

---

## 5. The daily sync — 30 minutes

Once a day, same time. The manager pushes the brief **before** the sync, so the
Workflow page and the conversation never disagree. Four sections, fixed order,
hard cap.

| # | Section | Contents | Cap |
|---|---|---|---|
| 1 | **Decisions for you** | Each with the rule registered before the run, the result, and the verdict that follows. Tier-3 only — everything else has already applied itself. | 3 |
| 2 | **What moved** | Lanes completed since yesterday, what they concluded, what auto-applied, what was killed. | 1 line ea. |
| 3 | **What is running** | Live lanes, spend so far, expected completion. Anything blocked, and on what. | 1 line ea. |
| 4 | **Spend** | Yesterday, month-to-date, against budget. Cost per unit delivered. | 4 numbers |

**If it does not fit in 30 minutes, the manager has failed to prepare** — that
is the signal, not an excuse to run long. Three decisions is the cap because a
fourth means the queue is producing faster than the operator can adjudicate,
which is itself a thing to fix rather than absorb.

What the operator never does again: read a 400-line memo to make a decision,
approve a demotion, or triage a backlog.

**Monthly, one question:** how many legs advanced a stage, how many were
killed, and what did we learn? If that is zero two months running, the answer
is not more process.

---

## 6. Cost and model discipline

There is no cost ledger today. 263 session rows across ~90 keys carry **zero**
cost or token fields. The load-bearing measurement, from CLAUDE.md's own record
(2026-09-17, one resumed lane, ~10 minutes): `cache_read` 78,988,432 →
95,867,825 (+16.9M), `cost_usd` 53.38 → 65.26 (**+$11.88**) — almost entirely a
resumed session re-reading its own accumulated context every turn. A fresh
session starts near 40k.

### Model by task class

| Task | Model | Why |
|---|---|---|
| Manager | `opus` | Must hold the whole picture and judge what to spawn. |
| Research lane | `sonnet` | Well-specified question, mechanical execution, result checked against a rule it did not write. A weak model cannot fake a pass. |
| Build lane | `sonnet` | `opus` if it touches an order path. |
| Sweep dispatch, log reads, extraction | `haiku` | No judgement involved. |
| Anything on a real-money order path | `opus` | Recoverability is the criterion, not difficulty. |

`create_session`'s `model` parameter **defaults to the calling session's
model** — omit it and a lane silently inherits the manager's `opus`.

### Session hygiene

- **fresh by default** — resume only when the next unit needs context the
  previous session built *in its head*, never context that is on disk. Subject
  overlap is not benefit.
- **one question** — a lane that must answer two questions is two lanes. This
  is the context-overload control.
- **ceiling** — a per-lane dollar ceiling. On breach the manager *reads what
  the lane has produced*, then kills or re-scopes. Never interrupt blind: an
  interrupt forfeits everything not yet landed.
- **record the choice** — model and fresh/resume, with the reason, on the
  lane's checklist row.

**The meter's job is not accounting, it is scoping.** A number the manager must
report daily forces it to ask what a lane is worth before spawning it.

---

## 7. The work plan

Every item names the session that runs it and carries a row in
`MANAGER-CHECKLIST.json` with the same id. Phases A and B are almost entirely
deletion and rewiring; nothing new gets built until C.

### PHASE A — make the daily loop possible (week 1)

| id | item | who |
|---|---|---|
| **A1** | **Cost meter.** Capture cost and tokens per session into one register; render in the brief. Per-lane ceiling + a daily total. | build lane |
| **A2** | **Cut `CLAUDE.md` to ~15K tokens.** Was 547 KB ≈ 136K tokens, of which the generated SESSION BRIEF was 39.4% — 215 KB of overdue monitoring rows injected before every session's first tool call. Keep: orientation, the two execution gates, the tier table, the ladder, pointers. Everything else → `docs/reference/`, read on demand. | this session |
| **A3** | **Daily brief generator.** The four fixed sections, rendered from the queue + lane state + cost meter. Pushed before the sync, served on the existing Workflow page. | build lane |
| **A4** | **Manager contract.** One page: what it does, what it may not do, spawn rules, the model table, the budget. Replaces ~6,200 lines of process skills. | this session |
| **A5** | **Stand down the governance layer.** Disable the governance crons; archive the registers (951 open backlog rows); retire the lease, work store, session registry, merge queue, coordination board, and the `duty` / `backlog-drain` / `full-system-audit` skills. | this session + build lane |
| **A6** | **Pull the two negative-OOS Alpaca legs.** `tlt_pullback_1h` (−4.41R, n=94) and `tlt_pullback_1d` (−3.92R, n=8, 0 of 4 folds positive) off `alpaca_live`. One-line config change, reversible, does not wait on D1. **Tier-3 — needs explicit operator approval.** | operator |

### PHASE B — make the ladder mechanical (week 1–2)

| id | item | who |
|---|---|---|
| **B1** | **Invert the execution-gate guard.** Demotion becomes free. A new guard blocks any leg reaching a Stage-2 roster without a fresh evidence record that clears its declared bar. **This is the load-bearing change.** | operator + build lane |
| **B2** | **Alpaca mirror invariant.** Extend the Bybit roster-sync test to `alpaca_live` / `alpaca_portfolio`. Precondition for Gate 2 working on that side. | build lane |
| **B3** | **R4 as the demotion gate.** Flip to enforcing, pointed at demotion rather than promotion. Recommended 2026-07-30, built, shipped observe-only, never armed. | operator + build lane |
| **B4** | **Re-scope the review packet.** Grade Stage 2 on money and Stage 1 on cost fidelity. Without this it keeps grading 52 legs against a 20-trade floor and emitting nothing, forever. | build lane |

### PHASE C — unblock the research loop (week 2)

| id | item | who |
|---|---|---|
| **C1** | `decision_rule` in the queue schema, plus a guard that refuses a queued unit without one. | build lane |
| **C2** | Make the 19 artifact-only workflows commit to a corpus. A result nobody can query later is not evidence. | build lane |
| **C3** | Let `replay-pregate` land: add `runtime_logs/**` to `TIER1_SURFACE`; cut the head count to fit trainer memory or raise the memory. | build lane |
| **C4** | Wire the 9 orphan harnesses; replace the smoke fixture with a real corpus. | build lane |
| **C5** | **Write the first ten pre-registered questions.** The only item that builds nothing — and the only one that produces knowledge. | operator + manager |

### PHASE D — repair the evidence base (deferred, operator's call on timing)

| id | item | who |
|---|---|---|
| **D1** | **Flip the cost defaults on, and re-run.** `src/runtime/execution_costs.py` models fee + slippage + funding correctly; the harnesses set slippage and funding to `0.0` by default, deliberately, for backward compatibility. So every "passed the backtest" verdict in the corpus is fee-only and optimistic by an unknown amount — measured once at +0.57R on the one leg anyone checked. 6 of 13 harnesses have no cost terms at all, including the `vwap` harness. | research lane |
| **D2** | **Cut Stage 2 to what actually passes.** Depends on D1 — until the re-run we do not know which legs clear Stage 0. Today 3 of 44 meet the `live_money_ready` bar and none is on `bybit_2`. | operator + build lane |
| **D3** | **Cost-fidelity job.** Weekly: compare each Stage-1 leg's realized cost against its harness assumption. This *is* Gate 1. Build only once D1 has given it something to compare to. | build lane |

> **What deferring Phase D costs, stated rather than buried:** until D2, real
> money keeps running legs that fail their current evidence bar. **A6 pulls the
> two worst without waiting for the re-run.**

---

## 8. Open decisions

| # | Question | Recommendation |
|---|---|---|
| 1 | Does the soak book stay at 26 legs, or get curated? | **Keep it wide.** It is an instrument, not a decision surface, and the cost-fidelity job will tell us which legs are worth keeping — a measurement rather than a guess. |
| 2 | Cut real money now, or go flat during the transition? | **Cut rather than stop.** The mechanics data is worth something and the exposure is a few hundred dollars. But pull the two negative-OOS Alpaca legs (A6). |
| 3 | What is the daily budget? | **Operator's number.** A2 alone should cut per-lane cost substantially, but the meter needs a line to report against or it is just accounting. |
| 4 | How aggressive is the Phase-A deletion? | **Aggressive.** Partial removal leaves the treadmill running — the crons keep firing, the briefs keep growing, and the manager keeps having somewhere to put its effort that is not research. |

---

*Every figure on this page carries its population. Where a number could not be
established from the repo, it is absent rather than estimated. Live figures read
from `/api/bot/performance` and the committed registers on 2026-09-21.*
