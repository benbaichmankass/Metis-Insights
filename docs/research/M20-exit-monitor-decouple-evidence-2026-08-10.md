# The 60-second exit-evaluation ask: what the tick actually costs

**Session** `session_011iUN3roukhbRWwuioX8pRD` · **2026-08-10** · the measurement half is
Tier-1; the change it argues for is **Tier-2** and is a proposal, not a merge.

**Operator ask:** *"no live trade should ever go more than 60s without a realtime
evaluation … build this out correctly (this time) and test that it improves
performance."*

This document exists because the honest answer to "should we decouple the exit monitor
from signal generation?" was **unknowable until the tick was split per hook**, and the
split now exists (`/api/diag/tick_cost`, live since earlier today).

---

## 1. The measurement

**Population — state it before reading anything into it.** Live trader, `git_sha
cdf61d18`, one process started `22:05:14Z`, read at `22:54:37Z`: **18 ticks over ~49
minutes**. Counters are per-PROCESS and reset on restart. A max over 18 ticks is not
the claim a max over 3,000 is, and nothing below should be read as a steady-state
distribution.

```
tick total        mean 104,232 ms   max 125,028 ms   (last 107,580 ms)
  run_one_tick    mean  53,911 ms   max  73,270 ms   51.7% of total
  order_monitor   mean  48,740 ms   max  52,828 ms   46.8% of total
attributed_pct 98.5   ·   hook_names_refused 0
```

**The two wraps are siblings, not nested** — `src/main.py:688` and `src/main.py:697`
are separate `with _tick_hook(...)` blocks at the same level. Checked in the source
rather than inferred, though the arithmetic agrees independently: 53,911 + 48,740 =
102,651, which is exactly `hooks_attributed_mean_ms`, and sits just under the 104,232
total. Were the monitor nested inside the tick, the sum would exceed the total.

**`attributed_pct` 98.5 is the load-bearing number.** The wraps are deliberately coarse,
so `100 − 98.5 = 1.5%` is *every other hook combined* — the pairs executor, the macro
thesis, five prop prompts, two reachability alerts, the IB-state dump, the exposure
soak. They are collectively ~1.5 s of a ~104 s tick. **The split is the whole story;
there is no hidden third cost.**

---

## 2. What this says about the 60-second ask

**Today a live trade waits a mean of 104 s and a peak of 125 s between evaluations** —
roughly **2× the 60 s target**, and the peak is more than 2×. The ask is not currently
met, and now there is a number for by how much.

The monitor is not the problem, and it is also not free:

| | mean | max | clears 60 s? |
|---|--:|--:|:-:|
| today (monitor rides the tick) | 104.2 s | 125.0 s | **no** |
| monitor decoupled onto its own loop | 48.7 s | 52.8 s | **yes — by 7 s at the peak** |

So the decouple is **necessary and barely sufficient.** That second half is the finding
that matters, and it is the opposite of what a "just decouple it" plan assumes: the
monitor's own runtime becomes the cadence floor, and at **52.8 s peak** against a 60 s
target there is **13% headroom on an 18-tick sample.** Anything that grows the monitor —
another open position, another venue, one more broker-state sweep — puts it back over
the line, and the failure would be silent because nothing today watches the monitor's
own runtime as a budget.

**A decouple shipped without a budget on the monitor's own cost would meet the ask this
week and quietly stop meeting it later.** That is the same shape as both June 2026
wedges (`MB-20260609-001`, `BL-20260609-001`): every component individually cheap, the
sum unwatched.

---

## 3. What I propose (Tier-2 — needs one OK, not merged)

1. **Decouple the monitor onto its own loop**, so its cadence is bounded by its own
   runtime rather than by signal generation's. This is the change the ask names.
2. **Ship it with a declared budget on the monitor's own wall-clock**, surfaced the
   same way the tick cost is: measurement first, and read `max` beside its `n`. The
   budget is a *measurement with an alarm*, **not** a cap that skips positions — a
   monitor that skips a position to stay inside a budget is a monitor that stopped
   monitoring, which is strictly worse than being slow.
