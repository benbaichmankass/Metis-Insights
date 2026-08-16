# Operator decision memo — M20 / M31, overnight session 2026-08-16

**Purpose:** one place for every decision this session queued, so the morning
does not start by reading a night of coordination-board comments. Written by
the session that queued them.

**Nothing in here was acted on.** Every item is Tier-3 or needs a judgement
call that is not mine. Where I had an opinion I say so and label it as one.

---

## 0. The distinction that matters before reading § 1

The registry (`config/lever_reachability.json`) holds **five** entries at
`disposition: queued_tier3`, and I described them in overnight pings as "three
arm_r corrections". **That was imprecise, and the split is the useful part:**

| | count | what it needs from you |
|---|--:|---|
| **Measured, verdict is bad** | **3** | a DECISION — the evidence is in |
| **Unmeasured** | **2** | MEASUREMENT, not a decision — a re-sweep is running now |

An `unmeasured` entry is queued because a declared arm with no measurement
behind it should not sit unflagged, **not** because I am asking you to change
it. Do not read the five as five pending decisions.

---

## 1. Declared exit levers whose arm cannot fire (Tier-3, decision needed)

**The mechanism.** A leg's take-profit is clamped at `_TP_SENTINEL_CAP_PCT =
0.099`, so the highest MFE it can print before the TP fills is
`cap_R = 0.099 / (risk/entry)`. When `trail_decay_arm_r > cap_R`, **the lever
can never arm** — it is declared, shipped, and inert.

**Read the basis column.** `entry − stop_loss` is *not* the entry risk (a stop
is trailed and amended), and the error has **no fixed sign**, so there is no
correction factor. Everything below uses `signalLogic.risk_per_unit`, the sized
risk the strategy actually used.

| leg | declared arm | cap_R | reachable | basis | n |
|---|--:|--:|--:|---|--:|
| `gld_pullback_1d` | 5.06 | 2.20–3.01 | **0.0%** | complete history | 8 |
| `qqq_trend_long_1d` | 3.56 | 2.13 | **0.0%** | order-packages + candle ATR agree exactly | 1 |
| `xrp_pullback_2h` | 4.49 | 3.92–8.38 | **33.3%** | truncated, recency-biased | 6 |

### `gld_pullback_1d` — the decidable one

Not one entry **in the leg's entire life** could have reached its declared arm.
`risk/entry` ran 3.294–4.506% against the 1.956% the arm needs. This is a
complete history, not a sample.

### `qqq_trend_long_1d` — thin but corroborated

n=1 entry-conditioned, which is thin and I am not hiding it. What raises it
above a single observation is that **two independent bases agree exactly** at
`cap_R 2.13` against arm 3.56.

### `xrp_pullback_2h` — neither inert nor reachable, and this one is a real choice

4 of the 6 newest entries could not have armed it. **Do not read 33.3% as a
lifetime rate** — the sample is truncated by the relay's byte budget and
recency-biased.

This is the leg behind the 18-day XRP short. Its `cap_R` at entry was 3.92,
i.e. in the *unreachable* part of its own distribution, which is why the trail
ran at base mult for the whole hold.

**Three options, no default smuggled in:**

1. **Re-sweep the arm at live parity and take the corrected value** — the
   narrow re-sweep is running now (§ 3); this is the option that has evidence
   coming.
2. **Record the lever as `inert`** so the coverage matrix stops counting it as
   shipped. Honest, and cheaper than (1).
3. **Leave it and accept the risk**, recorded as `accepted_risk` with a date so
   no future session re-discovers it as an anomaly.

⚠️ **A note on option 1 that the first re-sweep result already complicates** —
see § 3. A live-parity re-sweep can return an arm that *also* fails its gate,
in which case the answer is not a new number.

---

## 2. Unmeasured declares (no decision asked)

| leg | arm | why it is queued |
|---|--:|---|
| `trend_donchian_sol_4h` | 5.57 | candle screen reads 2.8% reach — points at near-inert, but that basis **overstated xrp by 2.7×** and is not a bound. Not recorded as a verdict. |
| `scha_trend_long_1d` | 2.00 | screen reads 73.6%; the arm sits just below the median ceiling, making this the leg **most sensitive to which basis is used** and least safe to grade off a screen. |

Both are in the running re-sweep.

---

## 3. The narrow arm_r re-sweep — running, and its first result is awkward

Live-parity (`--tp-cap-pct 0.099 --split-target-oos 50 --p80-only`) over the six
legs declaring `trail_decay_arm_r`. It replaced a broad fleet sweep that would
have taken **~25 hours** to reach `xrp_pullback_2h` (it had covered 7 of 55 legs
in ~4 h, and none of the queued legs were among them).

**First leg out, and it is worth pausing on:**

```
== trend_donchian (BTCUSDT 1h) ==
   p80 winner-MFE arm = 5.5R
   decay_p80arm5.5R_t2.5 -> is_oos_fail
