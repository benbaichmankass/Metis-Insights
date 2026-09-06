# ML-2, the predictive bracket — the corpus, the instrument, and what would have to be true

**MI-151** · work object [`WO-20260906-ML-2-THE-PREDICTIVE-BRACKET`](../claude/work/objects/WO-20260906-ML-2-THE-PREDICTIVE-BRACKET.yaml) · branch `claude/ml2-predictive-bracket-20260906` · PR #11150

⚠️ **PROPOSE-ONLY.** Per-leg take-profit values are Tier-3. Nothing in `config/strategies.yaml` is touched, no target value is proposed as a number, and nothing is armed. What ships is Tier-1 and observe-only: a model, a corpus builder, an eval harness, 40 tests, and this memo.

---

## The operator decision this serves

Asked directly what to do about the 25 targetless legs, the operator answered **"Build ML-2, the predictive bracket"** (2026-09-06). The framing is the spec:

> *"the brackets need to be the prediction of where we think the price is gonna go… if we see that we're getting close to the take profit but the momentum is still strong, then we can adjust that during the trade. But there shouldn't be, like, an endless bracket."*

[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md) § E-ML names ML-2 and § E3.6 states the falsifier:

> *"a predictive bracket is a **claim about where the trade will exit**, so it is graded against realised exits — calibration first (does the stated expectation match the observed distribution?), P&L second. A bracket that improves net R while being systematically wrong about **where** trades exit has not met this bar."*

---

## The short version

| question | answer |
|---|---|
| What corpus? | The **offline harness over the trainer's historical candle store** — the unit MI-148 named and left unclaimed. **Built and run: 9,814 trades / 3 legs / 2021–2026**, from 166k–184k 15m bars per symbol. |
| Do backtest and live exit locations agree? | **NOT ESTABLISHED, and this is a finding rather than a caveat.** The instrument that would answer the nearest question (`backtest_fidelity_calibrate.py`) **has never been run** — and it grades the wrong quantity anyway. |
| The calibration read? | **Calibration PASSES** (n=9,814, MACE 0.0061). **Sharpness FAILS against the `tp_r`-equivalent baseline: 0 of 5 quantiles.** § 4. |
| So is ML-2 the answer? | **No — it is REFUTED, and that is the deliverable.** The six decision-time features add nothing over a fixed multiple of risk. The answer is a per-leg MFE quantile **in R**, which is MI-148's proposal unchanged. § 4. |
| The gate before any target changes? | § "The gate", five conditions — and condition 2 is now **settled negative**, which changes what the remaining work is. |

---

## 1. The corpus — stated, and why it is this one

### In-sandbox it is impossible, and that was MEASURED, not assumed

| probe | result |
|---|---|
| `https://api.bybit.com/v5/market/kline` | **403** at the proxy |
| `https://ict-bot.duckdns.org/api/health` | 200 (live VM reachable — but it is the *live* population, n=1–8/leg) |
| `data/ohlcv/*.csv` (5 files) | **exactly ONE unique close each** — flat fixtures, not candles |
| `data/btc_1m_sample.csv` | real, 10,080 rows = **7 days** |
| `data/backtest_candles.csv` | real, 5,000 rows = **3.5 days**, from 2022 |

The `data/ohlcv/*.csv` line is the one worth carrying: those files are named like candles and are constant-price test fixtures (`btc_5m_2026.csv` opens and closes at 95000.0 for all 300 rows). Eyeballing the head of the file does not reveal it; `len(set(closes)) == 1` does. **A corpus built on them would have produced a confident, fully-calibrated, entirely meaningless model.**

### The un-blocked route is the trainer, and it holds a real corpus

MEASURED via `trainer-vm-diag` #11146 (`ls -la` on the trainer's `data/`, read 2026-09-06):

| symbol | rows (15m) | ≈ span |
|---|---:|---|
| `ETHUSDT` | 184,443 | ~5.3 y |
| `ADAUSDT` | 184,126 | ~5.3 y |
| `BTCUSDT` | 175,297 | ~5.0 y |
| `BNBUSDT` | 174,239 | ~5.0 y |
| `AVAXUSDT` | 166,748 | ~4.8 y |

