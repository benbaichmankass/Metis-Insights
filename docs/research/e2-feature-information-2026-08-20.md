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

<!-- RESULTS PENDING — filled from the trainer run, with the population on every number.
     Nothing is written here until the run completes and its controls are checked. -->

---

## 6. Disposition

Per §3.1 of the process doc: whatever the sign, this is a statement about **the
constructs tried over the substrate available**, with a date and a corpus attached. It
does not close the thread.
