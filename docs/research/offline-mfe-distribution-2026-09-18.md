# The offline MFE distribution at stated n — and what it says about a per-leg take-profit

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-307 · `2026-09-18` · object `WO-20260918-RESEARCH-THE-OFFLINE-MFE-DISTRIBUTION-AT-STATED-N`
> · row `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON`

**This is clause (2) of
`OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON`:**
*"the offline-harness MFE distribution is produced at stated n and a per-leg proposal is put in
front of the operator off THAT n rather than off the live 1–8."*

**⚠️ PROPOSE ONLY. Nothing in `config/` is touched. Every `tp_r` and `atr_stop_mult` is Tier-3
and the operator's.**

---

## 0. The answer in one paragraph

The distribution was produced, at **n = 112–1,004 per leg on 19 legs** (against the live n = 1–8),
and the instrument was **validated against a positive control it could have failed**. The result
does **not** support proposing a per-leg `tp_r`, and the reason is the finding: **the fleet already
has a per-leg take-profit on every leg, and it is set by `TP_VENUE_CAP_PCT = 0.099` — a single
fleet-wide constant named for a Bybit boundary — not by any leg's evidence.** Expressed in each
leg's own R that undeclared target ranges from **1.74R to 7.20R (a 4.1× spread nobody chose)**, and
it is **not inert**: over the 19 measured legs (n = 112–1,004 trades each) it ends between **5.1%**
(`trend_donchian`, n=409) and **44.2%** (`trend_donchian_ada_4h`, n=233) of trades. Above it, a declared
`tp_r` changes nothing at all. So the actionable decision is not 19 per-leg numbers; it is the one
number that is already acting, and whether a per-leg target should sit **below** it.

---

## 1. Population, and what could not be measured