plus 5m siblings, `GLD`/`IEF`/`IWM`/`GDX`/`IAUM`/`DBA`/`DBC` 1d, `GC_F`/`HG_F`/`ES_F` 1d+1h, and `BTCUSDT-5m-YYYY-MM` monthlies back to 2023-01.

**This is what MI-148 gated clause 1 on**, in its own words: *"an MFE distribution at proper n … the un-circular source is the offline harness over historical candles, which reads no live broker state and is not blocked on anything. That is the next unit of work."*

### The conversion, and the one row shape that cannot be converted

The harness emit carries `entry`, `sl`, `direction`, `confidence`, `gross_r`, `mfe_r` — but **not `exit_price`**. It is exactly recoverable, because `risk = |entry − sl|`:

```
risk_frac = |entry − sl| / entry
mfe_frac  = mfe_r   × risk_frac        # percent-of-entry
exit_frac = gross_r × risk_frac
```

⚠️ **Except under the M20 bank lever.** `scripts/backtest_trend.py` blends the exit R *after* computing it —

```python
if banked:
    r = bank_frac * bank_at_r + (1.0 - bank_frac) * r
```

— so `gross_r` becomes a **blend of two fills** and inverting it would manufacture an exit location that never existed. **The emit carries no bank flag**, so a row cannot self-diagnose. The builder therefore takes `--bank-frac-asserted` and REFUSES the file rather than inverting. Three states, never collapsed: `exact` / `blended_unrecoverable` / `unreadable`. `mfe_frac` survives a banked run because MFE is a path statistic computed before the blend; `exit_frac` does not. Reported separately for exactly that reason.

### The basis is percent-of-entry — and one correction to MI-148, in its favour

Inherited, not re-argued. But MI-148's *stated reason* is worth correcting because it under-sells the backtest corpus: it avoids R because the R denominator is contaminated (`trades.stop_loss` is the FINAL trailed stop). **That is a property of the live journal, not of R.** In the harness `Trade.sl` is the entry bar's stop and no lever overwrites it, so the backtest corpus can express **both** bases exactly and the builder emits both. The percent-of-entry columns remain the ones the grader uses, so the two instruments still speak one language.

### Features are decision-time and EXOGENOUS, asserted by arithmetic

§ 0.2 names the root cause of every negative exit result to date: all **11 of 11** features every exit study learns from are ENDOGENOUS, so *"no lever beats holding"* only ever meant *"no function of these eleven inputs beats holding"*. ML-2's feature list is closed and entry-bar only — `risk_frac`, `is_long`, `confidence`, `hour_sin`, `hour_cos`, `dow` — and `test_no_outcome_column_is_a_feature` asserts `set(FEATURE_NAMES) & set(OUTCOME_NAMES) == ∅` rather than leaving it to review.

⚠️ **This is a deliberately thin exogenous set and it is not the one § 0.2 asks for.** § 0.2's complaint is that nothing about *the rest of the market* is visible — peer symbols, regime, session, book state. Of those, only session is here. **So a negative sharpness result from this feature set is weaker evidence than it looks**: it says these six entry-bar quantities carry no information about exit location, not that no exogenous feature does. The cross-asset block (`ml/datasets/cross_asset_features.py`, 16 columns) and the regime heads are the obvious next inputs and are **not** wired in here.

---

## 2. ⚠️ Do backtest exit locations match live ones? NOT ESTABLISHED

**The brief asked this as a first-class question and the answer is no, with two distinct reasons.**

**(a) The nearest existing instrument has never been run.** `scripts/research/backtest_fidelity_calibrate.py` calls itself *"the earned-trust linchpin (P0)"* and its docstring names its output convention as `comms/research/backtest_fidelity_<strategy>_<symbol>.json`. **No such file exists anywhere in the repo.**

