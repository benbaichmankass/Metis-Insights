# E2 — does anything in the widened panel carry information about forward R?

**Date:** 2026-08-20 · **Step:** E2 of
[`../design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md)
· **Tool:** [`scripts/research/e2_feature_information.py`](../../scripts/research/e2_feature_information.py)

E2 is the step the process doc marks *"**This step has never been run.** It is the
reason step E3 has been guesswork."* This is the first run.

---

## 1. Two things had to be built first, because neither existed

E2's falsifier is *"a feature that does not beat a shuffled-label control carries no
information."* Scoping that against the code found the control had no implementation,
and that the nearest existing thing was the leaky version of the test.

### 1.1 The function that looks like E2 is pooled and un-purged

`scripts/research/analyze_exit_head.py:270` calls
`_univariate_fdr(usable, feats, fdr_alpha)` on the **entire** row set — before and
outside the fold loop, with no folds, no purge, no embargo and no grouping. The
correct splitter, `_grouped_purged_folds`, sits in the same file at line 170 (trades
ordered by first `decision_time`, split into `n_folds+1` contiguous blocks, train rows
purged when `label_t1` reaches within `embargo_bars` of the test block's earliest
`label_t0`) and is used **only** by the multivariate head below it.

Per-bar triple-barrier labels overlap in time by construction, so the analytic
BH-FDR q-values that function reports rest on an independence assumption the panel
violates. **E2 imports the splitter and never calls the univariate**; both facts are
asserted by self-tests rather than left to review.

### 1.2 No shuffled-label control existed anywhere in the repo

`shuffled_label` / `label_shuffle` / `permutation_test` / `shuffle_label` matched
**zero files**. The only `shuffle` is permutation *importance*
(`analyze_research_panel.py:688,1143`), which permutes a **feature** and asks how much
discrimination a fitted model loses — a different null answering a different question.
E2's declared falsifier had no implementation. It does now.

---

## 2. The design, and the one detail the null's validity rests on

| element | choice |
|---|---|
| target | **`forward_r`** (continuous) primary; binary `label_hold` reported alongside, never substituted |
| split | `_grouped_purged_folds`, **imported** |
| statistic | **absolute mean of per-fold Spearman** between feature and `forward_r`, on each fold's *test* rows |
| null | **trade-block** label shuffle (`trade_block_cyclic`) |
| decision rule | **Westfall–Young max-statistic FWER threshold** at α |
| controls | positive (`f(label)+noise`, must fire) + negative (pure RNG, must stay silent) |

**Why mean-of-signed-then-absolute, not mean-of-absolutes.** A feature whose sign
flips fold to fold cancels toward zero and correctly fails. Mean-of-absolutes would
reward exactly that instability, which is the shape of an overfit feature.

**Why the shuffle is at the trade-block level.** Rows within one trade share an
overlapping label window and are strongly dependent. A row-level shuffle destroys that
dependence and yields a null tight enough that a trade-structured feature clears it
almost automatically — a harness that manufactures positives.

⚠️ **This rationale was overbroad on first writing, and the self-test caught it.**
The original claim was that the block null is *always* wider. Measured on an
i.i.d.-per-row probe feature it was **narrower** — block 95th percentile 0.0917 vs row
95th 0.1045 — and the test failed. The correction: **the inflation requires both series
to be trade-structured, not just the label.** If the feature is independent across
rows, the label's block structure alone barely moves the null. It matters here because
the panel's features are *not* i.i.d. — `running_mfe_r`, `upnl_r`, `bars_in_trade`,
`dist_to_stop_atr` are all functions of the trade's own path (that is precisely §0.2's
complaint about the substrate) and are strongly autocorrelated within a trade. The
self-test now probes with a trade-structured feature and asserts the block null is the
wider one, so if the rationale ever stops holding the tool fails rather than quietly
scoring against a bad null.

**The label horizon is a condition on the answer, not a detail.** `forward_r` is a
**triple-barrier** outcome, so it is defined against a vertical barrier — here the tool
default `--time-stop-bars 12`, which on a 15m panel is **3 hours**, with `--tp-r 2.0`.
The `ict_scalp` adapter's own `timeout_bars` default is **24 bars**, so the label horizon
is deliberately *shorter* than the span a trade may actually run. That is a legitimate
pre-registered choice — E2 asks whether a feature predicts R over a stated horizon — but
it means a negative here is a negative **at 12 bars**, not at all horizons, and §3.1's
"record the conditions" applies directly. A longer-horizon arm is the cheapest follow-up
if this returns negative, and it is named here rather than discovered later.

**Why FWER and not per-feature α.** Scoring N features at α=0.05 makes roughly one
spurious "informative" *expected* on a 16-feature panel. The pointwise verdict is
reported for diagnosis; the pre-registered decision is the max-statistic threshold,
which controls family-wise error without assuming the features are independent — and
they are heavily dependent, being functions of one path. Controls are scored **outside**
the family, so a planted synthetic cannot inflate the threshold it must clear.

---

## 3. The harness-validity gate — why a negative here is admissible at all

Every run injects and reports two synthetic features:

- **positive control** `__ctrl_signal` = monotone in the label + noise. **Must** reach
  `informative_fwer`.
- **negative control** `__ctrl_noise` = pure RNG. **Must not** reach even
  `informative_pointwise`.

If either misbehaves the run returns `verdict: "harness_invalid"` and **no negative
from it is admissible**. A self-test plants a dead positive control and asserts the run
is refused — a gate that cannot fail is not a gate.

**Underpowered is `unmeasured`, never negative.** `_grouped_purged_folds` yields nothing
below `n_folds+1` trades, and a handful of trades gives a null so wide nothing could
ever clear it. Runs below the declared floors name the binding floor and stop. Reporting
one as *"no feature carries information"* would be a confident negative computed from
almost no data — the same sin as a green run that measured nothing.

---

## 4. Substrate — stated, because E2 was pre-specified to run over the *widened* panel

**The local repo cannot support this run, and that is worth recording**: five of the six
committed candle files are constant-price placeholders (300 rows, **1 distinct close**
each — `btc_5m_2026`, `eth_5m_2026`, `qqq_15m_2026`, `spy_15m_2026`, `spy_5m_2026`;
added as "placeholder OHLCV data" in `29014899`). Only `data/backtest_candles.csv` is
real (5,000 rows, 4,873 distinct closes, BTC 1m, 2022-07-23→27), and it has no peer, so
no honest `state: joined` cross-asset block is buildable locally — ρ against a
zero-variance series is undefined.

The real substrate is the trainer's `datasets-out/market_raw`, enumerated in
trainer-diag **#10014**:

| symbol | interval | rows | span |
|---|---|--:|---|
| BTCUSDT | 15m | 175,296 | 2021-08-20 → 2026-08-19 |
| ETHUSDT | 15m | 175,296 | 2021-08-20 → 2026-08-19 |
| XRPUSDT | 15m | 175,296 | 2021-08-20 → 2026-08-19 |
| ADAUSDT | 15m | 175,296 | 2021-08-20 → 2026-08-19 |
| AVAXUSDT | 15m | 172,766 | 2021-09-15 → 2026-08-19 |
| SOLUSDT | 15m | 169,920 | 2021-10-15 → 2026-08-19 |

**Target: `XRPUSDT` 15m** — the operator's own motivating case (a short XRP held while a
long ETH was opened at ρ 0.88), and the one symbol whose **both** configured peers have
full-overlap series: ETHUSDT (ρ 0.8763) and SOLUSDT (ρ 0.8451). Converted and verified
genuinely varying in **#10016**: XRP 175,296 rows / 25,486 distinct closes, ETH 175,296 /
115,161, SOL 169,920 / 51,877.

Transport was verified rather than assumed: the tool's sha256 on the trainer matched the
reviewed commit byte-for-byte, and its self-tests were required to pass **on that box**
before any number it produced was read.

---

## 5. Result

### 5.1 The population, stated once and applying to every number below

`XRPUSDT` 15m · `ict_scalp` harness · **10,103 labelled rows from 530 of 530 trades**
(19.06 rows/trade) · **25 dense feature columns, of which 22 are scoreable** (three are
constant — see 5.4) · cross-asset **`state: joined`**, both configured peers
(`ETHUSDT`, `SOLUSDT`) joined, `rows_with_xa` 10,103, **`row_coverage` 1.0** over 175,296
indexed bars · `base_hold_rate` 0.469.
Config: `n_folds` 4 · `embargo_bars` 12 · `n_shuffles` 1000 · α 0.05 ·
`time_stop_bars` 12 (3h) · `tp_r` 2.0 · seed 20260820.
Source: trainer-diag #10022 (build + primary) and #10023 (all three targets).

**The exogenous half was genuinely MEASURED this time**, not unmeasured — every row
carried a real peer block from two full-overlap 5-year series. That is the precondition
E1 existed to create, and it held.

### 5.2 The controls behaved, so the negative below is admissible

| control | statistic | verdict |
|---|--:|---|
| `__ctrl_signal` (positive, must fire) | 0.5644 | **FWER pass**, p = 0.000999 |
| `__ctrl_noise` (negative, must stay silent) | 0.0017 | no pass, p = 0.880 |

`harness_valid: true` on **all three** targets.

### 5.3 The headline, and why it must not be quoted alone

| feature | `forward_r` | `advantage_r` | `label_hold` |
|---|--:|--:|--:|
| `feat_upnl_r` | **0.5753** ★ | 0.0062 | 0.0078 |
| `feat_dist_to_stop_atr` | **0.4668** ★ | 0.0168 | 0.0066 |
| `feat_running_mfe_r` | **0.4334** ★ | 0.0319 | 0.0212 |
| `feat_running_mae_r` | **0.4168** ★ | 0.0476 | 0.0376 |
| `feat_dmae_dt` | **0.2836** ★ | 0.0092 | 0.0087 |
| `feat_in_trade_vol_ratio` | **0.1311** ★ | 0.0240 | 0.0192 |
| `feat_mfe_giveback_r` | 0.1140 · | 0.0342 | 0.0295 |
| `feat_xa_peer2_rel_strength` | 0.0485 | 0.0467 | 0.0519 |
| `feat_xa_peer2_ret` | 0.0252 · | 0.0032 | 0.0052 |
| `feat_xa_peer1_ret` | 0.0244 · | 0.0051 | 0.0025 |
| `feat_xa_breadth_up` | 0.0203 · | 0.0028 | 0.0014 |
| `feat_bars_in_trade` | 0.0202 | 0.0035 | 0.0082 |

★ = clears the pre-registered family-wise bar · · = clears its own null only.
(Full 22-row table in the committed reports.)

| target | verdict | FWER threshold | n FWER | n pointwise |
|---|---|--:|--:|--:|
| `forward_r` (pre-registered primary) | `informative_features_found` | 0.1230 | **6** | 11 |
| `advantage_r` | **`no_feature_beats_control`** | 0.1037 | **0** | **0** |
| `label_hold` (pre-registered secondary) | **`no_feature_beats_control`** | 0.0898 | **0** | **0** |

**Every one of the six `forward_r` winners collapses by one to two orders of magnitude
on `advantage_r`.** `feat_upnl_r` falls 0.5753 → 0.0062, a factor of **93**.

That is not a surprise and it was not discovered by inspecting the scores: it was
**predicted from reading `src/research/triple_barrier.py` before interpreting them**.
`forward_r` is measured **from entry**, so it shares its baseline with `feat_upnl_r` and
with every path feature tracking accrued R. A trade up 1.5R now tends to still be up
about 1.5R at the time stop. The six "wins" are substantially arithmetic about **where
the trade already is**, not information about **where it is going**. There is no
lookahead — the feature and label windows are disjoint by construction — which is
exactly what makes this dangerous: a fully-controlled, purged, embargoed, correctly
FWER-adjusted, entirely misleading headline.

### 5.4 What E2 actually answers

On the decision-relevant targets — the incremental value of holding, and its sign —
**not one of 22 scoreable features carries information** — **9 endogenous** path features
plus **13 exogenous** peer features (16 xa columns less the 3 constant presence flags),
which reconciles: 9 + 13 = 22.
**Zero at the family-wise bar and zero at the pointwise bar**, where ~1.1 pointwise hits
would be expected by chance at α = 0.05 across 22 features.

Three features return `null` rather than a score — `feat_xa_peer1_present`,
`feat_xa_peer2_present`, `feat_xa_breadth_present`. They are **constant** on this panel,
because coverage is 1.0 and every row had both peers. They correctly drop out instead of
contributing a fabricated 0.0 to an aggregate (the manifest's own
`constant_feature_cols` flags the first). A presence flag is worth its column precisely
when coverage is *not* 1.0; here it is, and the honest score is "undefined", not "zero".

### 5.5 The negative survives the scale-free check, and the check is not inert

The pre-registered rule is a raw max-|statistic| threshold, and that is **scale-dependent**.
Null widths here differ by design and by a factor of ~5: a path feature is strongly
autocorrelated within a trade and gets a wide own-null (`feat_running_mae_r` 0.0848,
`feat_xa_peer1_beta` 0.0954), while a near-white peer return gets a tight one
(`feat_xa_peer1_ret` 0.0193, `feat_xa_peer1_ret_lag1` 0.0177). A single max-statistic bar
is therefore set mostly by the wide-null features and is **conservative for the
narrow-null ones — which is exactly the exogenous block E1 built.** Leaving it there
would have meant announcing "no peer feature clears the bar" under a bar tilted against
peer features.

So a scale-free **Westfall–Young min-p** companion was added (each statistic converted to
a p-value against its own null before the family-wise minimum) and all three targets
re-scored (trainer-diag #10024, tool `56a22590`, 31/31 self-tests on the box):

| target | FWER thr | n FWER | min-p thr | n min-p | min-p vs FWER differs on |
|---|--:|--:|--:|--:|---|
| `forward_r` | 0.1230 | 6 | 0.004995 | **7** | `feat_mfe_giveback_r` |
| `advantage_r` | 0.1037 | 0 | 0.004995 | **0** | **nothing** |
| `label_hold` | 0.0898 | 0 | 0.003996 | **0** | **nothing** |

**The companion is demonstrably capable of firing on this exact panel** — it promotes
`feat_mfe_giveback_r` on `forward_r`, which the raw rule misses — and it promotes
**nothing** on either decision-relevant target. The zero is a measured zero, not an
inert rule producing a vacuous agreement. (The one feature it rescues is endogenous, so
even the scale-free rule surfaces no peer feature anywhere.)

⚠️ **Two of the eleven endogenous features named in §0.2 are NOT in this measurement.**
The manifest reports `dropped_all_null_feature_cols: ["feat_taker_imbalance",
"feat_taker_imbalance_intrade"]` — the candle CSV carries no taker data, so both columns
were all-null and dropped before scoring. The panel is therefore **9 endogenous + 16
exogenous = 25 columns, 22 scoreable**, and **E2 says nothing about the two order-flow
features.** That is a real scope limit, and it lands on a family the literature survey
(§1.5.7) had already named as untried — microstructure/order-flow exit triggers. It is
recorded here so nobody reads this negative as covering them.

**This is the finding with consequences for the programme.** §0.2 diagnosed the failure
of ~20 lever cells as *"no function of these eleven endogenous inputs beats holding"* and
proposed E1's widening as the fix. E1 delivered the widening; E2 now measures that **the
widened panel does not help either — at this leg, this horizon, this substrate.** And it
supplies a mechanism for why the lever sweeps kept returning `honest_negative`: levers
built on features that look strongly informative against `forward_r` were reading a
target that shares their own baseline.

---

## 6. Disposition

Per §3.1 of the process doc: whatever the sign, this is a statement about **the
constructs tried over the substrate available**, with a date and a corpus attached. It
does not close the thread.
