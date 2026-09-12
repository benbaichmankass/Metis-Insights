# Why did the average win fall? — decomposing the winner-size collapse

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-277 · `WO-20260912-DECOMPOSE-THE-WINNER-SIZE-COLLAPSE-AND-GRADE`
**Cycle priority:** `CY-20260906-TRADING-TRUTH`
**Predecessors, built on rather than redone:** [`bleed-attribution-2026-09-11.md`](bleed-attribution-2026-09-11.md) (MI-271, merged `85bcd5703`) · [`stop-width-counterfactual-2026-09-11.md`](stop-width-counterfactual-2026-09-11.md) (MI-275, merged `bf92d258c`)
**Reproduce:** `python3 scripts/research/winner_size_collapse_2026_09_12.py --trades <trades.json> --packages <order_packages.json> --out <out.json>`

MI-271 measured that `bybit_1`'s average win fell **$404 → $112** while its win rate moved only 4.9pp, and concluded the expectancy flip was *"driven overwhelmingly by winners getting smaller, not by winning less often"*. It did not establish why. That is this unit's question.

---

## 0. The verdict, in six sentences

**The collapse is real, it is an R collapse and not a sizing collapse, and every candidate mechanism inside the system tested negative.** Position size did not fall — measured over the whole book it *rose* (median risk $303.60 → $438.10); the losing side is unchanged at ≈1R in both eras, which is the control that isolates the winner side; the venue take-profit clamp truncated **16.7% of pre-era winners and 0.0% of post-era winners**, so it cannot be the ceiling; no exit lever became more aggressive (zero packages in either era declare a trailing stop or a ladder rung); and leg composition explains essentially nothing — holding the leg mix fixed moves the number by ~7%, while the legs themselves moved by ~75%, with **all 7 legs that won in both eras falling (exact two-sided p = 0.0156)**.

**But two things MI-271 asserted do not survive its own population definition, and one of them inverts.** Its §4.3 decomposition table is computed **with the pairs sleeve included** — the only table in that memo that is — and on the pairs-**excluded** population it uses everywhere else, the win rate falls **19.5pp (53.2% → 33.7%)**, not 4.9pp, and the average loss **improves** 36% rather than worsening 20%. *"It is not a win-rate collapse"* is an artifact of the filter.

⚠️ **And the magnitude of the winner collapse is NOT established.** On broker-**measured** rows it reads **−38.5%**, not −72%; the pre-era measured winner cell is **n = 15** pooled across all three Bybit accounts; and the single largest concentration of the collapse sits in `netting_attributed` — **100% ESTIMATED in both eras** and the exact path `OI-20260908` establishes can close a live position at a manufactured price.

---

## 1. Population — stated first, because the headline changes its qualitative conclusion on it

