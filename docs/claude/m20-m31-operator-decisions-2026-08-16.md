# Operator decision memo — M20 / M31, overnight session 2026-08-16

> ## ✅ FOUR OF THESE WERE DECIDED — 2026-08-16, operator, in-conversation
>
> **This memo was written as a queue. It is now partly a RECORD.** Read this
> block before § 1 and § 4, whose bodies below describe the state *before* the
> decision and are preserved as the reasoning that produced it.
>
> | item | decision | landed as |
> |---|---|---|
> | `gld_pullback_1d` 5.06R | **record inert** | `disposition: recorded_inert` |
> | `qqq_trend_long_1d` 3.56R | **record inert** | `disposition: recorded_inert` |
> | `xrp_pullback_2h` 4.49R | **widen the sample first — NO disposition change** | stays `queued_tier3`, reasoning written into the row |
> | `--tp-cap-pct` default | **flip to live parity** | `default=LIVE_TP_CAP_PCT` (0.099) |
> | `--split-target-oos` default | **separate from the floor** | `default=DEFAULT_SPLIT_TARGET_OOS` (50), floor stays 25 |
>
> **The registry could not express two of them.** `disposition` had exactly three
> values and all three are wrong for "record inert": `ok` asserts the lever is
> FINE (the guard refuses it for an inert verdict, correctly), `queued_tier3` says
> a decision is still PENDING, and `accepted_risk` says the arm was knowingly LEFT
> AS-IS — the *opposite* disposition, what you record when you decline to act.
> Filing the operator's decision as `accepted_risk` would have inverted it. So
> `recorded_inert` was added, with the invariant that keeps it honest: it requires
> `verdict='inert'`, because a `vol_conditional` lever arms on SOME entries and
> recording it inert asserts more than was measured. Falsified, not assumed —
> setting it on `xrp_pullback_2h` makes the guard exit 1.
>
> **STILL QUEUED — and they need MEASUREMENT, not a decision:**
> `trend_donchian_sol_4h` and `scha_trend_long_1d`, both `unmeasured`, both
> `skipped_thin` on the re-sweep at 14 and 21 winner MFEs against a 30 minimum.
> Asking the operator to decide these would be asking them to rule on absent
> evidence.
>
> ⚠️ **`--tp-cap-pct 0` is now the explicit opt-out and is REQUIRED to reproduce
> any verdict recorded before 2026-08-10.** Those numbers were measured on the
> uncapped geometry; re-running them on the new default silently compares two
> different books.

**Purpose:** one place for every decision this session queued, so the morning
does not start by reading a night of coordination-board comments. Written by
the session that queued them.

**Nothing in here was acted on *when it was written*** — four items have since been decided (see the block above); the rest is Tier-3 or needs a judgement
call that is not mine. Where I had an opinion I say so and label it as one.

---

## 0. The distinction that matters before reading § 1

The registry (`config/lever_reachability.json`) holds **five** entries at
`disposition: queued_tier3`, and I described them in overnight pings as "three
arm_r corrections". **That was imprecise, and the split is the useful part:**

| | count | what it needs from you |
|---|--:|---|
| **Measured, verdict is bad** | **3** | a DECISION — the evidence is in |
| **Unmeasured** | **2** | MEASUREMENT, not a decision — the re-sweep has since RUN; see § 2 for what it did and did not settle |

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

**Three options, no default smuggled in — and option 1 is now CLOSED for two of
the three legs. Read § 3 before choosing.**

1. ~~**Re-sweep the arm at live parity and take the corrected value.**~~
   **ELIMINATED by evidence for `gld_pullback_1d` and `xrp_pullback_2h`** (§ 3).
   An earlier draft called this *"the option that has evidence coming"* — it
   came, and it closed the option rather than filling it in:
   - `gld_pullback_1d` → the re-sweep returns **3.86R**, which is **itself above
     that leg's live `cap_R`** (2.20–3.01). The population test then showed why:
     the p80 is computed over a book whose `risk/entry` is ~1.4× tighter than
     the live one. **Taking that value would ship a second inert arm wearing a
     PASS badge.**
   - `xrp_pullback_2h` → its re-swept **2.17R would** be reachable, and the cell
     **still fails OOS**. So lowering the arm is not the answer there either.
   - `qqq_trend_long_1d` → **skipped, thin** (21 < 30 winner MFEs). Still
     untested, so option 1 is neither open nor closed for it — that is *absence
     of evidence*, not a verdict.
