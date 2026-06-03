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
| G7 | `ml/promotion/gates.py` is **advisory-only — never blocks**; promotion is discretionary | no automated champion-challenger gate |

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

### Session 0.2 — Sample-uniqueness + recency weighting *(Tier-1 tooling; Tier-3 to adopt in a manifest)*
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

### Session 0.3 — HPO + early stopping + class weights *(Tier-1)*
- **Deliverable:** an Optuna HPO harness that tunes LightGBM over **purged-CV folds**
  (TPE + pruning), early stopping on a validation fold, and class weights for the
  trade-outcome models (today they have none despite imbalance → `f1=0`). Save best params
  back into the manifest as a proposal.
- **Guardrail:** HPO **must** run on purged folds (Session 0.1) or it tunes to leakage.
- **Success:** measurable OOS lift vs hard-coded defaults on ≥1 model under purged WF-CV;
  no leakage (verified by 0.1 test).
- **Effort:** M.

### Session 0.4 — Promotion gates that actually compute & (optionally) block *(Tier-1 to compute; Tier-3 to enforce)*
- **Deliverable:** turn `ml/promotion/gates.py` from advisory into a real
  champion-challenger gate: pre-registered quantitative criteria (min shadow volume, OOS
  edge vs the incumbent/baseline under purged WF-CV, drift within KS/PSI bounds, min days
  in shadow). `python -m ml gate-check <id>` returns PASS/FAIL per criterion; `/ml-review`
  cites it in `promotion_recommendations[]`.
- **Note:** gate *enforcement* of the `shadow→advisory` flip stays operator-gated (Tier-3);
  the gate just makes "is it ready?" mechanical and non-discretionary.
- **Effort:** S–M.

---

## Phase 1 — Break the decision-model data wall *(highest payoff)*

**Why:** the setup-quality / trade-outcome / prop models are the ones meant to make the bot
*decide better*, and they're untrainable on ~50–80 trades (G4). The fix is well-trodden:
manufacture a **dense, properly-labeled** dataset of *hypothetical* setups instead of
waiting for real closed trades. Depends on Phase 0 (need purged CV + honest holdout to
trust the result, and the live-vs-synthetic domain-shift check).

### Session 1.1 — Triple-barrier labeler → `setup_candidates` dataset family *(Tier-1)*
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

### Session 1.2 — Meta-labeling model (the proper "should-I-take-this-trade") *(Tier-1 trainer/family; Tier-3 manifest)*
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

### Session 1.3 — Backtest-augmented per-trade labels *(Tier-1; closes MB-20260530-001)*
- **Deliverable:** have the backtest harnesses emit **per-trade rows** in the
  `setup_labels`/`trade_outcomes` schema (entry/SL/TP/outcome/r_multiple + signal-time
  features), tagged `source=backtest`; extend those families with a `source` column + an
  `include_backtest` flag. **Train on live+backtest, evaluate on a REAL-trade holdout only.**
  Exclude `execution_quality` (it learns real slippage; synthetic fills would poison it).
- **Pairs with** Phase 1.1 (both are "manufacture labels from history") and the 5y window /
  FVG-strategy backtesting work.
- **Effort:** M–L.

### Session 1.4 — Cross-symbol transfer *(Tier-1 experiment; Tier-3 manifest)*
- **Deliverable:** joint BTC+MES training (or pretrain-on-liquid-proxy → fine-tune) for the
  regime and decision families — a cheap small-data lever we don't use, natural since we
  already run two symbols.
- **Success:** transfer config beats the per-symbol model on the smaller-data symbol (MES)
  under purged WF-CV.
- **Effort:** M.

---

## Phase 2 — Better features *(high ROI; attacks "volatile won't separate" at the input)*

**Why:** the regime models' weak volatile separation (G5) is more a *label+feature* problem
than a capacity problem. Range-based vol estimators and microstructure flow are the highest
proven ROI per hour after Phase 1. Caveat from the research: **microstructure alpha decays**
— engineer it, monitor it via drift, don't assume permanence.

### Session 2.1 — Range-based volatility estimators *(Tier-1 family; Tier-3 manifest)*
- **Deliverable:** add **Yang-Zhang** (handles overnight gaps + drift, ~8× efficiency) and
  **Garman-Klass** vol to `market_features`; let regime manifests select the vol feature.
- **Lowest-effort, near-free regime-separation fix.** Re-run the regime eval (under Phase 0
  CV) to quantify the f1_volatile lift vs the current close-to-close-ish rolling vol.
- **Effort:** S.

