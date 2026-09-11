# Would the pre-e35 2.5-ATR stop have been hit? — the stop-width counterfactual

> **Doc status:** `live` · category `research` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-275 · `WO-20260911-THE-STOP-WIDTH-COUNTERFACTUAL-WOULD-THE-PRE`
**Row:** `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` (third reading)
**Cycle priority:** `CY-20260906-TRADING-TRUTH`
**Predecessor, and the starting point rather than a thing redone:** [`bleed-attribution-2026-09-11.md`](bleed-attribution-2026-09-11.md) (MI-271, merged `85bcd5703`)
**Reproduce:** `python3 scripts/research/stop_width_counterfactual_2026_09_11.py --trades <trades.json> --packages <order_packages.json> --out <out.json>`

MI-271 recommended exactly one measurement before any revert decision, and recommended **not** reverting until it existed: *for each post-2026-08-30 stop-out on an e35 leg, would the pre-e35 2.5-ATR stop have been hit?*

---

## 0. The verdict, in five sentences

**Do not revert e35. The measurement MI-271 asked for has now been made, and it clears e35 on the losses.** Of the five post-deploy stop-outs that e35's own stop actually ended, **all five would have been stopped by the pre-e35 2.5-ATR stop as well** — and **not one of them reached its take-profit at any horizon out to the end of available data**, so there was no winner for the tighter stop to destroy. Run in reverse over the pre-e35 book, the tighter e35 stop would have killed **1 of 14 winning packages, worth $5.61 of $24,967.77 — 0.02% of pre-era winnings.** Because position size is strictly risk-based (`qty = balance × risk_pct / |entry − sl|`), **stop width changes neither the dollar risk nor the dollar reward of a trade** — only which of the two it hits — so "it would have stopped anyway" means "the same dollars, later".

**What did change is the market, measured in the unit the stop is denominated in and with the truncation confound removed:** at every fixed horizon from entry, adverse excursion is **1.4–1.8× larger** and favourable excursion **0.19–0.58×** smaller than before the split, collapsing the favourable:adverse ratio from 1.0–4.6 down to 0.33–0.54. Entries stopped working; the stops are not the problem.

---

## 1. Population — stated first

| | |
|---|---|
| **Source** | `/api/diag/journal?table=trades&limit=1000` + `…&table=order_packages&limit=1000`, pulled 2026-09-11T19:5xZ |
| **Trades window** | ids 4701–5701, `created_at` 2026-08-17T02:52Z → 2026-09-11T20:01Z |
| **Decision population** | MI-271's `population()`, **imported not restated**: `status=closed` AND NOT `is_backtest` AND `pnl IS NOT NULL` AND not the pairs sleeve → **298 rows** |
| **Then restricted to** | e35 legs carrying an **`atr_stop_mult`** change, split on `created_at` (OPEN time) against `2026-08-30T08:53:19Z` |
| **Final units** | **18 pre-era packages (28 rows) · 15 post-era packages (23 rows)** |
| **Excluded** | `ada_pullback_2h`, 4 packages — e35 changed its `tp_r` only, its stop was never touched, so it has no stop-width counterfactual |
| **Price basis** | OKX `*-USDT-SWAP` 1m history (a perpetual, matching Bybit linear) |

### ⚠️ The unit of observation is the ORDER PACKAGE, not the trade row

One signal fans out to several accounts, each writing its own `trades` row with the **same symbol, direction, entry and stop**. Those rows share **one price path**, so they are not independent observations of this question. Measured over the whole decision population:

| group | rows | packages | inflation |
|---|---|---|---|
| e35 | 55 | 37 | **1.49×** |
| untouched control (`ict_scalp_*`) | 173 | 136 | **1.27×** |
| B4 geometry | 18 | 11 | 1.64× |
| other | 52 | 36 | 1.44× |
| **all** | **298** | **220** | **1.35×** |

