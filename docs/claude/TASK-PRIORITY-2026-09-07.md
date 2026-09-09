# Task-level priority — 2026-09-07

> **Doc status:** `live` · category `plan` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

> **This ranks TASKS, not milestones.** Operator directive, 2026-09-07, verbatim:
>
> > *"the priority doesn't [get] assign[ed] to me by the milestone. Right? It has to be
> > by the task. So it could be that there's a milestone that we don't generally wanna
> > push right now, but that there's a subtask there which is important for unlocking
> > some of the things for our priorities."*
>
> So the unit of priority below is a task. Each row names its **parent milestone**,
> **what it unblocks**, and **why it sits where it does**. Several rows deliberately
> reach into tracks we are **not** funding.
>
> ⚠️ **OPEN MILESTONES ARE TRACKS, NOT DEBT.** This document does **not** recommend
> closing, pruning or consolidating any milestone, and no future revision of it should.
> The operator rejected that framing on 2026-09-07: a milestone nobody is pushing is a
> track that is deliberately unfunded right now, which is a normal and healthy state.
> A milestone's dormancy is an input to this ranking, never a problem for it to solve.

**Anchor:** [`CYCLE-PRIORITY.json`](CYCLE-PRIORITY.json) — **`CY-20260906-TRADING-TRUTH`**,
operator-set 2026-09-06, **re-affirmed 2026-09-07** (*"I do like your priority, so I wanna
stick with that"*):

> *Understand what the trading system is actually doing, and make the instruments that
> measure it trustworthy — BEFORE building more on top.*

⚠️ **THE ORDER IS THE WHOLE POINT, and it is what produces the ranking below.** Repair the
MEASUREMENT before acting on what it says. A verdict computed from a contaminated
instrument is worse than no verdict, because it gets acted on. That single rule is why
three tasks that ship no trading behaviour at all sit above every task that does.

**Companion documents.** The live plan is
[`WORKPLAN-2026-08-29.md`](WORKPLAN-2026-08-29.md) — this ranking does not replace it, it
**re-reads its lanes through the cycle priority**, which is newer than it. Where a lane
order and this ranking disagree, the cycle priority wins and this document says so
explicitly.

---

## How a task earned its rank

Stated so the ordering is arguable rather than asserted:

1. **Does it repair an instrument, or does it act on one?** Repair outranks action. This
   is the cycle priority's own sequencing rule, not a preference.
2. **How many other measurements are conditioned on it?** A defect that makes *other*
   numbers uninterpretable outranks one that makes a single number wrong.
3. **Is it blocked on METHOD or on DATA VOLUME?** A volume-blocked task cannot be
   accelerated by working on it, so it ranks below unblocked work of similar value
   however important it is. **MI-155 makes this the binding distinction this cycle.**
4. **Does real money touch it?** Two real-money accounts currently carry trust flags.
5. **Is it already in flight?** In-flight work is listed at its true rank so the picture
   is complete, and marked, so nobody starts it twice.

⚠️ **RANK IS NOT IMPORTANCE.** A task can be the most valuable thing on this list and sit
at rank 9 because it is blocked on data volume. Read the *reason* column, never the number
alone.

---

## The ranking

| # | task | parent milestone | what it unblocks | why here |
|---|---|---|---|---|
| **T-1** | **The record of a trade does not say what happened** — R sign-inverted, `exit_reason` frozen, `/api/bot/trades/closed` silently empties (`MI-144`) | M31 / M20 | Nearly everything below. MI-146's realised-close attribution (bracket 29.2% / active-mgmt 7.5% / plumbing 63.3%, n=120) **reads `exit_reason`**, so today those are a lower and an upper *bound*, not values. Every exit verdict and every promotion gate inherits this. | **Rank 1 because it is the instrument the most other instruments are built on.** Already the operator's declared top priority. It is measurement repair, which the cycle priority puts before everything. In flight — `session_01L7dTAuGMdn3CTGeyYV5Mox`. |
| **T-2** | **The dollar readout — not R — is the broken instrument** (see § "The measurement contradiction" below; **this corrects the standing diagnosis**) | M31 / dashboard | The operator's own answer to *"are we making money"*. Every dollar figure on the SPA, in the digest, and in every review. | **Rank 2 because it is a live, quotable, wrong number** that no gate protects against — gates read `expectancyR`, which is the *sound* instrument here. Nobody is working on it. Cheapest real win on this list: the decomposition is already done below. |
| **T-3** | **Both real-money accounts carry a journal trust flag** — `journalTrust` reads `accountsKnownDivergent: ["bybit_2"]` and `accountsUnrecorded: ["alpaca_live"]` (verified live 2026-09-07) | M15 / M31 | Any claim about real-money performance at all. `bybit_2` and `alpaca_live` are the entire real-money surface. | **Rank 3: it is scoped to real money and it is a declared, machine-readable admission that the journal disagrees with the venue.** Below T-1/T-2 only because those two corrupt a *wider* population; this one is narrower and already flagged rather than silent. |
| **T-4** | **The committed backtest candle fixtures are flat** — `data/ohlcv/btc_5m_2026.csv` has exactly one unique close across 300 rows (`MI-157`) | M40 | Anything measured on the committed fixtures. A test that passes on a flat series has asserted nothing. | Instrument repair, and cheap. Ranks below T-1..T-3 because it contaminates the **test** surface rather than the **live** one. In flight — `session_01MLKjDPG9n1MHBByYEAEgjc`. |
| **T-5** | **Give every enabled+live leg a venue-reachable take-profit** (`MI-156`) and **ship the `tp_intent` reader** (`MI-158`) | M20 | 25 of 44 enabled+live legs (56.8%) that currently have no reachable target: 15 declare `tp_r >= 20`, clamped by `TP_VENUE_CAP_PCT = 0.099` to the venue's *rejection boundary*; 10 declare none. | **The highest-value UNBLOCKED trading task, and it is unblocked for a specific reason:** it needs **no** fidelity evidence. A target the venue accepts beats one it silently clamps away, so MI-155's data-volume wall does not bind it. Operator Tier-3 approval given 2026-09-07 (*"ok on the gld tp4 and the 24 declarations"*). In flight. |
| **T-6** | **Extend M31's declare-time reachability guard to cover the VENUE clamp** | **M31** (a track we are not otherwise funding) | Makes T-5 permanent instead of a one-time sweep. Today nothing stops leg #45 from declaring `tp_r: 50` and being silently clamped again. | ⚠️ **This is the ranking reaching into a milestone nobody is pushing, and it is the clearest case on the list.** M31's P1 already ships a *declare-time lever-reachability* guard (#9614) — it did not catch the 25 unreachable targets because venue clamping is outside its scope. The nearest capability to what MI-146 found already exists, in a track we are not funding. **Building this in M20 instead would be the "mechanism that already existed" anti-pattern.** |
| **T-7** | **`alpaca_live`'s routed real-money leg cannot place an order** — both gates read live, every signal journals `dry_run_no_order_placed` (`MI-140`) | M15 | The only Alpaca real-money leg. `alpaca_live` is `mode: live` / `real_money` / `strategies: ['tlt_pullback_1h']` — verified in `config/accounts.yaml` this session. | Real money, and currently inert. ⚠️ **Ranked below T-5/T-6 deliberately: do not assume it is a bug.** *"Declared live and correctly refusing"* is a real outcome and would move the defect to the declaration. `ALPACA_CASH_SETTLEMENT_MODE=apply` is armed on this exact account. Operator-selected; blocked only on a session slot. |
| **T-8** | **The trailing / banking half of the exit thesis** — move the stop up to bank R as it accrues | M20 | The half of M20's thesis the operator is still owed. The *target* half closed end-to-end on 2026-09-07 (MI-146→158). | **Ranked here, above the volume wall, because it is NOT wholly blocked by it — and that split is the useful part.** A *per-leg* trail geometry needs per-leg exit evidence and is blocked with everything else at T-9. A *fleet-wide* rule (bank at +XR, one threshold for all legs) is gradeable on the pooled corpus, where n is the sum over legs rather than 8. **Scope it fleet-wide first; do not wait on volume for the version that does not need it.** |
| **T-9** | **Raise per-leg exit-evidence volume** — the constraint behind the whole M20 calibration route (`MI-155`) | M20 | Every calibrated per-leg exit lever: trail geometry, exit ladders, per-leg bracket calibration, the exit-head consumers. | ⚠️ **This is the most important task on the list that you cannot make progress on by working on it.** All 44 enabled+live legs grade `insufficient_n`; max per-leg live n is **8** against a floor of **30**, and the verdict is invariant for any floor in [10, 30] — so it is **data volume, not method**, and lowering the bar does not rescue it. It ranks 9th precisely *because* effort does not move it. What DOES move it: pooled/hierarchical estimation across legs, or accepting the abstain and shipping only volume-free changes (which is what T-5 is). |
| **T-10** | **11 of 44 enabled+live legs have no pre-live backtest at any level** (`MI-147`) | M20 / M39 | Knowing which live legs were ever tested. Over the 44, **0** had a backtest read and dispositioned before going live (median lag 56 days, max 88) — soak was the first measurement, not a confirmation. | Squarely the cycle priority — it is a statement about what we actually know. Ranked below the volume wall because it is a **census**, not a repair: it tells you where you are exposed without changing it. |
| **T-11** | **Verify the 31 unverified milestone rows** in `ROADMAP.md` | roadmap governance | An accurate roadmap. **Sized, not guessed:** 18 of 33 rows carry a newest internal date ≥30 days old (median 42); 4 carry no date at all. | The direct follow-on from this session, which corrected **2** rows against the field and honestly marked the other **31** unverified. Ranked here because a stale-but-accurate row costs nothing until someone plans off it — and § "Next" is now corrected, which was the part that actually misled a reader. |
| **T-12** | **Scaffold guarded register writers** — `OPEN-ITEMS`, `MANAGER-CHECKLIST`, `pr-landing` (`MI-149`) | operating model | Queue throughput for every session. Seven guard failures on one day's PRs, every one a field omission on a register write. | **The highest queue-throughput item on the list, and it ships no trading behaviour** — which is exactly why it sits below the measurement work this cycle rather than above it. ⚠️ The guards must **not** be weakened; the fix is upstream of them. |
| **T-13** | **One repeating exception is 47% of the entire operator ERROR+ feed** (`MI-141`) | M17 | The operator's ability to see a real alarm. | A desensitised alarm is a **P1 in its own right** in this repo — the channel is the instrument, so this is measurement repair too. ⚠️ Silencing without root-causing does **not** count, and any latch must be durable/wall-clock: a per-process one resets on every trader restart. Operator-selected. |
| **T-14** | **Finish the observation sweep — 56 of 82 rows have never been looked at on the fleet** (`MI-142`) | operating model | Converts 56 *"we did not look"* rows into observations. | Pure "understand what the system is actually doing" — the cycle priority's first clause. Ranked below the repairs because observing through instruments T-1 and T-2 have not yet fixed would need redoing. |
| **T-15** | **Widen broker-truth cost coverage** — only **3 of 814** closed trades carry broker-truth fees; funding read 0 everywhere | **M24** (dormant 40 days) | M24 P3/P4, both explicitly blocked on it — and, more importantly this cycle, whether net-R is real at all. | ⚠️ **A second reach into an unfunded track.** M24 has been quiet since 2026-07-29 and we are not pushing it. But its own row states the blocker as *"feeding a ~99%-estimate net-R into the EV scorer just re-derives the fixed model"* — which is a **TRADING-TRUTH statement**, not an M24 statement. The task serves this cycle; the milestone it lives in does not. |
| **T-16** | **18 evidence workflows upload artifacts and land nothing** (`BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING`) | **M40** | The live plan's own **B8** (*"the matrix's evidence has no durable path — 3,781 cells live inside expired CI artifacts"*). | ⚠️ **A third cross-milestone pull, and it resolves a dependency the LIVE PLAN records as belonging elsewhere.** B8 says this *"is the same root as Lane T and should be fixed there, not per-sweep"* — Lane T is M40 work. Evidence that expires is an instrument that erases itself, so it is in-cycle. |

---

## Tracks we are deliberately not funding — and what this ranking took from them

Named explicitly, because the operator's instruction was that the ranking must be able to
reach into a dormant track and pull one thing out.

| track | funded this cycle? | what this ranking pulled out | why |
|---|---|---|---|
| **M31** | no | **T-6** — extend the reachability guard to the venue clamp | The nearest existing capability to the defect MI-146 found. Rebuilding it inside M20 would be `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`. |
| **M24** | no (quiet 40 days) | **T-15** — broker-truth cost coverage | Its blocker is phrased as a *measurement-trust* problem, which is this cycle's subject. |
| **M40** | partially | **T-16** — evidence that outlives its CI artifact | It is the root of the live plan's B8, which M20 cannot fix from inside itself. |
| **M15** | no (as a migration) | **T-3**, **T-7** | Both are about the real-money surface, not about platform migration. The milestone is dormant; the tasks are not. |
| **M21** | no | **nothing** | ⚠️ **Recorded deliberately as a null result.** M21 is dormant with E-3 closed as an honest negative and one pending trainer A/B (E-3c). Read against `CY-20260906-TRADING-TRUTH` it offers no task this cycle needs. *"We looked and found nothing"* is a different statement from *"we did not look"*, and this is the former. |

---

## The measurement contradiction, verified this session (T-2)

The standing account of this defect is **wrong about its cause**, and the correction
changes what should be done about it. Both readings are below so the record is legible.

**MEASURED 2026-09-07** — `GET https://ict-bot.duckdns.org/api/bot/performance`, HTTP 200,
`window=all`. ⚠️ **STATE THE POPULATION:** the figures below are the **`paper` sub-block**
(`n = 872`), *not* the top-level block. The top-level (real-money / not-paper, `n = 424`)
reads `totalPnl −69.53` **and** `expectancyR −0.3153` — **consistent in sign, no
contradiction there at all.**

| paper sub-block (n = 872) | value |
|---|---|
| `totalPnl` | **+$120,790.89** |
| `expectancyR` | **−0.1341** |
| `rCoverage` | **1.0** |
| `pnlCoverage` | **0.2041** |
| `rBasis.refusedWrongSide` | **0** |
| `rProvenance.contaminated` | 91 of 872 = **10.4%** |
| PnL rows NOT measured | 694 of 872 = **79.6%** |

Both figures are arithmetically sound (`totalR/n` and `totalPnl/n` reproduce the reported
values exactly), so neither is a computation bug.

**The concentration, which is the actual finding:**

| leg | n | `totalPnl` | `totalR` | `expectancyR` |
|---|---:|---:|---:|---:|
| **`ict_scalp_mgc_15m`** | **6** | **+$231,532.00** | +12.96 | +2.16 |

**Six trades — 0.69% of the population — carry 192% of the entire paper book's headline
PnL.** Their R is a thoroughly ordinary +2.16R. The top 10 legs by |PnL| are 20.0% of rows
and 127% of the headline.

**So the two numbers do not contradict each other — they measure different things, and one
of them is untrustworthy.** `expectancyR` is risk-normalized, so a futures multiplier
cannot dominate it, and it sits at **`rCoverage` 1.0**. `totalPnl` is raw dollars at
measured coverage **178 of 872 rows = 20.4%** (the paper population above), dominated by an MGC contract multiplier on six of those rows. This is
the class `CLAUDE.md` already documents (*"4 orphaned `ib_paper` rows carrying
+$284,084.92 — a stale mark times a futures multiplier"*) — same shape, different rows.

⚠️ **THE STANDING DIAGNOSIS IS WRONG, AND WRONG IN THE DIRECTION THAT MATTERS.** It is
recorded as R contamination (`MI-30`: *17.2% of closed rows store a stop on the wrong side
of entry*; `MI-144`), with the consequence that *"every promotion and demotion gate reads
`expectancyR`"* — implying the gates are compromised. Measured on this population:
`refusedWrongSide` is **0**, R contamination is **10.4%**, and `rCoverage` is **1.0**.
**R is the SOUNDER of the two instruments here, and the gates are reading the better
number.** What is 79.6% unmeasured is the **dollar** figure.

⚠️ **What that does NOT establish.** `MI-30`'s 17.2% is a claim over *all closed rows*, a
different population from the paper sub-block, and I did **not** reproduce it. Do not read
this as refuting MI-30 — read it as: *the contradiction the operator was shown is not
produced by the cause MI-30 names.* MI-30 stays open on its own terms.

**What follows for the work:** the fix is on the **PnL provenance / notional** side — gate
or normalize the dollar headline and publish its concentration beside it — **not** on the
R side. Ranking this as an R-contamination task would have pointed a session at the
instrument that is working.

---

## What this ranking does not do

- **It does not close, prune, merge or consolidate any milestone**, and it does not
  suggest that having many open is a problem. They are tracks.
- **It does not replace the live plan.** [`WORKPLAN-2026-08-29.md`](WORKPLAN-2026-08-29.md)
  remains the plan of record; this orders tasks *across* it and the milestone set.
- **It does not re-open the cycle priority.** `CY-20260906-TRADING-TRUTH` stands,
  re-affirmed by the operator on 2026-09-07.
- **It does not grade the 31 unverified `ROADMAP.md` rows.** That is T-11, and pretending
  otherwise is the exact defect this session was dispatched to fix.