**Instrument:** `scripts/research/mi307_offline_mfe.py` (Tier-1, new this unit; **14 selftests**,
of which 4 are negative controls that fail if the rule they guard is deleted). It **imports** rather than re-derives every definition it
uses: `exit_capture.mfe_r_of` (the one MFE reader), `bracket_calibration.quantile` (the one
quantile — so these figures are comparable to MI-148's live instrument by construction),
`tp_venue_cap.TP_VENUE_CAP_PCT` (the one clamp), and
`m20_fleet_exit_sweep.{classify, resolve_data, base_args, harness_implements_flag}` (the one leg→harness→own-declared-params
resolver, so each leg runs **config-exact**: its own `donchian`, `atr_stop_mult`, `trail_mult`,
`min_confidence`, `adx_min`, `long_only` and declared trail-decay levers).

**Candles:** Binance Vision USDⓈ-M futures archive, **2021-01-01 → 2026-09-16**, fetched with the
repo's own `scripts/ops/fetch_backtest_candles.py --source binance_vision`.

> ⚠️ **`api.bybit.com` is geoblocked from these containers** — HTTP 403, CloudFront country block,
> measured this session. The Binance Vision fallback in that fetcher is what makes this unit
> possible in-session at all; it took **62 s** for 12,480 SOLUSDT 4h bars. This is the same
> substrate `BL-20260727-BYBIT-USGEOBLOCK-GHRUNNER` records for GH runners.

### 1.1 The 55 legs, and why 36 carry no number

**POPULATION: all 55 entries in `config/strategies.yaml`.** State the reason per leg, because
"we did not look" and "there is nothing there" are different facts:

| state | legs | why |
|---|---:|---|
| **`ok` — measured** | **19** | crypto symbol + a harness implementing `--tp-cap-pct` |
| `no_data` | 25 | **non-crypto**: GDX · GLD ×2 · IAUM · IEF · IWM · MES · MGC ×3 · MHG · QLD · QQQ ×2 · SCHA · SLV ×2 · SPLG · SPY ×2 · TLT ×2 · TQQQ · USO · XAUUSD |
| `not_capped_capable` | 8 | the 7 `ict_scalp_*` legs and `fvg_range_15m` |
| `no_harness` | 3 | `fade_breakout_4h`, `turtle_soup`, `vwap` — `m20_fleet_exit_sweep.classify()` returns `None` for them |

`19 + 25 + 8 + 3 = 55`. ✅

⚠️ **`not_capped_capable` is a separate state and is NOT `no_data`.** Those 8 legs are crypto, their
candles **were fetched** (5m and 15m, 200k–600k bars each) and are staged; what is missing is one
harness flag — `backtest_ict_scalp.py` and `backtest_fvg_range.py` implement no `--tp-cap-pct`.
Reporting them as `no_data` would assert the **feed** cannot serve them, which is false, and would
hide a gap that is one flag wide behind a data-availability wall. The consequence is what earns it
a state: with no capped arm there is no live-comparable distribution **and no positive control**
(§ 2.1), so an uncapped-only figure for those legs would be an unfalsifiable one. The membership
test is `m20_fleet_exit_sweep.harness_implements_flag`, which **reads the harness source** rather
than a hardcoded list, so it cannot go stale the day a harness gains the flag.

⚠️ **This is the family where a target would matter most, which is why it is named loudly rather
than dropped.** MI-278 U2 measured take-profit as the **only** exit lever with attributed mass on
`ict_scalp` — 18 of 49 winners ended exactly at target — and `ict_scalp` is the one family that
declares `tp_at_r` rather than the `tp_r` sentinel. **The highest-value legs for this question are
the ones this unit cannot grade.** Filed.

**The 25 non-crypto legs are the named hazard arriving, not a surprise.** It is the same limit
`#12205` measured (GLD, MGC, QQQ, SLV, SPY, TLT, USO → 0 candles). ⚠️ **It is a feed limit, never a
statement about those legs** — and it is why `qld_trend_long_1d` / `tqqq_trend_long_1d` still read
`tp_intent: unexamined` with reason `never_swept_no_free_lane_candle_feed`.

⚠️ **The 3 `no_harness` legs are a resolver gap, not a family gap.** `backtest_fade.py` exists and
implements `--emit-trades` and `--tp-cap-pct`; `classify()` simply has no branch for `fade`. Filed.

### 1.2 Provenance — run through the canonical module, and the answer is uncomfortable

Per the work object, the mix is reported through **`src/runtime/provenance.py`**, not a bespoke
predicate. Run against a harness emit row it returns:

```
classify_row(harness_row) -> ('unverified', '(none)')
classify_pnl(harness_row) -> ('unverified', '(no provenance on either key)')
```

**Every one of the ~7,000 harness rows behind this memo is `unverified`, and that is the correct
reading, not a defect to explain away.** A harness row carries no `exit_price_source` because no
broker ever filled it. **That is the defining property of a backtest and it is precisely why
fidelity is gate condition 1** — the n is large and the provenance is nil, which is the exact
trade the live arm makes in reverse.

The **live** comparison arm (`/api/diag/position_telemetry`, read 2026-09-18, n=313) carries its own
provenance and it is equally stark: **`peak_provenance: estimated` on 313/313** and
**`peak_r_is_lower_bound: true` on 313/313**, with `peak_state` `measured` 202 / `thin_window` 111.

---

## 2. Method — two arms, never pooled

| arm | `--tp-cap-pct` | what it is for |
|---|---|---|
| **uncapped** | `0` (no take-profit exit path) | the **target-setting** basis |
| **capped** | `0.099` + the leg's own `tp_r` | the **live-comparable** basis |

**Why both.** The capped arm reproduces the live book exactly — and therefore truncates `mfe_r` at
each trade's own `cap_r`, so it can say nothing about what lies above. That is the right book for
MFE *parity* (which is why `m31_harness_mfe_dist.py` **refuses** to commit an uncapped distribution
under the name Check B reads) and the wrong book for setting a target.

**The quantity that matters is not `P(mfe_r ≥ x)`.** A declared `tp_r` does not set the target:
`tp_venue_cap.py` states that **no `tp_r` reproduces the clamp**, because the effective target is
`min(cap_r, tp_r)` with `cap_r = 0.099 × entry / risk`. So the reach a candidate would actually
produce is

> **`effective_reach(x) = P( mfe_r ≥ min(cap_r, x) )`**

computed per trade. It is an exact **count**, not a quantile — deliberately, because two
incompatible percentile conventions already exist in this repo for good reasons
(`m31_mfe_parity._pct` nearest-rank, `bracket_calibration.quantile` linear-interpolated) and adding
a third to express something natively countable is the drift both modules warn about.

⚠️ **`cap_r` is computed from the harness's OWN entry stop**, and this is the one denominator the
live side cannot produce honestly: `trades.stop_loss` is the **final trailed** stop, so live R is
contaminated (`bracket_calibration.py` § "WHY PERCENT-OF-ENTRY AND NOT R"; MI-144 measured 104 of
1,287 rows carrying 96.6% of `totalR`). The harness emits the entry stop and its `gross_r` is
exactly −1.0 on a stop-out. **That is why this unit can report in R where MI-148 deliberately
cannot** — and it follows MI-155's reconciliation throughout: **MEASURE in percent-of-entry,
EXPRESS the config knob as `tp_r`.**

⚠️ **MFE is still censored by the trail in both arms**, and that is not a defect: a trade ends when
its exit mechanism ends it. These figures describe the excursion available **under the current
trail**, which is exactly what a take-profit would compete with.

### 2.1 The positive control — the instrument could have failed and did not

If `effective_reach(x)` is the right quantity, then evaluated at a leg's declared `tp_r` it must
predict the **observed** `take_profit` exit share of the **capped** run. The two arms are different
books with different trade counts (e.g. `sol_pullback_2h`: 200 uncapped vs 277 capped), so this is
not circular.

**POPULATION: all 19 measured legs.**

| | mean | median | max abs |
|---|---:|---:|---:|
| observed − predicted | **−0.3 pp** | **+0.1 pp** | **3.2 pp** |

**19 of 19 legs agree within 3.2 pp.** Worst: `trend_donchian_ada_4h` (pred 47.4%, obs 44.2%) and
`sol_pullback_2h` (pred 41.0%, obs 37.9%).

---

## 3. The distribution, at stated n

**POPULATION: uncapped arm, all trades (not winners-only — see § 3.1), 2021-01-01 → 2026-09-16.**

| leg | n | MFE p50 | p80 | p90 | cap_r p50 | declared `tp_r` | `tp_intent` |
|---|---:|---:|---:|---:|---:|---:|---|
| `trend_donchian_eth_prop` | 1004 | 0.69 | 1.79 | 2.87 | 4.15 | 6.0 | *(absent)* |
| `trend_donchian_1h` | 843 | 0.98 | 2.83 | 4.50 | 5.26 | 50.0 | *(absent)* |
| `trend_donchian_eth` | 633 | 1.07 | 3.09 | 4.46 | 4.01 | 50.0 | `none` |
| `avax_pullback_2h` | 398 | 0.88 | 2.40 | 3.50 | 2.27 | 50.0 | *(absent)* |
| `trend_donchian` | 400 | 1.12 | 3.63 | 5.63 | 7.20 | 50.0 | `none` |
| `htf_pullback_trend_2h` | 384 | 0.98 | 2.15 | 3.12 | 2.93 | 50.0 | *(absent)* |
| `trend_donchian_sol_prop` | 370 | 1.07 | 2.56 | 3.53 | 3.04 | 6.0 | *(absent)* |
| `eth_pullback_2h` | 368 | 0.85 | 2.04 | 3.09 | 2.61 | 50.0 | `none` |
| `trend_donchian_sol` | 339 | 1.33 | 3.31 | 4.71 | 3.05 | 50.0 | `none` |
| `eth_pullback_prop_2h` | 335 | 0.95 | 2.37 | 3.33 | 2.51 | 6.0 | *(absent)* |
| `xrp_pullback_2h` | 268 | 1.17 | 3.32 | 4.56 | 2.12 | 3.0 | *(absent)* |
| `ada_pullback_2h` | 253 | 1.14 | 4.40 | 7.02 | 3.19 | 4 | *(absent)* |
| `trend_donchian_avax_4h` | 233 | 1.12 | 3.18 | 4.46 | 2.46 | 50.0 | `none` |
| `sol_pullback_2h` | 200 | 1.10 | 3.29 | 4.75 | 1.74 | 50.0 | `none` |
| `trend_donchian_sol_4h` | 193 | 1.47 | 5.43 | 6.53 | 2.32 | 50.0 | `none` |
| `trend_donchian_ada_4h` | 173 | 1.56 | 3.46 | 5.51 | 2.00 | 50.0 | `none` |
| `trend_donchian_eth_4h` | 167 | 1.43 | 4.07 | 6.46 | 2.44 | 50.0 | `none` |
| `trend_donchian_xrp_4h` | 146 | 0.64 | 2.35 | 3.52 | 2.43 | 3 | *(absent)* |
| `squeeze_breakout_4h` | 112 | 0.96 | 2.15 | 3.80 | 2.96 | 50.0 | `none` |

**Every leg clears the 30-reading floor `winner_mfe_p80` uses, by 3.7× to 33×.** The smallest,
`squeeze_breakout_4h` at n=112, is still **14× the largest live per-leg n** (25).

### 3.1 All trades, not winners-only — and why the existing p80 arm is not this

`m20_fleet_exit_sweep.winner_mfe_p80` already computes a per-leg MFE percentile and this is **not**
a duplicate of it. It restricts to `net_r > 0` because it calibrates a **trail arm**, and a trail
can only act on a trade that is already winning. **A take-profit is hit by any trade whose
excursion reaches it — including every trade that went +2R and then reversed into a stop.** Those
are exactly the trades a target would have changed, so grading a target on a winner-only population
removes the evidence for it. Both are published in the JSON; only the all-trades figure is used
here.

---

## 4. Finding 1 — the fleet already has a per-leg take-profit, and nobody chose it

`tp_r: 50.0` is read across this repo as "no R target". **It is not.** With `tp_r` above the clamp
the effective target is `cap_r`, and `cap_r = 0.099 × entry / risk` is a **different number for
every leg**, because `risk` is that leg's own `atr_stop_mult × ATR`.

**Measured, per leg, over the populations in § 3:**

| | value |
|---|---|
| `cap_r` p50 range across the 19 legs | **1.74R (`sol_pullback_2h`) → 7.20R (`trend_donchian`)** — a **4.1× spread** |
| the clamp-set target **fires** on | **5.1% (`trend_donchian`) → 44.2% (`trend_donchian_ada_4h`)** of trades |

**So `tp_intent: {mode: none, reason: "trail_is_the_profit_exit"}` — carried by 22 legs — is not
accurate as stated.** On `trend_donchian_ada_4h` the trail is the profit exit on 56% of trades and
a 9.9%-of-entry clamp is the profit exit on the other 44%. The declaration is right about intent
and wrong about mechanism, and it is the mechanism that trades.

### 4.1 Every leg that declares a real `tp_r` has it overridden on a majority of trades

**POPULATION: the 6 measured legs whose declared `tp_r` is below the 50R sentinel.**

| leg | declares | clamp is **tighter** on |
|---|---:|---:|
| `eth_pullback_prop_2h` | 6 | **96.7%** |
| `trend_donchian_sol_prop` | 6 | **93.8%** |
| `trend_donchian_eth_prop` | 6 | **79.0%** |
| `xrp_pullback_2h` | 3 | **78.4%** |
| `ada_pullback_2h` | 4 | **71.1%** |
| `trend_donchian_xrp_4h` | 3 | **69.9%** |

**6 of 6.** This is the population statistic
`OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED` needs and has never had: its
clause (b) requires a qualifying trade to have `cap_r > tp_r`, and on `trend_donchian_xrp_4h` —
one of the three legs it names as eligible — **only 30.1% of trades satisfy that**. The row's
suspicion was right and is now quantified.

### 4.2 Above the clamp, a declared `tp_r` is inert

`effective_reach(x)` **flattens** above `cap_r`, because the clamp sets the target either way:

| leg | eff. reach @ 3R | @ 4R | @ 6R | @ 10R |
|---|---:|---:|---:|---:|
| `trend_donchian_ada_4h` | 47.4% | 47.4% | 47.4% | 47.4% |
| `sol_pullback_2h` | 42.0% | 41.0% | 41.0% | 41.0% |
| `trend_donchian_sol_4h` | 42.0% | 40.9% | 39.9% | 39.9% |

**On 13 of the 19 legs, the effective reach at 4R and at 10R differ by less than 1 pp.** For those
legs, declaring `tp_r: 4` and declaring `tp_r: 50` produce **the same book**. Any proposal in that
band is a documentation change wearing the costume of a parameter change.

---

## 5. Finding 2 — where this sits against the fidelity gap (gate condition 1)

The work object requires this and it is the most consequential section.

`docs/research/ml2-predictive-bracket-2026-09-06.md` § 4 measures the two populations disagreeing
by **~2.5×** about crypto p90 MFE — backtest BTC **3.87%** (n=3,194) vs MI-148's live **9.70%**
(n=63) — and § 5 makes that magnitude the reason condition 1 is an **abstain floor**.

> ⚠️ **THE TIMEFRAME CONFOUND IS NOT THIS UNIT'S FINDING, AND CLAIMING IT WOULD BE WRONG.**
> **MI-155 established it on 2026-09-07** and `docs/research/RESEARCH-CAPABILITY-INDEX.md:179`
> already records the verdict in terms: *"measured inside the live crypto book alone p90 goes
> 2.16% (1h) → 7.07% (2h) → 9.77% (4h), a 4.5× span on horizon with no harness involved. So the
> ~2.5× is a **horizon-composition artifact**, not a fidelity failure — which removes it as
> evidence of infidelity without establishing fidelity."* That reading stands and this unit
> **confirms** it. Three things below are new, and they are the only things claimed here:
>
> 1. **a BACKTEST-side ladder** — MI-155's was live-only, so the confound had never been shown on
>    the arm the ~2.5× was measured *from*, at identical params;
> 2. **an independent reproduction of the memo's own 15m figure** (p90 4.32% vs its 3.87%), which
>    is the positive control that the ladder is measuring the same thing;
> 3. **the per-leg matched-timeframe comparison (§ 5.1)** — which MI-155 explicitly could **not**
>    make, because `n_backtest = 0` on all 44 legs, and which only becomes possible because of the
>    reference extension in § 7.3.

The memo states its own backtest population plainly: **`trend_donchian` 15m only**. Its live arm is
**"mixed symbols and strategies"**. Two measurements, one new and one confirming:

**(a) A controlled backtest ladder — BTCUSDT `trend_donchian`, identical params (`donchian 20`,
`atr_stop_mult 2.5`, `trail_mult 5.0`), only the bar changes:**

| timeframe | n | p50 | p80 | **p90** | reached 9.9% |
|---|---:|---:|---:|---:|---:|
| **15m** | 3,451 | 0.88% | 2.64% | **4.32%** | 1.36% |
| 1h | 843 | 1.91% | 5.66% | **9.38%** | 9.02% |
| 2h | 436 | 2.83% | 8.13% | **13.87%** | 14.91% |
| 4h | 205 | 4.41% | 12.35% | **18.15%** | 26.34% |

⚠️ **The 15m row independently reproduces the memo's figure** — 4.32% against its 3.87%, on a
different candle source, a different span and this repo's current params. That is the positive
control for this section: the instrument agrees with the prior measurement *where the populations
match*. The ladder then spans **4.2×** on timeframe alone, and **at 1h the same leg reads 9.38%,
within 3% of MI-148's live 9.70%.**

**(b) CONFIRMING MI-155 — the live data shows the same ladder, so it is not a harness artefact.**
Re-measured here at greater depth than MI-155's read (its max per-leg live n was 8; today's is 25). Live
`position_telemetry`, n=202 gradeable, joined to each leg's declared timeframe:

| timeframe | n | p50 | p80 | p90 |
|---|---:|---:|---:|---:|
| 5m | 40 | 0.46% | 1.50% | 1.90% |
| 15m | 22 | 0.76% | 1.30% | 2.74% |
| 1h | 54 | 1.01% | 2.20% | 3.20% |
| 2h | 37 | 1.01% | 4.66% | 8.63% |
| 4h | 33 | 3.16% | 9.51% | 9.75% |
| 1d | 16 | 3.16% | 3.48% | 3.69% |

**5.1× from 5m to 4h, in the live book itself.** And the composition is the point: **only 10.9% of
the live arm sits on a 15m bar**, against a backtest arm that is 100% 15m.

### 5.1 What follows — condition 1 is RE-AIMED, not cleared

**Do not read this as clearing condition 1.** Two things, and they point opposite ways:

1. **The cited ~2.5× is not evidence of backtest↔live infidelity.** It is a horizon difference
   wearing a fidelity label — exactly the defect `exit_location_fidelity.py`'s own docstring says
   its `(symbol, family, timeframe)` key exists to prevent. The abstain should not rest on it.
2. **⚠️ At MATCHED timeframe the sign REVERSES, and that is the more uncomfortable result.**
   With the reference extended (§ 7.3), MI-155's instrument now reports both sides **per leg, at
   matched symbol / family / timeframe** — the comparison condition 1 actually asks for, which the
   ~2.5× figure was standing in for. **POPULATION: the 12 legs with both sides present; live n=74
   total (1–9 per leg), backtest n=4,218.**

   | | value |
   |---|---|
   | legs where **backtest p90 > live p90** | **12 of 12** |
   | ratio | median **2.8×**, range 1.5× – 28.7× |

   The memo concluded backtest was 2.6× *pessimistic*; per leg it is **optimistic**, consistently.

   ⚠️ **The DIRECTION is not by itself evidence of infidelity, and saying so would overclaim.**
   Live `peak_r` carries `peak_r_is_lower_bound: true` on **314/314** rows — it is understated by
   construction, because the last telemetry write precedes the close by up to one exit-loop pass.
   That artefact alone predicts backtest > live, so 12-of-12 is the *expected* sign, not a finding.
   What is **not** obviously explained by one pass of lag is the **magnitude** (median 2.8×), and
   quantifying that lag is its own unit. ⚠️ And every one of these legs is at **n_live = 1–9,
   far below the abstain floor of 30**, so this is a **flag, not a verdict** — which is exactly
   what the instrument itself says by grading all 12 `insufficient_n`.

