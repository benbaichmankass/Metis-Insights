# M20 exit-capture census — where the exit money actually is

**Date:** 2026-08-10 · **Status:** measurement only, nothing graded, no lever shipped
**Driver:** operator directive 2026-08-10 — *"capital efficiency isn't just a gate for
testing, it's a principle… we need a much better way of understanding when it's time to
get out of a trade… exhaustive testing to make sure we have checked all the levers that
make sense to check, not just one or two."* Then: *"yes measure first, go ahead."*

## Why a census before a lever

`capture_ratio` (realized R ÷ maximum favourable excursion) has been computed in this
repo since `src/research/excursions.py` landed and **no gate, sweep verdict, or coverage
cell has ever read it** (`BL-20260810-EXIT-GATE-BLIND-TO-CAPTURE-AND-CAPITAL`). M20's
gate optimises `net_R` and `maxDD`, both indifferent to handing a winner back. That is
how 266 of 400 coverage cells came to be graded `honest_negative` **by an objective
function that could not see the thing being complained about.**

So: size the prize and order the legs first. The alternative is designing a lever from
one remembered trade.

## The two complaints are not the same size

The operator described two things. Measured, they differ by roughly two orders of
magnitude.

### 1. "Got within cents of the take profit, then it turned into a loss"

Well-posed only where a leg has a **fixed, operative** target. That is `ict_scalp`
(`tp_at_r: 1.5` on every live leg) — the trail families' declared `tp_r: 50.0` is a
disabled-TP **sentinel**, not a target, and treating it as one is what made the first
census print a reassuring `near_miss_90_pct: 0.0`.

POPULATION: **all 7 runnable** `ict_scalp` legs (`ict_scalp_mgc_15m` is
`blocked:data_missing`), backtest on `m27_data`, full history, config-exact base
**including `--sim-breakeven`**.

| leg | trades | losers | reversed from ≥90% of TP | clean TP hits | R left *(unbounded)* | per trade |
|---|--:|--:|--:|--:|--:|--:|
| `ict_scalp_sol_15m` | 422 | 201 | 0.50% (1) | 82 | 3.85R | 3.8 |
| `ict_scalp_eth_15m` | 387 | 238 | 0.84% (2) | 85 | 7.45R | 3.7 |
| `ict_scalp_xrp_15m` | 361 | 187 | 1.60% (3) | 71 | 9.48R | 3.2 |
| `ict_scalp_xrp_5m` | 748 | 374 | 1.34% (5) | 178 | 11.86R | 2.4 |
| `ict_scalp_5m` | 697 | 365 | 1.64% (6) | 165 | 51.82R | 8.6 |
| `ict_scalp_sol_5m` | 915 | 470 | 0.64% (3) | 234 | **135.81R** | **45.3** ⚠ |
| `ict_scalp_avax_5m` | 1102 | 550 | 0.73% (4) | 267 | **182.34R** | **45.6** ⚠ |
| **total** | **4,632** | **2,385** | **1.01% (24)** | **1,082** | 402.6R | |

**The complaint is real and it is rare** — 24 trades, 1.01% of losers, on every leg.

