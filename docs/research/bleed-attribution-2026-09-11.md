# Why is the book bleeding? — attributing the 2026-08-30 directional-leg break

> **Doc status:** `live` · category `research` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-271 · `WO-20260911-WHY-IS-THE-BOOK-BLEEDING-ATTRIBUTE-THE`
**Row:** `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
**Cycle priority:** `CY-20260906-TRADING-TRUTH`
**Reproduce:** `python3 scripts/research/bleed_attribution_2026_09_11.py --trades <journal.json>`

The operator, 2026-09-11: *"we've had a pretty miserable week performance wise… it's been a few weeks where we've really dropped a lot of capital, and we need to figure out why we're bleeding so hard and what we're gonna do about it."*

---

## 0. The verdict, in four sentences

**The bleed is real, and it began 2026-08-27 — three days BEFORE the e35 bracket-geometry change that everyone has been treating as the prime suspect.** Two-thirds of the post-deploy loss sits in `ict_scalp_*` legs that declare no bracket geometry at all and that e35 could not have touched even in principle, and those are the *best-measured* losses in the book. e35 did do something real and provenance-clean — it drove its own legs' stop-out rate from 0/5 to 6/7 (p=0.015) — but its legs carry 23% of the loss on 30%-measured data, so **e35 is a genuine secondary effect, not the cause of the bleed**.

**The primary cause is a market-regime change that the system was not built to detect, and still cannot detect.** Reverting e35 would address at most ~23% of the loss and would leave the dominant two-thirds unexplained.

---

## 1. Population — stated first, because the headline changes sign on it

| | |
|---|---|
| **Source** | `/api/diag/journal?table=trades&limit=1000`, pulled 2026-09-11T17:51Z |
| **Span** | ids 4701–5700 · `created_at` 2026-08-17T02:52Z → 2026-09-11T17:32Z |
| **Decision population** | `status=closed` AND `NOT is_backtest` AND `pnl IS NOT NULL` AND not the pairs sleeve → **292 rows** |
| **Pairs excluded** | 264 rows (separate order path, already exonerated) |
| **PnL provenance** | measured 124 / estimated 371 / unverified 61 / **fabricated 0** over the 556-row pre-pairs-filter set → **coverage 22.3%** |

### Three truncation and contamination caveats that bound everything below

1. **The window is a 1000-row TAIL ordered by id, not a lifetime.** A trade OPENED before id 4701 but CLOSED inside the window is **absent**. That biases the PRE era toward short-duration trades. Correcting it needs the full DB, which `/api/diag/journal` cannot serve (hard cap 1000, no offset, no WHERE).

2. **`ib_paper` is excluded from every money headline.** Its PRE era is **2 rows carrying +$252,602** at **coverage 0.0** — trade 4773, MGC 95 lots × 262.3 × multiplier 10, sourced `recorded_exit_price`, which `CLAUDE.md` states "was never broker truth", on the account with the standing journal-quantity divergence (`OI-20260826-MGC-JOURNAL-QTY-DIVERGENT-UNOWNED`, journal 54 lots vs venue 11). Left in, that single row **inverts the sign of the entire pre-period.**

3. ⚠️ **Provenance coverage roughly DOUBLED across the split point** — untouched control 31.8% → 66.3%, e35 16.7% → 30.4%. So every naive before/after PnL comparison here is partly comparing *a poorly-measured period* against *a well-measured one*. This is handled by reporting each result twice, on all rows and on measured rows only, and by trusting only what survives both.

---

## 2. Instrument repairs, done before any verdict

### 2.1 The split is on `created_at`, never `closed_at`

A leg carries the geometry it was **opened** under. A trade opened 08-21 and closed 09-02 carries the pre-e35 bracket however late it closed. This is the exact trap `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS` had to sharpen its own clause (b) to close, on a real trade (4904) that satisfied the loose wording while carrying the old geometry. Split point: **2026-08-30T08:53:19Z** (commit `892c9a2c`).

### 2.2 `exit_reason` cannot measure stop-out rate — it had to be rebuilt

`reconciler_filled` is **145 rows, 40.5% of the population** — the single largest exit label — and it is the frozen-label defect (`BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE`): the label is pinned at the moment `exit_price` was NULL and **no writer ever re-runs the classifier** when a price later arrives. Adjudicating those 145 rows against their own declared levels: **30 reached the stop and 12 reached the target — 29% of them did hit a bracket.** A stop-out rate read off `exit_reason` is therefore not a measurement of stop-outs.

So exit location is **adjudicated** from `(direction, exit_price, stop_loss, take_profit_1)` with a 15bp touch tolerance, in five never-collapsed states: `reached_stop` · `reached_target` · `neither` · `ungradeable_no_price` · `ungradeable_no_levels`. **`ungradeable` is never folded into `neither`** — that would manufacture a clean negative (sub-class C of UNPROVENANCED DIAGNOSTIC OUTPUT).

**Positive control, run before the adjudicator was used for anything:**

| control | result |
|---|---|
| rows independently labelled `sl`/`sl_cross` | **76 of 86 gradeable adjudicate `reached_stop` — 88.4% recall** |
| rows independently labelled `tp`/`tp_cross` | **28 of 28 adjudicate `reached_target` — 100%** |

**Circularity probe.** `exit_price_source=verdict` and `recorded_exit_price` are the bot's own declared level rather than a fill, so adjudicating them against that same level risks tautology. Measured: `verdict` (n=13) adjudicates 85% `reached_stop` — genuinely suspect — but `recorded_exit_price` (n=17) adjudicates only 41%, and the three dominant sources are `candle_at_close` (n=139, 40%), `exchange_fill` (n=80, 64%) and `bybit_closed_pnl` (n=30, 60%). **Circularity is confined to ~13 rows and is not driving the result.** Every stop-rate claim below is additionally re-run on broker-measured exit prices only.

### 2.3 The control was contaminated, and the change census is what caught it

**2026-08-30 does not carry one change.** Enumerated before attributing anything:

| when | change |
|---|---|
| **2026-08-29T17:08Z** | `91de68b9c` **M20 B4 — bracket geometry on 8 MORE legs**: `mgc_pullback_1d`, `spy_trend_long_1d`, `qqq_trend_long_1d`, `iwm_trend_long_1d`, `slv_trend_1h`, `tlt_pullback_1h`, `uso_trend_1h`, `scha_trend_long_1d` |
| **2026-08-30T08:53Z** | `892c9a2c` **e35 geometry, 9 legs** |
| 2026-08-30 | `BYBIT_HEDGE_MODE_SYMBOLS` armed (2 pairs → 8 → 12 in ~10h) |
| 2026-08-30 | five order-path commits (arbitration annotate, flatten marking, retCode check, audit fixes) |

Every B4 leg would otherwise have been graded as part of the "untouched control" — the very group whose stability the e35 verdict rests on. **An analysis splitting only on e35 is comparing *changed* against *partly-changed* and calling the second one a control.**

⚠️ **And e35 itself is not "2.5 → 2.0 on 9 legs".** Read off the commit it is a five-level ladder: → 2.0 (`trend_donchian`, `eth_4h`, `ada_4h`, `avax_pullback_2h`), → **1.5** (`sol_4h`, `avax_4h`), → **3.0, WIDER** (`htf_pullback_trend_2h`), **tp_r only, stop untouched** (`ada_pullback_2h`), and 2.0 + tp_r 50→3 (`xrp_4h`). `trend_donchian_eth_prop` was HELD at 2.5.

**The control was therefore rebuilt** as legs declaring **neither** `atr_stop_mult` nor `tp_r` in `config/strategies.yaml` — the `ict_scalp_*` family — which are *structurally immune* to any bracket-geometry change rather than merely un-edited, and so cannot be silently contaminated by a future geometry commit.

---

## 3. Phase 1 — the discriminating measurement (the row's own `clears_when`)

### 3.1 Stop-out rate, adjudicated, before vs after, by geometry group

Population: the 292-row decision set of §1 (closed, non-backtest, `pnl NOT NULL`, pairs excluded), split on `created_at`. Every rate below carries its own n and a 95% Wilson interval; the stop-rate denominator is the **gradeable** rows only, with `ungradeable` reported separately and never folded in.

| group | era | n | win rate (95% CI) | **stop rate (95% CI)** |
|---|---|---|---|---|
| **e35** | pre | 30 | 0.667 [0.488, 0.808] | **0.067 [0.019, 0.213]** (2/30) |
| **e35** | post | 23 | 0.043 [0.008, 0.210] | **0.565 [0.368, 0.744]** (13/23) |
| **untouched control** | pre | 88 | 0.545 [0.442, 0.645] | **0.540 [0.436, 0.641]** (47/87) |
| **untouched control** | post | 83 | 0.313 [0.224, 0.419] | **0.659 [0.551, 0.752]** (54/82) |
| B4 geometry | pre | 8 | 0.625 | 0.750 (6/8) |
| B4 geometry | post | 10 | 0.500 | 0.500 (5/10) |

**The two groups degraded by DIFFERENT mechanisms.** The e35 legs' stop-out rate went up **8.5×** and its confidence interval does not overlap its own pre-period. The control's barely moved (0.540 → 0.659, CIs overlapping). Meanwhile the control's *win rate* fell hard while its stop rate held — i.e. the control legs are stopping out about as often as before but **their winners collapsed**, which is a regime signature, not a stop-width signature.

### 3.2 Hypothesis tests

| test | all rows | measured rows only |
|---|---|---|
| e35 pre vs post (win) | 20/30 → 1/23, **p < 1e-5** | 3/5 → 0/7, p = 0.045 |
| control pre vs post (win) | 48/88 → 26/83, **p = 0.0032** | 13/28 → 14/55, p = 0.082 |
| **PRE cross-section (falsifier)** | **p = 0.289 — indistinguishable** | p = 0.656 |
| POST cross-section | **p = 0.0069** | **p = 0.334 — NOT significant** |
| difference-in-differences | −0.391 | — |
| **e35 STOP rate, pre vs post** | CIs disjoint | **0/5 → 6/7, p = 0.015** |
| control STOP rate, pre vs post | overlapping | 14/27 → 39/54, p = 0.086 |

**The falsifier passes:** the two groups were statistically indistinguishable *before* the change (p=0.289), so the post-period gap is not a pre-existing difference.

⚠️ **But read the second column.** The e35-specific *win-rate* effect **does not survive** restriction to broker-measured rows (p=0.334). Only one thing survives both columns: **the e35 stop-out rate rise (p=0.015 on broker fills alone)**. That is the single provenance-clean, statistically supported e35 finding in this analysis, and it is a statement about *mechanism*, not about *net PnL*.

### 3.3 MFE-at-stop — **NOT ANSWERABLE, and that is the honest result**

The row asks for MFE-at-stop. `position_telemetry` carries `peak_r` and spans the right window, so the quantity exists. It still cannot discriminate:

- **Join rate 36%** (105 of 292 rows carry telemetry with a `trade_id`).
- Cells after splitting by group × era: **e35 pre n=2, e35 post n=8, control post n=15, control pre n=0 (empty).**

No conclusion is supportable at those n's, and the empty cell removes the comparison entirely. Weakly and non-significantly, e35 post stop-outs reached a median **0.515R** before stopping — i.e. they were in profit and gave it back — but n=8.

**Why it is unavailable is itself the finding:** `record_position_telemetry` only reached most legs on **2026-09-07** via MI-164 (`OI-20260907-TELEMETRY-HOOK-...`), *eight days after the break*. **The one instrument that could separate "the stop is too tight" from "the entries stopped working" was installed a week after the event it was needed for.** Filed.

---

## 4. Phase 2 — the multi-week attribution (the operator's actual question)

### 4.1 The bleed predates e35 by three days

`bybit_1` daily realised PnL, close-time basis, all 26 observed days:

```
08-17    +32   08-24  -2438   08-31  -3901   09-07  -1581
08-18   -207   08-25  +1514   09-01  -3235   09-08    -90
08-19  +6352   08-26  +4173   09-02  -1481   09-09  -2779
08-20    +33   08-27   -897 ◀ STREAK STARTS  09-10  -2925
08-21  +1354   08-28  -6654 ◀ WORST DAY      09-11  -2166
08-22  +4600   08-29   -631                  
08-23  +1272   08-30  -1241 ◀ e35 SHIPS 08:53Z
```

**16 consecutive losing days, 2026-08-27 → 09-11, totalling −$36,997.** (The row says 15 days from 08-28; the marginal day is −$897 and does not matter — **both readings start before the change**.) The worst single day in the entire window is **2026-08-28, two days before e35**. The only commit on 08-27 was comment-only; nothing behavioural landed that day.

Equity curve: peak +$16,685 → final −$20,312, **max drawdown $36,997**.

### 4.2 Where the money actually went (post-deploy opens, `ib_paper` excluded)

| group | PnL | share | n | measured PnL | coverage |
|---|---|---|---|---|---|
| **untouched control (`ict_scalp_*`)** | **−$30,057** | **66%** | 78 | −$19,921 | **0.65** |
| **e35** | −$10,501 | 23% | 23 | −$1,539 | 0.30 |
| other | −$4,914 | 11% | 31 | −$1,311 | 0.39 |
| B4 geometry | −$173 | 0% | 10 | −$676 | 0.50 |
| pairs sleeve | +$34 | −0% | 112 | −$11 | 0.01 |
| **TOTAL** | **−$45,610** | | 254 | −$23,458 | |

**Two-thirds of the loss is in legs e35 never touched, and it is the two-thirds with the best provenance.** The e35 legs' losses are both smaller and the least-measured (30% coverage).

Top contributors by absolute loss, all post-deploy opens — note the top seven are all scalps:

| account | strategy | PnL | n | basis |
|---|---|---|---|---|
| `ib_paper` | ict_scalp_mgc_15m | −21,070 | 5 | partial 4/5 *(excluded above — qty-divergent account)* |
| `bybit_portfolio` | ict_scalp_5m | −7,754 | 6 | partial 4/6 |
| `bybit_1` | ict_scalp_avax_5m | −5,135 | 16 | partial 14/16 |
| `bybit_1` | ict_scalp_sol_5m | −4,841 | 17 | partial 9/17 |
| `bybit_1` | ict_scalp_sol_15m | −4,592 | 10 | partial 4/10 |
| `bybit_1` | ict_scalp_xrp_15m | −4,113 | 4 | partial 1/4 |
| `bybit_1` | ict_scalp_eth_15m | −3,361 | 10 | partial 8/10 |
| `bybit_portfolio` | xrp_pullback_2h | −2,818 | 3 | **RECONSTRUCTED (0 measured)** |
| `bybit_1` | trend_donchian_avax_4h | −1,944 | 5 | **RECONSTRUCTED (0 measured)** |

### 4.3 The decomposition — it is NOT a win-rate collapse

`bybit_1`, all closed rows, pre vs post open:

| | n | win rate | **avg win** | avg loss | expectancy | total |
|---|---|---|---|---|---|---|
| **PRE** | 235 | 0.460 | **$404** | −$269 | **+$40** | +$9,486 |
| **POST** | 207 | 0.411 | **$112** | −$322 | **−$144** | −$29,798 |

**Win rate fell only 4.9pp. Average win fell 72%.** Average loss worsened 20%. The expectancy flip from +$40 to −$144 is driven overwhelmingly by winners getting smaller, not by winning less often.

That matters because it points at a completely different remedy than "the stops are too tight". It is the signature of a market in which **moves stop extending** — entries still get their initial push (win rate holds) but nothing runs. Consistent with it: the control's stop rate held flat while its win rate fell.

Concentration: **top 14 losers = 50% of gross loss, across 119 losing trades.** Broad-based, mildly concentrated — not a handful of blow-ups.

### 4.4 Real money — separated, never blended

| account | class | n | PnL | measured | coverage |
|---|---|---|---|---|---|
| `bybit_2` | **real_money** | 40 | **−$20.36** | −$38.44 | 0.750 |
| `alpaca_live` | **real_money** | 0 | — | — | — (no closed rows in window) |

**Real-money exposure to this bleed is ~$20.** `bybit_2` shows the same shape as the paper books (win rate 54.2% → 0.0% over 16 post trades, stop rate 34.8% → 81.3%) at negligible size. `bybit_portfolio`, which mirrors `bybit_2`'s roster by enforced test invariant, is the size-scaled read: **−$15,632**.

**The capital being lost is paper.** That is not a reason to relax — `bybit_portfolio` is the honest forecast of what `bybit_2` would have lost at size — but the operator should know the real-money damage to date is two figures, not five.

### 4.5 ⚠️ The "profitable fortnight before" may itself be partly an artifact

Measured-provenance-only weekly PnL for `bybit_1`:

| week | measured PnL (n) |
|---|---|
| 2026-08-17 | **— (zero measured rows)** |
| 2026-08-24 | −$6,399 (19) |
| 2026-08-31 | −$6,829 (24) |
| 2026-09-07 | −$6,936 (21) |

**On rows that are broker truth, `bybit_1` lost ~$6.4–6.9k in every week that can be measured at all — including the week before the break** — while the week that carries the "+$17,951 profitable fortnight" headline contains **zero measured rows**. This does not prove the pre-period was unprofitable; it proves **nobody can currently say it was profitable**, and the baseline the whole "something broke" framing rests on is weaker than it looks. Filed.

---

## 5. Phase 3 — remedy (Tier-3 items are PROPOSALS; nothing here is enacted)

### 5.1 The honest headline

**Most of this is the market, and the system behaved close to correctly.** Legs that are structurally incapable of being affected by any geometry change lost two-thirds of the money, with their stop-out rate essentially flat and their average win down 72%. That is what a regime change looks like from inside a trend/breakout book. Dressing it up as a bug would buy a Tier-3 change that makes things worse.

**But two things are not just the market**, and both are stated as findings rather than being folded into "the market chopped":

1. **e35 raised its legs' stop-out rate from ~0 to ~57% (p=0.015 on broker fills).** Whether that is net-harmful is **not established** — a tighter stop is *supposed* to stop out more often, and e35 passed a 4–6 fold walk-forward. Its legs did lose $10.5k over 23 trades, but 70% of that is unmeasured.
2. **Nothing alarmed for 16 days.** See §6.

### 5.2 PROPOSAL A (Tier-3, recommended) — do NOT revert e35 yet; instrument it for 2 weeks

**Rationale:** the evidence for reverting is weaker than it looks. e35's legs are 23% of the loss on 30%-measured data, n=23 post trades, against a 4–6 fold walk-forward that passed. Reverting on 23 trades is exactly the low-n reaction this repo has a guard-shaped hole for. **And the discriminating measurement is now cheaply available for the first time** — MI-164 put `record_position_telemetry` on these legs on 2026-09-07, so `peak_r` + `r_to_stop` can answer the counterfactual directly: *for each e35 stop-out, would the pre-e35 2.5-ATR stop have survived?*

**The change I would make:** none to `config/`. Run the counterfactual for 2 weeks or n≥30 e35 stop-outs, whichever first, then decide with evidence instead of instead of evidence.

**Cost of waiting, stated:** e35 legs are running ≈ −$457/trade × ≈2 trades/day ≈ **−$900/day, on paper books**, with real-money `bybit_2` exposure at ~$20 total. That is an affordable price for a decision that is currently unsupportable.

### 5.3 PROPOSAL B (Tier-3, the fallback if the operator wants action now)

If the operator prefers to act rather than wait, the narrowest defensible change is to revert **only the `atr_stop_mult` half, and only on the two legs tightened furthest**, leaving every `tp_r` change alone:

```yaml
# config/strategies.yaml — PROPOSAL ONLY, NOT APPLIED
trend_donchian_sol_4h:
  atr_stop_mult: 2.5   # revert from 1.5 (e35 sm1.5) — PROPOSED 2026-09-11
