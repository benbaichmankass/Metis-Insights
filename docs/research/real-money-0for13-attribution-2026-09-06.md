# Real money went 0-for-13: fix the instrument, then attribute the loss

**Date:** 2026-09-06 · **Session:** `session_01XRYfPGr4jAHr4k1rmvTKFi` (sub-session)
**Parent object:** `WO-20260906-REAL-MONEY-WENT-0-FOR-13-THE`
**Tier:** 1 (research/docs). **Nothing is applied.** The `config/strategies.yaml`
proposal in § 5 is Tier-3 and is for the operator to decide.

> **Bottom line.** (1) The R instrument is unsound and its sign is wrong: on the exact
> 30d real-money window the promotion gates read, 12 contaminated rows (30.8%) carry
> **117%** of the window's R, and removing them flips `expectancyR` from the published
> **+0.98 to −0.24**. (2) The e35 hypothesis is **not supported as the primary cause**.
> Trades entered on an e35 leg under the new geometry are **2 of 13 losses and 13.5% of
> the money**. The dominant loser, `ict_scalp_5m` (6 trades, 58.6% of the loss), has had
> **no config change at all since 2026-08-16**. (3) What did change is the market: median
> favourable excursion per trade collapsed **2.11R → 0.15R**, and **not one of the 13
> losers ever reached +1R in its favour**. A wider stop cannot manufacture profit that
> never existed. My confidence is **high** on (1) and (2), **moderate** on the positive
> regime attribution in (3).

---

## 0. What I measured against, and how

Everything is measured live on 2026-09-06, not inherited from the dispatch.

| source | what | how it was bounded |
|---|---|---|
| `/api/bot/db/table/trades` | 5516 rows, full table | paged 500 at a time; `total: 5516`, 5516 fetched |
| `/api/bot/db/table/trades?filter_col=account_id&filter_val=bybit_2` | 1656 rows | **`filter_state: applied`** asserted before use |
| `/api/bot/db/table/order_packages` | 4433 rows | needed for the declared-initial-risk record |
| `/api/bot/performance?window=30d` | the published instrument | reproduced exactly, see § 1.3 |
| Bybit linear-perp 15m candles | MAE/MFE per trade | via `trainer-vm-diag` relay, issues #11107–#11110 |

`bybit_2` is the only real-money book; balance **$264.86**.

**R-provenance grading uses `src/runtime/r_provenance.py::classify_r` and PnL provenance
uses `src/runtime/provenance.py`, imported — not re-derived.** Both modules already
encode this vocabulary carefully and CLAUDE.md forbids a second copy of it.

### 0.1 An instrument I built, tested, and threw away

The counterfactual needs the venue's own price path. Bybit's API is geo-blocked from a
Claude sandbox (HTTP 403, CloudFront country block), so I first built the replay on
**Coinbase spot** as a proxy and validated it before trusting it: *a row we know hit its
stop must show MAE/D ≥ 1.0.*

**It failed 4 of 11 known stop-outs (36%).** Mean spot-vs-perp basis at entry was 0.23%
against a **median stop distance of 0.65%** — the noise is a third of the signal. Worse,
**all four failures understated excursion**, which biases the counterfactual toward
"a wider stop would have survived" — i.e. *toward the hypothesis under test*.

So the proxy was discarded and the whole computation was re-run on Bybit's own candles
through the trainer relay. On that data **6 of 6** known stop-outs reproduce. The rejected
pass is recorded in `docs/research/data/mae-mfe-bybit-perp-2026-09-06.txt`, because the negative
result is the reason the relay was used.

---

## 1. DELIVERABLE 1 — the R instrument is unsound, and its sign is wrong

### 1.1 The mechanism, read from the code

`_clean_trades.r_multiple` computes `pnl / (|entry − stop| · |qty| · contract_value)`.

The **`abs()` is load-bearing**: a stop stored on the *wrong side of entry* still yields a
positive risk, so the row produces a finite R instead of being refused. And
`trades.stop_loss` holds the **final** stop — `order_monitor._apply_update` writes trailing
amends into it. A trade that trailed its stop through breakeven therefore stores a stop
beyond entry; `|entry − stop|` collapses toward zero and R explodes.

**R is defined against entry-time risk. The column holds exit-time stop.** That is the bug,
and it is structural, not a data-entry error.

### 1.2 Contamination across the whole journal

**Population: every row `/api/bot/performance` would aggregate, with no time or account
filter — `status='closed'`, not `is_backtest`, `pnl IS NOT NULL`, minus the three documented
exclusions (`orphan_adopt`, `superseded`, `exchange_reset_flat`). n = 1286.**
These are the figures `scripts/research/r_contamination_audit.py` prints, so a future session
can re-derive them exactly.