3. **Do not set the budget value from this sample.** 18 ticks in one 49-minute window,
   on one process, is not a distribution — it is a reading. The exposure-ceiling rule
   applies verbatim (`gross-exposure-governance-DESIGN.md` § 6/§ 7): a bound set
   below normal operation silently throttles correct work, and § 7 forbids shipping a
   value with no soak behind it. **Let the per-hook counters accrue across restarts and
   market sessions first**, then set it against the observed max.
4. **Investigate the 48.7 s itself as a separate lever.** Halving the monitor buys more
   margin than the decouple does, and unlike the decouple it needs no new loop. The
   first question is how much of it is per-position broker round-trips (the Bybit
   every-tick protection sweep, the Alpaca sweep, the cadence-gated IB one) versus DB
   work — which the current instrumentation cannot answer, because `order_monitor` is
   one wrap.

**The instrumentation's own cost, measured rather than asserted.** The split adds 14
context-manager entries per tick to the live monitor, and *"it is only a timer"* is a
claim, not a number — the same claim every individually-cheap hook in both June wedges
came with. Benchmarked (7 runs × 1000 ticks, median): **25.6 µs per tick for all 14
phases**, 1.83 µs each — **0.000053%** of the measured 48.7 s, and ~39,000 ticks
before it costs one second in total. Measured in the x86 sandbox, not on the aarch64
VM, so treat it as an order of magnitude rather than the figure; even at 10× slower it
is 0.0005%. There is no performance argument against merging this, and now there is a
number behind saying so.

**Sequencing recommendation:** (4) before (1). If the monitor is 48.7 s because of
broker reads that can be batched or cached, the decouple lands on a much safer footing —
and if it is irreducible, that is exactly the number the budget in (2) has to be set
against. Building the loop first would mean building it around a cost nobody has looked
inside.

---

## 4. Where to look first — a STRUCTURAL finding, not a timing

The split is not live yet (Tier-2, § 3), so nothing below is a measurement. But the
source already narrows where to point it, and this is a checkable prediction rather
than a guess — **it is falsifiable the moment the split deploys.**

**10 of the 14 phases can reach a broker.** Four are DB-only
(`reconcile_options_expiry_and_assignment`, `sweep_unlinked_packages`,
`sweep_stuck_linked_packages`, `check_naked_positions`) and should come back cheap;
if any of those is expensive, the cost is not what this section predicts and the
hypothesis is wrong.

**The specific prediction: `account_open_positions` is called from THREE separate
phases per tick, and it is not memoized.**

| phase | call site |
|---|---|
| `reconcile_open_trades` | `order_monitor.py:3752` |
| `reconcile_orphan_exchange_positions` | `order_monitor.py:2562` |
| `watchdog_stuck_strategies` | `order_monitor.py:4801` |

Checked in `src/units/accounts/clients.py::account_open_positions` (353 lines, no
decorator, no cache) — the only caching anywhere near it is a **local**
`positions_cache[aid]` inside `_watchdog_stuck_strategies`, which dedupes within that
one phase and cannot help the other two. So for each live account the broker's
open-positions endpoint is hit up to **three times per tick** where once would do,
and with ~8 live accounts across Bybit / IBKR / Alpaca that is on the order of ~24
round-trips per tick against ~8 needed.

**If that is where the time is, the remedy is a shape this repo already shipped
today**: the per-tick connector memo behind `CANDLE_CACHE_TTL_FRACTION`
(`BL-20260810-TICK-CHAIN-260S-PER-TICK`) — one read per account per tick, handed to
each phase. It changes no semantics, because all three phases already treat the
result as a point-in-time snapshot.

**Two reasons not to just do it now.** First, it is a guess until the split says so:
a memo that saves nothing is complexity added to the live order path for nothing.
Second — and this is the one that would bite — the three phases have **different
fail-safety contracts** around a `None` read (*"we could not look"*, never *"flat"*),
and a shared memo must not let one phase's failed read become another phase's
confirmed-flat. That is the collapsed-state defect in a place where it closes real
positions. Measure first, then design the memo around that contract explicitly.

---

## 4b. THE SPLIT DEPLOYED — the prediction holds, but is wrong about dominance

Merged `2a289dd`, trader restarted 14:15:35Z, read at 14:28Z. **n = 3 ticks**, so
every number here is provisional — a max over 3 ticks is not the claim a max over
3000 is, and I am not going to pretend otherwise. What *is* robust at n=3 is the
**ordering**, because the top gap is ~4×, not 10%.