| | |
|---|---|
| **Source** | `/api/diag/journal?table=trades&limit=1000` + `…&table=order_packages&limit=1000`, pulled **2026-09-12T01:0xZ** direct over `https://ict-bot.duckdns.org` |
| **Trades window** | ids **4709–5708**, `created_at` 2026-08-17T13:36Z → 2026-09-12T01:04Z |
| **Packages window** | 1000 rows, `created_at` 2026-07-16T06:03Z → 2026-09-12T01:04Z |
| **Decision population** | MI-271's `population()`, **imported not restated**: `status=closed` AND NOT `is_backtest` AND `pnl IS NOT NULL` AND not the pairs sleeve → **294 rows** |
| **Primary account** | `bybit_1` — **79 pre · 101 post** (the account MI-271's headline is about, and the largest n) |
| **Split** | `created_at` (OPEN time) against **2026-08-30T08:53:19Z**, imported from MI-271 |
| **Package join** | **180 of 180** on `bybit_1` — 100%, zero `pkg_not_in_window` |

### ⚠️ 1.1 THE FILTER IS THE FIRST FINDING, AND IT CHANGES THE CONCLUSION

MI-271's §4.3 table says *"bybit_1, all closed rows, pre vs post open"*. Reproduced on both filters — **the pairs sleeve is 266 of the 446 rows**, and carries −$28.91 of PnL between them:

| population | era | n | win rate | avg win | avg loss | expectancy |
|---|---|--:|--:|--:|--:|--:|
| **pairs INCLUDED** (MI-271 §4.3) | pre | 231 | 0.455 | $411.92 | −$268.29 | +$40.90 |
| | post | 215 | **0.409** | $119.20 | −$311.54 | −$135.24 |
| **pairs EXCLUDED** (every other MI-271 section) | pre | 79 | 0.532 | $1,020.79 | −$901.57 | +$120.45 |
| | post | 101 | **0.337** | $283.08 | −$578.20 | −$288.27 |

The pairs-included row reproduces MI-271 to within the window drift (it pulled ids 4701–5700 at 17:51Z on 09-11; this pull is ~7h later and 8 ids along), so the population is positively identified rather than guessed at.

**What inverts:**

| MI-271 §4.3 claim | on the pairs-EXCLUDED population |
|---|---|
| *"Win rate fell only 4.9pp"* | fell **19.5pp** (53.2% → 33.7%), a 36.7% relative fall |
| *"Average loss worsened 20%"* | **improved 35.9%** (−$901.57 → −$578.20) |
| *"driven overwhelmingly by winners getting smaller, not by winning less often"* | **both terms are comparably large** (below) |

Expectancy shift-share, each term substituted alone with the interaction residual named rather than distributed by convention:

| term | pairs INCLUDED | pairs EXCLUDED |
|---|--:|--:|
| total Δexpectancy | −$176.14 | −$408.71 |
| win-rate term | −$30.77 (**17.5%**) | −$374.88 (**91.7%**) |
| avg-win term | −$133.05 (**75.5%**) | −$392.20 (**96.0%**) |
| avg-loss term | −$23.59 (13.4%) | **+$151.45** (−37.1%) |
| interaction residual | +$11.29 (−6.4%) | +$206.92 (−50.6%) |

⚠️ **The large interaction on the right is not noise to be explained away — it is the finding.** Sequential substitution cannot cleanly separate two terms that both moved a long way, and that is precisely what distinguishes the two populations: on the pairs-included view one term dominates, on the pairs-excluded view both do.

**Why the percentage survived while the levels did not.** The 266 pairs rows are near-breakeven and roughly half-winning, so they dilute both eras at similar rates: the *ratio* $411.92/$119.20 = 0.289 is close to $1,020.79/$283.08 = 0.277. MI-271's **−72%** is therefore robust; its **$404 → $112** and its win-rate and avg-loss statements are not. This is the class `CLAUDE-RULES-CANONICAL` § "Always state the population" exists for, and it is filed rather than merely noted.

### 1.2 The measurement basis, and a defect that sits in a denominator

`src/units/accounts/risk.py::_size_unbounded` is the one sizer: `qty = (balance × risk_pct) / (|entry − sl| × contract_value)`. So every closed trade satisfies exactly

```
pnl = risk_usd × R        where risk_usd = |entry − sl| × qty × contract_value ,  R := pnl / risk_usd
```

⚠️ **`trades.stop_loss` IS THE WRONG FIELD FOR THIS AND USING IT BREAKS THE ARITHMETIC.** It is the **final, trailed** stop — levers amend it in place. MI-275 §3.4 established the same field distinction for exit *adjudication*; here the consequence is worse, because the quantity sits in a **denominator**. Measured: trade **5027** (`ict_scalp_sol_15m`) reads `|entry − stop_loss| = 0.000885` on SOL, giving risk **$0.88** and **R = 3,672** on a $3,225 win — one row that alone moves the pre-era mean R from 7.3 to **102.6**.

The basis is therefore `order_packages.sl` / `.entry`, which are entry-frozen and which MI-275 §3.3 proved land on each leg's declared ATR multiplier to three decimals. **`contract_value = 1` is asserted, not assumed**: re-deriving pnl from `(exit − entry) × size × direction` gives a median ratio of **1.0000 on `bybit_1`** (n=179), 1.0000 on `bybit_portfolio` and `alpaca_*`, 0.9707 on `bybit_2` (fees) and **0.10 on `ib_paper`** — the futures multiplier, correctly showing up as a systematic residual on the one venue where the premise fails. `ib_paper` is not in any headline here.

---

## 2. Phase 1 — is the collapse MEASURED?

**Coverage tripled across the split**, which is the confound: `bybit_1` rows read **13/79 = 16.5% measured** pre and **50/101 = 49.5%** post. Classification is `provenance.classify_pnl` (worst recognised bucket across `pnl_source` + `exit_price_source`), imported. **Positive control, run before the probe was used for anything:** over all 1000 pulled rows it returns `measured 131 / estimated 371 / unverified 498` across eight distinct raw `exit_price_source` values — so the probe demonstrably finds every bucket it reports, and a quiet cell is a real negative. `fabricated` is **0** in this window; `local_markprice` does not appear.

### 2.1 The coverage shift made the headline CONSERVATIVE, not inflated

Winners standardised to the other era's provenance mix (a standardisation, not a correction — it assumes only that a bucket's observed mean estimates that bucket):