| R-provenance state | n | share |
|---|---|---|
| `contaminated` | 104 | 8.1% |
| `confirmed_initial` | 172 | 13.4% |
| `unverified` | 1010 | 78.5% |
| `no_basis` | 0 | 0.0% |

| subset | n | sum R | mean R |
|---|---|---|---|
| `contaminated` | 104 | **+4232.03** | **+40.69** |
| `confirmed_initial` | 172 | **−84.81** | **−0.49** |
| all | 1286 | +4382.14 | +3.41 |

**8.1% of rows carry 96.6% of all R.** On the rows whose risk basis is *confirmed to be the
initial risk*, expectancy is **−0.49R**.

⚠️ **Do not over-read this one.** Removing the contaminated rows here does **not** flip the
whole-journal figure negative — it collapses it to **+0.127R** over the remaining 1182 rows,
because `unverified` still carries +234.93 and `unverified` is *"we could not look"*, not a
clean bill. The sign flip happens in § 1.3, on the real-money window the gates actually read.
The honest whole-journal claim is that the aggregate is **an artifact of 8.1% of rows and is
indistinguishable from zero once they are removed**, not that it is negative.

**Positive control for the discriminator:** `|R| > 10` occurs **26 times among the 104
contaminated rows and 0 times among the 172 confirmed_initial rows**. The separation is
total — this is not a threshold fitted to the answer.

*(An earlier pass over the wider population that does **not** apply the three exclusions —
n = 1451 — gives the same picture slightly stronger: 124 contaminated, 8.5%, carrying 98.8%
of R, `confirmed_initial` mean −0.53R, control 35 vs 0. Both are correct for their stated
population; the n = 1286 figures are quoted here because they are the ones the committed
script reproduces.)*

### 1.3 The exact window the promotion gates read — reproduced, then decomposed

`/api/bot/performance?window=30d` defaults to `demo=False` (**real money only**) and applies
three documented exclusions (`orphan_adopt` pseudo-strategies, `superseded` rows,
`exchange_reset_flat` rows). Applying exactly those:

| | my reproduction | the live endpoint |
|---|---|---|
| n | **39** | 39 |
| wins | **15** | 15 |
| totalPnl | **−3.6266** | −3.6266 |
| totalR | **+38.29** | +38.2891 |

An exact match on all four — so what follows decomposes *the instrument itself*, not a
lookalike.

| r_provenance | n | sum R | sum PnL |
|---|---|---|---|
| `contaminated` | 12 | **+44.83** | +61.65 |
| `confirmed_initial` | 7 | −2.76 | −12.20 |
| `unverified` | 20 | −3.78 | −53.07 |
| **TOTAL** | **39** | **+38.29** | **−3.63** |

**12 rows — 30.8% of the window — carry 117.1% of its R.** Remove them and

> `totalR` **−6.54** over 27 rows → `expectancyR` **−0.242**

which now agrees with `totalPnl −3.63` and `profitFactor 0.95`. **The published +0.98 has
the wrong sign.**

### 1.4 The exit-path split is contaminated too — grade it from price, not from the label

The dispatch asked for the closed set split by exit path. That split cannot be taken from
`exit_reason`, because the label is frozen at the one moment the answer could not be known:
`src/runtime/provenance.py` records that no writer ever re-runs `_classify_broker_exit` once
a price arrives late, and that **91 of 155 (58.7%)** `reconciler_filled` closes had in fact
reached a declared bracket level.

Measured on Bybit's own candles for the 13 post-window graded rows — `MAE/D ≥ 1.0` means
price actually reached the stop:

| | count |
|---|---|
| labelled `exit_reason='sl'` | **6** |
| price says the stop was **reached** | **9** |
| **mislabelled stop-outs** (reached the stop, labelled otherwise) | **3** — ids 5312, 5359, 5461 |

So the honest post-window split is **9 stop-outs, not 6**, and three of them hide inside
`reconciler_filled` / `netting_attributed`. Grading strategy quality over the pooled set
grades the reconciler; grading it over the *labelled* split still does, just less.

**Consequence:** R feeds the promotion gates, so every promote/demote verdict computed from
`expectancyR` over a population containing contaminated rows is currently unsafe. **No claim
in § 2–4 uses R.**

---

## 2. DELIVERABLE 2 — the windows, re-measured

Population: `bybit_2`, `status='closed'`, non-backtest. "Graded" = `pnl IS NOT NULL`.

