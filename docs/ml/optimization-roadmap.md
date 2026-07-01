# ML Optimization Roadmap (multi-session)

> **Status:** living plan, opened 2026-06-03 (from a deep-research + codebase-inventory
> session expanding the `/ml-review` brainstorm). This is a **deep-dive plan**, not a
> commitment to ship — each phase/session is taken up deliberately, validated, and
> (for Tier-3 items) operator-approved before it influences live trading.
>
> **Owners / cadence:** worked across many sessions. `/ml-review` reads this file each
> run, reports progress against it, and may add/close items in
> [`docs/claude/ml-review-backlog.json`](../claude/ml-review-backlog.json) as it goes.
> The model-training execution itself runs on the trainer VM via the `model-training`
> skill (this doc plans; that skill builds).
>
> **Canonical authority above this doc:** `docs/CLAUDE-RULES-CANONICAL.md` →
> `docs/ARCHITECTURE-CANONICAL.md` → `ROADMAP.md` → current sprint log → skills.
> Scope siblings: [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (M9/M10 AI
> platform), [`docs/ml/training-center.md`](training-center.md),
> [`docs/claude/ml-training-policy.md`](../claude/ml-training-policy.md).
>
> **Successor program (2026-07-01):** this program's phases (validation discipline →
> decision-data wall → features → regime plumbing → MLOps) are largely complete and
> established that *architecture is rarely the binding constraint on tabular features —
> labels/validation/sample-size are*. The forward frontier is **new model *types*** —
> representation/embedding models, unsupervised regime discovery, deep sequence models,
> a shared self-supervised **"reads-everything" encoder**, cross-sectional rankers, and
> RL sizing/exit policies — captured in **M19**:
> [`docs/research/ai-model-strategy-roadmap-2026-07-01.md`](../research/ai-model-strategy-roadmap-2026-07-01.md).
> That doc carries the reframe (labels-not-compute), the compute-tier economics
> ($0 → ~$10/mo → larger), and the encoder-feeds-boosting-heads architecture forward.

---

## 0. North star + the one finding that orders everything

A deep-research pass (regime detection, sequence/deep models, the small-data problem,
RL, feature engineering, MLOps) and a full inventory of our own ML code converged on a
single conclusion:

> **On intraday trading over tabular features, model *architecture* is rarely the binding
> constraint. Labeling quality, validation discipline, sample size, and feature quality
> are.** (DLinear beats Transformers on TS benchmarks; gradient-boosted trees stay at the
> frontier on tabular finance data; off-the-shelf time-series foundation models "perform
> poorly" in finance without fine-tuning.)

So this roadmap deliberately spends effort on **discipline → data → features → regime
plumbing → MLOps**, and explicitly **parks** the tempting-but-low-ROI/high-risk work
(end-to-end Transformers, RL for sizing, GAN/diffusion synthetic data) on a research shelf
with trigger conditions.

**Where we are (the gaps this roadmap closes), verified from code 2026-06-03:**

| # | Current state | Consequence |
|---|---|---|
| G1 | Eval = single time-aware 80/20 holdout; **no walk-forward CV, no purge/embargo** | promotion evidence is measured through a leaky lens |
| G2 | **No hyperparameter search**, no early stopping; LightGBM params hard-coded | models left on the table; over/under-fit unmanaged |
| G3 | **No recency weighting**; widening BTC window to 5y *degraded* regime f1_volatile (MB-20260601-001) | "wide window dilutes recent signal" |
| G4 | Decision models train on **~50–80 live trades**; several collapse to majority class (`f1=0`) or lose to a per-group-mean baseline | setup-quality/trade-outcome models not useful |
| G5 | ~7 features; **no order-flow, no range-based vol estimators, no funding/OI, no cross-asset**; `account_context`/`review_journal` families exist but **unused** | "volatile class won't separate" attacked at the wrong layer |
| G6 | Shadow predictions fire **only on an actionable signal**; strong regime heads (1h f1_vol 0.45, MES-1d 0.65) get **zero** track record (MB-20260529-001) | regime promotion pipeline jammed both ways |
| G7 | ~~`ml/promotion/gates.py` is **advisory-only — never blocks**; promotion is discretionary~~ **CLOSED (S-MLOPT-S4):** `gate-check` now computes a mechanical PASS/FAIL champion-challenger packet (OOS edge vs baseline under purged WF-CV + KS/PSI drift bounds + shadow volume/soak); flip enforcement stays Tier-3 | ~~no automated champion-challenger gate~~ → "is it ready?" is now non-discretionary |

**Definition of done for the whole roadmap:** the bot moves from *"all-shadow, nothing
influences orders, data-starved decision models"* to *"disciplined, well-featured models
with honest walk-forward evidence, where the genuinely strong ones earn their way through
the operator-gated `shadow→advisory` switch."*

**Tiering reminder (governs execution, not this plan):** trainer-VM tooling
(splitters, labelers, dataset families, trainers, HPO) is **Tier-1 autonomous**; new/edited
`ml/configs/*.yaml` manifests and any live-runtime/order-path change are **Tier-2/3,
operator-gated**. Every phase below marks the tier of its *execution*.

---

## Phase ordering at a glance

```
Phase 0  Discipline & validation        (foundation — do FIRST; makes 1–4 honest)
Phase 1  Break the decision data wall   (highest payoff; depends on Phase 0)
Phase 2  Better features                 (high ROI; partly parallel to Phase 1)
Phase 3  Regime plumbing + modeling      (unblocks the promotion pipeline)
Phase 4  MLOps maturation                (drift-triggered retrain, tracking, gates)
Phase R  Research shelf                  (parked; trigger-gated)
```

Dependencies: **0 → 1**; **0 → 3.2**; **2.1 → 3.x re-eval**; **0.4 → every promotion**.
Phases 1 and 2 can run in parallel after Phase 0. Phase 3.1 (per-bar scoring) is the
single highest-leverage *unblock* and can start any time (it's independent of Phase 0/1).

---

## Phase 0 — Validation & training discipline *(foundation)*

**Why first:** every later metric (does meta-labeling beat baseline? did the new feature
help? is this model ready for advisory?) is only trustworthy if measured under
leakage-free walk-forward CV with proper sample weighting. Cheap, no new infra, unblocks
honest promotion. Closes G1, G2, G3, G7.

### Session 0.1 — Purged & embargoed walk-forward CV *(Tier-1)* — ✅ DONE 2026-06-03 (S-MLOPT-S1, PR #2674)
- **Deliverable:** a `PurgedWalkForwardSplitter` in `ml/experiments/splitters.py` (purge
  train samples whose label window overlaps the test block; embargo a gap after each test
  block), wired as an opt-in eval mode in `ml/experiments/runner.py` alongside the current
  `time_aware_holdout`.
- **Reference:** de Prado, *Advances in Financial ML* Ch. 7; `mlfinpy` cross-validation.
- **Success:** the existing models re-evaluated under purged WF-CV; report the (expected)
  drop vs the optimistic 80/20 holdout. Add a regression test pinning purge/embargo
  boundaries (no future row in any train fold).
- **Effort:** S.
- **Shipped:** `split_purged_walk_forward` + the reusable two-sided `purge_and_embargo_indices`
  primitive (ready for a later combinatorial purged CV); opt-in `purged_walk_forward` runner
  path (multi-fold, pooled metrics sample-weighted by `n_eval`, `cv_folds.json` artifact,
  full-data refit as the deployable `model_state`); leak regression test (no future-dated row
  in any train fold; purge + embargo boundaries pinned on row position **and** the time
  column); `scripts/ml/eval_split_compare.py` re-eval tool. **No manifest default eval
  changed.** Re-eval on the trainer VM (#2675): the optimism gap is real — `btc-regime-1h-lgbm-v2`
  weighted_f1 0.7185→0.6742; `setup-quality-lgbm-v2` MAE 0.065→0.086 / MSE 0.0094→0.0175
  (the latter now likely below its mean baseline → `MB-20260603-001`). Sprint log:
  [`docs/sprint-logs/S-MLOPT-S1.md`](../sprint-logs/S-MLOPT-S1.md).

### Session 0.2 — Sample-uniqueness + recency weighting *(Tier-1 tooling; Tier-3 to adopt in a manifest)* — ✅ DONE 2026-06-03 (S-MLOPT-S2; adopted via Tier-3 #2679)
- **Deliverable:** average-uniqueness sample weights (overlapping label windows) and an
  age-decay `sample_weight` option in the LightGBM trainers; expose both as manifest knobs
  (`sample_weight: {uniqueness: true, half_life_days: N}`).
- **Directly fixes G3 / MB-20260601-001:** evaluate whether recency-weighted 5y (or a
  shorter effective window) restores `btc-regime-*-lgbm-v2` f1_volatile on a **fixed recent
  holdout**. This is the window-length/recency sweep MB-20260601-001 calls for.
- **Success:** a sweep table (1y / 2y / 3y / 5y / 5y+decay) with f1_volatile on the fixed
  recent holdout; pick the config that maximizes it; propose as the manifest default
  (operator-gated edit).
- **Effort:** M.
- **Shipped (Tier-1 tooling):** `ml/trainers/sample_weights.py` — opt-in
  `trainer_config.sample_weight: {half_life_days, uniqueness, label_horizon, time_column}`,
  folded into both LightGBM trainers (recency decay × de Prado average uniqueness,
  mean-normalised, composing with any `class_weight`); **default-preserving** (knob absent →
  no behaviour change). `scripts/ml/window_recency_sweep.py` runs the 1y/2y/3y/5y(+decay)
  sweep against a fixed recent **purged** holdout (reuses the S-MLOPT-S1 primitive). Tests in
  `tests/ml/test_sample_weights.py` + `tests/ml/test_lightgbm_trainer.py`.
- **Sweep done (trainer VM #2677 + half-life sweep #2678):** a recency-decayed 5y window
  beats both the plain 5y **and** the 1y window on `f1_volatile` for all three models;
  per-model `f1_volatile`-optimal half-lives are **1h=90d, 15m=60d, 5m=60d** (180d was too
  slow). **Tier-3 manifest-adoption draft PR open** (adds `sample_weight` to the three
  `btc-regime-*-lgbm-v2` manifests) — **operator-gated, pending approval before merge.**
- NOTE: average-uniqueness on **fixed-horizon** bar labels is near-uniform (the active lever
  here is recency); uniqueness earns its keep once spans vary (Phase 1 triple-barrier).

### Session 0.3 — HPO + early stopping + class weights *(Tier-1)* — ✅ DONE 2026-06-03 (S-MLOPT-S3)
- **Deliverable:** an Optuna HPO harness that tunes LightGBM over **purged-CV folds**
  (TPE + pruning), early stopping on a validation fold, and class weights for the
  trade-outcome models (today they have none despite imbalance → `f1=0`). Save best params
  back into the manifest as a proposal.
- **Guardrail:** HPO **must** run on purged folds (Session 0.1) or it tunes to leakage.
- **Success:** measurable OOS lift vs hard-coded defaults on ≥1 model under purged WF-CV;
  no leakage (verified by 0.1 test).
- **Effort:** M.
- **Shipped (Tier-1):** `scripts/ml/hpo_sweep.py` — Optuna TPE + MedianPruner over the
  S-MLOPT-S1 `purged_walk_forward` folds (the manifest's own `split_strategy` is **ignored**
  and forced to purged WF-CV, so HPO cannot tune to leakage). Searches `lgbm_params` +
  `n_iter`; `--tune-class-weight <label>` adds the minority-class weight for imbalanced
  trade-outcome models. Enqueues the manifest's current params as trial 0 → reports
  **best-vs-baseline** on the same folds; emits a `proposed_trainer_config` patch (a
  **proposal** — manifest adoption is Tier-3). The CV objective (`cv_evaluate`) is a pure
  function unit-tested without Optuna (`tests/ml/test_hpo_sweep.py`).
- **Pending:** trainer-VM HPO run on ≥1 model (real OOS-lift demo; install `optuna` into the
  venv) → per-model Tier-3 param proposals. Early-stopping *inside the trainer* is folded
  into the `n_iter` search for now; a trainer-level early-stop knob is a follow-up.

### Session 0.4 — Promotion gates that actually compute & (optionally) block *(Tier-1 to compute; Tier-3 to enforce)* — ✅ DONE 2026-06-03 (S-MLOPT-S4)
- **Deliverable:** turn `ml/promotion/gates.py` from advisory into a real
  champion-challenger gate: pre-registered quantitative criteria (min shadow volume, OOS
  edge vs the incumbent/baseline under purged WF-CV, drift within KS/PSI bounds, min days
  in shadow). `python -m ml gate-check <id>` returns PASS/FAIL per criterion; `/ml-review`
  cites it in `promotion_recommendations[]`.
- **Note:** gate *enforcement* of the `shadow→advisory` flip stays operator-gated (Tier-3);
  the gate just makes "is it ready?" mechanical and non-discretionary.
- **Effort:** S–M.
- **Shipped (Tier-1 compute):** new `ml/promotion/oos_edge.py` — `compute_oos_edge()` re-runs
  the candidate's manifest through the S-MLOPT-S1 **purged & embargoed WF-CV** (reuses
  `iter_folds` + the runner's per-fold fit/score + `_aggregate_fold_metrics` pooling) and
  runs a **baseline trainer** (default constant per-group-mean — the G4 comparator) through
  the *same folds*; the oriented edge is positive ⇔ the candidate beats the baseline OOS.
  **Never scored on a holdout** (the no-leakage guardrail). Added the `oos_edge` gate to
  `gates.py` (required; `insufficient_data` when no datasets supplied) and made `drift_clean`
  gate on numeric **KS ≤ 0.2 / PSI ≤ 0.25** ceilings (not just the verdict bucket). `min shadow
  volume` (`sample_sufficiency`) + `min days in shadow` (`shadow_soak`) already existed — now
  one mechanical packet. Wired through `gate-check`/`stage-guard` (`--datasets-root`,
  `--baseline-trainer`, `--n-folds`/`--label-horizon`/`--embargo-fraction`) and into the
  `/ml-review` skill (cite the packet's `ready` + `blocking[]`). Boundary tests pin each
  criterion's pass/fail edge (`tests/ml/test_gates.py`, `tests/ml/test_oos_edge.py`). Enforcement
  of the flip stays Tier-3 — the gate computes "is it ready?", the operator still pulls the lever.

---

## Phase 1 — Break the decision-model data wall *(highest payoff)*

**Why:** the setup-quality / trade-outcome / prop models are the ones meant to make the bot
*decide better*, and they're untrainable on ~50–80 trades (G4). The fix is well-trodden:
manufacture a **dense, properly-labeled** dataset of *hypothetical* setups instead of
waiting for real closed trades. Depends on Phase 0 (need purged CV + honest holdout to
trust the result, and the live-vs-synthetic domain-shift check).

### Session 1.1 — Triple-barrier labeler → `setup_candidates` dataset family *(Tier-1)* — 🔄 IN REVIEW 2026-06-03 (S-MLOPT-S5)
- **Deliverable:** a new family in `ml/datasets/families/` that, for **every historical
  candidate setup** the strategies could have taken (from `signals` / `signal_audit` +
  `market_raw` candles), labels the outcome with an upper (TP), lower (SL), and vertical
  (timeout) barrier sized to local volatility — yielding thousands of labeled rows from bar
  history instead of 78 trades.
- **Reference:** de Prado triple-barrier; `mlfinpy` labeling.
- **Leakage discipline:** features at signal time only; barrier outcome is the label.
- **Domain-shift caveat (must document + mitigate):** synthetic-barrier fills ≠ live fills
  (slippage/partials/latency). Model realistic fills in the labeler and **always evaluate
  on a held-out set of REAL live trades**, never on synthetic rows.
- **Success:** family builds ≥ low-thousands of labeled candidates per symbol; a baseline
  trained on it and evaluated on the **live** holdout.
- **Effort:** L.
- **Shipped (Tier-1):** the reusable labeler `ml/datasets/labeling/triple_barrier.py` —
  de Prado symmetric **CUSUM event sampler** (`cusum_events`, breach side → long/short
  candidate) + **triple-barrier labeler** (`label_event`: TP/SL sized to signal-bar local
  vol + vertical timeout). **Realistic-fill discipline** mitigates synthetic optimism:
  touches on bar high/low (not close), **adverse-first** on a straddling bar (resolve to the
  stop, never claim the profit), and an optional `slippage` charge per fill. New
  `setup_candidates` family wires it over a `market_raw` dataset: CUSUM-sampled events,
  entry at the **next bar's open** (no signal-bar look-ahead), signal-time features
  (past-only) + the barrier outcome (future-only) → `leakage_test_status: passed` **by
  construction** (feature/label windows never overlap). Every row carries
  `is_live_trade: false` so a later PR appends REAL closed-trade rows and the evaluator
  holds those out — the **mandatory real-trade eval** the domain-shift caveat demands. Pure
  stdlib (no numpy/pandas) so it unit-tests in CI; labeler-boundary + family-build tests
  (`tests/ml/test_triple_barrier.py`, `tests/ml/test_setup_candidates.py`). Builds the
  **meta-labeling** base table for S-MLOPT-S6 (which adds the manifest + decision model;
  manifest adoption is Tier-3). Density verification (≥ low-thousands of candidates per
  symbol) runs on the trainer VM — reported in the sprint log.

### Session 1.2 — Meta-labeling model (the proper "should-I-take-this-trade") *(Tier-1 trainer/family; Tier-3 manifest)* — 🔄 IN REVIEW 2026-06-03 (S-MLOPT-S6)
- **Deliverable:** a secondary model that, given a primary strategy signal + signal-time
  features, predicts **whether to act** (and at what size-tilt) — the de-Prado-correct
  version of our `setup-quality` model. New manifest mirroring the existing lgbm-regression
  stack but on the `setup_candidates` labels + meta-label target.
- **Replaces the failing path:** `setup-quality-lgbm-v2` lost to a per-group-mean baseline
  at n=80 (MB-20260527-003, demoted to research_only). Meta-labeling on the dense dataset
  is the path to actually beating that baseline.
- **Success:** beats the per-group-mean baseline on the **live** holdout under purged
  WF-CV (Phase 0); if so → propose `shadow` registration.
- **Effort:** M (after 1.1).
- **Shipped (Tier-1 stack; Tier-3 manifest):** the meta-label model **reuses the existing
  `LightGBMRegressionTrainer`** on the binary `won` target (regress → probability) paired
  with `ClassificationEvaluator` (which scores a regression output as a probability:
  accuracy/precision/recall/f1/brier) — no new trainer needed. **The domain-shift
  discipline is now real machinery, not a caveat:** `setup_candidates` gained a
  `live_trades_db` source that appends REAL closed trades (located at the bar covering each
  trade's entry, same past-only feature space, actual realized outcome, `is_live_trade:
  true`); a new **`live_holdout` split strategy** (`ml/experiments/splitters.py`) trains on
  the synthetic candidates and **evaluates on the REAL trades** — the mandatory held-out
  real-trade eval. The manifest `ml/configs/setup-candidates-metalabel-v1.yaml` ships at
  `research_only` with `split_strategy: live_holdout` (flip to `purged_walk_forward` for the
  leak-free within-distribution check + the S-MLOPT-S4 `gate-check` oos_edge-vs-baseline).
  Tests: live-holdout split (partition + missing-population guards), real-trade append,
  manifest validity, end-to-end runner drive (`tests/ml/test_metalabel.py`,
  `tests/ml/test_setup_candidates.py`). **Trainer-VM eval is an honest NEGATIVE:** on 352
  real BTCUSDT trades (24.4% win rate) the model scores accuracy 0.670 vs the majority-class
  baseline's 0.756 → does **not** beat the baseline → **no promotion proposed**. The
  leak-free real-trade eval correctly blocks a synthetic-only-good model; the large
  synthetic↔real gap (win rate 0.457 vs 0.244 — CUSUM events ≠ the strategies' setups) is
  the lever for the next sprints (wire logged signals as the event source; S7 backtest-
  augmented labels; S8 cross-symbol). **Adopting the manifest / promoting past shadow is
  Tier-3 (operator-gated).**
- **S6 follow-up — signal-log event source (Tier-1, `MB-20260603-002`)** — 2026-06-03,
  sprint log [`S-MLOPT-S6-FU.md`](../sprint-logs/S-MLOPT-S6-FU.md), PR #2712. The
  highest-leverage S6 lever: `setup_candidates.py` gained a `signal_log_db` kwarg that
  samples candidates from the strategies' **real decision points**
  (`trade_journal.db::signals` buy/sell rows — the audit-log dual-write) and labels them
  with the SAME triple-barrier as CUSUM. New `event_source ∈ {cusum, signal_log, live}`
  schema column disambiguates the three samplers. New manifest
  `ml/configs/setup-candidates-metalabel-siglog-v1.yaml` (Tier-3 proposal, `research_only`)
  mirrors the S6 stack on the signal-log distribution. The headline trainer-VM eval
  (signal-log meta-label vs majority baseline on the 352 real BTCUSDT holdout) lands in
  `MB-20260603-002` `evidence_log`. Composes with S7 (enlarge the real holdout) + S8
  (cross-symbol). **Eval result (#2716): honest negative** — signal-log acc 0.526 < the
  S6 CUSUM run (0.670) < the 0.756 baseline. Win rate barely moved (CUSUM 0.457 →
  signal-log 0.469), so the event sampler is NOT the lever; the synthetic triple-barrier
  **label** is. `MB-20260603-002` RESOLVED-NEGATIVE.
- **S6 follow-up 2 — backtest-label event source (Tier-1, `MB-20260603-003`)** — 2026-06-03,
  sprint log [`S-MLOPT-S6-FU-2.md`](../sprint-logs/S-MLOPT-S6-FU-2.md). The next lever after
  the signal-log negative isolated the synthetic **label** as the gap. `setup_candidates.py`
  gained a `backtest_trades_db` kwarg (+ `include_backtest` convenience reusing
  `live_trades_db`, + `backtest_strategies` filter) that reads the `is_backtest=1` rows the
  S7 `backtest_recorder` writes from the strategies' standalone harnesses
  (`scripts/backtest_{squeeze,fade,trend,ict_scalp}.py`, `src/backtest/run_backtest_vwap.py`)
  and emits them tagged `event_source="backtest"` + `is_live_trade=False` (train side),
  carrying the harness's **actual realized outcome** (real slippage + real entry/exit logic)
  instead of a synthetic triple-barrier. New manifest
  `ml/configs/setup-candidates-metalabel-backtest-v1.yaml` (Tier-3 proposal, `research_only`)
  mirrors the S6/S6-FU live_holdout protocol on the backtest distribution — the
  apples-to-apples backtest-train + real-eval the signal-log run approximated. **Eval
  (#2718): the best of three label sources** — acc 0.756 (ties the baseline; vs CUSUM
  0.670, signal-log 0.526), **precision 0.50 lifts off the 0.244 base rate**, brier 0.196
  (best-calibrated) — but ties baseline by abstention (recall 0.047), not a clean beat.
  **Cleaner re-run (#2724, all 3 harnesses native-TF, +ict_scalp `--emit-trades`): acc
  0.741, prec 0.40 — still no baseline beat.** Across all FOUR label sources (CUSUM 0.670 /
  signal-log 0.526 / backtest-1h 0.756 / backtest-native 0.741) backtest labels are the
  best by precision (0.40-0.50 > the 0.244 base rate) but none beats the n=352 majority
  baseline — a confirmed **data-scale floor**; the lever is more real data or better
  features (S9), not another label distribution. `MB-20260603-003` **resolved**; the
  backtest-label infra (event source + recorder bridge + ict_scalp `--emit-trades`) stands
  as reusable tooling; manifest stays `research_only`.

### Session 1.3 — Backtest-augmented per-trade labels *(Tier-1; closes MB-20260530-001)* — 🔄 IN REVIEW 2026-06-03 (S-MLOPT-S7)
- **Deliverable:** have the backtest harnesses emit **per-trade rows** in the
  `setup_labels`/`trade_outcomes` schema (entry/SL/TP/outcome/r_multiple + signal-time
  features), tagged `source=backtest`; extend those families with a `source` column + an
  `include_backtest` flag. **Train on live+backtest, evaluate on a REAL-trade holdout only.**
  Exclude `execution_quality` (it learns real slippage; synthetic fills would poison it).
- **Pairs with** Phase 1.1 (both are "manufacture labels from history") and the 5y window /
  FVG-strategy backtesting work.
- **Effort:** M–L.
- **Shipped (Tier-1):** `trade_outcomes` + `setup_labels` gained a **`source`** column
  (`"live"`/`"backtest"`) + an **`include_backtest`** flag — default off (reads only
  `is_backtest=0` live trades, unchanged), on also surfaces the `is_backtest=1` rows the
  harnesses record. New **`ml/datasets/backtest_recorder.py`**: a pure `sim_trade_to_trade_row`
  mapper (`sim.ledger.SimTrade`→trades row: `won`/`pnl`/`pnl_percent`/`r_multiple` derived so
  the families work unchanged; `setup_type` falls back to the strategy) + `write_backtest_trades`
  that INSERTs **`is_backtest=1`** rows — excluded from every live/stats/default-dataset path
  (the same contract the M5 `backtest_results` writer relies on), **opt-in**, no autonomous
  money-DB write. The S-MLOPT-S6 `live_holdout` split was generalized with `live_flag_true_value`
  so the eval recipe is **train on live+backtest, hold out REAL only** (`live_flag_column:
  source`, `live_flag_true_value: live`) — keeping the domain-shift discipline (never eval on
  synthetic/backtest rows). `execution_quality` deliberately **not** augmented (it must learn
  real slippage). Tests cover the mapper, the writer (`is_backtest=1`-only), both families'
  `include_backtest`, and the source-based holdout; trainer-VM end-to-end demo (run a sim
  backtest → record to a temp DB → build `trade_outcomes` with `include_backtest` → enlarged
  training set) in the sprint log. Wiring the recorder into each harness (the opt-in money-DB
  write) is a one-call follow-up.

### Session 1.4 — Cross-symbol transfer *(Tier-1 experiment; Tier-3 manifest)* — ✅ DONE 2026-06-06 (S-MLOPT-S8; QUALIFIED-POSITIVE, research_only→shadow proposed)
- **Deliverable:** joint BTC+MES training (or pretrain-on-liquid-proxy → fine-tune) for the
  regime and decision families — a cheap small-data lever we don't use, natural since we
  already run two symbols.
- **Success:** transfer config beats the per-symbol model on the smaller-data symbol (MES)
  under purged WF-CV.
- **Effort:** M.
- **Shipped (Tier-1 enabler; Tier-3 manifest):** `setup_candidates.iter_rows` gained
  `market_raw_paths` (a list, or comma-separated string from the build CLI) → builds a
  **joint multi-symbol** dataset: each symbol is CUSUM-sampled + vol-bucketed against its
  **own** distribution (BTC and MES volatilities differ) then concatenated, with the existing
  `symbol` column carrying the source so a model can condition on / transfer across symbols.
  Refactored into a per-symbol `_iter_one_symbol` helper (singular `market_raw_path` path
  unchanged). New `ml/configs/setup-candidates-metalabel-xsym-v1.yaml` (Tier-3 proposal) =
  the S6 meta-label with `symbol_scope: all` + a `symbol` categorical feature. Tests:
  `_resolve_market_raw_paths` forms + joint build concatenates both symbols with per-symbol
  bucketing (`tests/ml/test_cross_symbol.py`). The transfer experiment (joint BTC+MES vs
  MES-only meta-label, scored on the REAL MES holdout) runs on the trainer VM — reported in
  the sprint log. **Adopting the manifest / promoting past shadow is Tier-3 (operator-gated).**
- **EVAL DONE (S-MLOPT-S8, trainer-vm-diag #2891-#2894, 2026-06-06; sprint log
  [`S-MLOPT-S8.md`](../sprint-logs/S-MLOPT-S8.md), `MB-20260606-002`):** the joint dataset
  builds clean (22,814 rows: BTC synth 15,737 + MES synth 6,723 + BTC real 354 + MES real
  **0**). **Data fact that reframes the experiment:** MES has ZERO closed trades — all 354
  real trades are BTCUSDT — so there is **no real MES holdout** and the intended **BTC->MES
  transfer is unmeasurable**. The only runnable test is the **reverse** (does pooling MES
  synthetic + a `symbol` feature help the real-BTC holdout, the actual n~352 floor). Clean
  ablation on the identical 354-trade real-BTC holdout (only diff = {+MES synthetic,
  +symbol}): **JOINT acc 0.7571 / precision 0.5417 / f1 0.2321** vs **BTC-only acc 0.6808 /
  precision 0.2093 / f1 0.1374**, majority baseline acc 0.7514 (base rate 0.2486). The joint
  is the **first meta-label in this family to edge above the majority baseline** (0.7571 >
  0.7514) with a decision-useful precision (0.54 = 2.2x base rate; BTC-only 0.21 is *below*
  base rate). **QUALIFIED POSITIVE, not a decisive floor-break:** win is on BTC (reverse
  transfer) not MES; leak-free purged-WF within-distribution does not corroborate; n=354 is
  noisy. **Does cross-symbol pooling break the n~352 floor? Weakly / not decisively** — it
  produces the best real-holdout meta-label to date but the binding constraint stays data
  scale (354 BTC trades, 0 MES). **Proposal (Tier-3, operator-gated):** research_only ->
  shadow so the model accrues an observe-only track record; daily rebuild wired opt-in/
  default-off behind `ICT_BUILD_XSYM=1` in `build_trainer_datasets.sh`. Follow-up levers:
  re-run with a real MES holdout once MES accrues closed trades (the *intended* transfer);
  add MGC/MHG; combine with the S9 range-vol/yz features (the feature lever that *did* lift).
- **FOLLOW-UP DONE (S-MLOPT-S8-FU, 2026-06-06; `MB-20260606-003`):** the
  "combine with the S9 range-vol/yz features" lever was built + evaluated —
  `setup_candidates` builder v1→v2 now emits the four range-vol estimators at
  signal time, and `setup-candidates-metalabel-xsym-yz-v1` (research_only)
  stacks the joint pooling with them. **HONEST NEGATIVE: the two levers do NOT
  stack.** On the identical 354-trade real-BTC `live_holdout` (trainer-vm-diag
  #2906): joint+yz acc 0.7203 / precision 0.2105 vs joint-only acc 0.7571 /
  precision 0.5417 — the range-vol features *degraded* the meta-label (precision
  back to base rate, accuracy below the majority baseline). The S9 lever is
  specific to the **volatility-regime** label; it does not transfer to the
  **win/loss decision** target, and at n=354 the extra features overfit. Verdict:
  joint-only `xsym-v1` stays the family's best; `xsym-yz-v1` stays research_only
  as a documented negative. Also re-confirmed Experiment 3: MES still has 0
  closed trades, so the intended BTC→MES transfer remains unmeasurable. Sprint
  log [`S-MLOPT-S8-FU.md`](../sprint-logs/S-MLOPT-S8-FU.md).

---

## Phase 2 — Better features *(high ROI; attacks "volatile won't separate" at the input)*

**Why:** the regime models' weak volatile separation (G5) is more a *label+feature* problem
than a capacity problem. Range-based vol estimators and microstructure flow are the highest
proven ROI per hour after Phase 1. Caveat from the research: **microstructure alpha decays**
— engineer it, monitor it via drift, don't assume permanence.

### Session 2.1 — Range-based volatility estimators *(Tier-1 family; Tier-3 manifest)* — ✅ DONE 2026-06-04 (S-MLOPT-S9)
- **Deliverable:** add **Yang-Zhang** (handles overnight gaps + drift, ~8× efficiency) and
  **Garman-Klass** vol to `market_features`; let regime manifests select the vol feature.
- **Lowest-effort, near-free regime-separation fix.** Re-run the regime eval (under Phase 0
  CV) to quantify the f1_volatile lift vs the current close-to-close-ish rolling vol.
- **Effort:** S.
- **Shipped (S-MLOPT-S9, sprint log [`S-MLOPT-S9.md`](../sprint-logs/S-MLOPT-S9.md), `MB-20260603-004`):**
  new `ml/datasets/volatility_estimators.py` (Parkinson / Garman-Klass / Rogers-Satchell /
  Yang-Zhang variance estimators) + four new past-window `market_features` columns
  (`parkinson_vol` / `garman_klass_vol` / `rogers_satchell_vol` / `yang_zhang_vol`),
  `builder_version v2 → v3`. New manifest `btc-regime-1h-lgbm-yz-v1.yaml` (Tier-3,
  `research_only`) is a clean A/B vs the v2 champion — identical everything except the vol
  feature set (regime spec frozen on `yang_zhang_vol`). Leakage-safe by construction
  (past-only window). **A/B (#2720): POSITIVE** — on a v3 `market_features` rebuild, the yz
  head beat the v2 champion on every metric (`f1_volatile` 0.4624 → 0.4724 +0.010, accuracy
  +0.016, weighted_f1 +0.012) under `time_aware_holdout`. **The Phase-0 purged WF-CV confirm
  (#2736, 2026-06-04) is also POSITIVE leak-free** — 1h yz beats v2 on `f1_volatile`
  (0.5036 vs 0.5009, +0.0027), `macro_f1` (+0.0067), `accuracy` (+0.0114). Promotion proposal
  written (`research_only → shadow`, Tier-3, operator-gated); full `shadow → advisory` blocked
  by `MB-20260529-001` (per-bar scoring, S13). Extended to 5m/15m BTC + 5m/15m MES
  (`*-lgbm-yz-v1.yaml`, research_only; A/B in `MB-20260603-004`). `MB-20260603-004` resolved.

### Session 2.2 — Order-flow / microstructure features *(Tier-1 core done; Tier-2 capture gated)* — 🔄 IN PROGRESS 2026-06-04 (S-MLOPT-S10)
- **Deliverable:** capture L1/L2 from Bybit + IBKR (new `market_raw` sub-stream + storage),
  compute **Order-Flow Imbalance (OFI)**, **VPIN** (volume-bucketed flow toxicity), spread,
  microprice. Bigger lift (a live capture path + storage), so scoped as its own Tier-2
  sub-project.
- **Reference:** Easley/López de Prado/O'Hara VPIN (2012); DeepLOB shows microstructure
  features transfer across instruments.
- **Effort:** L.
- **Shipped (S-MLOPT-S10, sprint log [`S-MLOPT-S10.md`](../sprint-logs/S-MLOPT-S10.md), `MB-20260604-002`):**
  the **Tier-1 estimator core** `ml/datasets/orderflow_features.py` (Cont OFI, Easley-LdP-O'Hara
  VPIN + bulk-volume classification, Stoikov micro-price, relative spread) + 16 CI tests; ruff
  clean. **Defining constraint:** unlike S9 (range-vol from OHLC) and S11 (funding/OI from REST
  history), order-flow needs L1/L2 + tick data we neither capture nor can backfill — so it
  **cannot be A/B'd offline**; the **Tier-2 live-capture path is the gate.** Design proposal
  [`docs/ml/orderflow-capture-design.md`](orderflow-capture-design.md): store **per-bar
  aggregates** (`ofi`/`vpin`/`rel_spread_mean`/`microprice_dev`) as a `market_microstructure`
  side-stream that reuses the S11 as-of-join + drift machinery (storage-bounded), joined into
  `market_features` via an optional `microstructure_path` (`builder_version v4 → v5`). **Three
  operator decisions pending** before the Tier-2 build: capture host (trainer-VM side-car
  preferred, WS9-safe) / transport (free REST polling vs paid ccxt.pro WS) / scope (BTCUSDT-only
  to start). On sign-off: capture service → accrue → join columns + A/B manifest → purged-CV A/B
  vs the v2/yz champions. Microstructure alpha decays — monitor via KS/PSI drift if promoted.

### Session 2.3 — Crypto funding-rate + open-interest features *(Tier-1 family; Tier-3 manifest)* — 🔄 IN REVIEW 2026-06-04 (S-MLOPT-S11)
- **Deliverable:** funding-rate **z-score / extremes** and **open-interest change** from
  Bybit. Research nuance: funding is mostly a *trailing* byproduct of momentum — its signal
  is in the *extremes*, not the level. Cheap, high-value, unused today.
- **Effort:** S–M.
- **Shipped (S-MLOPT-S11, sprint log [`S-MLOPT-S11.md`](../sprint-logs/S-MLOPT-S11.md), `MB-20260604-001`):**
  new `ml/datasets/funding_oi_features.py` (rolling z-score, extreme magnitude `|z|`,
  log-change, change-z — the signal is in the EXTREMES, not the level) +
  `ml/datasets/adapters/bybit_funding_oi.py` (off-VM Bybit V5 funding-rate +
  open-interest history fetcher, ccxt-mocked in tests) + `scripts/ml/fetch_funding_oi.py`
  (side-stream builder). `market_features` gains an optional `funding_oi_path` join → five
  new past-only, **as-of-aligned** columns (`funding_rate` / `funding_rate_zscore` /
  `funding_rate_abs_z` / `open_interest_change` / `open_interest_change_zscore`),
  `builder_version v3 → v4`, **default-preserving** (omit the path → 0.0 columns, every
  existing build + non-crypto symbol unchanged; leakage-safe by construction). New manifest
  `btc-regime-1h-lgbm-funding-v1.yaml` (Tier-3, `research_only`) = a clean A/B vs the v2
  champion. Opt-in daily-cycle wiring via `ICT_BUILD_FUNDING_OI=1` (default off). **A/B eval
  (#2745) NEGATIVE** — funding-v1 `f1_volatile` 0.4859 vs v2 0.5009 (−0.0150) under purged
  WF-CV. The off-VM adapter works end-to-end, but Bybit's public **OI history retention is
  ~8 days** (191 rows vs funding's full 5y 5460 rows), so the OI features were effectively
  untested and the funding-rate features added noise not signal on the *volatility-regime*
  label. Infra stands; manifest stays `research_only`. Follow-up levers (`MB-20260604-001`):
  wire funding into `setup_candidates` (a decision target may suit it better) / a rolling
  forward OI capture. Mirrors the S9 shape exactly.

### Session 2.4 — Cross-asset/macro for MES + wire `account_context` *(Tier-1 family; Tier-3 manifest)* — 🔄 PART A DONE 2026-06-04 (S-MLOPT-S12)
- **Deliverable:** DXY / VIX-term-structure / rates conditioning features for MES; and wire
  the existing-but-**unused** `account_context` family (equity curve, daily PnL, open-trade
  count) into the decision models.
- **Part A SHIPPED (S-MLOPT-S12, sprint log [`S-MLOPT-S12.md`](../sprint-logs/S-MLOPT-S12.md), `MB-20260604-004`):**
  `ml/datasets/macro_features.py` (VIX level/z + term-structure slope `VIX/VIX3M−1`,
  DXY z/return, 10y level + 3m-10y slope; pure, past-only, **one-day-lagged** so a daily
  close never reaches a same-day intraday bar); off-VM `yfinance_macro` adapter +
  `scripts/ml/fetch_macro.py`; `market_features` `builder_version v5→v6` (optional
  `macro_path`, **default-preserving** → 0.0 when omitted, every existing build unchanged);
  research_only A/B manifest `mes-regime-5m-lgbm-macro-v1` (identical to the v2 head except
  the macro group, same frozen regime spec). Daily `build_mes_market` fetches the stream once
  (`ensure_mes_macro`, `MES_MACRO=1`, non-fatal) and joins it on 5m/15m + the deep 1d head.
  The A/B is **evaluable once the daily cycle rebuilds MES `market_features` on v6 with the
  macro stream** — `MB-20260604-004` holds the review note. Macro alpha for an intraday regime
  head is plausibly thin; a negative is an acceptable, documented outcome (as S11 was).
- **Part B (`account_context` wiring) SHIPPED 2026-06-07 (S-MLOPT-S12-PartB; `MB-20260604-003`):**
  the per-signal snapshot enrichment landed. New `src/units/accounts/context_snapshot.py` +
  new `trade_journal.db::account_context_snapshots` table (UNIQUE `(order_package_id,
  account_id)`, 11 cols), written by `Coordinator.multi_account_execute` once per dispatch
  **AFTER eligibility filtering, BEFORE the per-account RiskManager runs** — so the snapshot
  is leak-safe by construction (state captured PRE-decision; `daily_risk_state`'s running
  totals can't contaminate it). Best-effort writer (swallows every exception), gated by
  `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED` (default off → enabled). `account_context` family
  `builder_version v1 → v2`, adds `include_snapshots=True` opt-in + 5 nullable `*_at_signal`
  columns; LEFT JOIN goes `trades.id → order_packages.linked_trade_id → snapshots.order_package_id`,
  default-off so every existing v1 build is byte-identical. 30 new tests (writer helpers +
  family LEFT JOIN + coordinator hook integration); ruff + canonical-db-resolver +
  dry-run-guard + silent-empty-guard all clean. Tier-2 live-trader hook ships as draft PR
  pending operator approval before merge+deploy; once enabled, evidence accrues for the
  natural follow-up: a research_only `account-context-snapshot-v1` manifest that trains on
  the populated `*_at_signal` columns (still unstarted — needs an accrual window first).
- **Effort:** M.

---

## Phase 3 — Regime plumbing + modeling *(unblocks the promotion pipeline)*

**Why:** today's `/ml-review` keeps reporting the regime promotion is jammed both ways —
the only head with shadow evidence (5m) is the weakest, the strong heads (1h/MES) get zero
track record (G6, MB-20260529-001). This phase fixes the plumbing first, then improves the
model.

### Session 3.1 — Per-bar regime scoring path *(Tier-2 — live runtime; HIGHEST-LEVERAGE UNBLOCK)* — 🔄 IN REVIEW 2026-06-04 (S-MLOPT-S13)
- **Deliverable:** a per-bar/per-tick regime-scoring hook (`_emit_tick_level_predictions`,
  or equivalent) in the trading loop, independent of actionable-signal emission, so **every
  shadow-stage regime head logs predictions on its own (symbol,timeframe) bar cadence** —
  not only when a 5m BTC vwap signal happens to fire.
- **Closes MB-20260529-001 option (a).** Without this, no strong regime head can ever clear
  a `shadow→advisory` promotion on order-influencing evidence. Independent of Phases 0–2 —
  can start immediately.
- **Care:** write-rate control (don't flood `shadow_predictions.jsonl`); reuse the frozen
  `regime_spec` bucketing so live features match training.
- **Effort:** M.
- **Shipped (S-MLOPT-S13, sprint log [`S-MLOPT-S13.md`](../sprint-logs/S-MLOPT-S13.md)):**
  new `src/runtime/regime_bar_scoring.py::emit_regime_bar_predictions`, called once per tick
  from `src/runtime/pipeline.py::run_pipeline`. Discovers the `shadow`-stage model set
  (same auto-wire source as the signal path), keeps the **regime** heads (those with a frozen
  `regime_spec`), fetches each head's own market candles (`connector_for_symbol` → BTC/Bybit,
  MES/IBKR), reuses `regime_shadow.feature_row_for_predictor` (live `vol_bucket` against the
  frozen edges — so per-bar feature rows are **identical** to signal-time ones), and writes one
  record to the same `runtime_logs/shadow_predictions.jsonl` via `with_shadow_preds` — so
  `/api/bot/shadow/*`, `/api/bot/trades/scores` and the `gate-check` `shadow_soak` criterion
  consume it unchanged. **Observe-only** (only `ShadowPredictor.predict`; no order-path reach),
  **deduped to one record per closed bar** by `(model_id, last_bar_ts)` (write-rate control),
  **never raises**, kill-switch `REGIME_BAR_SCORING_DISABLED` (default off → on). 19 new tests
  (`tests/runtime/test_regime_bar_scoring.py`) + 39 reused-helper tests pass; ruff clean.
  **Tier-2 — DRAFT PR pending operator approval before merge + deploy**, then on-VM
  verification (confirm via the diag relay that the 1h/15m/MES heads emit non-zero continuous
  counts). **Key finding → `MB-20260604-005` (this Phase 4.2 / train-serve parity):** the live
  regime row carries `vol_bucket` + one vol value; the heads also train on the range-vol
  estimators, log-return lags and time features (absent live → NaN), and the S9 yz heads'
  frozen `vol_feature_column` (`yang_zhang_vol`) is computed live as close-to-close vol. This
  is **pre-existing** (shared with the signal-time path + the v2 heads), so S13 reused the same
  computation deliberately; closing the parity gap (compute the named range-vol estimator +
  lag/time features live) is the prerequisite for trusting the per-bar evidence enough to
  promote any head `shadow → advisory`. See Session 4.2 below.

### Session 3.2 — Causal HMM / GMM regime family *(Tier-1 experiment)* — 🔄 IN REVIEW 2026-06-04 (S-MLOPT-S14)
- **Deliverable:** an alternative regime trainer using a **causal (filtered, not smoothed)**
  Gaussian HMM (`hmmlearn`) and/or GMM/change-point (`ruptures`) on range-based vol features
  (Phase 2.1) — naturally recency-weighted, interpretable, posterior-probability output.
- **Discipline (mandatory):** use **filtered** (causal) probabilities only — Viterbi/
  forward-backward smoothing leaks the future and inflates backtests. Heed the credible
  "illusion of regimes" dissent: validate OOS under purged WF-CV, compare head-to-head vs
  the LightGBM regime head, and only keep it if it adds OOS edge.
- **Effort:** M.
- **Shipped (S-MLOPT-S14, sprint log [`S-MLOPT-S14.md`](../sprint-logs/S-MLOPT-S14.md)):** a
  **self-contained Gaussian HMM** (no `hmmlearn`/`sklearn` dependency — `hmmlearn` was not a
  dep and the predictor runs on the live trader if ever promoted, so it stays light).
  `ml/trainers/causal_hmm_regime.py` (`CausalHMMRegimeTrainer`, NumPy, trainer-VM only):
  diagonal-Gaussian **GMM EM** over the S9 range-vol features (deterministic quantile init,
  seeded) → per-state emissions + a **transition matrix** from soft consecutive-bar
  responsibilities + a **per-state regime-label distribution**. `ml/predictors/causal_hmm_regime.py`
  (`CausalHMMRegimePredictor`, pure-stdlib): runs the **filtered forward recursion** only
  (`alpha_t(k) ∝ e_k(x_t)·Σ_i alpha_{t-1}(i)·A[i,k]`) — the state posterior at bar *t* depends on
  `x_1..x_t` only; **no Viterbi / forward-backward smoothing**. It is stateful across a
  chronological pass (matching how `time_aware_holdout` / each `purged_walk_forward` fold feeds
  the evaluator) with an **auto-reset on a non-monotonic `ts`** so there is no cross-fold leak.
  Research_only A/B manifest `ml/configs/btc-regime-1h-hmm-v1.yaml` vs the `btc-regime-1h-lgbm-v2`
  champion (same `market_features` dataset/label/split). 11 tests including the **causal-invariance**
  test (the filtered posterior at *t* is byte-identical whether or not future bars are appended)
  + an evaluator-integration test (>0.9 acc on cleanly-separated synthetic regimes via the standard
  `PREDICTOR_CLASS` resolution); ruff clean; manifest loads via `TrainingManifest`. **Tier-1**
  trainer/predictor/tests; the manifest is a **Tier-3 research_only proposal**. The trainer-VM
  purged WF-CV A/B vs the LightGBM head (`eval_split_compare.py`, the leak-free comparator) is the
  follow-up — per the dissent, the HMM is kept only if it adds OOS edge; a negative is an acceptable,
  documented outcome (as S11 was).
- **A/B result (#2784, 2026-06-04) — honest NEGATIVE.** Purged WF-CV on BTC 1h (n_eval=21,900,
  5 folds): the HMM loses to `btc-regime-1h-lgbm-v2` on `macro_f1` (0.5675 vs **0.6537**, −0.086)
  and `f1_volatile` (0.2688 vs **0.5010**, −0.232). The HMM's accuracy is higher (0.778 vs 0.725)
  only because it rarely commits to volatile (precision_volatile 0.495 but **recall_volatile 0.189**
  vs the LightGBM head's 0.636) — the filtered (honest, no-smoothing) posterior lags regime
  transitions, so it under-detects the very class it exists for. **Not promoted; manifest stays
  research_only;** the HMM family infra stands as reusable tooling. The discipline did its job — the
  holdout looked respectable (and smoothing would have flattered it further) but the leak-free CV
  correctly rejects it. Side benefit: the run reconfirmed the LightGBM head's real leak-free
  strength (macro_f1 0.654 / f1_volatile 0.501) — the phase-4 detector candidate for S15.

### Session 3.3 — Regime-router phase-4 detector *(ties to MB-20260601-002; Tier-2/3)* — 🔄 IN REVIEW 2026-06-04 (S-MLOPT-S15)
- **Deliverable:** when the regime router's phase-4 is taken up, wire the best
  **non-collapsing** regime head (today `btc-regime-1h-lgbm-v2`, f1_vol 0.45 — NOT the
  collapsed `regime-classifier-baseline-v0`, f1_vol 0) as the classifier detector, after it
  has accrued a shadow track record (depends on 3.1). Reconcile the
  `regime-classifier-baseline-v0` manifest(shadow)/registry(research_only) stage drift.
- **Effort:** S–M (mostly wiring + the stage-drift fix).
- **Key scoping finding (S-MLOPT-S15):** phase-4 as written was a **category error** — the
  ADX detector + policy table key on a **trend** axis (`chop`/`transitional`/`trending`), but
  every registry regime model (incl. `btc-regime-1h-lgbm-v2` and the collapsed baseline)
  predicts a **volatility** axis (`range`/`volatile`). A vol classifier cannot drop-in replace
  the trend detector. Operator chose to pursue **both** fixes as separate tracks:
  - **S15a (shipped, Tier-1 + Tier-3 manifest):** a **trend-regime model**. New
    `ml/datasets/labeling/trend_regime.py` (Kaufman efficiency ratio of the forward window →
    chop/transitional/trending) wired into `market_features` as `trend_regime_label`
    (builder_version v6→v7, default-preserving, future-only label → leak-safe) + research_only
    manifest `btc-regime-1h-trend-lgbm-v1.yaml` (same features/dataset/split as the v2 vol head).
    This is the true ADX-detector drop-in candidate. **Stage drift reconciled**
    (`baseline-regime-classifier.yaml` shadow→research_only). 16 tests + ruff clean.
    **Trainer-VM purged-CV A/B (#2787) — POSITIVE-but-modest, leak-free:** macro_f1 0.325
    (vs ~0.185 majority — does NOT collapse, predicts all 3 trend regimes), purged≈holdout
    (no leakage), f1_chop 0.463 / f1_trending 0.348 / f1_transitional 0.163; balanced label
    distribution from the default ER thresholds. A viable non-degenerate trend-axis detector
    (the artifact that didn't exist before); research_only pending class-weight tuning +
    a head-to-head vs ADX-14's forward-predictiveness.
  - **S15b (shipped 2026-06-04, Tier-2, observe-only):** wired the existing vol classifier as a
    **second, orthogonal regime axis**. New `src/runtime/regime/vol_detector.py::detect_vol_regime`
    — a pure detector (parallel to the ADX `detector.py`) that buckets the live
    `rolling_log_return_vol` against the deployed regime head's **frozen `vol_bucket` edges**
    (resolved once/process from the registry shadow-stage specs via `regime_spec_of`, reusing
    `regime_shadow` bucketing) and **collapses to 2-class `calm`/`volatile`** (lowest bucket→calm;
    operator-chosen taxonomy). Parity guard: only adopts edges from heads whose `vol_feature_column`
    is `rolling_log_return_vol` (skips the yz heads). `would_gate(..., vol_regime=)` evaluates a 2-D
    `trend_vol` cell, **default-preserving** (byte-identical 1-D when no vol label);
    `StrategyIntent.vol_regime` + `_stamp_regime_on_meta` (all 11 builders) + `_shadow_regime_gate`
    thread/log the vol axis (`enforced:false`). `config/regime_policy.yaml` `schema_version 1→2` +
    an EMPTY `trend_vol:{}` (authoring `off` cells needs a vol-split of the matrix — evidence
    accruing now). 31 new tests; ruff clean. LOGS but never enforces.
- Live phase-4 enforcement (replacing ADX, or authoring 2-D `off` cells) stays Tier-3 + waits on a
  shadow track record (S13, now flowing) + train/serve feature parity (`MB-20260604-005`); the
  trend axis additionally waits on the S15a class-weight + head-to-head-vs-ADX follow-ups.

---

## Phase 4 — MLOps maturation

**Why:** we already run a strong shadow→advisory ladder and KS/PSI drift — Phase 4 is the
"~70% there" polish that makes retraining efficient and promotion mechanical. Closes G3
(retrain trigger) and G7 (gates).

### Session 4.1 — Drift-triggered, recency-weighted retraining *(Tier-1/2)* — 🔄 IN REVIEW 2026-06-07 (S-MLOPT-S16)
- **Deliverable:** add **ADWIN** (`river`) online drift detection on streaming features so
  retrains fire on *drift*, not just the fixed daily timer; couple with the recency-weighted
  windows from Phase 0.2. Another angle on G3.
- **Effort:** M.
- **Shipped (Tier-1 trainer-side tooling):** pure-stdlib **ADWIN detector** in
  `ml/shadow/adwin.py` (Bifet & Gavaldà 2007 — Hoeffding cut `ε = √((1/2m)·ln(4w/δ))` over
  harmonic-mean sub-window sizes; default `δ=0.002` River-conservative, `min_window=10`,
  `max_window=10k`; FIFO-trims past the cap, skips non-finite values silently). **Composes
  with** — does not replace — the window-over-window KS/PSI in `ml/shadow/drift.py`: KS/PSI
  is the daily snapshot comparator, ADWIN is the online change detector that fires retrains
  *between* snapshots. New drift-retrain orchestrator `ml/shadow/drift_retrain.py::evaluate_models`
  walks every deployed head (`shadow` / `advisory` / `limited_live` / `live_approved`), streams
  the head's real-time shadow scores (backfill rows excluded — synthetic timestamps would
  pollute the online detector), runs ADWIN, and emits one `RetrainDecision` row per head
  (`dispatch` / `skip_no_drift` / `skip_no_manifest` / `skip_thin_data`). New
  `python -m ml drift-retrain` CLI + `scripts/ops/run_drift_retrain.sh` orchestrator that
  fires `python -m ml train <manifest>` per dispatch row — the trainer reuses whatever
  `sample_weight.half_life_days` the manifest already declares (the S-MLOPT-S2 recency-decay
  knob), so the retrain naturally down-weights the stale tail ADWIN told us to forget.
  **Conservative + logged:** ships `RETRAIN_PLAN_ONLY=1` so the first soak only writes
  `runtime_logs/drift_retrain.jsonl`; flip to `RETRAIN_PLAN_ONLY=0` once the operator has eyeballed a
  few cycles. New trainer-side `ict-drift-retrain.{service,timer}` (hourly, **DISABLED by
  default**) in `deploy/training-vm-cloud-init.yaml`. 17 tests
  (`tests/ml/test_adwin.py` + `tests/ml/test_drift_retrain.py`) pin the cut bound, the
  stationary / step-change / min-window / cap edge cases, the manifest resolver, the
  watched-stage filter, the backfill exclusion, the JSONL writer, and both CLI exit codes;
  ruff clean. **No auto-promotion past `shadow`** — trainer registers candidates,
  `promotion-readiness` (S18) / operator decide what clears the bar.

### Session 4.2 — Experiment tracking + train/serve parity *(Tier-1/2)* — 🔄 train/serve parity SHIPPED 2026-06-04 (S-MLOPT-S17)
- **Deliverable:** lightweight run tracking (MLflow/W&B or a disciplined runs table in
  `trainer_store.db`) and a feature-versioning check that guarantees the live shadow path
  computes features identically to the trainer (catch the classic "feature computed
  differently in backtest vs live" bug). We already approximate a registry/feature store
  via the federated `trade_journal.db`/`trainer_store.db`.
- **Train/serve parity (S-MLOPT-S17, shipped — closes `MB-20260604-005`):** the live regime
  scoring row (`src/runtime/regime_shadow.py::feature_row_for_predictor`, used by both the
  signal-time and per-bar shadow paths) now reproduces the full `market_features` row the
  heads train on — the four range-vol estimators (reusing `ml/datasets/volatility_estimators.py`)
  + log-return + its two lags + hour/day-of-week — and buckets `vol_bucket` against the
  estimator named by each head's frozen `vol_feature_column` (the yz-head fix). Verified by a
  parity test that uses `MarketFeaturesBuilder` itself as the oracle (live row == builder row
  bit-for-bit). This was the gate on promoting ANY regime head past `shadow`; now unblocked on
  the post-deploy track record. **Still TODO this session:** the experiment/run-tracking half.
- **Trainer-side realized join SHIPPED (S-MLOPT-S8-FU, 2026-06-06):** `gate-check`'s
  `live_agreement` + `drift_clean` could not compute on the trainer because the live
  `shadow_predictions.jsonl` wasn't synced there (and `/api/bot/trades/scores` is unreachable
  from a web session). `scripts/ops/sync_trainer_data.sh` now pulls
  `shadow_predictions.jsonl` (+ `_backfill`) into the trainer `runtime_logs/` the same way it
  pulls `trade_journal.db`, so `python -m ml gate-check` / `model-attribution` compute the
  realized join **locally**. Verified (trainer-vm-diag #2907-#2908): 28,666 fresh shadow lines
  pulled; `gate-check btc-regime-1h-lgbm-yz-v1 → advisory` runs the join locally (READY:false —
  `shadow_soak` 1.8/7d + the join is data-thin: the post-6/5 per-bar record doesn't yet overlap
  a closed-BTC trade window). The infra unblock is durable; evidence accrues with the soak.
- **Effort:** M.

### Session 4.3 — Full champion-challenger automation *(Tier-1 compute; Tier-3 enforce)* — 🔄 IN REVIEW 2026-06-07 (S-MLOPT-S18)
- **Deliverable:** close the loop on Phase 0.4 — every model carries a live PASS/FAIL gate
  status; `/ml-review` promotion recommendations are derived mechanically from it; the
  operator's role narrows to approving a green gate.
- **Effort:** S.
- **Shipped (Tier-1 trainer-side tooling):** new **promotion-readiness report generator**
  `ml/promotion/readiness_report.py` — thin orchestrator on top of
  `ml.promotion.stage_guard.run_stage_guard` that sweeps the whole registry (regime profile
  auto-applied via `thresholds_for`, OOS-edge baseline auto-selected for regime heads in
  `run_stage_guard` — so the yz-1h `~6/12` check and every future promotion is one CLI call,
  not a per-model gate-check stitched together by hand), buckets the resulting proposals
  into `promote` / `demote` / `hold`, and renders both a machine-readable `report.json` +
  an operator-readable `SUMMARY.md`. New `python -m ml promotion-readiness` CLI +
  `scripts/ops/run_promotion_readiness.sh` orchestrator that writes the artifact under
  `runtime_logs/trainer_mirror/promotion_readiness/<UTC-date>/` (so the existing
  `publish_trainer_mirror.sh` rsync picks it up) AND — when any model crosses the ready
  bar (CLI exits 10) — pushes a one-line `pending_pings/*.json` onto the live VM via SCP
  so `ict-telegram-bot.service` delivers the operator ping. Quiet days never push a ping.
  New trainer-side `ict-promotion-readiness.{service,timer}` (daily, **DISABLED by
  default**) in `deploy/training-vm-cloud-init.yaml`. 13 tests
  (`tests/ml/test_readiness_report.py` + the promotion-readiness case in
  `tests/ml/test_promotion_cli.py`) pin bucketing, Markdown rendering, the
  missing-`datasets_root` warning, the quiet-vs-actionable ping message, JSON+MD
  persistence, `build_readiness_report` end-to-end, and the CLI's `--output-dir` write
  path; ruff clean. **Reports only — never auto-promotes.** The `shadow → advisory` flip
  stays Tier-3: a `promote` proposal is an invitation, not an action.

---

## Phase R — Research shelf *(parked; do NOT pursue until a trigger fires)*

The research was explicit that these are low-ROI / high-risk at our scale. Parked with
trigger conditions so we revisit deliberately, not on hype.

| Idea | Why parked | Trigger to revisit |
|---|---|---|
| **RL for sizing/strategy** | Documented sim-to-real collapse (e.g. +300% sim → −70% live); non-stationarity; needs a high-fidelity simulator | Never for sizing/strategy by default. Supervised meta-labeling + rules/Kelly sizing is the chosen path. |
| **RL for execution scheduling** | Most of the benefit is captured by classical Almgren-Chriss / adaptive VWAP-TWAP at a fraction of the risk | Only if slippage becomes a **measured** drag AND we have a faithful LOB simulator |
| **GAN / diffusion synthetic data** | High effort, unresolved validation metrics, mode-collapse risk | Only after block-bootstrap (cheap, safe) is exhausted and data is still the bottleneck |
| **End-to-end Transformer / deep TS predictor** | Trees stay at the frontier on tabular finance at our data scale; the DLinear/PatchTST debate shows the deep edge is small + tuning-dependent | Consider only as a **feature generator** (learned vol/regime embedding feeding LightGBM), and only after Phases 0–2 |
| **Zero-shot TS foundation models (Chronos/TimesFM/Moirai)** | Documented to "perform poorly" off-the-shelf in finance; only finance-fine-tuned versions help | Only if we commit to a fine-tuning project with finance pretraining data |
| **LLM-driven trading signals** | FINSABER (2025): LLM strategies deteriorate under honest broad evaluation | Not on this roadmap; the existing M13 AI-analyst (advisory insights) is the sanctioned LLM surface |

---

## Toolbox (open-source, vetted in the research pass)

| Library | Use here | Phase |
|---|---|---|
| `mlfinpy` (OSS de Prado fork) | triple-barrier, meta-labeling, purged/embargoed CV, sample uniqueness | 0, 1 |
| `Optuna` | HPO (TPE + pruning) over purged folds | 0.3 |
| `hmmlearn` / `ruptures` | causal HMM / change-point regime models | 3.2 |
| `river` | ADWIN online drift, streaming models | 4.1 |
| `tsfresh` | candidate intraday feature generation (feed LightGBM; watch multiple-testing) | 2 |
| `Darts` / `NeuralForecast` (Nixtla) | *only if* we try a deep TS feature-generator | R |
| `ProsusAI/finbert` | *only if* we build a news/sentiment feature | R |

---

## Sprint map (→ `ROADMAP.md` § "M14 — ML Optimization Program")

This program is registered in `ROADMAP.md` as **M14**; each session below is a numbered
sprint there. Recommended execution order is **S13 → S1–S4 → S5–S6 → rest** (per-bar
scoring unblocks the regime pipeline; the discipline foundation must precede trusting any
data-wall result).

| Sprint | Phase/session | Sprint | Phase/session |
|---|---|---|---|
| S-MLOPT-S1 | 0.1 purged WF-CV | S-MLOPT-S10 | 2.2 order-flow/VPIN |
| S-MLOPT-S2 | 0.2 uniqueness + recency + window sweep | S-MLOPT-S11 | 2.3 funding/OI |
| S-MLOPT-S3 | 0.3 Optuna HPO + early stop + class wt | S-MLOPT-S12 | 2.4 cross-asset/macro + account_context |
| S-MLOPT-S4 | 0.4 promotion gates | S-MLOPT-S13 | 3.1 per-bar regime scoring |
| S-MLOPT-S5 | 1.1 triple-barrier `setup_candidates` | S-MLOPT-S14 | 3.2 causal HMM/GMM |
| S-MLOPT-S6 | 1.2 meta-labeling model | S-MLOPT-S15 | 3.3 regime-router phase-4 |
| S-MLOPT-S7 | 1.3 backtest-augmented labels | S-MLOPT-S16 | 4.1 drift-triggered retrain |
| S-MLOPT-S8 | 1.4 cross-symbol transfer | S-MLOPT-S17 | 4.2 experiment tracking + parity |
| S-MLOPT-S9 | 2.1 range-based vol estimators | S-MLOPT-S18 | 4.3 champion-challenger automation |

## How this roadmap is tracked

- **This file is the plan of record.** Each session above, when taken up, is executed via
  the `model-training` skill (trainer-VM) and/or a normal PR; on completion, tick it here
  and log specifics to the relevant backlog.
- **`/ml-review` reads this file every run**, reports progress against the active phase, and
  keeps [`docs/claude/ml-review-backlog.json`](../claude/ml-review-backlog.json) in sync
  (the open items MB-20260529-001, MB-20260530-001, MB-20260601-001, MB-20260601-002 are
  already the concrete first tasks of Phases 3.1, 1.3, 0.2, and 3.3 respectively).
- **Nothing here flips a live switch autonomously.** Trainer tooling is Tier-1; manifests
  and live-runtime/order-path changes are Tier-2/3 and ship only with operator approval.