**So condition 1 remains UNMET, for a corrected reason, and it is now measurable.** What changed is
that the question can be asked per leg at all; what has not changed is that no leg has the live
depth to answer it.

---

## 6. Finding 3 — the sweep already answered "which `tp_r`", and the answer was none

Before proposing any per-leg number, the recorded evidence was checked rather than assumed.

**POPULATION: `docs/research/e35-bracket-corpus.jsonl`, all 8,520 rows, restricted to the 19 legs
here → 3,965 rows, of which 3,413 carry a real `tp_r` (< 50).** Values swept:
**{1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0}**, roughly **180 cells per leg**.

**12 of the 19 legs have ZERO cells passing both IS and OOS.** The 7 that have any have 1–3 out of
~180, which at that multiplicity is what noise produces.

**This is what `tp_intent: {mode: none, evidence: "no_gate_passing_timeout_free_tp_cell"}` already
records, and this unit does not overturn it — it explains it.** The reach a target would buy is
largely *already being bought by the clamp*, so a `tp_r` below `cap_r` mostly just truncates the
trail earlier, and the sweep measured that it does not pay.

⚠️ **A reach-rate is not a P&L claim.** `effective_reach(x)` says a target at `x` would have been
**touched**; it says nothing about whether the leg earns more. Net-R is a sweep's question, and the
sweep has answered it.