| | n | avg win |
|---|--:|--:|
| pre winners, measured | 5 | $1,292.05 |
| pre winners, estimated | 37 | $984.14 |
| post winners, measured | 16 | $423.18 |
| post winners, estimated | 18 | $158.54 |
| **actual** | | **$1,020.79 → $283.08 (−72.3%)** |
| **post at the PRE provenance mix** | | **$190.05 → −81.4%** |

Measured rows show **larger** wins than estimated rows in **both** eras, and the post era has proportionally more of them. So the coverage shift pushed the post average **up**. Correcting for it makes the collapse **worse**. The artifact hypothesis in its simple form — *"the −72% is manufactured by the coverage shift"* — is **refuted, with the direction stated.**

### 2.2 ⚠️ But the MAGNITUDE is not established, and this is the honest limit

Pooling all three Bybit accounts to thicken the thin pre-era measured cell:

| measured-only winners, pooled `bybit_*` | n | median R | mean R | avg win |
|---|--:|--:|--:|--:|
| pre | **15** | 4.512 | 5.820 | **$687.70** |
| post | 16 | 2.426 | **6.502** | **$423.18** |

**−38.5%, roughly half the headline — and the mean R rises.** Worse for a clean read, the two cells are not like-for-like: the pre cell is `{tp 5, reconciler_filled 5, tp_cross 2, sl 2, exit_head 1}` and the post cell `{tp 5, tp_cross 4, reconciler_filled 3, sl_cross 2, sl 1, exit_head 1}`. Restricted to the least-defective exit cell — trades that actually reached a declared target, on measured prices — the movement **reverses**:

| measured winners, `tp`/`tp_cross` only | n | median R |
|---|--:|--:|
| pre | 7 | 4.835 |
| post | 9 | **9.841** |

**On broker-measured trades that reached their target, achieved R nearly doubled.** That is not the shape of "winners got smaller".

### 2.3 Where the collapse actually lives, and it is a known-defective path

Winners by exit reason, median R:

| exit reason | pre n | pre med R | post n | post med R |
|---|--:|--:|--:|--:|
| `reconciler_filled` | 17 | 3.872 | 11 | **0.687** |
| `netting_attributed` | 11 | 3.143 | 6 | **0.656** |
| `tp` | 8 | 4.655 | 6 | **9.917** |
| `tp_cross` | 1 | 11.786 | 4 | 9.500 |
| `sl` | 4 | 1.248 | 3 | 1.198 |

Split further by provenance, `reconciler_filled` reads **measured 4.824 (n=2) → 3.353 (n=3)** against **estimated 3.872 (n=15) → 0.417 (n=8)**. The collapse is concentrated in the estimated rows of the two reconciler-family labels.

⚠️ **`netting_attributed` is 100% ESTIMATED in both eras** — 22/22 pre, 17/17 post, 39 `bybit_1` rows carrying −$914.61 then −$4,769.54. That is the population of `OI-20260908-A-HEDGE-BOOK-FLAT-READ-IS-CLOSING-LIVE-BYBIT-POSITIONS-AND-THE-FLAP-GUARD-CANNOT-SEE-IT`, a live unfixed defect in which a zero-size hedge-book sibling makes `_bybit_position_protection` return `_flat` for a symbol that is not flat and the reconciler closes a live position with an estimated pnl. **This memo does not establish that any of these 39 rows is a false close** — that needs the venue-side read `OI-20260908` specifies. What it establishes is that the path carrying the largest single share of the "winner-size collapse" is the path that row says cannot be trusted, and it is added to that row as evidence.

**Phase 1 verdict.** The collapse is **not** an artifact of the coverage shift — that hypothesis is refuted and its direction is the opposite of what it needed to be. It is **not fully measured either**: at n=15 pre-era measured winners the magnitude spans −38.5% to −81.4% depending on the population, and the cleanest sub-cell moves the *other way*. **What IS measured without touching an exit price at all is §3.4**, and that is what carries the verdict.

---

## 3. Phase 2 — the decomposition

### 3.1 The identity, and it closes exactly

`mean(pnl | win) = mean(risk_usd) × mean(R) + cov(risk_usd, R)`, on `bybit_1`, entry-frozen basis, **residual asserted zero inside the transform**:

