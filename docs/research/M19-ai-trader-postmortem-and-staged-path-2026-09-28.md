# M19 "AI trader" — post-mortem of the 13 retired manifests, and a staged path to the goal

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Written 2026-09-28 by lane **R-M19** (`session_01SQtJGs52hkSe2xVnMmnbJh`, Fable),
dispatched by Manager Session 2026-09-27 (`session_01KAQRJxRbRwpYTkPgBjNVyQ`).
Operator goal (2026-07-01, restated 2026-09-28): *an independent AI trader — one
overall model that reads everything.* Standing rule (operator, 2026-09-27):
*never kill an idea after one bad pass.*

**This document is not a memo.** It is pointed at by four `research/queue/`
units and three pipeline records, listed in § 6, and it is the post-mortem those
units' decision rules are written against. Delete it and they lose their basis.

Everything marked **MEASURED** was read this session from the file named beside
it. Everything marked **RECORDED** is quoted from a committed evidence doc or
ROADMAP row written by the session that ran the experiment, and was **not**
re-run here. "not found" means the search is stated and came back empty.

---

## 1. What was tried — the 13 manifests, one row each

Source of truth for every row: the YAML under `ml/configs/retired/` (MEASURED —
`trainer_config.feature_columns`, `target_column`, `dataset`, `evaluator_config`
read from each file), the closure text in `ml/configs/retired/README.md`, and
the numbers in the ROADMAP M19 phase table (`ROADMAP.md:828-841`) and the
evidence docs it cites. Retired in commit `a188a6fb3` (PR #8501, 2026-08-06).

Shared structure, so it is stated once: **12 of the 13 heads predict a
price-derived proxy label and are graded on `macro_f1` / `f1_volatile`.** None of
the 13 is graded on the net-of-cost R of any trade decision. The 13th (the
conviction stacker) predicts a trade outcome and was graded on 20 rows.

| # | manifest (`model_id`) | predicts | features | label | n | OOS result (RECORDED) | stated closure reason |
|---|---|---|---|---|---|---|---|
| 1 | `btc-regime-15m-lgbm-emb-v1` | BTC 15m vol regime | 7 hand features (`vol_bucket`, `rolling_log_return_vol`, `log_return` + 2 lags, `hour_of_day`, `dayofweek`) + 32 `tsfm_emb_*` (frozen Chronos-Bolt-Tiny, random-projected 256→32) | `regime_label` ∈ {range, volatile}; volatile = forward 5-bar rolling vol > `vol_threshold` 0.005 → **4.6 % positives** | 175,272 rows (`market_features` v003); `time_aware_holdout` 0.2 | Δmacro_f1 **+0.041** (0.576→0.617), Δf1_volatile +0.030 vs no-emb base | base-rate cliff: lift decays to +0.012 at the shipped 0.005 threshold; `fc` forecast feature gives ~2× the lift there; superseded |
| 2 | `btc-regime-15m-lgbm-emb-pcv-v1` | same | same | same | same rows; **purged walk-forward CV**, 5 folds, embargo 0.01, `label_horizon` 5 | Δmacro_f1 **+0.052** / Δf1_vol +0.035 at vol_threshold 0.003; **+0.012, f1_vol flat at 0.005** (`T0.1-embedding-followup-evidence-2026-07-01.md` L68-80) | same |
| 3 | `eth-regime-15m-lgbm-emb-pcv-v1` | ETH 15m vol regime | same | same | ETH v003, purged CV | Δmacro_f1 **+0.058** / +0.016 | same (ETH arm) |
| 4 | `btc-regime-1h-lgbm-emb-pcv-v1` | BTC 1h vol regime | same | same | BTC 1h, purged CV | Δmacro_f1 **+0.017**, f1_vol −0.001 | "weakest arm, data-poorer" |
| 5 | `btc-regime-15m-lgbm-emb-pca-v1` | BTC 15m vol regime | 7 hand + 32 emb reduced by **past-only PCA** (v004) | same | v004, `time_aware_holdout` 0.2 | PCA ≈ random projection **within ±0.001** | redundant; simpler projection kept |
| 6 | `btc-regime-15m-lgbm-fcemb-pcv-v1` | BTC 15m vol regime | 7 hand + 6 `fc_*` quantile-forecast cols + 32 emb (v520) | same | purged CV | fc+emb macro_f1 +0.015, dilutes fc's f1_vol to +0.005 | "not worth the 32 extra dims"; `fc` alone preferred |
| 7 | `btc-regime-15m-lgbm-corpusemb-pcv-v1` | BTC 15m vol regime | 7 hand + 16 `corpus_emb_*` from the T1.2 SSL encoder (daily macro/rates/FX/credit panel) | same | purged CV, same folds as base and emb arms | macro_f1 **0.584** / f1_vol **0.303** (13-series corpus) → **0.269** (28-series) vs emb 0.611 / 0.423 — loses to both base and emb on f1_vol (`T1.2-ssl-encoder-AB-evidence-2026-07-04.md` L35-65) | clock mismatch: daily backdrop vs 15-min bursts; wider corpus made f1_vol worse |
| 8 | `btc-direction-15m-lgbm-emb-v1` | BTC 15m **direction** (up/down/flat, 5-bar horizon) | 7 hand + 32 emb | `direction_label`, 3-class | v003, purged CV | Δmacro_f1 **+0.004**; direction ≈ 3-class chance (~0.34 macro_f1) | "embeddings carry volatility structure, not directional edge" |
| 9 | `conviction-meta-emb-btc-v1` | **P(win) of a closed BTC trade** | `c_strat c_setup c_wr c_reg adx_14 regime vol_regime direction strategy_name` + 8 emb | `won` (trade outcome) | **99 closed BTC trades**; `time_aware_holdout` 0.2 → **n_eval = 20, 5 positives** | Δmacro_f1 +0.039 (0.561→0.600) — **not significant** (`T0.3-conviction-embedding-evidence-2026-07-01.md` L28-55) | label wall; "don't chase raw embeddings on the conviction head yet" |
| 10 | `btc-regime-15m-tcn-v1` | BTC 15m vol regime | raw 64-bar windows × 4 (`log_return`, `rolling_log_return_vol`, `hour_of_day`, `dayofweek`); causal TCN, 48 ch, dilations 1-16, class_weight volatile 28 | `regime_label` @ 0.005 (~3.6 % volatile on `market_sequences` v001) | purged CV 5 folds, **~87.6k eval bars**; GPU burst $0.0269 | macro_f1 **0.534** / f1_vol **0.162** vs LightGBM incumbent 0.56-0.58 / 0.19-0.39 (`MB-20260703-001`) | below the tree on both metrics; "price-only depth exhausted" |
| 11 | `btc-regime-15m-tcn-vt003-v1` | same, label rebuilt at vol_threshold **0.003** | same | denser volatile class | purged CV | at matched 0.003 the TCN **loses f1_volatile by 0.021**, leads macro_f1 by +0.006 (inside noise) (`T1.1-tcn-label-sensitivity-evidence-2026-07-06.md` L60-69) | "negative stands at denser labels" |
| 12 | `btc-regime-15m-tcn-vt004-v1` | same at **0.004** | same | same | purged CV | monotone in base rate, still not above the tree (same doc, L14-16) | same |
| 13 | `corpus-ssl-encoder-mae-v1` | nothing — **self-supervised** MAE over the daily `corpus_panel`; emits `corpus_emb_0..15` | 13 → 28 daily series; seq_len 64, d=16, mask 0.5 | none (reconstruction loss is a diagnostic, not the gate) | pretrained on trainer CPU ($0); val_loss 2.0 → 1.3 with the wider panel | gate = downstream A/B, row 7: **fails both comparators** | "third independent negative on price-representation"; encoder inert at candidate |

Three arithmetic cross-checks on the table (MEASURED): 9 embedding-family + 3 TCN
+ 1 SSL = 13 = the files moved in `a188a6fb3`; 12 of 13 target `regime_label` or
`direction_label`; the only trade-outcome head has n_eval 20 = 0.2 × 99 rounded.

**Not found:** a committed per-run `metrics.json` for any of the 13 in this repo
(`experiments-runs/` is trainer-resident and not committed); the numbers above
are the evidence docs' transcriptions of them. A per-trade P&L or net-R figure
for any of the 13 — none exists, because none of them produced a trade.

---

## 2. Why the family failed — classified, with the evidence for each

Ranked by how much of the outcome each explains. The first three are the
answer to the manager's question; the rest are checked and mostly cleared.

### Cause 1 (dominant) — WRONG TARGET: a proxy label graded by f1, never a trade decision graded by net-R

MEASURED over the 13 manifests: 12 predict whether the next 5 bars are
"volatile" or which way price moves; 0 predict take/skip, size or exit; 0 carry
a cost term; 0 are evaluated on R. The design doc chose this deliberately —
*"the binding constraint is labels, not compute → self-supervised representation
learning is the flagship"* (`ai-model-strategy-roadmap-2026-07-01.md` § "The one
reframe"). Routing around the label wall meant measuring a label nobody trades.

What that bought, at best: a vol-regime head that feeds the regime router's
hard OFF-cell. Even a perfect one only gates trades in declared cells. The
2026-09-27 performance review found the healthiest regime head in the fleet
(`sol-regime-15m-lgbm-fc-pcv-v2`, oos_edge +0.237) has **zero real-money order
effect** because no SOL OFF-cell exists for it to gate
(`research/queue/blocked/RQ-20260927-006.yaml`, `why_this_matters`, RECORDED).
A +0.05 macro_f1 on that label is not a step toward a trader; it is a better
thermometer.

The one durable win (`fc`, T0.4) confirms the reading from the other side: it is
the only signal that graduated to shadow, and it too is a vol-regime feature —
useful, and still not a trade decision.

### Cause 2 — LABEL DEFINITION at an unlearnable base rate, which made the metric threshold-fragile

RECORDED (`T0.1-embedding-followup-evidence-2026-07-01.md` L68-80): the emb lift
is +0.052 at vol_threshold 0.003 (~16 % volatile), +0.037 at 0.004, **+0.012 at
0.005 (4.6 %)**, −0.004 at 0.006 (2.6 %), where the base head's own f1_volatile
is 0.19-0.24. The TCN shows the same monotone dependence on the label's base rate
(`T1.1-tcn-label-sensitivity-evidence-2026-07-06.md` L14-16). So the family's
headline verdicts swing on a hand-picked threshold that was "locked by the
gate's semantics". A target whose sign flips on a filter choice is not a target
(CLAUDE.md RULE ONE, *always state the population*). The volatile class at
0.005 is 4.6 % of the 175,272 BTC-15m rows — too rare for either arm to learn;
the comparison was made where nothing could win.

### Cause 3 — TOO LITTLE DATA for anything trade-outcome-shaped, and every pooling lever tried failed for a structural reason

This is where the design doc was right about the constraint and the programme
still could not get past it. MEASURED / RECORDED, in order of attempt:

| attempt | rows | what went wrong | source |
|---|---|---|---|
| T0.3 conviction stacker (row 9) | 99 BTC closed trades → **n_eval 20 / 5 positive** | not significant; nothing to learn from | `T0.3-conviction-embedding-evidence-2026-07-01.md` L36 |
| D2 spike A — pool real + paper journal | 605 rows (375 real / 230 paper); **6 paper rows** predate the real holdout window (0 at a wider cut) | leak-free chronology leaves the pool empty; **EPV 5-7**, recall_win 0, no arm beats majority | `D2-spike-A-pooled-labels-evidence-2026-07-06.md` L38-62 |
| M23 P1 — backtest-augmented meta-label | train 1,685 `is_backtest=1` rows (3 legs, BTC), eval **376 real trades** | precision lifts 0.244→0.318 but the pooled edge is **base rate between cells**, not a within-cell take/skip edge; EV-gate net-positive region caps at **n ≤ 11 of 376** | `M23-phase1-pooled-2026-07-17.md` L33-45; `M23-phase1-C1-results-2026-07-17.md` L34-56 |
| M23 P2 — 3-symbol label-volume expansion | train pool 1,685 → **5,077**; live eval 376 → **383** (ETH +7, SOL 0) | tripling the train pool grew the eval book by 7 rows; `won_r` head never net-positive at any threshold | `M23-phase2-labelvol-findings-2026-07-19.md` L5-13, L63-73 |
| M23 P2b — eval widening | +17 live rows (400) | "the +17 rows did not move the wall"; VERDICT=FAIL at majority 0.735 | `M23-phase2-labelvol-eval-widening-2026-07-27.md` L56-61 |
| T1.3 ranker | 1,867 OOS backtest candidates | AUC 0.61-0.68 per fold, but selection loses to dumb priority (−$39) — the AUC lives in **owner identity + `cell_hist_winrate` (0.648 alone)** | `T1.3-ranker-findings-2026-07-16.md` L41-66, L84-96 |

The pattern across all six: whenever the eval slice was real-money trades it was
a few hundred rows, and whenever the model was allowed to see many legs at once
its "skill" was knowing which leg bleeds — the question the roster already
answers (R2) and that no trade-level model needs to.

### Cause 4 — CLOCK / REPRESENTATION-TARGET MISMATCH in the "reads-everything" attempt

RECORDED (`T1.2-ssl-encoder-AB-evidence-2026-07-04.md` § Conclusion): the daily
macro/rates/FX/credit panel encodes a risk-regime backdrop; the head classifies
15-minute bursts. Widening the corpus 13→28 series cut the encoder's val loss
2.0→1.3 and made the downstream f1_volatile **worse** (0.303→0.269). The
"reads everything" idea was tested once, against the one target it could not
help, and then filed as `MB-20260704-T12-SSL-NEGATIVE` with the right next step
(*a head whose target lives on the corpus's own clock*) that nobody picked up.
The infrastructure (corpus store, `ssl_corpus_encoder`, ONNX parity gate) is
sound and idle.

### Cause 5 — LEAKAGE GUARDS: present and adequate; not a cause

MEASURED across the 13 YAMLs: every LightGBM head lists `forbidden_features`
(`forward_log_return`, `forward_log_return_vol`, `regime_label`,
`trend_regime_label`, `direction_label`); 9 of 13 use `purged_walk_forward`
with `label_horizon: 5` and `embargo_fraction: 0.01`; the embedding side-stream
is as-of joined (`ml/datasets/embedding_features.py`). The two exceptions are
`time_aware_holdout` on rows 1, 5 and 9 (a weaker split, disclosed in the
evidence docs). No leak was found and none is alleged. If anything the guards
were good enough to make the negatives trustworthy.

### Cause 6 — COST MODEL: not a cause of these 13, but the reason nothing built on them could have been trusted either

Irrelevant to a macro_f1 verdict by construction — none of the 13 produced a
trade to cost. It becomes decisive the moment a model does. MEASURED status
today: `docs/research/d1-cost-stack-verification-2026-09-25.md` (PR #12928)
verified that every corpus harness CLI resolves slippage and funding through
the venue-aware defaults (`DEFAULT_SLIPPAGE_BPS_ROUNDTRIP=5.0`, perp 3.0,
`DEFAULT_FUNDING_BPS_PER_WINDOW=1.0`), that the in-process module globals still
default to 0.0 (so a harness called as a library, not via its CLI, is fee-only
unless the caller sets them), and that the committed per-trade rows in
`comms/strategy_evidence/runs/*/*__trades.jsonl` carry `cost_fee_r`,
`cost_slippage_r`, `cost_funding_r` and `net_r` per trade (MEASURED this
session: 7,631 rows across 122 files; the 49 files the current evidence records
point at hold 3,092 rows, equal to the sum of the records' `n_trades_oos`).
Checklist row D1 was killed as a duplicate of R1 (done). **Any model that
touches a trade must be graded on these rows or a harness run that produces
them, never on a label of its own.**

### Cause 7 — CAPACITY: not a cause

The TCN is ~5M parameters on 175k rows and lost to a boosted tree; the SSL
encoder has hidden 64 / d 16. Neither was compute- or capacity-limited; the
T1.1 spend was $0.0405 total. The design doc was right that compute was slack.

### The top three, in one line each

1. **Wrong target** — a volatility proxy graded by f1, when the goal is a trade
   decision graded by net-of-cost R. Every one of the 13. (§ 2 Cause 1)
2. **Label at an unlearnable base rate** — the 4.6 % class made the headline
   verdicts flip on a hand-picked threshold. (Cause 2)
3. **The label wall for real trade outcomes, with every pooling lever failing
   structurally** — 20 → 376 → 383 → 400 eval rows across five attempts, and
   pooled models learning the roster instead of the trade. (Cause 3)

---

## 3. What has changed since the retirement that makes a restart worth a pass

MEASURED this session, each one a fact the 2026-07 programme did not have:

1. **A costed, committed, config-exact trade corpus exists.** 49 legs, 3,092
   out-of-sample trades over 365 days, fee + slippage + funding per trade, one
   file per leg beside its evidence record (`comms/strategy_evidence/`). In July
   the closest thing was 1,685 fee-only backtest rows for 3 legs.
2. **The ladder decides edge offline** (`OPERATING-PLAN-2026-09-21.md` § 2; the
   bar in § 5.3: expectancy > 0 net of the full stack at n ≥ 30, positive in a
   majority of walk-forward folds, Stage-1 cost fidelity). A model that selects
   or sizes trades is graded by the same bar as a leg. There is no longer a
   reason to hold a meta-model to a real-money eval slice of 376 rows.
3. **The result contract exists** (`scripts/research/research_result.py`,
   `research/results/<unit>/<run>.jsonl`) and the decision-rule guard refuses a
   unit without a rule registered before the run (`check_research_queue_decision_rule.py`).
4. **The exit-head machinery has a walk-forward pass on record**:
   `RQ-20260927-001` recovered +1.31 R over n=119 genuinely out-of-sample
   trend_donchian trades (small, marginal, single split — RECORDED in that unit's
   `result`).

---

## 4. The staged path

**Principles every stage obeys**, derived from § 2 and not negotiable within it:

- **The target is a decision, graded on net-of-full-cost R.** Never a proxy
  label. Stage 0 grading happens on harness trades that carry the cost stack.
- **Within-cell.** Every statistic is computed after removing the cell (leg ×
  symbol) base rate. A model that only learns which legs bleed scores zero. The
  roster question belongs to R2 and the demotion mandate, not to a model.
- **The naive floor is named and must be beaten:** take-all, the strategy's own
  `confidence`, and the cell's trailing expectancy. Beating "random" is not the
  bar.
- **Decide before you measure.** Each unit's rule names every branch, including
  the *refine* branch — per the operator's rule, a first negative routes to the
  next stated refinement, not to a kill. A kill needs the stated exhaustion
  condition.
- **Leakage guards are inherited, not re-invented:** purge on exit time,
  embargo, as-of trailing features, no post-fill fields.
- **Power is arithmetic on a measured population**, quoted with the population.
- **Nothing here changes what runs.** Every stage is observe-only until a
  Tier-3 decision, and S1/S2 are *reductive or scaling* layers on existing
  legs — they never invent a trade. Only S3 proposes trades, and it enters the
  ladder as a leg.

### S1-v0 — is there any within-cell veto information in what the harness already knows at entry? (`RQ-20260928-002`, dispatchable)

| | |
|---|---|
| **target** | veto: skip the lowest-scored 25 % of a leg's signals (460 of the 1,839 out-of-sample trades, MEASURED by the census) |
| **data** | the 49 committed per-trade files the evidence records point at (3,065 readable rows; 27 `fade_breakout_4h` rows have no `exit_time` and are excluded, stated in the census) |
| **features** | only what is on the row at `entry_time`: `confidence`, stop distance %, direction, hour (sin/cos), day of week, and the cell's **trailing** expectancy over trades that had exited before this entry |
| **label** | `won = 1[net_r > 0]`, net of fee + slippage + funding |
| **leakage guards** | purge: train only on trades whose `exit_time` ≤ test-block start − 7 days; trailing stats strictly as-of; no post-fill field exists on the row |
| **baseline (naive floor)** | take-all; `confidence`-only veto at the same 25 %; permutation null of 500 random vetoes of the same size per fold |
| **statistic** | `veto_delta_r` = mean within-cell-demeaned net_r of kept trades − mean of all, pooled over the OOS folds |
| **min n / power** | two-sample on demeaned net_r (kept vs vetoed), d = 0.20 sd, α 0.05, power 0.80 → floor 2·(1.96+0.8416)²/0.04 = **392.4** per group; **vetoed group = 460** (25 % of the 1,839 OOS trades after the 40 % warm-up), MEASURED by `--census`. Clears by 67.6. |
| **harness** | `scripts/research/meta_veto_walkforward.py` — new this PR, stdlib-only, `--self-test` with a planted positive, a null and a base-rate-only control (all pass); `--census` computes no statistic |
| **route** | committed data, so any runner; dispatched as a `trainer-vm-diag` one-off (precedent `RQ-20260927-001`), result via `research_result.py` |
| **decision rule** | in the unit; summary: PASS if `veto_delta_r` > 0, `perm_p` < 0.05, and ≥ 3 of the scored folds positive → S1 (market-state features) is *worth* running and S2-sizing unblocks; REFINE if not significant but the `confidence`-only veto is → the strategies' own confidence is the veto and S1 tests whether a model adds to it; FAIL-BUT-CONTINUE if neither → S1 still runs once (market state is the untested lever), and only a second null there kills the veto line |

This unit is deliberately small. It is the cheapest falsifiable version of "a
meta-model on top of the strategies", it uses zero new data plumbing, and its
null is informative: if nothing on the row carries within-cell information, S1's
market-state features are the only lever left before the line is exhausted.

### S1 — a veto model with decision-time market state (`blocked/RQ-20260928-003`)

Same target, statistic, guards and floor as S1-v0, plus as-of features joined
from the candle corpus at `entry_time`: returns over 1/4/12 bars, realized vol
percentile, ADX, distance to the 20/50-bar range, funding rate where the venue
has one, the advisory regime head's `predict_proba` where a symbol has one
(`regime-selectivity` Rule 3 — the probability, never `max(proba)`). Baseline
adds the S1-v0 model itself: S1 must beat S1-v0's `veto_delta_r`, not just
zero. **Blocked on** the harness's candle join, filed as a build item
(`PI-20260928-VNMMNBJH-0001`). Route once built: `trainer-vm-diag` (candles are
trainer-resident under `datasets-out/market_raw`).

### S2 — a sizing and exit policy on top of the veto

Two halves with different readiness.

**S2-exit (`RQ-20260928-005`, dispatchable).** The exit-head pipeline is the one
model family in this repo that already produces a walk-forward net-R attribution
on harness trades. It has one pass (`RQ-20260927-001`, trend_donchian BTC 1h).
S2-exit asks whether the same recipe holds on the leg family with the most
costed trades and a real-money roster slot: `ict_scalp` 15m (MEASURED n_oos:
sol 132, xrp 129, eth 117). Route: `research-exit-head-build.yml` (`harness:
ict_scalp`), purged per-year walk-forward with 7-day embargo as in
`M20-exit-refinement-2026-07-12.md` § 8. Statistic: `recovered_R` (head-on −
head-off, both net of cost by construction). Floor: paired one-sample, d = 0.35,
→ 64.07; the smallest of the three legs has 117 OOS trades. Rule: PASS on ≥ 2
of 3 legs → the exit head is a per-family policy, not a donchian artefact, and
S2 composes veto + exit; FAIL on all three → exit-head stays donchian-only and
S2 is veto + fixed exits.

**S2-sizing (`blocked/RQ-20260928-004`).** Fractional risk by S1 score tercile
(0.5× / 1.0× / 1.5×, budget-neutral so total declared risk is unchanged),
graded on net-R per unit of max drawdown against flat 1.0× sizing **and** against
the existing conviction sizing (`C1-conviction-sizing-evidence-2026-08-04.md`).
Blocked on S1-v0's verdict (a sizing layer on a score with no veto information
is noise on noise) and on the harness gaining `--size-by-score`
(`PI-20260928-VNMMNBJH-0001`, same build item). Its rule and floor are
registered now so the run needs no interpretation later.

### S3 — a single model that proposes trades from market state

Designed, not queued, because its instrument does not exist and building it is
a real engineering item (`PI-20260928-VNMMNBJH-0002`).

| | |
|---|---|
| **target** | per closed bar, an action ∈ {flat, long, short} with a stop and target in ATR units — a *trade proposal*, not a direction forecast |
| **data** | the same candle corpus the legs run on; every symbol × timeframe that has a costed leg today |
| **features** | S1's market-state block; the corpus-panel embedding **only** on a daily-clock variant (Cause 4) |
| **label** | triple-barrier outcome of the proposed trade under the venue's cost stack, computed by the harness, not by the model's own labeler |
| **leakage guards** | purge on barrier exit, embargo one holding period, as-of everything; the model is frozen before each walk-forward fold as any leg's params are |
| **baseline** | the zero-trade strategy (net 0 R, the hardest floor in a losing book) and the best costed leg on the same symbol × timeframe |
| **grading** | **S3 is a leg.** It is wrapped as a strategy module that emits order packages from a registered model and runs through the same harness, the same cost stack and the same four-clause bar as every other Stage-0 candidate. Nothing about being "the AI" exempts it from Gate 1. |
| **min n** | the bar's own n ≥ 30 closed with expectancy > 0 net, positive in a majority of folds; power at d = 0.3 on per-trade net_r → 87.2 trades per leg, so a symbol × timeframe that cannot produce ~90 proposals over the corpus window is not a valid S3 cell |
| **rule (registered when the unit is written, before the first run)** | PASS → Stage 1 soak as a leg, cost fidelity per Gate 1; FAIL vs zero-trade → refine the action space once (fewer symbols, longer clock) before the S3 line is called exhausted; never promoted on live P&L |

The "reads everything" corpus enters S3 only where its clock matches the target
(`MB-20260704-T12-SSL-NEGATIVE`'s own next step). That is the one refinement of
the encoder line this document keeps open; it is not a return to feeding daily
embeddings into a 15-minute head.

### The exhaustion condition, stated so a kill is possible

The veto line (S1) is exhausted when S1-v0 **and** S1 are both null on the full
costed corpus at the declared power. The exit line is exhausted when S2-exit
fails on all three `ict_scalp` legs *and* the donchian pass does not replicate
on a second split. The S3 line is exhausted when it fails against the zero-trade
baseline after one action-space refinement. Until each condition is met the
default is the next stated refinement, per the operator's rule.

---

## 5. What must be built (filed, not proposed)

| id | what | why it is a build, not a question |
|---|---|---|
| `PI-20260928-VNMMNBJH-0001` | `meta_veto_walkforward.py`: as-of candle join for market-state features (`--candles-dir`, resolve via `scripts/ops/backtest_data_source.py`), `--size-by-score` for S2-sizing, and a `research_result.py` emit | S1 and S2-sizing cannot run without it; the S1-v0 harness is deliberately data-plumbing-free so it could ship this PR |
| `PI-20260928-VNMMNBJH-0002` | model-as-strategy adapter: a strategy module that reads a registered model's per-bar action and emits order packages through the existing harness path, so S3 is graded as a leg | S3 has no instrument; grading it any other way repeats Cause 1 |
| `PI-20260928-VNMMNBJH-0003` | follow-through: when `RQ-20260928-002` lands, promote or kill `blocked/RQ-20260928-003` and `-003` per their `clears_when` | the blocked directory is a waiting room, and its README says keeping it from becoming a graveyard is not yet mechanical |

## 6. Where this document is pointed at from

- `research/queue/RQ-20260928-002.yaml` (S1-v0) and `research/queue/RQ-20260928-005.yaml` (S2-exit) — dispatchable.
- `research/queue/blocked/RQ-20260928-003.yaml` (S1) and `blocked/RQ-20260928-004.yaml` (S2-sizing).
- `docs/claude/work/pipeline/` records `PI-20260928-VNMMNBJH-0001/-0002/-0003`.
- `scripts/research/meta_veto_walkforward.py` docstring.

## 7. What this document does not do

It changes no manifest, config, roster, mode, registry entry or order path. It
does not re-run any of the 13 retired manifests and does not claim their
recorded numbers were re-measured here. It does not un-retire anything: the
retired manifests stay where they are, because the path above does not need
them — it needs the trade corpus they never touched.