**The monitor's 44.2 s, split (mean ms, n=3):**

| phase | mean ms | share of monitor |
|---|--:|--:|
| **`strategy_monitor_loop`** | **19,636** | **44.4%** |
| `reconcile_open_trades` | 5,131 | 11.6% |
| `check_broker_naked_equity_positions` | 4,658 | 10.5% |
| `reconcile_orphan_exchange_positions` | 3,705 | 8.4% |
| `check_broker_naked_ib_positions` | 3,701 | 8.4% |
| `check_broker_naked_bybit_positions` | 2,633 | 6.0% |
| `watchdog_stuck_strategies` | 2,317 | 5.2% |
| `reconcile_netting_partial_closes` | 2,033 | 4.6% |
| the other 6 phases, combined | 225 | 0.5% |

The 14 phases sum to **44,038 ms against the parent's 44,248 ms — 99.5%**, so the
split is essentially complete; only 210 ms of the monitor is unattributed.

**Verdict on § 4's prediction: the four DB-only phases came back cheap as predicted
(225 ms combined, all six sub-250 ms), and all three `account_open_positions` phases
are in the top seven — so the hypothesis is not refuted. But it is WRONG about
dominance, which is what it was for.** The three predicted phases total **11,152 ms
= 25.2%** of the monitor. `strategy_monitor_loop` alone is **19,636 ms = 44.4%** —
**1.76× the three combined**, and § 4 never named it. A perfect `account_open_positions`
memo cannot touch it.

So the honest read: **the memo is worth a few seconds, not the lever.** It stays
worth doing (a third of ~11 s, bounded by the fact that those phases also do DB work,
so well under 7 s) and its collapsed-state hazard in § 4 is unchanged — but the first
question is now *what is the per-strategy loop spending 19.6 s on*, which this split
does not answer because the loop is wrapped as one phase.

**Correction to § 2's margin, from a bigger sample.** A pre-restart read at **n=123**
(vs the original 18) put the monitor's max at **54,537 ms**, not 52,828 ms:

```
tick total      mean 106,555 ms   max 135,648 ms      (123 ticks, one process)
  run_one_tick  mean  56,614 ms   max  92,388 ms   53.1%
  order_monitor mean  48,215 ms   max  54,537 ms   45.2%
```

So decoupling clears 60 s by **5.5 s, not 7 s — 9% headroom, not 13%.** More data
made the margin *thinner*, which is what a max does as its sample grows. That
strengthens § 3's "must ship with a budget" rather than weakening it, and it means
60 s should not be treated as safely cleared on any sample this size.

### 4b-bis. CORROBORATED on better samples (2026-08-12) — the ordering held, the absolutes moved

My § 4b split was **n=3** and I flagged it provisional, claiming only that the ORDERING
would survive. Two independent reads have since landed. Both agree, and they correct the
absolutes upward:

| | my n=3 | sibling warm **n=51** (#8792) | fresh n=5 (diag #8795) |
|---|--:|--:|--:|
| tick mean | — | **107.2 s** | 108.7 s |
| tick max | — | 122.2 s | 124.9 s |
| `run_one_tick` | — | 56.2 s (52.4%) | 57.4 s (52.8%) |
| `order_monitor` mean | 44.2 s | **49.3 s** (46.0%) | 49.7 s (45.8%) |
| `order_monitor` **max** | 48.9 s | *(not reported)* | **53.0 s** |
| `strategy_monitor_loop` | 19.6 s | **24.5 s** | 24.7 s (max 24.9 s) |

**The ordering survived exactly as claimed; my absolutes were ~20% low.** The
per-strategy loop is a *stable* **~24.5 s** — mean and max within 0.2 s at n=5 — which
is **~49.6% of the monitor**, not the 44.4% I reported. It is still the single largest
item by a wide margin, and the seven sweeps make up the other half
(reconcile_open_trades 5.0 s · check_broker_naked_equity 4.6 s ·
reconcile_orphan_exchange 3.6 s · check_broker_naked_ib 3.6 s · netting_partial 2.9 s ·
check_broker_naked_bybit 2.8 s · watchdog_stuck 2.2 s).

**The 60 s margin is unchanged and now triangulated.** Monitor max: **54.5 s** at n=123
(my pre-restart read, still the largest sample and therefore the number to quote),
**53.0 s** at n=5. So decoupling clears 60 s by **~5.5 s** — the § 4b figure, corroborated
rather than revised. Note the trader restarted twice today (20:14 Z, 23:25 Z), so every
`ticks_measured` resets with it; quote the denominator or the peak means nothing.

**My nesting fix is confirmed on live data, twice.** `attributed_pct` now reads **98.4**
(n=51) and **98.6** (n=5) with `nested_hooks: 14`, and in both the two top-level hooks
sum to the reported total EXACTLY (52.4+46.0 = 98.4; 52.8+45.8 = 98.6). That is the
arithmetic the 136.8% bug violated, checked against production rather than a planted case.

**What this sharpens for § 6.5.** The decouple buys ~5.5 s for six files and a re-armed
wedge risk. `strategy_monitor_loop` is 24.5 s in ONE un-decomposed phase — stable enough
that it is clearly systematic rather than incidental, ~0.47 s across ~52 strategies. That
is the cheaper and safer lever, it needs no concurrency, and the next probe is timing
INSIDE it per-strategy. Recommendation unchanged, now with a firmer number behind it.

### 4d. THE MONITOR IS TWO HALVES, NOT ONE HOT PHASE — and that changes the design

Read at 23:44:18Z, n=7, same process (started 23:25:22Z), diag #8797. This is the first
sample where **all 14 children are populated**, so the monitor can be checked as a whole
rather than by its largest child:

| | mean ms | max ms | % of monitor (mean) |
|---|---|---|---|
| `strategy_monitor_loop` | 24,304.7 | 28,221.0 | **49.3** |
| `reconcile_open_trades` | 5,020.3 | 5,315.4 | 10.2 |
| `check_broker_naked_equity_positions` | 4,619.9 | 4,631.0 | 9.4 |
| `reconcile_orphan_exchange_positions` | 3,512.5 | 3,972.8 | 7.1 |
| `check_broker_naked_ib_positions` | 3,486.0 | 6,651.4 | 7.1 |
| `reconcile_netting_partial_closes` | 2,906.1 | 3,355.6 | 5.9 |
| `check_broker_naked_bybit_positions` | 2,856.4 | 3,063.6 | 5.8 |
| `watchdog_stuck_strategies` | 2,201.6 | 2,443.6 | 4.5 |
| the other 6 phases combined | 200.3 | — | 0.4 |
| **`order_monitor` (parent)** | **49,297.6** | **56,799.0** | 100 |

**The decomposition is essentially complete**: children sum to 49,107.8 ms against a
parent of 49,297.6 — **189.8 ms unattributed, 0.39%**. So this is not a partial view
with a hiding place left in it. (`attributed_pct: 98.4` also reconciles exactly:
59,734.7 + 49,297.6 = 109,032.3 over a 110,820.1 ms tick.)

The structure it reveals is **two comparable halves**, which is not what "one dominant
phase" predicted: `strategy_monitor_loop` at 24.3 s (49.3%) and **everything else at
24.8 s (50.3%)** — of which six broker-facing reconcilers/sweeps are 22.4 s (45.4%).

**Why that is decision-relevant.** The 60-second ask is about **exit evaluation**, and
`strategy_monitor_loop` is the only phase that evaluates exits. The other 24.8 s are
reconciliation, self-heal and broker-state hygiene: they answer *"has the journal caught
up with the broker?"*, not *"should this trade exit now?"*. At least one of them
(`check_broker_naked_ib_positions`) is **already gated to 300 s** by
`IB_BROKER_NAKED_CHECK_SECONDS` and is deliberately not per-tick. Their required cadence
is a per-phase question — I am **not** claiming any of them is safe at an arbitrary
cadence — but it is demonstrably **not the 60 s the ask names**.

Taking the worst-case gap between two evaluations of the same package as one pass
duration (ordering is deterministic, so a package lands at a stable offset in each pass):

| design | worst-case gap | margin vs 60 s |
|---|---|---|
| whole `order_monitor` on a thread | **56.80 s** | **+3.20 s (5.3%)** |
| `strategy_monitor_loop` alone on a thread | **28.22 s** | **+31.78 s (53.0%)** |

**The approved whole-monitor design does not robustly clear the bar it was approved to
hit.** 3.20 s of margin is 5.3%, and it is measured against a max over **seven ticks** —
a lower bound on the population max, not the population max (the n=123 warm read put it
at 54.5 s; this n=7 sample already exceeds that at 56.8 s). Drop the stable-ordering
assumption and allow a gap of up to two passes and the whole-monitor design **fails
outright** at 113.6 s, while the exit-loop-only design still holds at 56.4 s.

**Revised recommendation: decouple the EXIT LOOP, not the monitor.** Move
`strategy_monitor_loop` to its own loop and leave the reconcilers/sweeps on the tick
where they are. This is strictly less code than the approved design (no phase needs a
new cadence, nothing else moves), it clears the target with 53% margin instead of 5.3%,
and it does not depend on halving anything first — which supersedes § 6.5's "decompose
24.5 s first" ordering. The four prerequisites in § 6.1–6.4 still apply in full, and
**6.1 is unchanged and still load-bearing**: whatever runs on the thread leaves the
liveness watchdog's coverage, because that coverage IS the inline execution.

One caveat this does not resolve: `reconcile_open_trades` (5.0 s) participates in close
detection, so *its* latency governs how quickly the journal learns a position closed.
That is a real requirement — it is simply a different one from exit-evaluation latency,
and conflating the two is what made the whole monitor look like one indivisible unit.

### 4e. POST-MERGE RE-READ AT n=172 — the split reproduces, both published MAXES were optimistic

§ 4d derived the design from **n=7**. The first post-merge read (diag #8806, 2026-08-12T18:12Z,
`ticks_measured: 172`, one process since 09:56Z) is a **25× larger sample**, and it splits the
result cleanly: **the structural finding holds, the two margin figures I published do not.**

**What reproduces — the two-halves claim, almost exactly:**

| | § 4d (n=7) | n=172 | |
|---|--:|--:|---|
| exit half (`strategy_monitor_loop`) | 24.3 s · 49.3% | **22.43 s · 48.4%** | mean |
| the 13 reconcilers | 24.8 s · 50.3% | **23.71 s · 51.1%** | mean |
| children ÷ parent | 99.6% | **99.5%** | coverage |

Two comparable halves, neither dominant, at 25× the sample. That is the finding the design
rests on, and it is now measured rather than indicated.

**What does NOT reproduce — the maxes, and the direction is against me.** The max is the
load-bearing statistic here (a mean that clears while the peak does not IS the 2026-06-09
incident shape), and both figures in the PR body and the board claim were taken from the
small sample:

| | published (n=7) | n=172 | verdict at 60 s |
|---|--:|--:|---|
| whole monitor on a thread | 56.80 s → clears by **+3.20 s (5.3%)** | **61.82 s** | **−1.82 s — FAILS** |
| exit loop only (shipped) | 28.22 s → clears by **+31.78 s (53%)** | **34.89 s** | **+25.11 s (41.8%)** |

So the shipped option still clears the ask with real headroom, but by **41.8%, not the 53% I
published** — and the option I rejected does not merely have a thin margin, it **exceeds 60 s
outright** on this sample. The decision is more clearly right than the evidence I gave for it;
the numbers I gave for it were wrong. Both are worth stating, and the second is the one that
would otherwise quietly rot into a cited figure.

I had already written that a 5.3% margin was "one added reconciler away from being gone."
It did not need another reconciler — it needed a larger sample of the reconcilers already there.

### 4f. LIVE CONFIRMATION (diag #8808, 2026-08-12T18:44Z) — deployed, and it works

`git_sha 64ee435f`, process up since 18:14:03Z. All three post-merge checks now have
answers rather than intentions.

**1. Thread segregation works, and is visible.** `offloop_hooks` carries exactly
`monitor.strategy_monitor_loop` — `n: 55`, mean **16.24 s**, max **34.13 s** — and that
name is **absent from the main `hooks` table**, where it sat before. Both halves of the
claim: the exit phase moved, and its cost is no longer divided by the main tick's clock.
An empty `offloop_hooks` would have been the silent failure; it is populated.

**2. `attributed_pct: 98.4`** (≤ 100), `nested_hooks: 16` = 13 `monitor.*` + 3 `pipeline.*`
— reconciles exactly, one child having left for the off-loop table and `pipeline.dispatch`
absent for want of a dispatch in the window. No double-count.

**3. The cadence, and the number that matters.** 55 passes over 1661 s of wall time is an
observed period of **30.2 s** against `EXIT_LOOP_INTERVAL_SECONDS=30`, with 54% of wall
time spent in-pass. The loop is period-targeting (`slack = interval − elapsed`), so the
inter-evaluation interval is `max(interval, pass)` — **30.0 s typical, 34.1 s worst**,
clearing the 60 s ask by **25.87 s (43.1%)**. Exit evaluation went from once per ~96 s
tick to once per 30.2 s: a **3.2× rate increase**, which is the deliverable.

**And the § 4e correction validated itself.** Live max **34.13 s** sits within **2.2%** of
the n=172 figure (34.89 s) and **21% above** the original n=7 claim (28.22 s). The small
sample was optimistic in the direction § 4e said it was; had I shipped on the original
number and never re-read, the published margin would have stayed wrong while the system
was fine — the least visible kind of error.

**Still open, unchanged:** the tick itself is mean **96.5 s** / max **115.7 s**
(`run_one_tick` 70.0 s mean, `pipeline.signal_build` 53.7% of tick). `BL-20260810-TICK-CHAIN`
is untouched by this work and stays open.

The paragraph below is preserved as written, because it was true of the read it describes
(diag #8806) and dating it is the point.

**Not yet verifiable, and stated rather than assumed:** the same read shows `git_sha 1a5126a1`
with `bot_uptime_s 29724` (process up since 09:56Z), i.e. the trader is **still running #8796's
code** — the merge had not been pulled + restarted at read time. So `exit_loop_health`,
`offloop_hooks`, and the live `max_pass_ms` are all **unobserved, not confirmed**. The
`attributed_pct: 98.3` / `nested_hooks: 18` above IS confirmed, and it is the pre-decouple
baseline for the ≤100 check, not evidence about the decouple.

### 4c. A defect in this instrumentation, found by reading its own output

The first post-deploy read returned **`attributed_pct: 136.8`** — a share of a whole
exceeding the whole. `snapshot()` summed **all** hooks flat to compute coverage,
which was correct while every wrap was a sibling (`run_one_tick` + `order_monitor`)
and became wrong the moment `monitor.*` children were added *underneath one of
them* — by me, the day before, without touching that function. `100 − attributed_pct`
is documented as "every other hook COMBINED"; at 136.8% it read as **−36.8%**.

Fixed: the coverage denominator now counts **top-level hooks only** (a dotted name is
a child), and `nested_hooks` is published so a reader can see why the listed means do
not add up to it. Reproduced at **199.5%** on a planted two-child case before the fix.

Worth stating plainly: **>100% is the lucky version of this bug.** It is impossible on
its face, so it announced itself. A double-count that had landed at 95% would have
read as excellent coverage and been believed — which is exactly the shape § 4c of the
sibling brief describes, and it happened inside the field built to prevent it.

---

## 6. APPROVED DESIGN — thread in-process (operator, 2026-08-11) + three prerequisites the proposal missed

Operator chose **thread in-process** over a separate service. Recorded here because
implementing it surfaced **three consequences that are not "move one call"**, each
confirmed against the code rather than anticipated.

### 6.1 The liveness watchdog stops covering the monitor — this is the load-bearing one

`src/main.py:901` says it outright, deliberately:

> *"…still stops the heartbeat because we run inline on the main [thread]"*

**The watchdog's coverage of the monitor IS its inline execution.** Move the monitor to
a thread and a wedged monitor leaves the main-thread heartbeat ticking normally —
`ict-liveness-watchdog` sees a healthy trader and never restarts. That is the exact
June-2026 wedge class (`MB-20260609-001`, `BL-20260609-001`) re-armed, and it is the
one outcome **strictly worse than a slow monitor**: today a wedged monitor freezes the
heartbeat and gets auto-healed within 8 minutes; after a naive decouple it would wedge
silently and indefinitely while positions went unmanaged.

**So the monitor needs its own dead-man switch, in the same change — not after it.**
`heartbeat.write_heartbeat` already accepts a `path=`, so the file half is trivial; the
work is teaching `scripts/check_heartbeat.py` (stdlib-only, deliberately) a second
target and deciding its stale threshold from the monitor's own measured max, not from
the tick's.

### 6.2 `tick_cost._hooks` is an unlocked module-level dict

`src/runtime/tick_cost.py:66` — a plain `Dict`, mutated by `record_hook` at :124 with
**no lock**, and the `len(_hooks) >= _MAX_HOOK_NAMES` check-then-insert at :130 is not
atomic. The 14 `monitor.*` phases call it. Run them from a second thread and they race
the main tick's writes.

Either add a lock, or give the monitor loop its own accumulator. **Do not** call
`begin_tick`/`end_tick` from the monitor thread — those mutate the same globals and
would corrupt the tick's own measurement.

### 6.3 `attributed_pct` breaks AGAIN, for the opposite reason

Fixed today (§ 4c) because nested children were double-counted into a share of the
tick. After the decouple the monitor is **no longer in the tick at all**, so counting
`order_monitor` against tick time measures a share of something that no longer contains
it. The field would read wrong a second time, in one day, from a change in a different
file — which is itself the argument for the monitor owning its own cost surface rather
than borrowing the tick's.

### 6.4 IB has a REGISTRY lock, not a USAGE lock

`src/units/accounts/ib_client.py:2317` holds `_REGISTRY_LOCK` — it guards the **client
dict**, not concurrent use of a live socket. Nothing stops the monitor thread and the
trader thread both driving `reqHistoricalData` / `reqAllOpenOrders` on the **same
clientId**, which is the documented multi-client collision class
(`BL-20260706-IBACCTUPDATES-COLLISION`, where a second subscriber simply never got its
`accountDownloadEnd`). The monitor's own `_check_broker_naked_ib_positions` is an
account-wide `reqAllOpenOrders` — precisely the expensive, collision-prone call.

An explicit lock around IB *access* (not just registry lookup) is required, and it
partially re-serialises the two loops on IB — so the decouple's benefit is full for
Bybit/Alpaca and **reduced for IB**, which is worth stating before measuring the result.

### 6.6 IMPLEMENTATION STATUS (2026-08-12) — four of five prerequisites landed

Operator approved the revised scope (§ 4d: move the exit loop only) on 2026-08-12.
What is on `claude/m20-exit-refinement-continue-g1qv46`, none of it yet moving
anything onto a thread:

| | state | notes |
|---|---|---|
| **prereq 0** — active-close marker | **DONE** | Not in the original § 6 list. The exit half WRITES `_TICK_ACTIVE_CLOSE_SYMBOLS` and the reconciler half READS it at four sites — sound only while they share a tick. Now a lock-guarded, self-pruning, time-bounded map (`ACTIVE_CLOSE_WINDOW_S`, 90s). 13 tests. |
| **the split** | **DONE** | `run_exit_evaluation_tick` / `run_reconciliation_tick`, `run_monitor_tick` a wrapper. Pure refactor, order + merged summaries asserted. |
| **6.1** liveness | **DONE** | `src/runtime/exit_loop_health.py` — four states, latched alert-only, `max_pass_ms` to check the ~28s claim in production. 15 tests. |
| **6.2** `_hooks` lock | **DONE** | And the comment CORRECTED: the lost-update race is **not reproducible** here (8×20k increments at `setswitchinterval(1e-9)`, zero lost; tests pass with the lock stubbed). Defensive correctness, not an observed bug. |
| **6.3** `attributed_pct` | **DONE** | Segregated by **thread identity**, not a name prefix — a naming rule is a contract a caller can break, a thread id is not. Off-loop time is published with no share and excluded from attribution. |
| **6.4** IB usage lock | **NOT DONE** | Design below. |
| **the wiring** | **NOT DONE** | Gated on 6.4. |

#### 6.4, verified and specified — plus a hazard the original note missed

The § 6.4 claim checks out: `ib_client.py:2317`'s `_REGISTRY_LOCK` guards only the
lookup/creation inside `ib_read_client_for` (:2400–2412). Once it returns, two
threads hold the **same** `IBClient`, and that object wraps one `ib_insync.IB` —
a single socket on a single clientId, driven by an asyncio loop that is explicitly
not thread-safe. This is worse than `BL-20260706-IBACCTUPDATES-COLLISION`, which
was two *different* clients on one account; here it is two threads on one socket.

Surface needing the guard: 17 public methods, ~14 touching the socket —
`connect` · `disconnect` · `place` · `place_protective` · `modify_protective` ·
`close` · `cancel_resting_protection` · `has_protective_orders` · `cancel` ·
`status` · `balance` · `positions` · `executions` · `self_test`.

**THE HAZARD THE ORIGINAL NOTE MISSED: it must be an `RLock`, not a `Lock`.**
Several of these call each other — `close` cancels resting protection then
re-reads `positions`; `connect` runs the liveness probe and the account warm-up.
A non-reentrant lock self-deadlocks the trader on the first such path, and the
symptom would be a frozen exit loop, i.e. exactly the condition 6.1 alerts on,
arriving via the fix meant to make the loop safe.

**Accepted cost, stated:** the two loops SERIALISE on IB. Each call is already
bounded (`IB_FETCH_TIMEOUT_S` 8s, `IB_PLACE_CONFIRM_S` 3s, `IB_CLOSE_CONFIRM_S`
6s), so the main tick's worst-case wait is bounded rather than unbounded — but the
decouple's benefit is FULL for Bybit/Alpaca and REDUCED for IB, and the 53% margin
in § 4d is a Bybit-side number.

**Why it is not in this branch:** `ib_client.py` is the most incident-prone file in
the repo (`BL-20260609`, `BL-20260610-009`, `BL-20260706-IBACCTUPDATES-COLLISION`,
`BL-20260709-IB-POSTRESTART-RECONNECT-WEDGE`, plus the account-warm-up race
recorded in `CLAUDE.md` § `IB_ACCOUNT_WARMUP_TIMEOUT_S` — that one is prose in
the env table, NOT a filed backlog row, so it is described here rather than
cited by an id that resolves to nothing).
A lock placed wrong there deadlocks the live trader. It wants a session with the
budget to read all ~14 call paths for reentrancy first, which is a bounded piece of
work and the correct next step — not something to squeeze in beside four other
changes.

### 6.5 Scope, honestly

That is: a monitor loop + its own heartbeat + a second watchdog target + a cost surface
+ an IB usage lock + tests — six files touching the live money loop, not one call moved.
**The margin it buys is 5.5 s** (§ 4b). Every one of the four items above is a
prerequisite rather than a nicety, and shipping the loop without 6.1 would trade a
measured 5.5 s gain for an unbounded silent-wedge risk.

> ⚠️ **SUPERSEDED by § 4d (n=7 full decomposition).** The paragraph below reasoned from
> a partial split in which `strategy_monitor_loop` was the only large child measured, and
> concluded that halving it was the better lever. With all 14 children populated the
> monitor is **two comparable halves**, and the better lever is to move only the exit half
> onto the loop — 53% margin instead of 5.3%, and *less* code than the approved design,
> with no halving required first. § 6.1–6.4 stand unchanged. Kept for the record:

**Recommendation unchanged from § 3(4), and now with the split behind it:**
`strategy_monitor_loop` is **44.4%** of the monitor in one un-decomposed phase. Halving
*that* buys more than the decouple does, needs no new concurrency, and cannot re-arm a
wedge. It is also the cheaper experiment. The loop should still be built — the operator
has approved it — but building 6.1–6.4 first is what makes it safe, and decomposing
19.6 s first is what tells us whether 5.5 s is the best available margin or merely the
first one found.

---

## 5. What is NOT claimed here

- **Not** that 104 s is the steady state. 18 ticks, one process, one 49-minute window.
  The honest statement is "on the sample available, the tick costs ~104 s and the ask
  is missed by ~2×", and the fix for the sample is time, not another read.
- **Not** that decoupling improves trading performance. It changes *evaluation latency*,
  which is a mechanism, not a result. The operator's phrasing — *"test that it improves
  performance"* — is the right bar and it is a separate experiment: the intrabar
  exit-evaluation A/B in the harness is the instrument for it, and it has already been
  built. Latency and edge are two claims and this document only supports the first.
- **Not** that the monitor is at fault for the tick being slow. It is 46.8% of it;
  signal generation is 51.7%. Both would have to shrink for the tick itself to come
  under 60 s — which is a different goal from the one that was asked for, and probably
  the wrong one to chase.