| | pre (42 wins) | post (34 wins) |
|---|--:|--:|
| mean risk_usd | $264.59 | $244.81 |
| mean R | 7.3214 | 4.6063 |
| cov(risk, R) | −916.39 | −844.60 |
| **= mean win** | **$1,020.79** | **$283.08** |
| median R | 3.2627 | 1.3673 |

| factor | Δ$ | share of the −$737.71 |
|---|--:|--:|
| **R effect** (`risk_pre × ΔR`) | **−718.38** | **97.4%** |
| size effect (`Δrisk × R_pre`) | −144.84 | 19.6% |
| interaction | +53.71 | −7.3% |
| covariance | +71.79 | −9.7% |
| residual | −0.00 | asserted zero |

### 3.2 ⚠️ SIZE DID NOT FALL — and a winners-only read says it did

The 19.6% size effect above is computed **on winners**, and that is a selection of the trades that happened to win. Over the whole book:

| | median risk_usd | mean risk_usd |
|---|--:|--:|
| pre, **all 79 rows** | $303.60 | $627.90 |
| post, **all 101 rows** | **$438.10** | $586.10 |
| pre, winners only (42) | $167.60 | $264.60 |
| post, winners only (34) | **$84.30** | $244.80 |

**Median risk per trade ROSE 44% over the full book.** Per leg it rose on 4 of the 5 `ict_scalp_*` legs present in both eras (`eth_15m` 1.60×, `sol_15m` 1.25×, `sol_5m` 1.18×, `avax_5m` 1.04×). The winners-only halving is the post-era winners being the small-risk trades, not sizing shrinking. **I reached the wrong reading from the winners-only figure first and it was the full-book check that corrected it** — recorded because the winners-only view is the one the decomposition identity naturally produces.

So: **`conviction_sizing` (live `apply`/`reductive` on `bybit_1` since 2026-08-05), the `alpaca_live` cash-settlement gate, a balance fall, and a margin clamp are all REFUTED as the mechanism** — none of them can raise median risk.

### 3.3 The venue cap is refuted, and by the cleanest margin here

`cap_r = TP_VENUE_CAP_PCT × entry / risk_distance`, `TP_VENUE_CAP_PCT = 0.099` imported from its one owner:

| winners | median `cap_r` | median declared `tp_r` | median achieved R / `cap_r` | **within 10% of the cap** |
|---|--:|--:|--:|--:|
| pre (42) | 66.00 | 8.10 | 0.1428 | **16.7%** |
| post (34) | 66.00 | 5.17 | 0.0434 | **0.0%** |

The ceiling did not move, and in the post era **not one winner came near it**. Winners are ending at a twenty-third of a ceiling that truncated one pre-era winner in six. A clamp nothing touches cannot be what stopped winners running. (ML-2's refutation — `docs/research/ml2-predictive-bracket-2026-09-06.md` §4, whose risk-scaled sharpness result is the one to cite, not the pooled one — says the clamp destroys `tp_r`'s *scaling*; that remains true and is a different claim from it *binding* here.)

### 3.4 The loser side is unchanged — the control that carries the verdict

| `bybit_1` | n | median R | mean R | median hold (h) |
|---|--:|--:|--:|--:|
| pre losers | 37 | **−0.891** | −0.826 | 3.05 |
| post losers | 67 | **−0.939** | −0.823 | 2.85 |
| pre winners | 42 | 3.263 | 7.321 | **4.99** |
| post winners | 34 | **1.367** | 4.606 | **1.90** |

**Losses still realise ≈1R.** That single fact does more work than anything else in this memo: it says the sizer is intact, the stops are intact, and the risk denominator is trustworthy — because if any of the three had moved, loss R would have moved with it. Only the winner side changed.

Median payoff ratio (median win R / median loss R): **3.663 → 1.455, −60%.**

**And §3.4's decisive half involves no exit price at all.** Pooled across the three Bybit accounts:

| | pre | post |
|---|--:|--:|
| n | 121 | 133 |
| **win rate** | **0.5372** | **0.2556** |
| median hold, winners | **5.17 h** | **1.90 h** |
| median hold, losers | 2.65 h | 3.12 h |

Hold time is `closed_at − created_at` — **no price, no provenance, no venue**. Winners are closing in **37% of the time** they used to while losers take **18% longer**. That cannot be manufactured by `candle_at_close`, by `netting_attributed`, or by any exit-booking defect, and it is the same shape MI-275 measured on the e35 legs by a completely independent route (truncation-free excursion: adverse 1.4–1.8× larger, favourable 0.19–0.58×).