trend_donchian_avax_4h:
  atr_stop_mult: 2.5   # revert from 1.5 (e35 sm1.5) — PROPOSED 2026-09-11
```

**Why only these two:** they are the largest dose (2.5→1.5, a 40% tighter stop), and the stop-rate finding is the only provenance-clean e35 result. **Why not the `tp_r` changes:** nothing in this analysis implicates them, and `ada_pullback_2h` — whose *only* e35 change was `tp_r` — shows no degradation (win 0.000 both eras, stop rate actually *fell* 1.000 → 0.500).

⚠️ **This proposal does NOT account for `ict_scalp_*`, and that is stated explicitly rather than papered over.** It addresses at most 23% of the loss. **A second cause — the regime — remains open and is the larger one.** `trend_donchian_sol_4h` is also already a SUNSET retirement candidate, so retiring it may dominate re-tuning it.

### 5.4 What NOT to do

- **Do not widen stops across the book to "stop getting stopped out".** The control's stop rate barely moved (0.540 [0.436, 0.641] on 87 gradeable pre-rows → 0.659 [0.551, 0.752] on 82 post-rows, overlapping intervals); its problem is winner size, not stop width. Widening stops there increases loss size against the one metric already 20% worse.
- **Do not retune `ict_scalp_*` on this evidence.** Its losses are real and well-measured, but this analysis establishes *that* they degraded, not *why*. That is a separate research unit.
- **Do not read the +$250k pre-period as a baseline.** See §1 caveat 2.

---

## 6. Phase 4 — the detector that is owed regardless

**16 consecutive losing days and −$36,997 of drawdown, and the operator found it, not a monitor.** This clause is owed whatever the attribution turns out to be.

The repo's standing hazard is the desensitised alarm — a detector that fires constantly gets walked past, and that is a P1 in its own right. **So the false-positive count is the design evidence.** Measured over the observed window:

| rule | `bybit_1` (26 days) | `bybit_portfolio` (18 days) | `bybit_2` (20 days) |
|---|---|---|---|
| 3 consecutive losing days | 1 fire | 1 fire | **2 fires** |
| **4 consecutive losing days** | **1 fire** | **1 fire** | **1 fire** |
| 5+ consecutive losing days | 1 fire | 1 fire | 1 fire |

**At N≥4 the rule fires exactly once per account over the whole window — on the real event, with zero false positives.** N=3 produces a spurious fire on `bybit_2`, whose daily PnL is dollars and whose sign is noise.

⚠️ **The dispatch asked what this would have fired on over 90 days. I cannot answer that and will not estimate it** — the 1000-row journal tail reaches back only 26 days, so a 90-day false-positive count has no denominator here. Computing it needs the full DB. What is established is 26 days, 3 accounts, zero false positives at N≥4.

**Proposed shape** (Tier-1 observability, buildable without an operator decision):
- Per-account, per-day realised PnL on the **provenance-clean** population, with `measured` / `estimated` counted separately so a streak cannot be manufactured by reconstructed rows.
- Fires on **N≥4 consecutive losing days OR drawdown-from-peak > X%**, latched **durably** per account (`runtime_logs/*_state.json`), not per process — the `BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART` lesson, where a per-process latch put 202 CRITICALs on the operator's channel.
- **Must gate on a minimum absolute size**, or `bybit_2` at ±$5/day pages constantly — the N=3 result above is that failure mode already visible at n=20 days.
- States never collapsed: `streak_active` / `no_streak` / **`insufficient_days`** (*we could not look*) — a book with no closes is not a book that is winning.

---

## 7. What this does and does not establish

**Established:**
- The bleed is real and began **2026-08-27**, three days before e35. 16 losing days, −$36,997 on `bybit_1`.
- **Two-thirds of the post-deploy loss is in geometry-immune legs** and is the best-measured portion.
- The mechanism is **winner-size collapse** (avg win −72%), not a win-rate collapse (−4.9pp).
- **e35 really did raise its legs' stop-out rate** — 0/5 → 6/7 on broker fills alone, p=0.015.
- The two groups were **statistically indistinguishable before** the change (p=0.289).
- Real-money loss to date is **−$20.36** on `bybit_2`; `alpaca_live` has no closed rows.

**NOT established, and explicitly refused:**
- That e35 made its legs *worse overall* — **p=0.334 on measured rows.** The strong-looking p=0.0069 depends on rows that are 77% unmeasured.
- **MFE-at-stop** — the row's second requested quantity. 36% join rate, cells of 2–15, one cell empty. Unavailable because telemetry arrived 2026-09-07.
- That the pre-period was genuinely profitable — the week carrying that claim has **zero measured rows**.
- A 90-day detector false-positive rate — the window reaches 26 days.
- Why `ict_scalp_*` degraded. *That* it did is established; the cause is a separate unit.

---

## 8. Rows filed

| id | register |
|---|---|
| `BL-20260911-THE-BLEED-PREDATES-E35-BY-THREE-DAYS-AND-THE-BRIEFED-08-30-ONSET-IS-WRONG` | performance |
| `BL-20260911-THE-MFE-INSTRUMENT-THAT-WOULD-ATTRIBUTE-THE-BREAK-ARRIVED-EIGHT-DAYS-AFTER-IT` | health |
| `BL-20260911-THE-PROFITABLE-FORTNIGHT-BASELINE-RESTS-ON-A-WEEK-WITH-ZERO-MEASURED-ROWS` | performance |
| `BL-20260911-NOTHING-ALARMED-ON-SIXTEEN-CONSECUTIVE-LOSING-DAYS-AND-THE-OPERATOR-FOUND-IT` | health |
| `BL-20260911-THE-B4-GEOMETRY-CHANGE-LANDS-INSIDE-WHAT-EVERY-E35-ANALYSIS-CALLS-ITS-CONTROL` | performance |