---

## 7. What is proposed

### 7.1 No per-leg `tp_r` is proposed, and that is the finding

For the 19 measured legs the MFE distribution **does not support** a per-leg `tp_r`, because above
`cap_r` a declared value is inert (§ 4.2) and below it the e35 sweep already tested 7 values × ~180
cells per leg and found nothing that generalises (§ 6). **`insufficient_n` is not why — n is
ample.** The answer is `none`, now on measured grounds rather than by default.

**That is the honest close the work object asks for, and it is a harder fact than a number:** the
sentinel idiom stands **by evidence**, not by nobody having looked.

### 7.2 What the operator is actually being asked to decide — Tier-3

**The decision is `TP_VENUE_CAP_PCT`, not 19 per-leg knobs.** It is the one parameter that is
already setting every leg's target, it is fleet-wide, it is named for a Bybit boundary, and it is
applied to legs that touch no Bybit account. Three options, stated without a recommendation because
the evidence does not pick one:

| option | what it means | what this memo supports |
|---|---|---|
| **A — leave it** | accept a 4.1× per-leg spread in the real target, set by an exchange limit | fully consistent with § 6; the sweep found nothing better |
| **B — declare it** | change `tp_intent` on the 22 `mode: none` legs to record that a clamp-set target exists and fires on 5–44% of trades | **supported now, Tier-1 documentation** — § 4 |
| **C — set it per leg** | give each leg its own cap in percent-of-entry, sized from its own `cap_r` | **NOT supported today** — needs condition 1 per leg at matched timeframe (§ 5.1) |