The same picture on `ict_scalp_*` alone — the geometry-immune legs carrying two-thirds of the loss, which no e35 or B4 change could have touched: win rate 0.517 → 0.397, winner median R 3.210 → **1.446**, loser median R −0.971 → **−0.964** (unchanged), median risk **UP** $264.40 → $311.70.

### 3.5 Exit levers are refuted

The competing *system* explanation for winners exiting sooner is that a trailing / `stale_stop` / `giveback_stop` lever became more aggressive. Measured on `bybit_1`: **zero** packages in either era declare a `trailing_stop` or any ladder rung (0/42 pre, 0/34 post), and `stale_stop` and `giveback_stop` appear **zero times** as an exit reason in either era. ⚠️ This is a statement about *this account in this window* and is **not** a general claim — MI-188b measured those levers closing 19 real trades elsewhere, and its warning that `exit_lever_soak.jsonl` reads **backwards** (its `mode` is a hardcoded literal) is why the soak was not used as evidence here.

### 3.6 Composition is refuted — the legs moved, the mix did not

Overall avg win = Σ (leg share of wins) × (leg avg win). Counterfactuals over the **7 legs winning in both eras** (legs in only one era are named, not dropped: pre-only `ict_scalp_5m`, `trend_donchian`, `trend_donchian_{ada,eth,sol}_4h`; post-only `sol_pullback_2h`, `trend_donchian_eth`, `trend_donchian_sol`, `xrp_pullback_2h`):

| | avg win |
|---|--:|
| pre actual (common legs) | $1,178.20 |
| post actual (common legs) | $290.28 |
| **post legs at the PRE mix** | **$311.53** |
| **pre legs at the POST mix** | **$1,168.51** |

**Holding the mix fixed reproduces the collapse almost exactly** ($311.53 vs the actual $290.28); holding the legs fixed reproduces the pre-era level almost exactly ($1,168.51 vs $1,178.20). The mix moves the number by ~1–7%; the legs move it by ~75%.

And the legs moved **unanimously** — all 7 fell, exact two-sided binomial **p = 0.0156**:

| leg | pre avg win | post avg win | |
|---|--:|--:|--:|
| `ict_scalp_sol_15m` | $2,206.21 | $56.44 | −97% |
| `ict_scalp_xrp_15m` | $260.44 | $12.89 | −95% |
| `ict_scalp_eth_15m` | $1,690.46 | $82.50 | −95% |
| `trend_donchian_avax_4h` | $2,105.17 | $384.55 | −82% |
| `ict_scalp_xrp_5m` | $1,687.65 | $601.38 | −64% |
| `ict_scalp_sol_5m` | $934.92 | $498.58 | −47% |
| `ict_scalp_avax_5m` | $413.14 | $223.82 | −46% |

### 3.7 Regime — and volatility rose rather than collapsed

`ict_scalp_*` stops are ATR-derived and their multipliers were never changed, so stop distance in bp of entry is a proxy for realised volatility at signal time. Pooled over legs with n≥3 in both eras: **61.9 bp → 79.6 bp, +29%**; per leg it rose on 7 of 9. The declared target, measured as an actual multiple of risk on the wire, also moved **further out**: median 1.77 R → 2.80 R.

So: volatility **up**, targets **further** in R, risk dollars **flat to up**, stops **wider** in price — and winners achieve **half** the R in **37%** of the time. That is not a vol collapse and it is not a geometry change. It is more movement with less follow-through, which corroborates MI-275 §8.1 (*"volatility did not collapse; the ratio of favourable to adverse travel did"*) on a different leg family by a different route.

### 3.8 A clamp I found in the data and could NOT attribute to code

**48 of 180 `bybit_1` rows sit at a stop distance of exactly `0.00150000` of entry** (8 decimal places, an 8.6× mode over the next most common value) — 34.2% of pre rows, 20.8% of post rows, **`ict_scalp_*` only**. ⚠️ **I could not find its source.** I searched `src/` for the literal `0.0015`, for `min_stop*`, `*_bps`, and clamp/floor names; the only hit is `hf_vwap_revert.py`'s unrelated `min_stop_pct: 0.003`. **MEASURED: the clamp. UNESTABLISHED: where it comes from.** **INFERRED** that it is a *floor* rather than a cap, from the two measurements above: a cap binds more as volatility rises, a floor binds less — volatility rose and the share at the value fell 34.2% → 20.8%.

It does not change the verdict, and it moves it the safe way: excluding the clamped rows, the winner collapse is **larger** (median R 2.573 → 0.536; avg win $1,092.07 → $174.83). Filed.