```

Two readings, and the second is the important one:

1. The live-parity arm comes out at **5.5R** against the **6.49** declared.
2. **That proposed cell then FAILS OOS.**

`trend_donchian` is the one leg I graded **`reachable` at 100%** (BTC 1h ATR
≈0.333% of price; `cap_R` p50 11.91 vs arm 6.49), so this is **not** an
arm-above-cap failure. It is a separate question — whether the lever earns its
place on that leg at all — and it means "re-sweep and take the number" is not
guaranteed to produce a number.

**n=1 leg. I am not grading the sweep off its first line.** Full results follow.

---

## 4. `--tp-cap-pct` default flip (Tier-3, carried over)

The sweep harness defaults to a cap that is not live parity, so a sweep run
without `--tp-cap-pct 0.099` measures a book production does not run. Every
measurement in this memo passes it explicitly. Flipping the **default** is
yours: it changes what every future sweep measures, including reruns of past
work whose numbers are already recorded.

---

## 5. Findings that are NOT decisions, but you should know

### Exit-mechanism coverage is uneven by family

| mechanism | module has no such lever | implemented, leg opts out | declared |
|---|--:|--:|--:|
| `stale_stop` | 19 | 24 | 3 |
| `giveback_stop` | **26** | 19 | **1** |
| `exit_head` | 26 | 17 | 3 |
| `trail_decay` | 8 | 23 | 15 |

`htf_pullback_trend_2h` — **18 of 47 live legs** — implements exactly **one** of
the four. `squeeze_breakout_4h` implements **none**. `trend_donchian` implements
all four.

**Zero orphaned declares** over 46 of 47 resolved legs (`ict_scalp_5m` does not
resolve and is ungraded — a clean count over an unstated denominator is not a
clean count). So no leg declares a lever its module cannot read; the unevenness
is a coverage gap, not a mis-declaration.

### The M20 exit levers have fired 13 times, ever

`stale_stop` 10 · `exit_head` 2 · `giveback_stop` 1 — and the single
`giveback_stop` firing is on a **paper** account. Against 1,142 closed trades,
with `reconciler_filled` at 44.6% (the exchange bracket is the dominant exit
path, as designed).

**This reframes "are the mechanisms performing well at strategy level".** There
is not enough live history to answer it — n=2 for `exit_head`, n=1 for
`giveback_stop`. The backtests are the evidence base; the live journal is not,
yet. It is also the sharpest argument for M31 telemetry: a lever's effect has to
be measured from the **counterfactual** on every trade, not from 13 firings.

### Tick cost, cause not established

Tick mean **83.9 s → 137.6 s**, persisting across a restart so not process
state. **A tail, not a uniform slowdown**: three timeframes are exactly
unchanged (0.95–1.03×) while four are 2.3–8.4×. Cache hit rate **45.6%**, above
the verified post-cap-raise reading — **so this is not a cache regression and
raising the cap again is not the answer.**

I hypothesised a shared ~26.6 s timeout ceiling. **A concurrent session refuted
it** with a cluster-tightness test: a confirmed timeout clusters at 0.001%
spread, mine at 1.837% — 1,837× looser, i.e. latency, not a bound. Recorded
because the retraction is the useful part.

Exit-evaluation `max_interval` **50.4 s against the 60 s requirement**, still
`within`. I earlier framed the tick regression as *pushing* that number; the
timeline refutes it (the worst reading predates the regression window) and I
withdrew it.

---

## 6. What shipped, and what is verified vs merely merged

| PR | what | state |
|---|---|---|
| #9588 | lever-reachability audit tool | merged |
| #9549 | ⚠️ **Tier-3 real money** — `trend_donchian_xrp_4h` trail_decay | merged **and deploy-verified** |
| #9614 | M31 P1 guard + P2 `position_telemetry` | merged **and live-verified** |
| #9633 | exit-mechanism coverage probe | green, merging |
| #9660 | `position_telemetry.account_id` fix | CI running |
| #9666 | two dispatch-layer backlog rows | CI running |

**Deploy verification used `bot_uptime_s`, not `git_sha`** — `git_sha` reads the
working tree and can report a SHA a running process is not executing.

**M31 P2 live:** table populating, cost **5.4 ms mean / 67.6 ms max over n=807**
(0.02% of the exit pass). The XRP trade now carries `peak_r 3.4179` against
`arm_r 4.49` and `cap_r 3.9233` — the arm-above-cap finding readable from data,
on a trade whose MFE was previously not reconstructible at all.

**A defect I shipped and found on the first post-deploy read:** `account_id` was
structurally unpopulatable (`order_packages` has no such column; the monitor has
no account in scope). Fixed in #9660. This is the argument for the verification
pass in general — the tests could not have caught it.

---

## 7. Two infrastructure gaps filed, not fixed

- **`trainer-vm-heavy-request` triggers no workflow.** Created, guard-enforced,
  consumed by nothing. A heavy job dispatched as the skill instructs is
  silently discarded — cost ~50 min of trainer time here, and I had already
  reported the work as done.
- **The diag relay double-prefixes a slashless `api/diag/…` path**, returning a
  bare 404 indistinguishable from a missing route.

Both are shared infrastructure with live sessions dispatching against them, so
they are filed with exact remedies rather than edited under someone.
