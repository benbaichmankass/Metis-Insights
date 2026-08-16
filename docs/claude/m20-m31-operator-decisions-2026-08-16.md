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

## 3. The narrow arm_r re-sweep — COMPLETE, and it inverts § 1's option 1

Live-parity (`--tp-cap-pct 0.099 --split-target-oos 50 --p80-only`) over the six
legs declaring `trail_decay_arm_r`. **All six answered in 4 minutes.** It
replaced a broad fleet sweep that would have taken **~25 hours** to reach
`xrp_pullback_2h` (it had covered 7 of 55 legs in ~4 h, none of them queued).

| leg | declared | p80 live-parity arm | verdict | OOS net_R | n |
|---|--:|--:|---|--:|--:|
| `trend_donchian` | 6.49 | 5.50 | **fails OOS** | −23.55 | 49 |
| `trend_donchian_sol_4h` | 5.57 | 1.50 | **fails OOS** | +18.08 | 52 |
| `qqq_trend_long_1d` | 3.56 | — | **skipped, thin (21 < 30)** | +24.68 | 40 |
| `gld_pullback_1d` | 5.06 | **3.86** | **PASS wf 5/6** | +20.98 | 50 |
| `scha_trend_long_1d` | 2.00 | — | **skipped, thin (14 < 30)** | +3.25 | 33 |
| `xrp_pullback_2h` | 4.49 | 2.17 | **fails OOS** | +12.12 | 53 |

**4 of 6: the lever does not earn its place at live parity.** Three fail OOS;
two have too few winner MFEs to grade — and the harness **declined to emit a
p80** rather than producing one off a thin sample, which is the right behaviour.

### ⚠️ The one PASS proposes an arm that is itself unreachable — do not ship 3.86

`gld_pullback_1d` is the leg measured **inert over its COMPLETE history** (0 of
8; `cap_R` 2.20–3.01; `risk/entry` 3.294–4.506%). The re-sweep proposes
**3.86R**, which needs `risk/entry ≤ 2.565%`:

```
best observed entry -> cap_R 3.01
proposed arm         3.86  -> exceeds it by 0.85R
ZERO of 8 live entries could arm it
```

**So § 1 option 1 — "re-sweep and take the corrected value" — would have
replaced one inert arm with a second inert arm carrying a PASS badge.** That is
worse than the state it fixes, because the badge suppresses the next question.

### ✅ RESOLVED — the two numbers describe two different books, and 3.86R is about the wrong one

An earlier draft of this section said *"I have not resolved the contradiction,
and cannot from here … both testable; neither tested."* **It has now been
tested.** Config-exact `gld_pullback_1d` on GLD 1d with `--tp-cap-pct 0.099`,
per-trade emit, **n=112**:

| population | risk/entry | implied `cap_R` |
|---|--:|--:|
| backtest p25 | 1.848% | 5.36 |
| **backtest MEDIAN** | **2.301%** | **4.30** |
| backtest winners median (n=44) | 2.299% | 4.31 |
| backtest p75 | 3.014% | 3.28 |
| **live band (n=8)** | **3.294–4.506%** | **3.01–2.20** |

**The backtest MEDIAN sits below the live MINIMUM.** Only **16 of 112** backtest
trades (14.3%) fall inside the live band at all. The live book enters at roughly
**1.4× wider risk/entry** than the backtest population — and since
`cap_R = 0.099 · entry / risk`, wider risk means a *lower* ceiling.

**Consistency check that validates the whole chain:** the live band 3.294–4.506%
implies `cap_R` **2.20–3.01**, which is exactly the independently measured
`cap_R` 2.20–3.01 in § 1. Two derivations, same answer.

**So the answer is the second branch, not the first:** the backtest population's
`risk/entry` differs systematically. The proposed **3.86R needs `risk/entry` ≤
2.565%** — met by **71 of 112 backtest trades (63.4%)** and by **0 of 8 live
entries (0.0%)**.

⚠️ **Therefore 3.86R must not be shipped.** It is a reachable arm *in the
backtest book* and an unreachable one *in the book that trades*. Both
measurements were correct all along; only the splice between them was wrong.

**What I am NOT claiming.** The live side is **n=8** — enough to show the
direction (its entire range sits above the backtest median) but not to
characterise the live distribution. And **why** the live book enters at wider
risk is untested: candidate causes are the ATR regime at those eight entry times
versus a 2010–2026 backtest average, or a sizing-path difference. That is the
next question, and it is not answered here.

**`xrp_pullback_2h` closes the other escape:** its proposed **2.17R would be
reachable** (`cap_R` 3.92–8.38) and the cell **still fails OOS**. So lowering
the arm is not the answer there either.

### What this does to § 1

The question I queued was *"what value should these arms be?"*. On this evidence
the answer for at least four of six is **"none — the lever should not be
declared on this leg"**. That is a larger call than a value change, and it is
yours. Nothing was flipped.