**The per-leg actionable band, for whenever C is revisited:** a `tp_r` below `cap_r` p10 binds on
~90% of trades; above `cap_r` p90 it is inert on ~90%. Both columns are in the committed JSON.

### 7.3 One thing this unit changes mechanically, tonight

`scripts/research/exit_location_fidelity.py` — **MI-155's condition-1 instrument** — was run this
session (it takes only `--api` + `--token`, both available). It graded **44 of 44 enabled+live legs
`insufficient_n`**, for **two** reasons:

- `no_backtest_corpus_for_leg` — its reference held **3 legs**, all `trend_donchian` 15m, and
  **transcribed** from MI-151's published table because the 9,814-row corpus behind it was produced
  on the trainer and never committed;
- `live_n_below_abstain_floor` — max `n_live` measured **25**, floor **30**.

**This unit supplies the first half.** `--write-reference` projects the uncapped arm into that
file's exact `(symbol, family, timeframe)` schema, taking it from **3 legs to 18**, from rows that
were **measured here and can be re-measured by re-running `--run`** — the difference between a
reproduction and a transcription. MI-151's 3 rows are **never overwritten**: they are the only
independent cross-check this unit has.

**Measured effect, re-running the instrument after the write:** **13 legs moved** from
`no_backtest_corpus_for_leg AND live_n_below_abstain_floor` to **`live_n_below_abstain_floor`
alone**, and the `btP90` column is populated for the first time (§ 5.1 is read off it).