*Positive control for that negative* (a search returning nothing is not proof of absence): `comms/research/` **does** exist and **does** hold one artifact (`crypto_correlation_2026-08-18.json`), and a whole-tree `find` for `*fidelity*` returns only source and test files — never an output. So the directory is real and writable, and the calibrator has produced nothing in it.

**(b) Even once run, it grades the wrong quantity for this question.** It measures agreement on **win-rate and the realised-R distribution** — an OUTCOME agreement. ML-2's claim is about **exit LOCATION in percent-of-entry**. Those can diverge: two books can share a win rate and an R distribution while exiting at systematically different distances, and the R axis carries the live journal's contaminated denominator into the comparison.

**What would settle it** is a two-sample comparison of the *exit-location* distribution (percent-of-entry) between the backtest emit and MI-148's live `--exits` view, per leg, with an abstain floor. Both halves now exist on one basis — this session built the backtest half and MI-148 built the live half — so it is **constructible and unblocked**, and it is the single highest-value next unit. It is **not** claimed as done here.

**Consequence for everything below:** every number from the backtest corpus is a statement about the harness's book. Whether the fleet's live trades exit in the same places is an open question, and the gate in § 4 treats it as one.

---

## 3. The instrument, and the trap it is built around

### Calibration alone is vacuous

**The unconditional empirical quantile is calibrated BY CONSTRUCTION.** A model that ignores every feature and reads one number off the leg's own MFE histogram passes E3.6's falsifier exactly. So there are **two bars, and neither substitutes for the other**:

1. **Calibration** — coverage matches the stated quantile. Necessary; trivially met by the baseline.
2. **Sharpness** — out-of-sample pinball loss beats the **unconditional** quantile. The only bar that says the features know anything about *this* trade.

`grade_model` reports both and refuses to collapse them. **A `calibrated` + `no_better_than_baseline` pair is a RESULT**: it says the per-leg MFE histogram is the whole answer and no model is needed — which is what MI-148 already proposed.

### Three defects, all found by RUNNING the controls rather than reviewing them

| # | defect | evidence |
|---|---|---|
| 1 | the optimiser **destroyed calibration it was handed for free** — step size not scaled to a target living at ~0.01–0.10 | **MACE 0.179 on PURE NOISE**, where the baseline is calibrated by construction. Fixed: target standardised, Robbins–Monro 1/√t step. |
| 2 | a plain `model < baseline` test scores **estimator efficiency** as a win | on no-signal data the fit still beat the baseline by **+0.006…+0.027 at every quantile**. The SGD intercept is a shrunk, lower-variance estimator of the same quantity. Fixed: the shuffled-label null now **gates** the verdict, per quantile (the pinball loss is asymmetric, so one null cannot gate five). |
| 3 | **the selftest graded ONE draw and was wrong** | it reported the no-signal arm as **5/5 — a complete false positive** — because that seed landed in the null's tail. Across 6 seeds the same arm gives **[0, 1, 0, 2, 0, 1] of 5**. |

Defect 3 is precisely the failure `scripts/research/e2_null_calibration.py` was written for, in its own words: *"a single run yields a single Bernoulli draw and cannot tell 5% bad luck from a broken null."* **A control is validated by its RATE.** The selftest now asserts one.

### The validated instrument

POPULATION: 6 independent seeds × n=500 synthetic rows, 5 quantiles (0.5–0.9), majority rule ≥3 of 5, `control_trials=8`.

```
SIGNAL     fires 6/6  (TPR 1.00)   sharp counts [5, 5, 5, 5, 5, 5]
NO-SIGNAL  fires 0/6  (FPR 0.00)   sharp counts [0, 0, 0, 0, 1, 0]
```

The signal arm's improvements run **0.56–0.82** against a null p95 of **0.04–0.14** — 8–20×. The gap between the two arms' sharp counts (5 vs ≤1) is where the majority rule sits, so the rule is not tuned to the boundary.