**Caveats that cut against my own reading:** one sweep, one split per leg,
`p80-only` (the fixed cells were verdicted separately and are not re-measured
here), and the two `skipped` legs are **absence of evidence, not evidence of
failure** — `qqq` and `scha` remain exactly as unmeasured as before.

Per-leg detail is recorded in `config/lever_reachability.json` under
`live_parity_p80_resweep_2026_08_16`, next to the reachability measurement it
can disagree with.

---

## 4. TWO sweep defaults, both Tier-3, both changing what every future run measures

### 4a. `--tp-cap-pct` (carried over)

The sweep harness defaults to a cap that is not live parity, so a sweep run
without `--tp-cap-pct 0.099` measures a book production does not run. Every
measurement in this memo passes it explicitly. Flipping the **default** is
yours: it changes what every future sweep measures, including reruns of past
work whose numbers are already recorded.

### 4b. `--split-target-oos` — the default equals the floor (added 2026-08-16)

```
MIN_OOS_TRADES = 25                                     # the floor a cell must clear
ap.add_argument("--split-target-oos", default=MIN_OOS_TRADES)   # the target
```

**The derived split targets EXACTLY the floor**, so any boundary loss puts the
window under it and the cell refuses with `insufficient_base`. Already filed as
`BL-20260814-SPLIT-TARGETS-EXACTLY-THE-FLOOR-SO-BOUNDARY-LOSS-ALWAYS-FAILS`,
and the sweep's own `insufficient_base_reason` docstring records it measured on
`htf_pullback_trend_2h`: **refused at n=24 under the derived split, graded at
n=95 under the corpus-standard one — same config, same day.**

**I hit this tonight and it nearly produced a confident wrong negative.** The
pullback re-sweep at the default refused **every cell on every leg**; at
`--split-target-oos 50` the `insufficient_base` count is **0** and real verdicts
appear. What saved it was not a check — it was `htf_pullback_trend_2h` reporting
insufficient at **407 lifetime trades**, which is implausible on its face. A leg
with a genuinely thin history would have produced the same output and been
believed.

**Why it is a decision and not a fix I should have made:** a table of
`insufficient_base` reads as *"no lever helps this family"*. Changing the default
changes what every future sweep measures **and** what already-recorded numbers
mean — the same property as 4a, which is why they belong together.

**My recommendation, labelled as one:** raise the default above the floor. I did
not, because past sweep results were produced under it and re-interpreting them
is yours to authorise.

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
| #9633 | exit-mechanism coverage probe | merged `41f9f046` |
| #9660 | `position_telemetry.account_id` fix | merged `84a2e40f` **and live-verified** |
| #9666 | two dispatch-layer backlog rows + this memo | merged `c986a70c` |
| #9671 | the re-sweep record (§ 3) into the registry | **open** at time of writing |

**Deploy verification used `bot_uptime_s`, not `git_sha`** — `git_sha` reads the
working tree and can report a SHA a running process is not executing.

**M31 P2 live:** table populating, cost **5.4 ms mean / 67.6 ms max over n=807**
(0.02% of the exit pass). The XRP trade now carries `peak_r 3.4179` against
`arm_r 4.49` and `cap_r 3.9233` — the arm-above-cap finding readable from data,
on a trade whose MFE was previously not reconstructible at all.

**A defect I shipped and found on the first post-deploy read:** `account_id` was
structurally unpopulatable (`order_packages` has no such column; the monitor has
no account in scope). Fixed in #9660. This is the argument for the verification
pass in general — the tests could not have caught it, because they asserted the
field round-trips, which it did; only the live journal could show the column was
never fed.

**#9660 is now live-verified** (12:09Z, after the deploy landed at 12:05):
**12 of 12** rows carry `account_id` across five accounts (`bybit_1`, `bybit_2`,
`alpaca_paper`, `alpaca_portfolio`, `ib_paper`), with `order_state: "applied"`
so the count is trustworthy. The decisive evidence is the **backfill**, not the
new rows — `pkg-a687f228480e4f96` read `null` at 12:03 and `alpaca_paper` at
12:09, i.e. the `COALESCE` update path repaired a pre-existing row against the
live journal rather than a fixture.

And the motivating trade is now fully attributed: `xrp_pullback_2h` / trade 4163
is on **`bybit_2` — real money** — at `peak_r 3.4179` vs `cap_r 3.9233` and
`arm_r 4.49`, `bars_held 200`, `rr_from_here 0.6329` (holding for the target
risks ~1.6× what it stands to make).

⚠️ **The `5.4 ms / n=807` cost figure above is from the first read.** A later,
independent read gives **6.4 ms mean / 55.1 ms max over n=306** — same
conclusion (negligible against a ~23.6 s exit pass), different sample. Neither
supersedes the other; they are two samples on two processes, and the max moved
*down* while n moved down, which is what a max does with fewer draws. Do not
read the pair as a trend.

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