**The inflation is not uniform**, so it does not merely scale n — it inflates the two arms of a before/after comparison by *different* factors (e35 1.49× against the control's 1.27×), which makes a Fisher test between them anti-conservative and unequally so. Every rate in this memo is therefore per package, with the row count stated beside it.

**This does not overturn MI-271.** Its headline table recomputed per package:

| group / era | MI-271 (per row) | per package | 95% CI |
|---|---|---|---|
| e35 pre | 2/30 = 0.067 | **2/20 = 0.100** | [0.028, 0.301] |
| e35 post | 15/25 = 0.600 | **8/17 = 0.471** | [0.262, 0.690] |
| control pre | 47/87 = 0.540 | **36/67 = 0.537** | [0.419, 0.651] |
| control post | 55/84 = 0.655 | **44/67 = 0.657** | [0.537, 0.759] |

e35 stop rate pre vs post **p = 0.023** (was p < 1e-4 per row); control **p = 0.217**, unchanged as not significant; the PRE cross-section falsifier still **passes at p = 0.304**; POST cross-section **p = 0.018**. **Every conclusion survives; only the p-values weaken.** That is a robustness result for MI-271, not a correction of it.

---

## 2. The recorded telemetry cannot answer this, and the reason is structural

This unit was dispatched on the premise that `peak_r` / adverse excursion could answer the counterfactual **"directly from recorded telemetry rather than from a re-simulation"**, because MI-164 installed `record_position_telemetry` on these legs on 2026-09-07. That premise is **half wrong, and the wrong half is load-bearing.** Read off `src/runtime/position_telemetry.py`, not inferred:

- It records **`peak_r` — maximum FAVOURABLE excursion** (MFE), from bar extremes, stamped ESTIMATED. It ships `peak_r`, `open_r`, `giveback_r`, `r_to_stop`, `cap_r` and **nothing that retains how far adverse price travelled**.
- `record_position_telemetry` is an **UPSERT** — one row per open trade, overwritten every exit-loop pass — so only the **last state** survives. There is no path.
- The counterfactual needs the adverse path **beyond** the tightened stop, i.e. price action **after the position was already closed**. Observation stops at the close, so **telemetry could not answer this at 100% coverage either**.

MI-271 refused the MFE-at-stop join on an n argument (36% join, cells of 2–15, one empty). That refusal was right, and the deeper reason is that **the quantity needed was never the one recorded.** Filed.

---

## 3. Instrument work, done before any verdict

### 3.1 The price basis — and why it is OKX

`api.bybit.com` returns **HTTP 403** from the research container (a CloudFront *country* block) and `api.binance.com` **451** (restricted location). Those are the **venue** refusing on geography, **not** the sandbox proxy dropping the request — worth recording, because the documented expectation is that non-allowlisted hosts are dropped at the proxy. OKX, Coinbase and Kraken all answer; OKX is the basis because `*-USDT-SWAP` is the **same instrument class** as Bybit linear perps, and its 1m history resolves back past 2026-08-19.

### 3.2 Three positive controls, run before the arms and reported whether they pass or fail

| control | result |
|---|---|
| **PC1 — basis.** Does the journal's declared entry sit inside the OKX 1m bar at that minute? | **FIT.** 33 packages, **0 unfit**, 27 inside the bar, **median deviation 0.0 bp**, max 19.2 bp against a 25 bp tolerance. |
| **PC2 — reproduction.** Can the simulator reproduce the stop-out that actually happened? | **PASS.** 5 of 5 post-era entry-stop stop-outs reproduced, the simulated touch landing **0.1–3.3 minutes** from the recorded `closed_at`. |
| **PC3 — false-positive bound.** On pre-era trades that did not stop out, does their own 2.5-ATR stop read untouched? | **1 of 18 reads touched** — and it is **not** a false positive. See §3.4. |

### 3.3 The geometry basis had to be rebuilt, and PC2 is what caught it

The obvious reconstruction — recompute the stop as `entry_price ± atr × mult` from the trade row — is **wrong**, in two ways that a code reading would not have surfaced:

1. **The declared stop is anchored to the PACKAGE entry, not the fill.** `pkg-65f02cffa856451f` declares `entry 7.128` while its trade row records `entry_price 7.099` — the signal's level against the actual fill — and the bot computed the stop from 7.128. Recomputing off the trade row produced a stop **0.29 ATR too tight**, which PC2 surfaced as a **15-hour** reproduction error.
2. **The declared stop is not always exactly `atr × mult`** — some packages carry a clamped or rounded level.

So the simulator **no longer recomputes the stop.** It takes the entry-frozen declared stop from `order_packages.exit_plan.stop.price` and scales its *distance* by the multiplier ratio, preserving whatever anchor, clamp and rounding the bot actually used:

```
counterfactual_distance = declared_distance × (other_mult / this_era_mult)
```

`atr × mult` is retained only as a **check**, and it lands exactly:

| | `declared_dist / atr`, by leg |
|---|---|
| **pre** | `trend_donchian` 2.5 · `_ada_4h` 2.5 · `_avax_4h` 2.5 · `_eth_4h` 2.5 · `_sol_4h` 2.5 |
| **post** | `trend_donchian` 2.0 · `_ada_4h` 2.0 · `_avax_4h` **1.5** · `_eth_4h` 2.0 · `_sol_4h` **1.5** · `_xrp_4h` 2.0 |

**Every package lands on its own era's multiplier to three decimals.** That is three things at once, independently of any prose: the e35 ladder is what the commit says, **the deploy reached the running trader**, and the geometry basis is right. (`entry_frozen_mult_disagrees_with_era` is empty — 0 of 33.)

### 3.4 Two adjudication disagreements, and they are different facts

Exit location was adjudicated a second way — **from the price path** (was the declared level *touched* inside the trade's own life?) rather than from the exit price. Reported **beside** MI-271's adjudicator, never instead of it. Of 33 packages, **4 disagree**, and they split cleanly:

**Two are genuine MI-271 false negatives** — stop-outs graded `neither` because the fill came back outside its 15 bp band:

| trade | leg | declared stop | exit | exit vs stop | stop touched | closed |
|---|---|---|---|---|---|---|
| **4916** (pre) | `trend_donchian_avax_4h` | 7.45989 | 7.488 | **37.7 bp** | 05:10 | 05:13:58 |
| **5421** (post) | `trend_donchian_avax_4h` | 7.309 | 7.321 | **16.4 bp** | 12:31 | 12:34:03 |

Both are `reconciler_filled`, both closed ~3 minutes after their stop was touched. **These are stop-outs counted as `neither`.** One sits in each era, so on the headline they roughly offset — but the pre-era one **deflates the pre-period stop rate and so inflates the pre→post rise** that the e35 indictment rests on, which is the direction that matters. MI-271's 15 bp tolerance is too tight for Bybit reconciler fills.

**Two are a definitional difference, not an error.** `pkg-65f02cffa856451f` and `pkg-27b4c7d12e794bcc` are graded `reached_stop` by MI-271 and `neither_touched` here. Both are right: MI-271 compares the exit to `trades.stop_loss`, which is the **final, trailed** stop, while this compares against the **entry-frozen declared** stop. Those are different questions and both answers are correct. Which brings us to the thing that actually bounds the revert decision.

### 3.5 ⚠️ Two of the seven post-era stop-outs were ended by a TRAILED stop, not by e35's

Reverting `atr_stop_mult` changes the **entry** stop. A trade whose stop had already been moved **inside** that level by a trailing lever before it was hit would have exited at the same trailed level under either geometry, so **reverting e35 cannot recover it**. Counting such trades as evidence for a revert overstates the case for one.

| post-era stop-out packages | 7 |
|---|---|
| exited at the **entry-declared** stop → the counterfactual applies | **5** |
| exited at a stop **amended tighter** after entry → e35's width is not what ended it | **2** |

The two excluded: `pkg-65f02cffa856451f` (`trend_donchian_avax_4h`, declared 1.5 ATR, final stop **1.32 ATR** from entry) and `pkg-27b4c7d12e794bcc` (`trend_donchian`, declared 2.0 ATR, final stop **0.276 ATR** from entry — trades 5674/5675/5676). The second is dramatic: a 2.0-ATR bracket that a lever tightened to roughly a seventh of its declared width before it was hit.

---

## 4. The economic fact that frames everything: stop width moves no dollars

`src/units/accounts/risk.py::_size_unbounded` is the one sizer in the codebase:

```
risk_distance = |entry − sl|
raw_qty       = (balance × risk_pct) / (risk_distance × contract_value)
```

So the dollar risk at the stop is `balance × risk_pct`, **independent of stop width**. And because the target is set as `tp_r × risk_distance`, the dollar win is `risk_usdt × tp_r`, **also independent of stop width**. e35 tightened the stop and the sizer widened the position to match — 1.25× on the 2.0 legs, **1.67×** on the two taken to 1.5.

**Therefore `atr_stop_mult` has no first-order effect on the dollars won or lost per trade. Its only effect is which of the two outcomes the price path reaches first.** (Second-order effects are real but small: a larger position pays more fees and slippage, and can meet the margin cap or the venue `min_qty`.) This is why "would have stopped anyway" is a complete answer rather than a partial one — it means *the same dollars, later*.

---

## 5. ARM A — the forward counterfactual (what the row literally asks)

**Population: the 5 post-deploy packages whose trades were ended by their own entry-declared e35 stop.** For each, the pre-e35 2.5-ATR stop is raced against the declared take-profit from the moment of entry. A horizon is unavoidable here — price action after the real exit is needed — so four are reported rather than one invented cap.

| horizon | `wide_stop_also_hit` | `target_reached_first` | `neither_at_horizon` |
|---|---|---|---|
| 24 h | 2 | **0** | 3 |
| 72 h | 2 | **0** | 3 |
| 168 h | **5** | **0** | 0 |
| to end of data | **5** | **0** | 0 |

| leg | actual e35 exit | 2.5-ATR stop would hit | delay | MFE in pre-e35 R | target touched? |
|---|---|---|---|---|---|
| `trend_donchian_ada_4h` | 09-03 01:14:55 | 09-03 01:16 | **+0.02 h** | 0.942 R | never |
| `trend_donchian` (BTC) | 09-09 09:46:04 | 09-09 09:55 | **+0.15 h** | 0.359 R | never |
| `trend_donchian_eth_4h` | 09-11 08:30:35 | 09-11 12:37 | +4.11 h | 0.087 R | never |
| `trend_donchian_xrp_4h` | 08-31 18:18:14 | 09-03 13:43 | +67.41 h | 0.587 R | never |
| `trend_donchian_avax_4h` | 09-06 14:33:20 | 09-10 23:06 | +104.54 h | 1.974 R | never |

**Not one of the five reached its take-profit at any horizon**, and the best favourable excursion any of them managed was **1.974 R** of pre-e35 risk against a `tp_r` of 50 (or 3 on XRP). Four of five never cleared 1.0 R. **There was no winner for the tighter stop to destroy.** Two of the five would have stopped within *minutes* of when they actually did; the other three were deferred by 4 h to 4.4 days, and then stopped.

⚠️ **One bias, stated rather than corrected, and it runs AGAINST this conclusion.** Arm A does not simulate the strategy's own levers — the donchian channel exit, the trail, `trail_decay`, `stale_stop`, `giveback_stop`. A trailing stop is hit *sooner* than a fixed one, so ignoring the levers biases Arm A **toward** `target_reached_first`, i.e. toward finding e35 harmful. It found that outcome **zero times in twenty cells**. Correcting the bias could only strengthen the verdict.

---

## 6. ARM B — the reverse counterfactual, which needs no horizon at all

Pre-deploy trades carried the 2.5-ATR stop and ran to a **known** outcome. Ask of each: would the **tighter e35 stop** have been touched inside the trade's **own observed lifetime**? That window is bounded by the trade itself, so **Arm B assumes no horizon**, and its damage cell is directly decision-relevant — a pre-era winner the e35 stop would have killed is e35 converting a winner into a loser, on a trade whose real outcome is not in doubt.

| | |
|---|---|
| pre-era packages graded | **18 of 18** (0 ungradeable) |
| e35 stop would have been touched | **3** |
| e35 stop untouched | **15** |
| of the **14 winning** packages, killed by the e35 stop | **1** |
| rate | **7.1%**, 95% CI **[1.3%, 31.5%]** |
| sensitivity, widest fanned window | 4 of 18 |

**The damage, in dollars: $5.61 of $24,967.77 of pre-era winnings — 0.0225%.**

| the 3 crossings | leg | outcome | own stop | e35 stop | MAE | pnl |
|---|---|---|---|---|---|---|
| `pkg-df81fe9f9bd0474f` | `trend_donchian_eth_4h` | **win** | 2.5 ATR | 2.0 ATR | 2.218 ATR | **+$5.61** |
| `pkg-b705f4e472f3488d` | `trend_donchian_eth_4h` | loss | 2.5 ATR | 2.0 ATR | 2.276 ATR | −$370.05 |
| `pkg-5eb2f3a75d394be3` | `trend_donchian_avax_4h` | loss | 2.5 ATR | 1.5 ATR | 4.738 ATR | −$1,059.61 |

The two losses would have been lost either way — their MAE exceeds **both** stops.

**And the size of a winner is almost perfectly inversely related to how close it came to the e35 stop**, which is the pattern a stop-width indictment would have to break. Ranked by package total, the **top ten** winning packages — $187.56 and above, $24,842 between them — every one has **MAE ≤ 0.90 ATR** against e35 stops at 1.5–2.0 ATR:

| package total | MAE | e35 stop | leg |
|---|---|---|---|
| $6,000.94 | 0.66 ATR | 2.0 | `trend_donchian_eth_4h` |
| $4,290.78 | 0.58 ATR | 2.0 | `trend_donchian` |
| $3,481.12 | 0.90 ATR | 1.5 | `trend_donchian_avax_4h` |
| $3,205.20 | 0.50 ATR | 2.0 | `trend_donchian_eth_4h` |
| $3,107.91 | 0.86 ATR | 1.5 | `trend_donchian_sol_4h` |
| $1,405.82 | 0.59 ATR | 1.5 | `trend_donchian_sol_4h` |
| $1,312.03 | 0.68 ATR | 2.0 | `trend_donchian_ada_4h` |
| $999.27 | 0.44 ATR | 2.0 | `trend_donchian_ada_4h` |
| $729.22 | 0.85 ATR | 1.5 | `trend_donchian_avax_4h` |
| $187.56 | 0.23 ATR | 1.5 | `trend_donchian_sol_4h` |
| — | — | — | — |
| $119.94 | **1.93 ATR** | 2.0 | `trend_donchian_ada_4h` — survives by **0.07 ATR** |
| $15.17 | 0.22 ATR | 2.0 | `trend_donchian` |
| **$5.61** | **2.22 ATR** | 2.0 | `trend_donchian_eth_4h` — **the one e35 would have killed** |

**The only two winners that came near an e35 stop are the two smallest in the book**, and the single one it would have killed is the smallest of all fourteen. A real winner in this population never went more than 0.90 ATR against its entry before running.

⚠️ **Arm B's own bias also runs toward exonerating e35**, and is stated for the same reason: where a lever closed a pre-era trade early, the observed window is shorter than the trade would otherwise have run, which gives the e35 stop less opportunity to be touched. The bias favours the status quo — i.e. favours *not* reverting — so it is the direction a revert decision should be told about.

---

## 7. The dose-response test inverts — and its decisive arm is empty

If stop width were the mechanism, the **dose must track the damage**: the legs tightened to 1.5 (a 40% cut) must degrade **more** than those tightened to 2.0 (a 20% cut), and the leg e35 **widened** to 3.0 must not degrade at all.

| dose | era | rows | pkgs | win rate | stop rate (gradeable) | Fisher p | PnL |
|---|---|---|---|---|---|---|---|
| **→1.5** (ratio 0.60) | pre | 7 | 7 | 0.857 | 0.000 (0/7) | — | +$7,959 |
| | post | 8 | 8 | 0.125 | **0.250** (2/8) | **0.467** | −$3,311 |
| **→2.0** (ratio 0.80) | pre | 21 | 11 | 0.667 | 0.000 (0/21) | — | +$13,782 |
| | post | 15 | 7 | 0.000 | **0.800** (12/15) | **< 0.0001** | −$6,710 |
| **→3.0 (WIDER)** | pre / post | **0** | **0** | — | — | — | — |
| `tp_r` only, stop untouched | pre | 2 | 2 | 0.000 | 1.000 (2/2) | 1.000 | −$1,820 |
| | post | 2 | 2 | 0.000 | 0.500 (1/2) | | −$634 |

**The smaller tightening produced the larger stop-rate rise** — 0.80 at a 20% cut against 0.25 at a 40% cut. That is the opposite of a dose response, and it is the arithmetic a stop-width mechanism has to explain.

⚠️ **Three caveats, because this test is weaker than the arms and must not be quoted as if it were not.**

1. **The groups are confounded by symbol and timeframe**, not randomised. →1.5 is SOL-4h + AVAX-4h; →2.0 is BTC (1h), ETH-4h, ADA-4h, XRP-4h. A multiplier is not a comparable dose across timeframes — the same `atr_stop_mult` is a completely different price distance, measured here as a median stop distance of **92 bp** on `trend_donchian` (BTC, 1h) against **386 bp** on `trend_donchian_ada_4h`.
2. **The within-symbol version is unavailable.** AVAX appears in both dose groups (`trend_donchian_avax_4h` →1.5 and `avax_pullback_2h` →2.0), which would have removed the confound — but `avax_pullback_2h` has **0 rows in the decision population**: all **46** of its rows in the window are `rejected` with a NULL `pnl` — it never opened a position here.
3. ⚠️ **The single most decisive arm is EMPTY, and that is a *could not look*, not a negative.** `htf_pullback_trend_2h` is the leg e35 **widened** to 3.0, and it has **8 rows in the window, all 8 `rejected`** — it has never opened a position here. `trend_donchian_eth_prop`, the within-family control deliberately HELD at 2.5, has **0 rows**. **Both of the e35 experiment's designed controls have no observations.** Filed.

---

## 8. What actually changed: the excursion regime, with the truncation confound removed

The question a counterfactual cannot answer is *what changed*. This one can, and it needs no alternative geometry: **measure how far price moves for and against an entry, in ATR units** — the unit the stop is denominated in, so the measurement is **invariant to the multiplier change under test**.

Measured over each trade's own lifetime:

| | pre (18 pkgs) | post (15 pkgs) | ratio |
|---|---|---|---|
| max **adverse** excursion, median | 0.766 ATR | 1.586 ATR | **2.07×** |
| max **favourable** excursion, median | 3.879 ATR | 0.656 ATR | **0.169×** |
| favourable : adverse | 5.065 | 0.414 | **0.082×** |

⚠️ **That measurement cannot be trusted on its own, and the confound would manufacture exactly the finding being claimed.** Post-era trades were stopped out *sooner* because their stops were tighter; a shorter observation window mechanically lowers MFE and caps MAE. So the table above is reported **with its control**: the same quantities over a **fixed number of hours from entry**, a window that does not depend on the geometry, on when the trade closed, or on which lever closed it.

| window | pre n | pre MAE | pre MFE | pre F:A | post n | post MAE | post MFE | post F:A | MAE ratio | MFE ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 h | 18 | 0.671 | 0.696 | 1.037 | 15 | 0.994 | 0.404 | 0.406 | **1.48×** | **0.58×** |
| 12 h | 18 | 0.881 | 1.548 | 1.756 | 14 | 1.201 | 0.393 | 0.327 | **1.36×** | **0.25×** |
| 24 h | 18 | 0.881 | 2.617 | 2.969 | 14 | 1.607 | 0.665 | 0.414 | **1.82×** | **0.25×** |
| 48 h | 18 | 1.459 | 6.690 | 4.585 | 11 | 2.367 | 1.283 | 0.542 | **1.62×** | **0.19×** |

**The finding survives the control at every horizon.** The lifetime figures (2.07× / 0.169×) overstate it somewhat, as expected; the truncation-free version is **1.4–1.8× more adverse travel and 0.19–0.58× of the favourable travel**, with the favourable:adverse ratio falling from 1.0–4.6 to 0.33–0.54. At 48 hours the median pre-era trade had run **6.69 ATR** in its favour; the median post-era trade manages **1.28 ATR**.

**That is a statement about entries, not exits.** It is also the geometry-independent confirmation of MI-271's winner-size collapse (avg win −72%), and it reaches legs across the book rather than only the ones e35 touched.

⚠️ **And it is why widening the stops back does not fix the book.** At 48 h the median post-era trade is **2.367 ATR** underwater. A 2.5-ATR stop survives that median by **0.13 ATR** — while its favourable excursion of 1.28 ATR cannot reach any `tp_r` target. Reverting buys a coin-flip against the median trade and still never reaches the target.

⚠️ **n is small on both sides (18 and 15 packages, 11 at the 48 h window, and the 48 h cell excludes 4 post-era packages whose window runs past the data end). Read the n before the ratio.**

### 8.1 A claim I nearly published and retract here

Reading the per-leg stop distances in bp of entry, `ada_pullback_2h` — whose multiplier e35 **never changed** (1.5 in both eras) — shows its stop distance **halving**, 718 bp → 307 bp. That looks like a volatility collapse driving every stop tighter in price terms regardless of e35, and it would have been a tidy mechanism.

**It does not hold.** Measured over every `order_packages` row in the window declaring an `atr` (any leg, any status, n = 21–137 per cell), ATR as a fraction of entry is **roughly flat** across the split: 5m **0.93×**, 15m **0.95×**, 1h **1.14×**, 2h **0.92×**, 4h **0.75×** (1d reads 1.67× on n = 12). The per-leg contraction came from cells of **2–5 packages**. **Volatility did not collapse; the ratio of favourable to adverse travel did.** Same vol, smaller winners, deeper adverse excursions — that is chop, not a vol regime change, and it is why §8 is measured in ATR units rather than in bp.

---

## 9. The MI-271 contradiction — resolved, and the merged memo is right

MI-271's merged memo concludes **"e35 is NOT the cause"**; its session summary reads **"e35 causation confirmed via two independent data sources"**. Both halves sit in **one** `post_turn_summary` object and disagree *with each other*:

- `status_detail`: *"audit complete: e35 shipped 08-30, loss onset 08-27; cross-check strong"* — **agrees with the merged memo.**
- `recent_action`: *"e35 **causation** confirmed via two independent data sources"* — the overstatement.

And the board records in terms what those two sources cross-checked. MI-271's own 2026-09-11T19:49:27Z comment: *"two lanes, two independent data sources, **same dated onset**"* — `/api/pnl/history` against the `trades` journal, agreeing the bleed starts **2026-08-27**. That finding **exonerates** e35 on timing; it is not a causation finding. **The word that belongs in `recent_action` is "onset", not "causation".**

**No correction to `main` is owed** — the memo never made the claim, and `docs/DOCUMENT-INDEX.md`'s entry for it is also correct. The contradiction lives only in a session-summary field. **And this unit did not settle it by reading:** Arms A and B, the dose-response and the excursion regime were all measured independently, and all four agree with the merged memo.

---

## 10. Remedy — Tier-3 items are PROPOSALS; nothing here is enacted

### 10.1 PROPOSAL A (recommended): do **not** revert e35. Record the decision and close the question.

MI-271 recommended waiting for this measurement; it now exists, and it does not support a revert:

- **Forward:** 5 of 5 post-era e35 stop-outs would have stopped under 2.5 ATR as well, **0 of 5** would have reached a target at any horizon.
- **Reverse:** the tighter stop would have cost **$5.61 of $24,967.77** of pre-era winnings — 1 winner of 14.
- **Dose-response inverts**, and its decisive arm has no observations.
- **Sizing makes the dollars invariant** to stop width; only the hit order changes.
- Every e35 cell carried a passing walk-forward with positive `d_net_r` (+1.0 to +28.7) — read off commit `892c9a2c8`, which is the evidence a revert would be discarding.

**The change I would make to `config/strategies.yaml`: none.** MI-271's PROPOSAL B (revert `atr_stop_mult` to 2.5 on `trend_donchian_sol_4h` and `trend_donchian_avax_4h`) should be **declined on this evidence** — and note `trend_donchian_sol_4h` recorded **0 stop-outs in 3 post-era packages**, so it is not even a member of the population that motivated it.

⚠️ **This is a decision to RECORD, not an absence of one.** "Do not revert" left unwritten reads as a question still open, and the next session re-opens it at the same n.

### 10.2 What the evidence points at instead — and it is NOT an exit change

The measured defect is **entry quality under the current regime**: favourable:adverse travel down 2–9× at fixed horizons, across legs e35 never touched. That is the `regime-selectivity` and entry-side question, not the exit-side one. I am **not** proposing a cell or a param here — authoring a `trend_vol` OFF cell needs its own walk-forward (`regime-selectivity` § no-cosmetic-cell), and doing it off n = 15 packages would be the cosmetic-cell anti-pattern this repo already has a backlog row for.

### 10.3 The instrument that would have caught this — flagged, deliberately NOT built here

**MFE/MAE in ATR units, per leg, per week** is a leading indicator of exactly this failure and is computable from data the system already has. It fell from 5.07 to 0.41 across the split while every PnL-based surface was still arguing about attribution.

⚠️ **I am not building it**, for two reasons: **MI-276** (`session_01MyUujGAQYs72UUgrfXXmFA`) owns the detector clause of this row (`WO-20260911-NOTHING-ALARMED-ON-EITHER-FAILURE-THE-SUSTAINED`), and a second detector landing in the same window is how two cadences come to disagree. Filed and flagged to that lane instead. **It is a complement, not a duplicate:** a losing-streak detector says *that* the book is bleeding; this says *why* — and it needs no provenance-clean PnL, which is what made the streak question hard.

### 10.4 What NOT to do

- **Do not revert e35 on the stop-rate rise alone.** The rise is real and provenance-clean, and this memo does not dispute it — but a tighter stop is *supposed* to stop out more often, and on this population it cost $5.61 and deferred nothing that was going to be a winner.
- **Do not widen stops anywhere to "stop getting stopped out".** At 48 h the median post-era trade is 2.37 ATR underwater with 1.28 ATR of favourable travel. A wider stop makes the loss arrive later at the same dollar size and still never reaches the target.
- **Do not read the two trailed stop-outs as e35 stop-outs.** Reverting `atr_stop_mult` would not have changed either; one was ended at **0.276 ATR** from entry against a declared 2.0.
- **Do not quote a rate from this population per trade ROW.** It inflates n by 1.27–1.64× and unequally between the arms.

---

## 11. What this establishes, and what it explicitly refuses

**Established:**
- **Every one of the 5 post-deploy stop-outs that e35's own stop ended would have been stopped by the pre-e35 2.5-ATR stop**, within 7 days — 2 of them within 9 minutes — and **none would have reached its take-profit at any horizon**.
- The tighter e35 stop would have killed **1 of 14 pre-era winning packages, worth $5.61 of $24,967.77 (0.02%)**.
- **Stop width moves no dollars**: sizing is `balance × risk_pct / |entry − sl|` and the target is `tp_r × risk_distance`, so both the dollar risk and the dollar reward are invariant to the multiplier.
- **The market changed, with the truncation confound removed**: adverse travel **1.4–1.8×**, favourable travel **0.19–0.58×**, favourable:adverse from 1.0–4.6 to 0.33–0.54, at every fixed horizon from entry.
- **ATR/price did NOT collapse** (0.75–1.14× on the timeframes with real n) — so this is chop at unchanged volatility, not a vol regime change.
- The **deploy reached the trader**: all 33 packages' entry-frozen stop distances land exactly on their own era's multiplier.
- **2 of 7 post-era stop-outs were ended by a trailed stop**, not by e35's width.
- MI-271's conclusions **survive** recomputation at package level; only its p-values weaken (e35 stop rate p = 0.023).
- The MI-271 contradiction is a **mis-compression in one summary field**; the merged memo is right.

**NOT established, and explicitly refused:**
- **That e35 is harmless in general.** This is **n = 5 forward and 18 reverse packages** over 12 days on two symbols' worth of dose. It establishes that e35 did not cause *these* losses, not that the geometry is right.
- **A dose-response.** The test inverts, but its groups are confounded by symbol and timeframe and its decisive arm (the WIDENED leg) has **zero** observations. It is corroboration, not evidence on its own.
- **What the counterfactual trade's PnL would have been.** The strategy's own levers cannot be re-simulated here, and at least one of them demonstrably rewrote a live stop in this very population. Only the touch question is answered.
- **Why `ict_scalp_*` degraded.** It carries two-thirds of the loss, declares no bracket geometry, and is out of this unit's scope. §8's excursion collapse is measured on **e35 legs only** — whether it holds on the scalps is unmeasured and is the obvious next unit.
- **Anything about real money at size.** Real-money e35 post-era exposure is **5 rows on `bybit_2` totalling −$14.06**.
- **A 90-day view.** The journal tail reaches 26 days.

---

## 12. Rows filed

| id | register |
|---|---|
| `BL-20260911-THE-STOP-WIDTH-COUNTERFACTUAL-NEEDS-THE-ADVERSE-PATH-AND-TELEMETRY-RECORDS-ONLY-THE-FAVOURABLE-ONE` | health |
| `BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS` | performance |
| `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY` | performance |
| `BL-20260911-EXIT-ADJUDICATION-AT-15BP-MISSES-STOP-OUTS-WHOSE-RECONCILER-FILL-LANDS-OUTSIDE-THE-BAND` | health |
| `BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT` | performance |
| `BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES` | health |
