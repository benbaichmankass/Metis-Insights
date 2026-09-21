# Research plan — what we work on, in what order, and why

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> **Companion to** [`OPERATING-PLAN-2026-09-21.md`](OPERATING-PLAN-2026-09-21.md) (how work moves) and
> [`ENGINEERING-PLAN-2026-09-21.md`](ENGINEERING-PLAN-2026-09-21.md) (the infra it needs).
> **Carried by** rows `R1`–`R6` in [`MANAGER-CHECKLIST.json`](../claude/work/MANAGER-CHECKLIST.json).
> This supersedes `ROADMAP.md` as the statement of what research happens next.

---

## The one-sentence version

**We do not know whether any strategy in this system has an edge, because the
instrument that would tell us is broken in a known direction.** Everything below
is ordered by that.

## What is measured, with populations

| | |
|---|---|
| **Real money, lifetime** | `/api/bot/performance` `realMoney` all-window, 2026-09-21: **n=431, 27.1% win, expectancyR −0.3231, profitFactor 0.68, totalPnl −$88.64.** 430 of 431 trades are crypto. |
| **The dominant leg is already gone** | `vwap` is **318 of those 431 trades (73.8%) at −0.4586R** — and it is on **no** live roster today (checked 2026-09-21 across all 11 accounts). The headline is therefore mostly a record of a leg already removed, not of what is running. |
| **The fleet** | 55 strategies, 45 `execution: live`, 10 `shadow`. Timeframes 16×1d, 14×1h, 7×4h, 7×2h, 6×15m, 5×5m. |
| **The review cannot grade** | `comms/strategy_reviews/2026-09-21/INDEX.json`: 52 legs graded, **`gradeable_now: 0`**, `below_evidence_floor: 52`, `actionable: 0`, `days_to_grade_all_reachable_point: 140.0`, `unbounded_no_closes: 23`. |
| **The backtest corpus is optimistic by an unknown amount** | `src/runtime/execution_costs.py` models fee + slippage + funding correctly. **The harnesses default `SLIPPAGE_BPS_ROUNDTRIP` and `FUNDING_BPS_PER_WINDOW` to `0.0`.** 6 of 15 harnesses carry no cost terms at all. Measured once, on `htf_pullback_trend_2h`: **+0.57R** of difference. |
| **Research does not terminate** | 404 memos; 117 dispositions; **1** ever marked `actioned`, none since 2026-08-31. The queue holds 5 units and the dispatcher has had nothing due since then. |

## The ordering principle

**Repair the instrument before acting on what it says.** A verdict computed
from a contaminated instrument is worse than no verdict, because it gets acted
on — and under the 2026-09-21 grant it now gets acted on *automatically*. That
is why `MD-PROMOTE-S1-S2` does not arm until **R1** lands.

---

## R1 — Re-run the corpus with the cost defaults ON

**The first thing, and nothing real depends on anything else until it is done.**

- **Question:** which legs still clear Stage 0 once slippage and funding are
  modelled at their real values?
- **Why first:** every "passed" verdict in the corpus today is fee-only.
  Auto-promotion reads that corpus. Until it is honest, the ladder would route
  real money on numbers we already know are wrong in the favourable direction.
- **Decision rule, registered now:** a leg that no longer clears its own
  declared bar under full costs is **demoted to `shadow` automatically** under
  `MD-DEMOTE-S1-OFF`. No memo, no meeting.
- **Expected shape, stated so it can be wrong:** on a +0.57R shift, legs whose
  edge was under ~0.6R will invert. Most of the fleet's declared edges are
  smaller than that. **Expect a majority of the 45 live legs to fail.** If the
  answer comes back "almost everything still passes", that is evidence the
  re-run did not actually change the cost terms — check the run, not the luck.
- **Population:** all 55 legs, full available history per leg, per-symbol.
- **Owner:** research lane. **Blocks:** R2, R3, and the arming of
  `MD-PROMOTE-S1-S2`.

## R2 — Cut the fleet to what passes

- **Question:** what is the roster once R1 has spoken?
- **Rule:** anything that does not clear Stage 0 under full costs leaves the
  live books. It keeps running on the **soak book**, which is what the soak book
  is for — shadowing on live data so we learn how to tweak it.
