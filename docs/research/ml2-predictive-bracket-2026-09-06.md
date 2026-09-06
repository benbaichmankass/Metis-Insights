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
| What corpus? | The **offline harness over the trainer's historical candle store** — the unit MI-148 named and left unclaimed. 166k–184k 15m bars per symbol. |
| Do backtest and live exit locations agree? | **NOT ESTABLISHED, and this is a finding rather than a caveat.** The instrument that would answer the nearest question (`backtest_fidelity_calibrate.py`) **has never been run** — and it grades the wrong quantity anyway. |
| The calibration read? | § "The calibration read" below. |
| The gate before any target changes? | § "The gate", five conditions. |

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

⚠️ **NOT OBTAINED IN THIS SESSION. Stated plainly rather than deferred into a
vaguer claim, because "the read is pending" and "the read came back negative"
are different facts and only one of them is knowable right now.**

Three trainer-relay runs were dispatched (`trainer-vm-diag` #11146, #11149,
#11154). What each established:

| run | outcome |
|---|---|
| **#11146** | ✅ the candle inventory above. Its harness step died on a nested-heredoc quoting error in my own command. |
| **#11149** | ✅ established that the trainer's **system `python3` has no pandas** — the harness needs the venv. My stdlib-only scripts ran there **unmodified**, which is the payoff of matching `bracket_calibration.py`'s no-numpy discipline. |
| **#11154** | dispatched with the venv fix and the bounded fit; **had not returned when this session closed.** |

**Why it did not return, measured rather than guessed:** the trainer is a
**1-OCPU** box and #11149 was still executing when #11154 started, so two
harness passes over 175k–184k 15m bars were contending for one core.

⚠️ **One cost of that is mine, not the box's, and it is worth recording because
it is a design lesson.** The first cut of the model ran a **fixed 300 epochs**,
and one evaluation fits 5 quantiles × (1 real + K control) models — with the
dispersion and per-leg passes multiplying that again, roughly **50M pure-Python
updates on a 3k-row corpus**. That is why #11149 was still going at 20 minutes.
It is now bounded by a total-update budget (`MAX_FIT_UPDATES`), which makes one
evaluation **~9 s regardless of n**, and the cap costs no accuracy: at the cap
(40 epochs, n=4000) the model still recovers a known conditional median to
**1.007** against a true 1.0 and **1.949** against a true 2.0. **A model that
cannot be run is not a conservative model, it is an absent one.**

**What this means for the rest of the memo — and what it does NOT mean.** The
corpus (§ 1), the fidelity finding (§ 2), the instrument and its validation
(§ 3), and the gate (§ 5) do not depend on this read: § 1 and § 2 are
measurements of the repo and the trainer, and § 3 is a property of the
estimator. What is **not** established is any statement about whether exit
location is actually predictable on this fleet.

**So no verdict is claimed here, in either direction.** In particular the
absence of a result is **not** evidence for the negative — `insufficient_n` and
`not_measured` are distinct from `miscalibrated` throughout this instrument
precisely so that a missing read cannot be quoted as a finding.

**To obtain it**, one relay dispatch, unblocked and needing no operator action —
its exact command is in issue #11154 and it re-runs against
`claude/ml2-predictive-bracket-20260906`.

---

## 5. The gate — what would have to be true before any leg's target changes

Per the brief: *"the output is a proposal with its gate."* **No per-leg target number is proposed here.** Five conditions, in order, and the first two are the ones this session did not clear:

1. **Backtest↔live exit-location fidelity is MEASURED, per leg, on the percent-of-entry basis** — § 2. Until then a backtest MFE quantile is a statement about the harness's book, not about what the fleet will do. An abstain floor, and a leg that fails it gets no target from this route.
2. **The calibration read clears E3.6's falsifier on the real corpus** — calibrated AND sharper than the unconditional quantile, with the shuffled-label null gating it and the split-dispersion arm agreeing (§ E4: `split_sensitive` is a **refusal, not a caveat**).
3. **Per-leg n is stated with the proposal and clears the eval floor.** MI-148 refused per-leg values at n=1–8; the backtest corpus fixes availability, not the discipline. A leg that does not reach the floor gets `insufficient_n` and no number — and `insufficient_n` is neither calibrated nor miscalibrated.
4. **The target is constructed per-leg and conditioned on the family's thesis** (E3.6(4)) — donchian: is the channel still being pushed; pullback: does ADX still clear its declared `adx_min`. ⚠️ **Never a lowered `tp_r`.** `tp_venue_cap.py` states that *no `tp_r` reproduces the clamp* — `cap_r = TP_VENUE_CAP_PCT × entry / risk` is a percent-of-entry against a multiple-of-risk, so an "equivalent" figure tightens the real target on every trade the clamp never bound.
5. **`ict_scalp` is exempt and stays exempt.** It is the fleet's existence proof — fixed `tp_at_r: 1.5`, zero clamp-binding, targets at quantile 0.69–0.92 of its own outcomes (MI-148, n=162 over 8 legs). MI-146 and MI-148 both flagged the inversion. **Do not "harmonise" the one calibrated family into the sentinel idiom.**

And one that is not a gate but a warning: **a leg that genuinely wants no target must DECLARE that** with an explicit key rather than carry a 50R sentinel. Today a decision and its absence are byte-identical.

---

## 6. Honest limits

- **The corpus is backtest-only.** § 2 — the fidelity question is open and named as the next unit of work.
- **The feature set is thin and is not the one § 0.2 asks for.** Six entry-bar quantities; no peer symbols, no regime, no book state. A negative sharpness result here is weaker than it looks.
- **The model is linear on purpose.** Per-leg n is in the low hundreds at best; a gradient-boosted model on that sample fits noise and produces a confident wrong level on a path that ends at a live order. Capacity exceeding corpus is the fitted-threshold failure this repo already pays for.
- **`trend_donchian` only.** The harness run covers one family. `ict_scalp` (exempt anyway) and the pullback family are not in the corpus.
- **No P&L claim is made anywhere in this memo**, per E3.6's ordering.
- **40 tests pin the distinctions, not the numbers** — so this memo's figures are not load-bearing for the suite, and re-running on a larger corpus does not require rewriting a test to match.

*Repo `origin/main` 817a5a5f. Trainer measurements via `trainer-vm-diag` #11146 / #11149 (run ids in the issue comments). Instrument validation is reproducible offline: `python3 scripts/research/ml2_bracket_train_eval.py --selftest`.*