2. **Record the lever as `inert`** so the coverage matrix stops counting it as
   shipped. Honest, cheap, and — on the evidence now in — **the only one of the
   three that is actually available for `gld_pullback_1d`.** Labelled as my
   reading, not a decision.
3. **Leave it and accept the risk**, recorded as `accepted_risk` with a date so
   no future session re-discovers it as an anomaly.

⚠️ **The larger question § 3 raises:** for four of six re-swept legs the answer
may be *"none — the lever should not be declared on this leg at all"*, which is
bigger than any value choice and is yours.

---

## 2. Unmeasured declares (no decision asked)

| leg | arm | why it is queued |
|---|--:|---|
| `trend_donchian_sol_4h` | 5.57 | candle screen reads 2.8% reach — points at near-inert, but that basis **overstated xrp by 2.7×** and is not a bound. Not recorded as a verdict. |
| `scha_trend_long_1d` | 2.00 | screen reads 73.6%; the arm sits just below the median ceiling, making this the leg **most sensitive to which basis is used** and least safe to grade off a screen. |

**The re-sweep has since RUN, and it settled only one of these two** (§ 3):

- `trend_donchian_sol_4h` → re-swept p80 **1.50R**, and the cell **fails OOS**.
  So this leg is no longer "unmeasured" — but the measurement says *the lever
  does not earn its place*, which is not the same as a corrected arm value.
- `scha_trend_long_1d` → **skipped, thin** (14 < 30 winner MFEs). The harness
  **declined to emit a p80** rather than producing one off a thin sample, which
  is the right behaviour. **This leg remains exactly as unmeasured as before** —
  absence of evidence, not evidence of failure, and it still needs measurement
  rather than a decision.

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
characterise the live distribution.

### ✅ The "why" is now ANSWERED — it is the ERA, and the sizing candidate is dead

*Added 2026-08-16 by the follow-on session. This paragraph used to end "that is
the next question, and it is not answered here"; it now is. Full working:*
[`m20-arm-reachability-is-a-vol-threshold-2026-08-16.md`](../research/m20-arm-reachability-is-a-vol-threshold-2026-08-16.md).

**Of the two candidates named above, one is refuted by code and the other is
measured.** Live and both harnesses compute `sl = entry ∓ atr_stop_mult*atr`
with **byte-identical** `_atr` helpers, so `risk/entry ≡ atr_stop_mult ×
(ATR/close)`. `sl` is fixed at signal time, *before* sizing runs — `RiskManager`
sets quantity, never stop distance. So **"a sizing-path difference" cannot be
the cause**, and an ATR-definition skew (which would have made this a parity
*bug*, not a regime fact) is ruled out too.

That leaves the ATR regime, and the by-year cut confirms it. `gld_pullback_1d`
arm 5.06 reachability, entry-conditioned, n=112:

| era | median `risk/entry` | `cap_R` @med | arm reachable |
|---|--:|--:|--:|
| 2018 | 1.805% | 5.48 | **7/7** |
| 2025 | 3.452% | 2.87 | 1/5 |
| 2026 | 7.396% | 1.34 | 0/2 |

**2025–2026 combined: 1 of 7, against the live basis's 0 of 8.** So the two
populations **agree once the era is held fixed** — the pooled 37.5% describes no
regime in particular. **It was never a population conflict; it was an unstated
era.**

⚠️ Regime-**clustered**, not a trend: 2011 (3.327%) and 2013 (3.169%) also sit
in the live band. *"Gold got more volatile recently"* is the wrong summary.

**Two consequences that change how you should read § 1 and § 3.**

1. **`gld_pullback_1d` is not inert as a property of the leg** — it was
   reachable **7/7 in 2018**. Every arm is a vol threshold: arm `A` on a leg
   with stop-mult `M` fires only while `ATR/close ≤ 0.099/(M·A)`.
2. **The whole arm-above-cap class is mechanical.** The six p80 arms shipped
   2026-07-12/13; the harness first gained `--tp-cap-pct` on 2026-08-10. They
   are p80s of an **uncapped** MFE distribution applied to a **capped** live
   book — so `BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP` is a
   downstream symptom of `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`,
   and it predicts which legs break (holds 3/3).

