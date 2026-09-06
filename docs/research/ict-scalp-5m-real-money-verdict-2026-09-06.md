# `ict_scalp_5m` on real money: the 54% win rate is not the strategy's win rate

**Date:** 2026-09-06 · **Session:** `session_01Ne7LK6wMCoLUwnKpCZTPqE` (sub-session)
**Tier:** 1 (research/docs). The `config/strategies.yaml` change shipped alongside this
document is **Tier-3 and is HELD** — it is for a human to confirm, not for me to land.

> **Bottom line.** The leg does not have a payoff problem *and* a hit-rate problem; it has
> one problem that presents as both. **Only 21 of its 37 real-money closes exited through
> its own declared bracket (`tp` or `sl`). On those it wins 8 of 21 — 38.1% — against a
> declared 1.5:1 payoff that needs 40.0% to break even.** The published 54.1% is reached
> only by counting 16 rows the *reconciler* closed, which are 9-of-11 winners and which the
> repo's own provenance rules say carry an unreliable exit label. So the honest reading is a
> leg sitting a hair under its own breakeven line before costs, with a modelled round-trip
> fee of **$0.29/trade against a mean result of −$0.34/trade** taking it the rest of the way
> down. **My confidence is high** on the decomposition and the fee scale, **moderate** on
> the exit-label split (§ 2.2 states why), and I am explicitly **not** claiming the leg is
> unprofitable in principle — see § 5 for what this cannot establish.

---

## 0. What I measured against, and how

Everything below is measured live on 2026-09-06 from the production journal. Nothing is
inherited from my dispatch; where the dispatch gave me a figure I reproduced it before using
it, and § 1.1 records the one place its population differs from mine.

| source | what | how it was bounded |
|---|---|---|
| `/api/bot/db/table/trades?filter_col=account_id&filter_val=bybit_2` | 1656 rows | **`filter_state: applied`** asserted before use; `total: 1656`, 1656 fetched, paged 500 at a time |
| `config/strategies.yaml`, `config/accounts.yaml`, `config/regime_policy.yaml` | declared geometry + routing | read from the tree at `origin/main` `8a16786d` |
| `src/core/coordinator.py:1346-1362` | the shadow gate's actual mechanism | read, not assumed |
| `docs/research/exit-refinement-coverage.json` | the exit-lever record | read per-lever, incl. each `ref` |
| `docs/research/real-money-0for13-attribution-2026-09-06.md` (`origin/claude/perf-rootcause-1b-20260906`) | the losing-week attribution | cross-checked, see § 1.2 |

`bybit_2` is the only real-money book. **No R multiple appears anywhere in this document** —
the R instrument is sign-inverted and unsafe (`PB-20260906-R-CONTAMINATION-QUANTIFIED`); every
figure here is dollars or a price-measured percentage move.

### 0.1 State the population — the two that are both correct