- **Why it is not a separate judgement call:** under the standing mandates this
  is `MD-DEMOTE-S1-OFF` firing on R1's output. R2 is the *observation* that it
  fired correctly, not a decision.
- **Expected outcome:** a much smaller live roster. **That is the point.** 55
  legs producing 71 closes a week is why nothing can be graded; fewer legs
  concentrating the same flow is how the review starts emitting verdicts.

## R3 — Cost fidelity as the standing Gate-1 test

- **Question:** is realized slippage what the harness assumed, per leg?
- **Why it matters more than expectancy here:** a 1d leg makes 10–30 trades a
  year and can never establish expectancy on a paper book in usable time — which
  is why **23 of 52 legs are `unbounded_no_closes`**. But *"is realized cost what
  we modelled?"* converges in a handful of trades, because it measures a
  per-trade quantity rather than a distribution of outcomes.
- **This is what makes the ladder traversable for the slow legs at all.**
- **Rule:** divergence beyond a stated tolerance demotes the leg
  (`MD-DEMOTE-S1-OFF`) and **invalidates its Stage-0 record**, because the
  backtest assumed a cost the venue does not charge.

## R4 — The first ten pre-registered questions

- Only after R1–R3, because a question answered against a broken instrument
  wastes the answer.
- Each carries `hypothesis / population / instrument / decision_rule / cost /
  tier` **before** it runs, so the result *is* the decision.
- **Where they come from, in priority order:**
  1. **The surviving legs** — for each one still live after R2, what is the
     single assumption most likely to be wrong?
  2. **The soak book** — 26 legs shadowing live data and nobody reads them. The
     operator named this explicitly: *"that's where all the strategies that
     haven't passed but that we still want to fine-tune can make decisions on
     real-time data so we can test the outcomes and see how we need to tweak
     them, and that also needs to be part of the research flow."*
  3. **Exit geometry**, which has the most prior work and the least resolution
     — `tp_r: 50` sentinels on the majority of legs mean the venue clamp, not
     the strategy, is setting most targets.

## R5 — Read the soak book on a cadence

- **The soak book is an instrument nobody looks at.** 26 legs on `bybit_1` and
  19 on `alpaca_paper` make decisions on live data continuously and produce no
  read.
- **Rule:** a standing weekly pass that grades every soak leg on mechanics and
  cost fidelity (not expectancy — see R3), and files each one into the research
  queue as either *tweakable with a named hypothesis* or *kill*.
- This is the part of the flow the operator asked for and that has never
  existed.

## R6 — Whether the system should trade at all right now

- **Asked explicitly rather than assumed, because it is the honest question.**
  Lifetime real-money expectancy is −0.32R over n=431. If R1 demotes most of the
  fleet, the live books may legitimately be near-empty for a period.
- **That is an acceptable outcome, not a failure.** The operator's own principle
  — *"we should only have strategies running that have a proven edge in the
  tests"* — implies an empty roster is correct when nothing has proven one.
- **Rule:** no leg is kept live to avoid an empty roster. If the answer is
  "nothing qualifies", we run nothing on real money and the soak book keeps
  accruing until something does.

---

## What this replaces

`ROADMAP.md` is 668 KB, reads 191 ✅ to 24 📋, was last verified 23 days ago and
carries its own header saying *"nobody has verified this document's status — do
not act on it as current."* It measures **construction**. This plan measures
**learning**, which is the thing that is not happening.

The roadmap is **not** rewritten — a 668 KB rewrite is exactly the kind of
work-about-work this reset exists to stop. It gets a header pointing here, and
it drops to level 8 of the instruction hierarchy: context, not instruction.

## What is deliberately NOT in this plan

- **New strategy families.** There is no case for adding a sixth idea while we
  cannot grade the five we have.
- **New ML heads.** The exit-head and regime work is real and is paused behind
  R1 for the same reason as everything else: it is tuned against a corpus whose
  cost model is wrong.
- **Anything that produces a memo as its terminal state.** A question that
  cannot state its decision rule up front does not get queued.