---

## 4. What the evidence does NOT support

- **That the collapse is −72%.** That figure is the all-provenance number on the pairs-excluded population. On broker-measured rows it is −38.5%; standardised to the pre-era provenance mix it is −81.4%; on measured trades that reached a declared target, achieved R **rose**. The direction is established; **the magnitude is not**, and the binding constraint is n = 15 pre-era measured winners.
- **That MI-271's "not a win-rate collapse" framing holds.** It holds only with the pairs sleeve in. Excluded, win rate falls 19.5pp and the two terms are comparable.
- **That the `netting_attributed` wins are real.** 39 rows, 100% estimated, on the path `OI-20260908` says can close a live position at a manufactured price. I did **not** run the venue-side read that would adjudicate them; that is `OI-20260908`'s own criterion and it needs a live position read, not a journal query.
- **That any single leg is the problem.** All 7 common legs fell and the mix explains nothing.
- **That this is `ib_paper`'s or the real-money book's story.** `ib_paper` fails the `contract_value = 1` premise (median derived/recorded 0.10) and is excluded from every number here. Real-money exposure is unchanged from MI-271's reading and is small.
- **That the regime reading generalises beyond crypto perps.** Every measurement here is Bybit linear USDT perps. The equity and futures legs were not tested.
- **Anything about a 90-day baseline.** The journal tail reaches **26 days** and `/api/diag/journal` serves 1000 rows with no offset and no `WHERE`. A longer window needs the full DB.

---

## 5. The open questions a remedy would have to answer

**Nothing here is enacted. No parameter is changed, no order placed, no config touched.** Every item below is a Tier-3 proposal routed to the manager, with its basis.

1. **Is the `netting_attributed` population trustworthy at all?** 39 `bybit_1` rows, 100% estimated, carrying the largest single share of the collapse, on a live unfixed defect. **A remedy computed off this population before `OI-20260908` lands is computed off a contaminated instrument** — which is the cycle priority's whole point. This is the one item I would put first, and it is a *measurement* fix, not a trading change.
2. **Does a remedy exist that does not require predicting follow-through?** The mechanism is that price moves *further* (vol +29%) and *converts less* (winner R halved, hold time −63%) while losses stay pinned at 1R. Widening stops is refuted by §3.4 — losses are already exactly 1R and widening only makes each one bigger. Tightening targets is refuted by §3.3 — nothing is reaching the current target, so a nearer one only caps the winners that do run. **A remedy therefore has to change which trades are TAKEN, not what happens after.** That is an entry-side question, and this unit produced no entry-side evidence.
3. **Should these legs trade at all in this regime?** `ict_scalp_*` is 2 of 3 of the loss with the best provenance, and its degradation is unanimous across symbols and timeframes. The lever this repo already has for that is `execution: shadow` — Tier-3, and **explicitly not proposed here**, because MI-271 §5.4 says *"do not retune `ict_scalp_*` on this evidence"* and that caution applies to retiring it too. What would answer it is a regime gate with a walk-forward behind it (`.claude/skills/regime-selectivity` governs the bar), not a reaction to 101 post-era rows.
4. **What is the 0.0015 stop-distance floor, and is it wanted?** It binds on 27% of scalp rows. Until its source is found nobody can say whether it is a deliberate protection or an accident, and it silently changes the risk denominator for a quarter of the book.
5. **What is the magnitude, really?** The honest answer needs measured coverage on the pre-era, which is now unrecoverable for that window — but it is recoverable *going forward*, and the cheapest version is whatever raises `bybit_1` coverage above 16.5%. `ict-exchange-fills-pull` is already hourly; why pre-era coverage was 16.5% and post-era 49.5% is not established here.

---

## 6. Rows filed

| id | register |
|---|---|
| `BL-20260912-MI-271-S-HEADLINE-DECOMPOSITION-TABLE-INCLUDES-THE-PAIRS-SLEEVE-EVERY-OTHER-SECTION-EXCLUDES` | performance |
| `BL-20260912-RISK-PER-TRADE-COMPUTED-FROM-TRADES-STOP-LOSS-USES-THE-TRAILED-STOP-AND-PUTS-IT-IN-A-DENOMINATOR` | health |
| `BL-20260912-A-STOP-DISTANCE-FLOOR-OF-0-0015-BINDS-ON-A-QUARTER-OF-SCALP-ROWS-AND-ITS-SOURCE-IS-NOT-IN-SRC` | health |
| `BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED` | performance |