| window | closed | graded | wins | win rate | PnL |
|---|---|---|---|---|---|
| 2026-08-16 → 08-29 | 24 | 23 | 14 | 60.9% | **+$40.96** |
| 2026-08-30 → 09-06 | 15 | 13 | **0** | **0.0%** | **−$43.12** |

The dispatch's figures reproduce exactly. Two rows carry `pnl=None` in each window
(`intent_reduce_executed` legs).

### 2.1 Where the −$43.12 actually came from

| leg | trades | PnL | share | config changed since 2026-08-16? |
|---|---|---|---|---|
| `ict_scalp_5m` | 6 | **−25.25** | **58.6%** | **NO — none at all** |
| `xrp_pullback_2h` | 3 | −9.90 | 23.0% | yes, but on **08-23** (`tp_r` 50→3, `trail_mult` 5→6) — **not e35** |
| `trend_donchian_xrp_4h` | 1 | −4.82 | 11.2% | **YES — e35** |
| `trend_donchian_eth_4h` | 2 | −2.30 | 5.3% | **YES — e35** (but see § 3.1) |
| `eth_pullback_2h` | 1 | −0.84 | 1.9% | **NO — none at all** |

**Legs with no configuration change whatsoever account for 7 of 13 trades and −$26.09
(60.5%) of the loss.**

---

## 3. Testing the e35 hypothesis

### 3.1 What e35 actually changed, and to which legs

Read from the diff of `892c9a2c` (2026-08-30 11:53 +0300 = **08:53Z**), parsed by strategy
name rather than by hunk:

```
trend_donchian          atr_stop_mult 2.5 -> 2      trend_donchian_ada_4h   atr_stop_mult 2.5 -> 2
htf_pullback_trend_2h   atr_stop_mult 2.5 -> 3      trend_donchian_avax_4h  atr_stop_mult 2.5 -> 1.5
trend_donchian_eth_4h   atr_stop_mult 2.5 -> 2      ada_pullback_2h         tp_r          50  -> 4
trend_donchian_sol_4h   atr_stop_mult 2.5 -> 1.5    avax_pullback_2h        atr_stop_mult 2.5 -> 2
trend_donchian_xrp_4h   atr_stop_mult 2.5 -> 2  ·  tp_r 50 -> 3
```

9 legs, 10 fields — matching `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`.
**`ict_scalp_5m`, `xrp_pullback_2h` and `eth_pullback_2h` are not in the set.**

### 3.2 The timing does not line up either

The losing run is 14 trades long and **starts 2026-08-28**, two days *before* e35 deployed.
Three of its members were **opened** on 08-21/08-22, under the old geometry, and merely
*closed* inside the post window (two of them by an operator flatten at 09:46–09:48Z on
08-30). Counting them as evidence about e35 would be backwards.

Of the **11 trades opened after the deploy**, only **2** are on an e35 leg:

| id | leg | opened | PnL |
|---|---|---|---|
| 5250 | `trend_donchian_xrp_4h` | 2026-08-30 23:46 | −4.82 |
| 5342 | `trend_donchian_eth_4h` | 2026-09-02 10:32 | −1.01 |

> **Trades entered on an e35 leg under the new geometry: 2 of 13 losses, −$5.83 of
> −$43.12 = 13.5% of the money.** The three real-money e35 legs are a small minority of
> both the count and the loss.

### 3.3 The counterfactual replay at the old `atr_stop_mult`

`atr_stop_mult` 2.5 vs the shipped 2.0 is a **1.25× wider stop**. Using Bybit's own 15m
candles, a trade survives iff its maximum adverse excursion stayed under 1.25 × its stop
distance. Over the **9 rows that price says actually reached their stop**:

| outcome | n | ids |
|---|---|---|
| would **not** have reached a 1.25× wider stop | **7** | 5250, 5312, 5359, 5369, 5409, 5429, 5461 |
| still stopped | 2 | 5316 (MAE/D 1.252 — marginal), 5450 (2.727) |

Taken alone this looks like support for the hypothesis: 7 of 9 stop-outs were **narrow**,
clustering just past 1.0 (1.027, 1.032, 1.038, 1.050, 1.053, 1.066, 1.144).

**It is not support, and § 4 is why.** A wider stop changes *where you exit a loss*. It
does not make a trade profitable. Not one of these trades was ever meaningfully in profit,
so the wider stop buys a **deferral, not a win** — the loss would have been taken later,
at a wider distance, and therefore likely **larger**.

---

## 4. What actually changed: the market stopped travelling

### 4.1 Favourable excursion collapsed 14×

Maximum favourable excursion as a multiple of each trade's own stop distance (MFE/D), from
Bybit's own candles. This is the measurement that settles it.