**All five queued entries now have a large-n entry-conditioned basis.** (An
earlier version of this paragraph named § 2's pair as *scha and qqq* — wrong:
§ 2's two are **`trend_donchian_sol_4h` and `scha_trend_long_1d`**, and `qqq`
belongs to § 1. Corrected, and `sol_4h` added, which that slip had omitted.)

| leg | § | prior basis | new basis | reachable |
|---|:-:|---|--:|--:|
| `gld_pullback_1d` | 1 | 8 live (complete) | n=112 | 37.5% pooled · **1/7 in 2025-26** |
| `qqq_trend_long_1d` | 1 | **n=1** live | n=81 | 19.8% — n=1 verdict confirmed |
| `xrp_pullback_2h` | 1 | 6 **truncated** (33.3%) | n=204 | **5.9%** — truncation overstated ~5.6× |
| `trend_donchian_sol_4h` | 2 | none (screen 2.8%) | n=127 | **0.0%, every year** |
| `scha_trend_long_1d` | 2 | none (screen 73.6%) | n=65 | **83.1%** — arm sits *below* its ceiling |

**Two of these look closable on the evidence, in opposite directions** —
`scha` toward `ok` (its arm is reachable), `sol_4h` toward `inert` (0 of 127, a
3.4× gap). **Both are yours; every verdict and disposition is untouched.** The
one edit I made was correcting `sol_4h`'s `unmeasured_reason`, whose text *"No
entry-conditioned pull yet"* had become false.

⚠️ **And a new Tier-3 item this surfaced, filed not acted on**
(`PB-20260816-BYBIT-TP-CAP-BINDS-ON-ALPACA-AND-IB-LEGS`): the 9.9% cap is
documented as a **Bybit** `ErrCode 10001` workaround, applied as a module
constant with no venue branch — and **no Bybit account carries GLD, QQQ or
SCHA**.