⚠️ **18, not 22 — MI-155's key is NOT unique over this fleet, and the collision is real.**
`(symbol, family, timeframe)` deliberately omits the leg, because it must join to a *live* leg by
those three fields. But **4 pairs of the 19 share a key while running different declared params**,
and their p90 disagrees by **1.13× to 1.67×**:

| key | legs | p90 spread |
|---|---|---:|
| `ETHUSDT / trend_donchian / 1h` | `trend_donchian_eth_prop` · `trend_donchian_eth` | **1.67×** |
| `ETHUSDT / pullback / 2h` | `eth_pullback_2h` · `eth_pullback_prop_2h` | 1.33× |
| `SOLUSDT / trend_donchian / 1h` | `trend_donchian_sol_prop` · `trend_donchian_sol` | 1.23× |
| `BTCUSDT / trend_donchian / 1h` | `trend_donchian_1h` · `trend_donchian` | 1.13× |

Taking whichever arrived first would bake an arbitrary pick into the artifact and hide a 1.67×
disagreement behind one number. **The larger-n arm supplies the figures and the collision travels
with the row** — `key_not_unique`, `key_shared_with`, `key_selection`, `key_collision_p90_spread`
and the per-leg p90s are all written into it, so a consumer meets the ambiguity instead of
inheriting it. Filed as a limitation of the schema, not worked around.

⚠️ **This does NOT clear condition 1 and must not be read as doing so.** The live half is a soak and
is unmet on every leg — **44 of 44 still `insufficient_n`**, max `n_live` 25 against a floor of 30.
What changes is the **reason** a leg abstains, which says the remaining work is **waiting**, not
**building**.

---

## 8. What this does not establish

- **Not a P&L claim.** § 6.
- **Not a fidelity clearance.** § 5.1 — and it opened a new question (backtest optimistic at matched
  timeframe) that it did not close.
- **Not applicable to the 36 unmeasured legs.** § 1.1. The 25 non-crypto ones are the binding gap
  and no in-session feed reaches them.
- **Not a live measurement.** Every figure here is simulated; `provenance.py` classifies all of it
  `unverified` (§ 1.2), correctly.
- **Nothing here was armed, merged into config, or proposed as a value.**

---

## 9. Reproduce

```bash
python3 scripts/research/mi307_offline_mfe.py --selftest      # 13 checks, 4 negative controls
python3 scripts/ops/fetch_backtest_candles.py --symbol SOLUSDT --interval 240 \
    --source binance_vision --start-date 2021-01-01 --output data/SOLUSDT_4h.csv
python3 scripts/research/mi307_offline_mfe.py --run
python3 scripts/research/mi307_offline_mfe.py --report
python3 scripts/research/exit_location_fidelity.py --ladder    # condition 1, live
```

Artifacts: `docs/research/mi307-offline-mfe-2026-09-18.json` (per-leg, both arms, full reach and
clamp-binding curves) and `docs/research/data/backtest-mfe-reference-2026-09-07.json` (extended).
`data/*.csv` is gitignored, so the candles are not committed — the fetch command above rebuilds them.