| window | n | median MFE/D | mean | max | reached ≥ +1R |
|---|---|---|---|---|---|
| PRIOR 08-16 → 08-29 (14/23 win) | 23 | **2.11** | 4.04 | 18.98 | **14 / 23 (60.9%)** |
| POST 08-30 → 09-06 (0/13 win) | 13 | **0.15** | 0.25 | **0.95** | **0 / 13 (0.0%)** |

**The single best trade of the losing week (0.95R) never matched the *median* trade of the
winning fortnight (2.11R).** Not one of the 13 ever reached +1R in its favour; only 2 of 13
reached even +0.5R.

Note the exact correspondence in the prior window: **14 trades reached +1R, and there were
14 wins.** The metric tracks the outcome.

**This is what refutes the stop hypothesis.** A stop is a floor under a loss; it cannot
create upside. In a week where the maximum favourable excursion available to *any* entry was
0.95R, no stop setting produces a winner.

It also cuts the other way against the geometry: e35 made stops **narrower**, which
*inflates* MFE/D (smaller denominator) for the affected legs. Post-window MFE/D is 14× lower
**despite** that upward bias.

### 4.2 Independent corroboration — the market, measured without reference to any trade

Coinbase spot 15m (the 0.23% basis is immaterial at this scale). "Directional efficiency" =
|net move| ÷ total path travelled; 1.0 is a pure trend, 0 is pure chop.

| window | sym | net % | median \|15m\| move | directional efficiency |
|---|---|---|---|---|
| PRIOR | BTC | **+24.05** | 0.093 | **0.098** |
| PRIOR | ETH | **+30.58** | 0.120 | **0.093** |
| PRIOR | XRP | **+39.20** | 0.190 | **0.060** |
| POST | BTC | **+2.08** | 0.091 | **0.023** |
| POST | ETH | **+1.60** | 0.108 | **0.014** |
| POST | XRP | **+1.65** | 0.160 | **0.010** |

**Per-bar volatility is essentially unchanged (0.093 → 0.091); directional efficiency falls
4–6×.** The market did not get quieter — it got *directionless*. That is precisely the
regime in which trend/pullback/breakout legs produce nothing and a tight-stop scalper is
whipsawed in both directions, which is exactly what `ict_scalp_5m` did: it lost on
**both** its longs (77298, 77923, 81857, 81127) and its shorts (77046, 77153).

> ⚠️ **A correction to the dispatch's own caveat.** It states *"BTC fell 81k → 77k in the
> window."* BTC did not fall: it opened the post window at 78234 and closed at 79864
> (**+2.08%**), with a high of 82283 and a low of 76219. The competing explanation is real,
> but it is **loss of directionality, not a decline** — and a decline would not explain a
> book that lost on longs and shorts alike.

### 4.3 Is 0-for-13 unusual for this book?

Population: `bybit_2` closed, non-backtest, `pnl NOT NULL`, **excluding `vwap`** (retired
2026-05-24, absent from both windows) — n = 129, 2026-05-14 → 09-04, **37.2% wins, −$3.71**.

Monthly: May 25.8% / −2.82 · Jun 69.6% / +28.72 · **Jul 25.7% / −25.00** · Aug 50.0% /
+29.94 · Sep 0.0% / −34.56. The book swings violently, and **July lost $25 at a 25.7% win
rate with e35 nowhere in existence.**

⚠️ **I have to correct my own first reading here.** I initially found "14- and 13-trade
losing streaks already in the history" and nearly reported them as precedent. Dating them
killed it: the 14 **is this event**, and the 13 (2026-05-14/15, −$2.32) was entirely
`orphan_adopt` reconciler artifacts, not strategy trades. The longest genuine pre-e35 losing
streak on comparable legs is **6** (2026-07-23→27, −$16.54).

So the run **is** roughly twice anything this book had done before. It is a real outlier —
just not one whose timing or composition points at e35.

---

## 5. What I propose, and what I do not

### 5.1 On e35 — recommend **do not revert on this evidence**

The exact diff a revert would need is § 3.1 read right-to-left. I am **not** recommending it:

- e35-leg trades entered under the new geometry are **2 of 13 losses / 13.5% of the money**.
- The dominant loser has **no config change at all**.
- The losing streak **began before the deploy**.
- Every leg — changed and unchanged alike — shows the same MFE starvation, and **no stop
  setting wins a trade that never goes into profit**.
- Reverting on this evidence would discard a geometry that passed a walk-forward, on the
  basis of a 2-trade sample, and would leave the operator believing a cause was addressed
  when it was not.

**n = 2 also means I cannot clear e35.** I can say it is not the primary cause of this week;
I cannot say it is harmless. `OI-20260830-...-NOT-YET-LIVE-VERIFIED` should stay open on its
own terms.