⚠️ **This validates the ESTIMATOR, not any market claim.** The synthetic signal is a linear dependence the model is specified to find. It says the harness can tell signal from noise; it says nothing about whether exit location is predictable.

---

## 4. The calibration read

**MEASURED** via `trainer-vm-diag` [#11154](https://github.com/benbaichmankass/Metis-Insights/issues/11154) (run `34041656868`, 2026-09-06), branch head `bba1defc`. Reproducible: `scripts/backtest_trend.py --emit-trades` over the trainer's `data/{BTC,ETH,ADA}USDT_15m.csv` → `ml2_bracket_corpus.py` → `ml2_bracket_train_eval.py`.

**POPULATION: 9,814 backtest trades across 3 `trend_donchian` 15m legs (BTCUSDT 3,194 · ETHUSDT 3,238 · ADAUSDT 3,382), entries 2021-07 → 2026, chronological split train 6,379 / eval 3,435. Zero rows dropped for a missing feature or outcome; `mfe_exact` 9,814 of 9,814; `malformed_lines_total` 0.** Outcome `mfe_frac`, basis percent-of-entry.

### Calibration — it passes, and that is the *weak* half

| target q | coverage | \|err\| |
|---:|---:|---:|
| 0.50 | 0.5141 | 0.0141 |
| 0.60 | 0.6099 | 0.0099 |
| 0.70 | 0.7048 | 0.0048 |
| 0.80 | 0.8000 | 0.0000 |
| 0.90 | 0.9019 | 0.0019 |

**MACE 0.0061.** So a stated q-quantile is reached (1−q) of the time, to within 1.4 points at worst. **E3.6's falsifier is cleared on this corpus** — and per § 3 that is, on its own, nearly worthless: the unconditional quantile clears it by construction.

### Sharpness — ⚠️ **ML-2 IS REFUTED, and this is the headline finding**

**MEASURED** via `trainer-vm-diag` [#11156](https://github.com/benbaichmankass/Metis-Insights/issues/11156) (run `34042377609`), branch head `b8a1aa54`, same corpus (n=9,814, byte-identical trade counts 3194/3238/3382).

| q | model | POOLED base | vs pooled | **RISK-SCALED base** | **vs risk-scaled** | null p95 | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0.50 | 0.008066 | 0.008542 | +0.0557 | 0.008055 | **−0.0014** | 0.0110 | `no_better_than_baseline` |
| 0.60 | 0.008623 | 0.009353 | +0.0781 | 0.008608 | **−0.0017** | 0.0180 | `no_better_than_baseline` |
| 0.70 | 0.008564 | 0.009593 | +0.1072 | 0.008565 | **+0.0001** | 0.0189 | `beats_baseline_within_null` |
| 0.80 | 0.007760 | 0.008944 | +0.1324 | 0.007751 | **−0.0011** | 0.0209 | `no_better_than_baseline` |
| 0.90 | 0.005585 | 0.006845 | +0.1840 | 0.005612 | **+0.0048** | 0.0233 | `beats_baseline_within_null` |

**Beats POOLED at 5 of 5. Beats RISK-SCALED at 0 of 5.**

## ⚠️ VERDICT: `calibrated_but_no_sharper_than_baseline`

**The entire apparent win was the volatility confound.** Against a baseline that already scales with risk — the `tp_r` equivalent — the model's improvement is **−0.0017 to +0.0048**, i.e. indistinguishable from zero and inside the shuffled-label null at every quantile where it is positive at all.

Stable and unambiguous: **`split_sensitive: False`** across splits 0.55 / 0.65 / 0.75, **`arms_consistent_with_headline: True`** (asserted, at matched control settings), and **all three legs individually** return the same verdict at n≈3.2k each (MACE 0.0102 / 0.0143 / 0.0182).

**What this says, stated plainly:**

1. **The six decision-time features carry no information about exit location beyond volatility.** Direction, conviction and session add nothing a fixed multiple of risk does not already have.
2. **`tp_r` is the right functional form and always was.** A target that is a multiple of risk is, on this evidence, as good as anything ML-2 can learn. **What is broken is not the absence of a model — it is `TP_VENUE_CAP_PCT` converting that multiple-of-risk into a fixed percent-of-entry and destroying the scaling.**
3. **The honest conclusion is MI-148's proposal, unchanged: a per-leg MFE quantile in R. No model required.** The table below is that quantity.

**This is E3.6's falsifier doing its job, and the result is more useful than a pass would have been.** Had ML-2 been graded only against the pooled baseline it would have reported *"5 of 5 quantiles, 3–8× the null"* and licensed a model the fleet does not need — and the numbers to do exactly that are already published in [#11154](https://github.com/benbaichmankass/Metis-Insights/issues/11154). ⚠️ **Do not quote that run's sharpness figures. They are against the wrong baseline and they are superseded by this table.**

⚠️ **What this does NOT establish.** It refutes *these six features on this corpus*, not the existence of exogenous information: § 0.2's complaint is that peer symbols, regime and book state are invisible, and **none of those is in this feature set**. A cross-asset or regime-conditioned re-run is a different experiment and this result does not pre-empt it — it does, however, mean the burden is on that experiment to beat the risk-scaled baseline, which this one did not.

### The per-leg MFE distribution — the numbers MI-148 could not compute

**POPULATION: the same 9,814 rows; per-leg n stated in the table. Backtest, `trend_donchian` 15m only.** Percent-of-entry.

| leg | n | p50 | p70 | p80 | p90 | p95 | reached 9.9% |
|---|---:|---:|---:|---:|---:|---:|---:|
| `trend_donchian_BTCUSDT_15m` | 3,194 | 0.75% | 1.58% | 2.36% | **3.87%** | 5.40% | **0.66%** |
| `trend_donchian_ETHUSDT_15m` | 3,238 | 1.13% | 2.39% | 3.51% | **5.62%** | 7.66% | **2.75%** |
| `trend_donchian_ADAUSDT_15m` | 3,382 | 1.38% | 2.87% | 4.19% | **6.84%** | 9.44% | **4.58%** |

### ⚠️ This CONTRADICTS MI-148's crypto arm, and the disagreement is itself the finding

MI-148 measured live crypto MFE **p90 = 9.70%** (n=63) and concluded the 9.9% venue cap was *"by accident, about right"* on Bybit-traded crypto — a ratio of ~1.0×. **The backtest says BTC p90 is 3.87% (n=3,194): a ratio of 2.6×, i.e. the cap is far too loose there too.**

State the populations, because they are not the same: MI-148's is **live** `position_telemetry`, n=63, mixed symbols and strategies, with `peak_r_is_lower_bound: True` on every row — so its p90 is a *lower bound* and the true figure can only be **higher**, widening the gap rather than closing it. Mine is **backtest**, n=3,194, one strategy at one timeframe, and includes many trades stopped out quickly.

**Neither is obviously the right number, and that is precisely the point:** two populations disagree by ~2.5× about the quantity a target would be set from. This is § 2's fidelity question arriving with a magnitude attached, and it is why fidelity is **condition 1** of the gate rather than a footnote. Do not set a target from either figure until they are reconciled.

What both agree on: **the declared 9.9% target is reached by essentially nobody** — 0.66% of BTC trades and 4.58% of ADA trades in the backtest, 4 of 102 (3.9%) live. The fleet's targets are not predictions that are sometimes wrong; they are levels almost nothing reaches.

---

## 5. The gate — what would have to be true before any leg's target changes

Per the brief: *"the output is a proposal with its gate."* **No per-leg target number is proposed here.** Five conditions, in order, and the first two are the ones this session did not clear:

1. **Backtest↔live exit-location fidelity is MEASURED, per leg, on the percent-of-entry basis** — § 2. Until then a backtest MFE quantile is a statement about the harness's book, not about what the fleet will do. **This is no longer a theoretical worry: § 4 measured the two populations disagreeing by ~2.5× about crypto p90 MFE** (backtest BTC 3.87% at n=3,194 vs MI-148's live 9.70% at n=63), which is exactly the quantity a target would be set from. An abstain floor, and a leg that fails it gets no target from this route.
2. ~~**A model clears E3.6's falsifier**~~ — **SETTLED NEGATIVE, § 4.** Calibration passed (MACE 0.0061, n=9,814); sharpness against the `tp_r`-equivalent baseline failed at **0 of 5** quantiles, stable across splits and identical on all three legs. **So this condition is not "not yet met" — it is answered, and the answer removes the model from the path.** What replaces it: the target is set from the **per-leg MFE quantile in R** (table below), and the gate on THAT is condition 1 plus condition 3, not a model.
3. **Per-leg n is stated with the proposal and clears the eval floor.** MI-148 refused per-leg values at n=1–8; the backtest corpus fixes availability, not the discipline. A leg that does not reach the floor gets `insufficient_n` and no number — and `insufficient_n` is neither calibrated nor miscalibrated.
4. **The target is constructed per-leg and conditioned on the family's thesis** (E3.6(4)) — donchian: is the channel still being pushed; pullback: does ADX still clear its declared `adx_min`. ⚠️ **Never a lowered `tp_r`.** `tp_venue_cap.py` states that *no `tp_r` reproduces the clamp* — `cap_r = TP_VENUE_CAP_PCT × entry / risk` is a percent-of-entry against a multiple-of-risk, so an "equivalent" figure tightens the real target on every trade the clamp never bound.
5. **`ict_scalp` is exempt and stays exempt.** It is the fleet's existence proof — fixed `tp_at_r: 1.5`, zero clamp-binding, targets at quantile 0.69–0.92 of its own outcomes (MI-148, n=162 over 8 legs). MI-146 and MI-148 both flagged the inversion. **Do not "harmonise" the one calibrated family into the sentinel idiom.**

And one that is not a gate but a warning: **a leg that genuinely wants no target must DECLARE that** with an explicit key rather than carry a 50R sentinel. Today a decision and its absence are byte-identical.

---

## 6. Honest limits

- **The corpus is backtest-only, and its book LOSES money in almost every year** (§ 4). Calibration is a statement about where price went, not about profitability; § 2's fidelity question is open and named as the next unit of work.
- **The feature set is thin and is not the one § 0.2 asks for** — six entry-bar quantities; no peer symbols, no regime, no book state. **This is the single most important caveat on the refutation**: it says these six carry nothing beyond volatility, NOT that no exogenous feature does. A cross-asset or regime-conditioned re-run is a different experiment, and its bar is now the risk-scaled baseline.
- **The model is linear on purpose** — and ⚠️ **its stated reason is now partly stale, so do not re-quote the old one.** It was chosen when per-leg n was expected to be "in the low hundreds at best"; the backtest corpus actually delivers **~3,200 rows per leg** (§ 4), which would support more capacity. The reason that survives is different and weaker: the corpus is one family at one timeframe on a net-negative book, and the features are the thin set above, so added capacity would fit that book's idiosyncrasies rather than the fleet's. **Revisit the model class once the fidelity gate is measured**, not before.
- **`trend_donchian` only.** The harness run covers one family. `ict_scalp` (exempt anyway) and the pullback family are not in the corpus.
- **No P&L claim is made anywhere in this memo**, per E3.6's ordering.
- **40 tests pin the distinctions, not the numbers** — so this memo's figures are not load-bearing for the suite, and re-running on a larger corpus does not require rewriting a test to match.

*Repo `origin/main` 817a5a5f→c641c563. Trainer measurements via `trainer-vm-diag` #11146 (candle inventory), #11149 (venv finding), **#11154 (the calibration read, run `34041656868`)** and #11156 (the risk-scaled re-grade); run ids are in the issue comments. Instrument validation is reproducible offline with no numeric stack: `python3 scripts/research/ml2_bracket_train_eval.py --selftest`.*