### Session 2.2 — Order-flow / microstructure features *(Tier-2 — needs live L2 capture)*
- **Deliverable:** capture L1/L2 from Bybit + IBKR (new `market_raw` sub-stream + storage),
  compute **Order-Flow Imbalance (OFI)**, **VPIN** (volume-bucketed flow toxicity), spread,
  microprice. Bigger lift (a live capture path + storage), so scoped as its own Tier-2
  sub-project.
- **Reference:** Easley/López de Prado/O'Hara VPIN (2012); DeepLOB shows microstructure
  features transfer across instruments.
- **Effort:** L.

### Session 2.3 — Crypto funding-rate + open-interest features *(Tier-1 family; Tier-3 manifest)*
- **Deliverable:** funding-rate **z-score / extremes** and **open-interest change** from
  Bybit. Research nuance: funding is mostly a *trailing* byproduct of momentum — its signal
  is in the *extremes*, not the level. Cheap, high-value, unused today.
- **Effort:** S–M.

### Session 2.4 — Cross-asset/macro for MES + wire `account_context` *(Tier-1 family; Tier-3 manifest)*
- **Deliverable:** DXY / VIX-term-structure / rates conditioning features for MES; and wire
  the existing-but-**unused** `account_context` family (equity curve, daily PnL, open-trade
  count) into the decision models.
- **Effort:** M.

---

## Phase 3 — Regime plumbing + modeling *(unblocks the promotion pipeline)*

**Why:** today's `/ml-review` keeps reporting the regime promotion is jammed both ways —
the only head with shadow evidence (5m) is the weakest, the strong heads (1h/MES) get zero
track record (G6, MB-20260529-001). This phase fixes the plumbing first, then improves the
model.

### Session 3.1 — Per-bar regime scoring path *(Tier-2 — live runtime; HIGHEST-LEVERAGE UNBLOCK)*
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

### Session 3.2 — Causal HMM / GMM regime family *(Tier-1 experiment)*
- **Deliverable:** an alternative regime trainer using a **causal (filtered, not smoothed)**
  Gaussian HMM (`hmmlearn`) and/or GMM/change-point (`ruptures`) on range-based vol features
  (Phase 2.1) — naturally recency-weighted, interpretable, posterior-probability output.
- **Discipline (mandatory):** use **filtered** (causal) probabilities only — Viterbi/
  forward-backward smoothing leaks the future and inflates backtests. Heed the credible
  "illusion of regimes" dissent: validate OOS under purged WF-CV, compare head-to-head vs
  the LightGBM regime head, and only keep it if it adds OOS edge.
- **Effort:** M.

### Session 3.3 — Regime-router phase-4 detector *(ties to MB-20260601-002; Tier-2/3)*
- **Deliverable:** when the regime router's phase-4 is taken up, wire the best
  **non-collapsing** regime head (today `btc-regime-1h-lgbm-v2`, f1_vol 0.45 — NOT the
  collapsed `regime-classifier-baseline-v0`, f1_vol 0) as the classifier detector, after it
  has accrued a shadow track record (depends on 3.1). Reconcile the
  `regime-classifier-baseline-v0` manifest(shadow)/registry(research_only) stage drift.
- **Effort:** S–M (mostly wiring + the stage-drift fix).

---

## Phase 4 — MLOps maturation

**Why:** we already run a strong shadow→advisory ladder and KS/PSI drift — Phase 4 is the
"~70% there" polish that makes retraining efficient and promotion mechanical. Closes G3
(retrain trigger) and G7 (gates).

### Session 4.1 — Drift-triggered, recency-weighted retraining *(Tier-1/2)*
- **Deliverable:** add **ADWIN** (`river`) online drift detection on streaming features so
  retrains fire on *drift*, not just the fixed daily timer; couple with the recency-weighted
  windows from Phase 0.2. Another angle on G3.
- **Effort:** M.

### Session 4.2 — Experiment tracking + train/serve parity *(Tier-1)*
- **Deliverable:** lightweight run tracking (MLflow/W&B or a disciplined runs table in
  `trainer_store.db`) and a feature-versioning check that guarantees the live shadow path
  computes features identically to the trainer (catch the classic "feature computed
  differently in backtest vs live" bug). We already approximate a registry/feature store
  via the federated `trade_journal.db`/`trainer_store.db`.
- **Effort:** M.

### Session 4.3 — Full champion-challenger automation *(Tier-1 compute; Tier-3 enforce)*
- **Deliverable:** close the loop on Phase 0.4 — every model carries a live PASS/FAIL gate
  status; `/ml-review` promotion recommendations are derived mechanically from it; the
  operator's role narrows to approving a green gate.
- **Effort:** S.

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