### 5.2 What the evidence does support — for the operator to decide (all Tier-3)

1. **`ict_scalp_5m` is the item, not e35.** 6 of 13 losses, 58.6% of the money, stop
   distances of **0.15%–0.80%** on BTC — inside the noise of a directionless tape. Its
   lifetime on `bybit_2` is 37 trades / 54.1% wins / **−$12.65**: it wins more often than it
   loses and still loses money, which is a payoff-geometry problem, not a hit-rate one.
   A regime gate on directional efficiency, or a stop floor, is the natural proposal —
   but **neither should be authored from n=6**, and § 1 means the R-based evidence normally
   used to argue it is unusable until the instrument is fixed.
2. **Nothing else should be re-tuned from this week.** n=13 against n=23.

### 5.3 What I did not do

Nothing was applied. `config/strategies.yaml`, `src/`, and the VM are untouched. No backtest
was run — the counterfactual is a measured replay against recorded venue prices, not a
simulation, and it cannot tell you what a *differently-sized* book would have done.

---

## 6. Confidence, and what this evidence cannot separate

| claim | confidence | why |
|---|---|---|
| The R instrument is contaminated and `expectancyR +0.98` has the wrong sign | **High** | exact reproduction of the endpoint (n/wins/PnL/totalR all match) + a discriminator with perfect separation |
| The published exit-path split understates stop-outs (9 real vs 6 labelled) | **High** | measured against venue candles; matches the mechanism `provenance.py` already documents |
| e35 is not the primary cause of the −$43.12 | **High** | composition, timing and the config diff all point the same way, and none depends on R |
| e35 is harmless | **Cannot say** | n = 2 |
| The market's loss of directionality is the primary cause | **Moderate** | two independent measurements agree (MFE/D 14×, efficiency 4–6×), but see below |

**What I cannot separate.** MFE/D is a joint function of entry quality *and* market
conditions. A world where the market went sideways and a world where the entries silently
got worse both produce collapsed MFE. The regime measurement in § 4.2 is independent of the
trades and shows the market genuinely changed — that is real evidence, and it is why my
confidence is moderate rather than low. But **I have no independent measurement of entry
quality**, so I cannot rule out that entries degraded *as well*. Anyone who tells you this
week was purely regime is over-claiming, and so would I be.

**And n = 13 is 13.** Every per-leg figure in § 2.1 rests on 1–6 trades. The
*aggregate* claims (MFE/D, efficiency, the R decomposition on n=1286) are much better
supported than any per-leg one, and the per-leg table should be read as *where the money
went*, not as *which leg is broken*.

---

## 7. Follow-ups this turned up

**Filed:** `PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG`
(performance backlog). It **extends** the open `PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN`,
which had already root-caused the mechanism to weighting; what is new is the exact
reproduction, the quantified share, and the perfectly-separating discriminator. **Merge and
close the two together** — they are one defect. The fix is a code change outside this
session's write scope: read the declared initial risk from `order_packages.meta` (which
`classify_r` already does), or refuse a wrong-side row instead of `abs()`-ing it, or publish
an `expectancyR` computed over `confirmed_initial` only.

**Not filed, because both already exist and are still open — cited instead:**

- **`BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE`** — the § 1.4 finding. 3 of 13
  post-window rows are mislabelled stop-outs. This is a **re-measurement** of that row with
  venue prices to adjudicate, which it did not previously have.
- **`BL-20260814-TRAINER-CANONICAL-RESOLVER-POINTS-AT-EMPTY-JOURNAL`** (`kept_open`) — #11109
  independently reconfirmed it three weeks on: `/home/ubuntu/ict-trading-bot/trade_journal.db`
  holds **0 `bybit_2` rows** and `/data/bot-data/trade_journal.db` is unopenable from the
  trainer. Anything assuming the trainer can read live trades is wrong today.

**Worth knowing, not a defect:**
- **Bybit is geo-blocked from a Claude sandbox** (HTTP 403). The `trainer-vm-diag` relay is
  the working path for venue market data; #11108/#11110 are the pattern. Note Bybit's kline
  endpoint returns the **newest** ≤1000 bars in `[start,end]` regardless of `start` — page
  **backward** or you silently get only the tail.

---

*Reproducer: `scripts/research/r_contamination_audit.py` (run it against the `rows` arrays from
`/api/bot/db/table/trades` and `/api/bot/db/table/order_packages`). Measured excursion data:
`docs/research/data/mae-mfe-bybit-perp-2026-09-06.txt`. Relay evidence: issues #11107, #11108,
#11109, #11110.*