**But the R figure needed fixing before it could be quoted, and this is the second
measurement defect this census produced.** `R left` is `mfe_r − net_r`, summing an
*unbounded intrabar peak*. Two legs report ~45R **per near-miss trade** against 2.4–3.8R
everywhere else, and the cause is structural rather than a property of those books: the
harnesses check the stop BEFORE the target inside a bar (*"pessimistic ordering: if both
touched in one bar, count SL first"*) while `best` is updated from `bar_high` at the top
of the same iteration. **One bar spanning from below the stop to far above the target
books a −1R loss AND records a huge MFE.** Three legs contribute 92% of the total from 13
trades.

So `near_miss_r_to_target` (`target_r − net_r`) is now the figure to read — bounded by the
trade's own plan, which is the most any exit change could have banked:

| basis | R | vs 1,623R gross from clean target hits |
|---|--:|--:|
| unbounded `R left` — **do not quote** | 402.6R | 24.81% |
| **bounded `R→target`** — the honest ceiling | **60.0R** | **3.70%** |

A **6.7× overstatement**, and 24.81% would have read as a major leak. The honest ceiling
is 3.70% of gross, and even that is a ceiling: it assumes every near-miss converts to a
full target hit.

**The mechanism is precise, and it is worth fixing on its own terms.** `ict_scalp` already
ratchets the stop to break-even at +1R (`monitor_breakeven_sl`, unconditional — no config
gate), and the census models it. So how does a trade reach ≥1.35R and still close at −1R?
Because **the ratchet arms on a bar CLOSE ≥ 1R while MFE is the intrabar HIGH.** A spike
that touches near-target and closes back below 1R never arms it. That is exactly the
shape the operator described, and it points at a specific, testable change: arm the
ratchet on an intrabar **touch** of 1R rather than a bar close. Prize bounded above by
60R across the seven legs; realistically ~24R (converting a −1R loss to ~0R), before
counting what a touch-armed ratchet costs on the trades that dip and recover — which is
the whole question, and the reason this is a sweep and not a patch.

### 2. "Holding on to trades that are no longer worthwhile" — this is where the money is

For a trail leg there is no target to nearly reach, so near-miss is undefined (`None`,
never `0.0`). The target-free form of the same question is the **giveback ladder**: of the
trades that went meaningfully favourable, how many still closed red, and what did it cost?

POPULATION: 44 trail legs, **LIVE-PARITY geometry** (`tp_cap_pct=0.099` for
donchian/pullback/squeeze; `fvg_range` carries no clamp so 0 *is* its live parity), full
backtest history.

> **10,415 trades · 4,616 reached +1R of open profit · 1,320 of those (28.6%) STILL
> CLOSED RED · 2,443R left on the table.**

That is ~75× the R in the near-TP-reversal pool.

| timeframe | legs | lost after +1R | rate | R left |
|---|--:|--:|--:|--:|
| 1h | 14 | 811 / 2,625 | 30.9% | 1,480.7R |
| 2h | 7 | 262 / 926 | 28.3% | 490.0R |
| 4h | 6 | 112 / 384 | 29.2% | 219.4R |
| 1d | 16 | 135 / 680 | **19.9%** | 252.9R |

| venue | legs | lost after +1R | rate | R left |
|---|--:|--:|--:|--:|
| bybit | 17 | 715 / 2,117 | **33.8%** | 1,400.4R |
| prop | 3 | 179 / 658 | 27.2% | 262.9R |
| ibkr | 4 | 55 / 205 | 26.8% | 96.5R |
| alpaca | 19 | 334 / 1,527 | **21.9%** | 617.8R |

Worst eight by R left — where a lever would pay most:

| leg | tf | venue | lost/reached | R left | R-weighted capture |
|---|---|---|--:|--:|--:|
| `trend_donchian_1h` | 1h | bybit | 150/374 (40.1%) | 316.5R | **−0.0150** |
| `trend_donchian_eth` | 1h | bybit | 106/273 (38.8%) | 189.9R | +0.0370 |
| `trend_donchian` | 1h | bybit | 73/181 (40.3%) | 154.1R | +0.1113 |
| `trend_donchian_eth_prop` | 1h | prop | 103/357 (28.9%) | 152.2R | +0.0146 |
| `htf_pullback_trend_2h` | 2h | bybit | 66/207 (31.9%) | 111.1R | +0.0377 |
| `spy_pullback_1h` | 1h | alpaca | 50/137 (36.5%) | 106.5R | +0.1200 |
| `eth_pullback_2h` | 2h | bybit | 49/136 (36.0%) | 101.4R | +0.0838 |
| `qqq_pullback_1h` | 1h | alpaca | 45/144 (31.2%) | 94.3R | +0.1228 |

**Two legs have NEGATIVE R-weighted capture** — of all the favourable excursion the book
was offered, they banked less than nothing: `trend_donchian_1h` (−0.0150) and
`fvg_range_15m` (−0.4778).

### Read 2,443R as an upper bound, not a prize

`R left` is `mfe_r − net_r`: the full swing from peak to close, i.e. what you would
recover by exiting **exactly at the peak**, which no lever does. Any real lever also
truncates winners — and **71.4% of the trades that reached +1R did NOT close red.** A flat
"exit at +1R" rule would harvest the 28.6% and destroy the 71.4%. This is the same result
the operator already anticipated from the other direction (`xrp_pullback_2h` sat 139 bars
on the 2h timeframe and finished at +3.94R): **the lever must be conditional — old AND not
working — never a flat rule.** The ladder's job is to say how much is on the table and
where; the M20 sweep's job is to find a condition that takes it without paying for it.

## Why the capture median is the wrong headline

`squeeze_breakout_4h` reads capture median −0.39 with 72.7% of trades under 30% capture,
which looks alarming. Its giveback ladder says 9/45 (20.0%), 13.3R — one of the *mildest*
in the fleet. A breakout book is structurally full of small pokes that fail; a bad
`cap <30%` can be that structure rather than leakage.

The regression test pins exactly this: a poke-book scores **worse** on `cap <30%` (70.0 vs
40.0) than a book that hands real winners back, and the ladder inverts it correctly (0 lost
at the 2R rung vs 40 lost and 128R). Read the ladder; the capture buckets are context.

## Defects found while measuring (all fixed in PR #8721)

Five, each of which would have produced a confident wrong reading. Note the shape they
share: **every one of them fails toward a number that looks like a finding.**

1. **`capture_mean` is denominator noise.** A loser peaking at 0.05R and closing at −1R
   contributes −20 to the mean by itself; `fvg_range_15m` printed −14.13. Reproduced in
   test: a book whose true capture is 0.8 prints −5.44. Added `capture_median` /
   `capture_mean_robust` (over an MFE floor) with `capture_lowmfe_n` always stated.
2. **A declared target is not an operative one.** Eight pullback legs declare `tp_r: 50.0`
   — a disabled-TP sentinel. Taken at face value, the census printed
   `near_miss_90_pct: 0.0` for `eth_pullback_2h`, the very leg the operator cited. Now
   derived from the population: if no trade ever entered even the widest band, the target
   is not operative → `None` plus a stated reason, never `0.0`.
3. **24 of 52 legs were structurally unmeasurable**, then a fifth family after that:
   `backtest_trend` / `backtest_squeeze` computed `mfe_r` and never emitted it, and
   `backtest_ict_scalp` nests it under `meta` while readers looked top-level — the scalp
   census measured **0 of 3,823 trades**. Now one accessor (`exit_capture.mfe_r_of`), and a
   leg that traded with zero capture coverage is flagged on its own line rather than
   appearing as a row of `None`s.
4. **The M20 P4.4 percentile arm was inert for the whole `ict_scalp` family.**
   `winner_mfe_p80` read `mfe_r` top-level, collected zero, and returned `None` — which its
   contract defines as *"fewer than 30 winners"*. It booked a plausible thin-sample
   abstention for a leg with 1,102 trades (`BL-20260810-P80-PERCENTILE-ARM-INERT-FOR-ICT-SCALP`).
   Scope of damage measured and it is **zero**: every scalp leg's `vol_trail` /
   `trail_geometry` cell is `n/a` (ict_scalp has no primary trailing stop), so no recorded
   verdict rests on the inert arm.
5. **The near-miss prize was 6.7× overstated by the spanning-bar artifact** documented
   above — 24.81% of gross rather than the honest 3.70%. Fixed with
   `near_miss_r_to_target`, and the giveback ladder now ships `r_left_median` beside its
   sum so the same skew is visible there without opening the artifact.

## The live-parity break, corrected

`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`: production clamps the 50R
sentinel to 9.9% from entry; the harnesses modelled no TP at all. Two corrections to the
first reading of that finding:

- **It is venue-blind.** The constant's comment says *"Bybit (and most exchanges)…"*, so
  the natural inference is that Alpaca/IBKR legs never bind it. The code has **no exchange
  or account branch** (`trend_donchian.py:388`, `htf_pullback_trend_2h.py:322`,
  `squeeze_breakout_4h.py:176`, `fade_breakout_4h.py:264`). Field beats comment — the break
  is **wider** than the Bybit-only reading, not narrower.
- **`fade_breakout_4h` is `execution: shadow`.** It carries the clamp but places no live
  order, so the break reaches money through **three** families, not four.

It is not a perturbation. Under live parity `tqqq_trend_long_1d` goes 32 → 75 trades and
median capture +0.40 → +1.05; `trend_donchian_ada_4h` goes 163 → 220. Every M20 verdict
recorded before 2026-08-10 was measured against an exit geometry production does not run.

## What this says to do next

1. **Design the stale/giveback lever against the 1h Bybit trend legs first** — 1,400R of
   the 2,443R sits on bybit, and `trend_donchian_1h` alone is 316R with negative
   R-weighted capture. Not against the whole fleet at once.
2. **The lever must be conditional.** 71.4% of +1R trades did not close red.
3. **Re-derive any pre-2026-08-10 verdict under live parity** before treating it as a
   negative. `honest_negative` measured on the wrong geometry is not honest yet.
4. **`ict_scalp`'s touch-vs-close ratchet** is a separate, small, cleanly-scoped fix with
   its own measured prize (~11R realistic across four legs).

Nothing here grades a leg or ships a lever. Thresholds are the operator's to set from
these distributions — the same discipline `capital_efficiency` follows.
