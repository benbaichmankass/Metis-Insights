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