| population | n | wins | win rate | PnL |
|---|---|---|---|---|
| **A.** closed · non-backtest · `pnl NOT NULL` · minus `superseded`/`orphan_adopt`/`exchange_reset_flat` (what `/api/bot/performance` aggregates) | 35 | 19 | 54.3% | **−$12.80** |
| **B.** closed · non-backtest · `pnl NOT NULL`, no exclusions (the dispatch's basis) | 37 | 20 | 54.1% | **−$12.65** |

The difference is exactly two `superseded` `intent_reduce` rows (ids 1725, 1729, +$1.06 and
−$0.91). **Every conclusion below holds identically on both**, so I quote population **B**
throughout to stay comparable with the dispatch and the 1B report. A further 4 closed rows
are ungraded (`pnl IS NULL` — ids 2765, 2783 `netted_misattributed`; 2060, 1738
`reconciler_incomplete`) and are counted nowhere.

Span: first close 2026-05-26, last close 2026-09-04.

### 0.2 PnL provenance — the payoff finding is not an artifact of manufactured numbers

Graded through `src/runtime/provenance.py::classify_pnl` (imported, not re-derived):

| bucket | source | n |
|---|---|---|
| `measured` | `bybit_closed_pnl` | 31 |
| `measured` | `bybit_closed_pnl_backfill` | 1 |
| `estimated` | `candle_at_close` | 3 |
| `fabricated` | — | **0** |

**32 of 35 rows are broker truth and none are fabricated.** This matters: the whole finding
is a statement about the *sizes* of wins and losses, and it would be worthless if those
dollars were reconstructions. They are not.

---

## 1. The two facts the dispatch gave me, checked

### 1.1 Lifetime — reproduces

37 trades / 54.1% / −$12.65 reproduces **exactly** on population B.

### 1.2 The losing week — reproduces, independently

The six real-money `ict_scalp_5m` closes from 2026-09-01 to 09-04 are ids 5312, 5316, 5369,
5409, 5429, 5450, at −5.5101, −5.5013, −3.1858, −2.7469, −4.2576, −4.0509 = **−$25.2526**,
against the week's −$43.12. **58.6%, confirmed** — arrived at from the journal, not from the
1B report. `git log -- config/strategies.yaml` shows the last commit touching the file is
`892c9a2c` (2026-08-30, the e35 geometry ship) and **`ict_scalp_5m` is not among its 9 legs**;
the block's own last substantive change is 2026-07-20. **Not an e35 casualty — confirmed.**

---

## 2. Why a 54.1%-win leg loses money

### 2.1 The declared geometry and the realised geometry point opposite ways

The leg declares `tp_at_r: 1.5` — a 1.5:1 reward-to-risk bracket, which breaks even at a
**40.0%** win rate. What it actually realises, in dollars:

| | n | gross | mean | median |
|---|---|---|---|---|
| wins | 20 | +$47.46 | **+$2.3732** | +$1.2427 |
| losses | 17 | −$60.11 | **−$3.5360** | −$3.8112 |

**Realised payoff ratio 0.671. Profit factor 0.790. Breakeven win rate required: 59.8%.
Actual: 54.1%.** The declared bracket says it needs 40%; the realised bracket says it needs
60%. That inversion *is* the payoff-geometry problem, and it is not subtle — it is a factor
of 2.23× between the reward:risk the config declares and the one the book delivers.

Note the price-move view does **not** show the same inversion: median favourable move 0.79%
vs median adverse move 0.61%, a ratio of 1.30 — close to the declared 1.5. **The geometry
degrades between the price path and the dollars**, which is what points at the exits rather
than at the entry signal or the stop placement.

### 2.2 The decomposition that explains it: two different books are being averaged

Split by how the trade actually left:

| exit path | n | wins | PnL | mean |
|---|---|---|---|---|
| `sl` | 13 | 1 | **−$45.12** | −$3.47 |
| `tp` | 8 | 8 | **+$22.58** | +$2.82 |
| **the strategy's own bracket** | **21** | **8 (38.1%)** | **−$22.54** | −$1.07 |
| `reconciler_filled` | 11 | 7 | +$4.57 | +$0.42 |
| `netting_attributed` | 3 | 2 | +$0.22 | +$0.07 |
| `tp_cross` | 1 | 1 | +$4.27 | +$4.27 |
| `backfill_closed_pnl_recovery` | 1 | 1 | +$0.83 | +$0.83 |
| **exits the strategy did not choose** | **16 (43%)** | **11 (68.8%)** | **+$9.89** | +$0.62 |

**This is the answer to the question.** The 54.1% headline is an average over two populations
that behave completely differently. On its own declared bracket the leg wins **38.1%** — just
under the 40.0% its own 1.5:1 geometry requires — and loses $22.54. The 16 reconciler-closed
rows win 68.8% but average **+$0.62**, i.e. they are mostly scratches: six of the twenty wins
are under $1.00 (+0.07, +0.09, +0.83, +0.84, +0.94, +0.98) and together carry $3.75.

The shape is a **truncated right tail against a full left tail**. Losses cluster at the stop
(median −$3.81; 14 of the 17 losses sit between −$2.37 and −$5.51, the other three at ≈−$0.90);
wins are bimodal — nine at $2.10 and above carry **80%** of all win dollars, and the other
eleven, all below $2.00, carry the remaining 20%. A strategy that takes its full loss but banks a large share of its
winners early cannot make a 1.5:1 bracket pay, however often it is right.

⚠️ **The confidence caveat, stated rather than buried.** This split is taken from
`exit_reason`, and the 1B report measured that label against Bybit's own candles and found
**91 of 155 (58.7%)** `reconciler_filled` closes had in fact reached a declared bracket level
— the label is frozen before the answer is knowable and no writer re-runs it. So the true
`sl`/`tp` count is probably **higher** than 21 and the reconciler bucket smaller. That
correction moves the finding in the **same** direction (more of the book is the strategy's own
under-water bracket, less is scratch noise), which is why I report it as moderate-confidence
rather than withdrawing it. It does mean **the exact 38.1% should not be quoted as precise.**

### 2.3 Costs take what is left

All 37 rows carry `cost_source: 'estimate'` — so `fee_taker_usd` is a **modelled** round-trip
fee (`src/runtime/trade_costs.py`), not broker truth, and I do not treat it as measured.

| | |
|---|---|
| modelled round-trip fee, total | **$10.67** |
| per trade | **$0.2885** |
| as a share of median notional ($374.02) | 0.075% |
| median adverse price move (≈ the stop distance) | 0.61% |
| **fee as a share of the risk taken per trade** | **≈ 12%** |
| mean per-trade result | **−$0.34** |

**The modelled fee is 85% of the average per-trade loss.** That is the Phase-0 finding
(*"real structural issue = fee load"*, `docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md`)
reproducing on live real-money data 14 months later, on a leg whose fix for it was a regime
gate rather than a cost reduction. A 5-minute scalp risking 0.61% per trade and paying 0.075%
round-trip is spending an eighth of its risk budget on entry and exit before the market moves.

I deliberately do **not** publish a "gross before fees" number. It would require assuming
Bybit's `closedPnl` is net of fees *and* that the modelled estimate matches what was actually
charged; the first is asserted in one docstring (`order_monitor.py:10466`, "fee-accurate")
and the second is a model. The ratios above hold without either assumption.

---

## 3. Where is the edge written, and was the bracket ever backtested?

### 3.1 The edge is written down, and its evidence is a 2026-05 pre-live gate

`config/strategies.yaml` documents the ruleset (liquidity sweep → displacement → FVG
wick-rejection → 1h EMA-20 HTF bias) and points at `src/units/strategies/ict_scalp.py` and
`docs/strategies/ict_scalp_5m.md`. The stated pre-live evidence is *"59.3% win rate, +0.301 R
expectancy, max DD 3.47R on 90 days of fresh BTCUSDT 5m candles"* (issues #1153/#1154,
PR #1156, 2026-05-14). Two observations:

1. **That gate is quoted in R**, on a 90-day window, and R is the instrument now known to be
   sign-inverted. I am not claiming the gate was wrong — it long predates the contamination
   and may well be clean — but **it has not been re-validated on a sound instrument**, and it
   is the only pre-live evidence the block cites.
2. Its **59.3%** projected win rate is almost exactly the **59.8%** breakeven this leg turns
   out to need. The live book came in at 54.1%. So the leg is not behaving wildly differently
   from its gate — it is missing it by ~5 points, on a geometry with no margin for that.

### 3.2 The bracket geometry has never been swept — and that is the one lever that is not blocked

From `docs/research/exit-refinement-coverage.json`, per lever, with each `ref` read rather than
the status alone:

| lever | status | what the `ref` actually says |
|---|---|---|
| `bracket_geometry` | **`pending`** | *"Not swept in the 2026-08-20 run (scope was the trend/pullback/squeeze fleet). Crypto — the free `data.binance.vision` lane DOES cover these, so this is genuinely pending, not blocked."* |
| `stale_stop` | `honest_negative` | swept 2026-07-28, GH run 30384837775 — real negative |
| `giveback_stop` | `honest_negative` | same sweep |
| `exit_ladder` | `honest_negative` | measured 2026-08-10, GH run 31344328313 |
| `exit_head_ml` | `blocked:no_lever_consumer_in_unit` | E1 **candidate**: auc 0.6038, beats_actual 12/12, n_oos 600 — awaiting E3 ship (Tier-3) |
| `trail_geometry` / `trail_decay` / `vol_trail` | `n/a` | all presuppose a primary trailing stop; `ict_scalp.monitor()` only trails to break-even after 1R |
| `regime_flip_exit` | `n/a` | see § 4 — **this reason is factually wrong** |

⚠️ **I am correcting my own dispatch here.** It told me these levers "all read `n/a`" and that
the leg has *"ZERO shipped exit levers … it has been trading a static bracket that no backtest
ever tested."* The conclusion is right; the reasoning is not, and the difference changes what
to do next. Three levers were genuinely swept and honestly failed. Four are **inapplicable by
construction**, not neglected — you cannot reshape a trailing stop on a strategy that has none.
One is a live candidate awaiting a Tier-3 ship. **Exactly one lever is both unrun and
applicable: `bracket_geometry` — and it is the one that directly addresses the payoff
inversion measured in § 2.1.** Reading all seven as neglect would spread effort across six
dead ends and miss the one that is loaded and pointed at the actual defect.

**So: was the bracket geometry ever backtested? The `tp_at_r: 1.5` / `atr_sl_buffer_mult: 0.20`
pair was validated once, in the 2026-05 pre-live gate, in R, and has never been *swept* — no
grid over tp/sl has ever been run for this leg.** It is not that no backtest exists; it is that
no backtest has ever asked whether 1.5 is the right number.

---

## 4. Two defects found on the way, filed not swept under the rug

**(a) `exit-refinement-coverage.json` gives a false reason for `regime_flip_exit: n/a`.** It
reads *"ict_scalp has no regime-policy cell so the flip never fires."* But
`config/regime_policy.yaml` lines **132 and 145** declare two `ict_scalp_5m` OFF cells
(`chop/volatile` and `trending/volatile`), operator-approved 2026-07-20 — and
`config/strategies.yaml`'s own `execution:` comment cites them as the leg's primary risk
control. **Field beats comment: the cells exist.** The `n/a` verdict may still be correct for
another reason, but its stated basis names a condition that is false, which is
UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A per `CLAUDE.md`. Filed to the health-review
backlog; not fixed here, because changing a lever verdict is a research call and this PR is
already a held Tier-3 change.

**(b) `execution: shadow` is a per-STRATEGY gate, not per-account — it stops three books, not
one.** `ict_scalp_5m` is routed to `bybit_2` (real money), `bybit_1` (paper) and
`bybit_portfolio` (paper). `coordinator.py:1346-1362` folds `execution: shadow` into
`effective_dry` for **every** account, so the flip also stops the two paper books executing.
Order packages are still written everywhere (that is the point of shadow — live data
collection continues), but **no new paper *fills* will accrue.** If the intent is "stop the
real-money bleed, keep accruing paper evidence", the narrower lever is removing
`ict_scalp_5m` from `bybit_2`'s `strategies:` list in `config/accounts.yaml`. I implemented
`execution: shadow` because that is what I was asked for and it is the stronger stop; the
choice belongs to the operator and is called out in the PR.

---

## 5. Verdict, and what it cannot establish

**What would have to be true to put this back on live money.** Not one of these is a
judgement call; each is a measurement that does not currently exist:

1. **A `bracket_geometry` sweep on `data.binance.vision` crypto data** showing a tp/sl pair
   whose realised payoff clears its own breakeven at the win rate the leg actually achieves —
   with in-sample and out-of-sample windows, and graded in dollars, not R.
2. **A cost-aware net figure.** At 0.075% round-trip against a 0.61% stop, the fee is ~12% of
   risk per trade. Any candidate geometry must clear breakeven *after* that, and a wider stop
   helps this ratio while a tighter one makes it worse — which is a real constraint on (1),
   not a free parameter.
3. **The exit-label question resolved**, so the `tp`/`sl` split can be graded from price
   rather than from a label the repo knows is wrong 58.7% of the time. Until then the 38.1%
   is directional, not exact.
4. **The pre-live gate re-run on a sound instrument** — its +0.301R is the leg's only
   entry-side evidence and R is currently sign-inverted.

**What this document explicitly does not establish, and I will not let it be read as though
it does:**

- **It does not show the entry signal has no edge.** Median favourable excursion (0.79%)
  exceeds median adverse (0.61%) — the entries do go the right way more than they go the
  wrong way. The failure is in converting that into dollars. A verdict on the entry needs the
  MFE/MAE work the 1B report started, not this.
- **n = 37 lifetime and n = 6 for the losing week are small.** Per my hard limits I have
  **authored no regime gate, no stop change and no parameter tweak**, and none should be
  authored from this. The 58.6% concentration is a real attribution; it is not a mandate to
  retune anything.
- **It does not establish that the market regime is the cause**, only that the leg's payoff
  geometry is under water independently of regime — the lifetime figure spans 3.3 months and
  is negative across it, not just in the last week.
- **It cannot say whether shadow will fix anything**, because shadow does not change the
  strategy — it stops it paying for the answer. The accrual it buys is only useful if
  (1)–(4) get done.

**Recommendation (a proposal, not an action).** Keep the leg in shadow. Run the
`bracket_geometry` sweep — it is unblocked, it is free, and it is aimed exactly at the
measured defect. Do not re-promote on the strength of a better week.