**Scope narrowed the same day, after checking the venues' own rules.** Alpaca
documents **no maximum take-profit distance**; **IBKR is reported to reject
stock orders more than ~10% from NBBO** (unverified — its primary pages 403'd),
which would make the cap approximately *correct* on that route. So the finding
holds for the **Alpaca-only** legs (`gld_pullback_1d`, `scha_trend_long_1d`) and
**`qqq_trend_long_1d` is not a clean instance** — it also routes to `ib_paper`,
and a package fanning out to both venues must satisfy the tighter rule, so a
fix would have to be **route-aware, not leg-aware**. The code comment's
*"Bybit (and most exchanges)"* was more accurate than my first reading of it.
Whether Alpaca would accept a farther TP **in practice** is still untested —
documentation is not the live API.

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

> **⚠️ SUPERSEDED 2026-08-18 — the record above stands as what was measured on 2026-08-16; the state it describes no longer holds.** `stale_stop` and `giveback_stop` were extracted to `src/runtime/exit_levers.py` and `htf_pullback_trend_2h` now implements **three of four** M20 mechanisms. `exit_head` is still absent and deliberately so — it needs an advisory-stage trained head this family does not have. Do not quote the "one of four" figure as current. The tripwire that forced this note is `tests/test_exit_mechanism_coverage.py::TestTheProbeCanSeeAPositive::test_htf_pullback_coverage_is_three_of_four`.


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

## 7. Two infrastructure gaps — FIXED (PR #9704), not filed

⚠️ **This section previously read "filed, not fixed", and that disposition was
overruled.** Operator, 2026-08-16: *"those are not small things to be brushed
over. Those are serious failures of the system that we even got to this point
with those gaps, and we need to fix them immediately."* The reasoning below for
filing rather than fixing — *"shared infrastructure with live sessions
dispatching against them"* — is preserved because it was wrong in an
instructive way: a shared relay that is **silently broken** is more dangerous to
a concurrent session than one being edited under them, and every hour it stays
filed is another session waiting on a dispatch that will never run.

- **`trainer-vm-heavy-request` triggered no workflow.** Created
  (`bootstrap-labels.yml:335`), guard-enforced (`.claude/hooks/vm_lane_guard.sh`),
  named as *the* required label for heavy work (`session-coordination/SKILL.md`
  §2b) — and consumed by nothing. A heavy job dispatched exactly as the skill
  instructs was silently discarded; cost ~50 min here (issue #9638), and I had
  already reported that work as done. **Two backlog rows already existed from
  two prior sessions**, which is the finding underneath the finding: observed
  twice, fixed zero times.
  **Fixed** by ORing the label into `trainer-vm-diag.yml`'s job condition. The
  lane-claim guard is unchanged.
- **The diag relay double-prefixed a slashless `api/diag/…` path**, returning a
  bare 404 indistinguishable from a missing route. **There were TWO resolvers** —
  the shell one on the `workflow_dispatch` path, and the Python `resolve_one` on
  the *issues* path, which is the one every session actually uses. Fixing only
  the first would have been **worse than fixing neither**, because it would have
  been reported closed. Both now strip the slashless form and carry a
  doubled-prefix backstop that hard-errors with the offending resolved path.

**Verified by falsification, not by reading the diff:** `resolve_one` was
extracted from the YAML and executed over 6 cases (all pass), then the strip was
deleted to simulate the pre-fix state — the backstop raised
`doubled upstream prefix: resolved '/api/diag/api/diag/status' from input 'api/diag/status'`,
proving the two layers are independent rather than one guard counted twice.
`scripts/ci/run_guards.py` → PASS 15 · FAIL 0.

---

## 8. What happens next, what it should produce, and what says it isn't working

**Why this section exists** (operator, 2026-08-16): *"this session cannot just
end open ended… we need to know what the expectations are, the performance of
the work that was done, and what the next work that we're gonna do is based on
what we see happen. Like, this can just end open ended, and then we don't do
anything until… for two months and then get surprised when things still aren't
working."*

So every open item below carries three things it did not carry before: **what it
should produce**, **when we would know**, and **the signal that says it is not
working**. An item with no failure signal is an item that will still be open in
two months, because nothing will ever tell us to look at it.

### 8.1 The honest performance read on what shipped

**M20's exit levers, as a live intervention, have not yet been shown to do
anything.** They have fired **13 times ever** against **1,142 closed trades** —
`stale_stop` 10, `exit_head` 2, `giveback_stop` 1, and that last one on a paper
account. The correct conclusion is **not** "they don't work"; it is that the
live journal **cannot answer the question at n=2 and n=1**, and any claim in
either direction from this data would be manufactured.

That is the single most important number this session produced, because it
changes what the next work *is*. Firing counts will not reach significance by
waiting — a lever that fires ~13 times in a quarter needs years. **The effect
has to be measured as a counterfactual on every trade, not as an outcome on the
13.** That is exactly what M31 position telemetry was built for, and it is why
M31 P3 (the readers) is the highest-value next item rather than more sweeping.

**What the session's own work is worth, stated at its real strength:**

| shipped | what it is worth | how strong |
|---|---|---|
| M31 P2 `account_id` (#9660) | telemetry rows are now attributable to an account | **live-verified** — 12/12 rows, 5 accounts, backfill observed working |
| `exit-mechanism-coverage-guard` (#9679) | an orphan declare can no longer land | **CI-enforced**, and it found 0 orphans over 46/47 legs |
| arm-above-cap mechanism (§1) | 3 declared levers proven inert | **measured**, one over complete history |
| `gld_pullback_1d` population (§3) | the 3.86R arm proven un-shippable | **measured**, n=112, two independent derivations agree |
| Path B re-sweep (§3) | 2 Path A PASSes on `sol_pullback_2h` | **measured**, but on a leg that cannot run the lever |
| relay fixes (§7) | two silent-dispatch failures closed | **falsification-tested**, not diff-read |

⚠️ **Not one of these changed a live trading outcome, and none was supposed
to.** They changed what is *measurable* and what can *silently land*. Judge them
on that, and if in three months nothing downstream has used them, the honest
read is that the measurement layer was built and never consumed — which is the
`exit_price_source` failure this repo has already had once.

### 8.2 The seven Tier-3 items — with what a decision buys

Five registry entries (`config/lever_reachability.json`) + two harness defaults.
**Three are decisions; two are measurement; two are defaults.**

| # | item | expectation if decided | failure signal |
|---|---|---|---|
| 1 | `gld_pullback_1d` arm 5.06 | **Record `inert`.** 0/8 over complete history, and the re-swept 3.86 is *also* unreachable (0/8 live) — so there is no third number to find. Buys: the coverage matrix stops counting a dead lever as shipped. | If anyone proposes a *new* arm without first showing `risk/entry ≤ 0.099/(2.0 × arm)` holds on live entries, the same error is recurring. |
| 2 | `qqq_trend_long_1d` arm 3.56 | **Record `inert`** (cap_R 2.13, two bases agree exactly). n=1 is thin — the corroboration is what carries it. | A later entry with `risk/entry` materially below 4.6% would falsify it; that single observation should reopen the item. |
| 3 | `xrp_pullback_2h` arm 4.49 | **A real choice, not a cleanup** — 33.3% reachable on a truncated, recency-biased sample. Expect to *widen the sample first*, not decide now. | If it is decided on the 6-row sample, the decision is being made on the same unstated-denominator basis this memo exists to stop. |
| 4–5 | the two `unmeasured` entries | Measurement, **not** a decision. Expect a number, then they join rows 1–3. | Still `unmeasured` in a month = the queue is a parking lot, not a queue. |
| 6 | `--tp-cap-pct` default → 0.099 | Every future sweep measures the book production runs. Buys: the class of error in §1 becomes unreachable by default. | Any sweep result quoted without the flag, after the flip, means the default did not take. |
| 7 | `--split-target-oos` default | It currently equals `MIN_OOS_TRADES` (25), so **the target IS the floor** and any boundary loss → `insufficient_base` on every cell. Expect a separated value. | A sweep returning `insufficient_base` on a leg with hundreds of lifetime trades — the exact tell that surfaced this (`htf_pullback_trend_2h` at 407). |

### 8.3 The next work, ranked, with expectations

**1 — M31 P3: the telemetry readers.** *Unblocked as of #9660; nothing else
gates it.* **Expect:** per-trade counterfactual R for each declared lever, so a
lever's value is answerable at n=1,142 instead of n=13. **Know by:** the first
read over a week of accrued rows. **Failure signal:** rows accruing with no
consumer — if `position_telemetry` reaches a month of data and nothing reads it,
it has become another written-and-never-read field, and the honest move is to
say so rather than keep writing.

**2 — `sol_pullback_2h`: implement, or drop the result.** The session's best
measured result (2 Path A PASSes, `ok/ok` both windows, IS=175/OOS=49) sits on
`htf_pullback_trend_2h`, **which implements no giveback lever** — the harness
applied it in-engine (`backtest_pullback.py:536-539`). **Expect:** a Tier-3
decision to *implement* it in the unit module. **Failure signal:** if it is
neither implemented nor explicitly dropped within a review cycle, a future
session will re-derive the same two PASSes and be equally unable to use them —
and that is precisely the two-month rot this section exists to prevent. ⚠️ The
declare path is **not** available as a shortcut: #9679 now fails CI on it.

**3 — Why the live book enters ~1.4× wider than the backtest.** §3 resolved
*that* it does and left *why* open. **A concurrent session (`session_01Xk2ozj`,
branch `claude/m31-tier3-gld-live-u2djba`) picked this up at 13:41Z** and has
already refuted the sizing-path hypothesis from code alone (`sl` is fixed at
signal time, before sizing runs), leaving the era/regime hypothesis. **Expect:**
if recent-era backtest entries land in the live band, this is a **methodology
finding for every arm sweep**, not a gld fact. **Failure signal:** if it lands
as a gld-only note, the generalisation was missed.

**4 — Tick cost, cause not established.** 83.9 s → 137.6 s, persisting across a
restart, a **tail not a uniform slowdown** (three timeframes unchanged, four at
2.3–8.4×), cache hit rate 45.6% — **so raising the cache cap is not the
answer** and must not be tried again as one. **Failure signal:** a proposed fix
that does not first explain why exactly three timeframes are unaffected.

**5 — `squeeze_breakout_4h` implements 0 of 4 mechanisms; `htf_pullback_trend_2h`
(18 of 47 live legs) implements 1 of 4 [**now 3 of 4 as of 2026-08-18** — see the superseded note above].** Not a bug — a coverage gap, now
visible. **Expect:** a deliberate decision per family, not per leg. **Failure
signal:** the matrix reporting the same distribution next quarter means it was
read as a report rather than a work list.
