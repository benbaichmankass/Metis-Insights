# AI Model Platform — Architecture

> **Status:** Canonical (AI scope). Adopted **S-AI-WS1**
> (2026-05-10). Refreshed through **S-AI-WS5-C**.
>
> **Authority:** Canonical for AI-specific architecture. System-wide
> canonical: [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md).
>
> **Owns:** ROADMAP.md milestones **M9** + **M10**.
>
> **Companion docs:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md);
> [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md);
> [`docs/data/`](../data/);
> [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md);
> [`docs/ml/`](../ml/);
> [`docs/sprint-plans/ai-traders/`](../sprint-plans/ai-traders/).

## Architectural principles

1. Live trading stability over feature growth.
2. Specialist models, not one master model.
3. Baselines before advanced families.
4. Reproducible datasets and training.
5. Promotion gates before live influence.
6. Doc updates are part of DoD.

## Architectural position

**No “master model.”** One orchestration layer consumes specialist
outputs + deterministic rules. **Deterministic risk controls are
outside the AI layer.** Model-unavailable degrades to deterministic.

## Five-layer model

| Layer | Owns | Examples |
|---|---|---|
| 1. Data | Market, account, news, labels, backtests | `ml/datasets/` (WS3 + WS5-A + WS5-B-PART-1) |
| 2. Feature / context | Engineered features, regime / account / mission context | `ml/datasets/families/market_features.py` (WS5-B-PART-2 PR 2B); future `ml/features/` for richer derivations |
| 3. Model | Specialist models | trainer/evaluator/predictor framework live (WS4 + WS4-FU); first baseline (WS5-A); regime-classifier baseline (WS5-B-PART-2 PR 2B) |
| 4. Orchestration | Combines specialist outputs | future coordinator extension; model registry live (WS4) |
| 5. Control (deterministic) | Risk rules, hard caps, broker validation, kill-switch | unchanged |

Layer 5 is the immutable safety floor.

## Current State — audit (verified 2026-05-10)

**Live trading path is fully deterministic.** No model wired in.

### Research and validation (experimental)

| Concern | Owner files | Notes |
|---|---|---|
| Pipeline types (WS2) | `src/pipeline/types.py` | Live-path migration deferred. |
| Dataset framework (WS3) | `ml/datasets/` | Three buildable families. |
| `backtest_results` (WS3) | `ml/datasets/families/backtest_results.py` | Read-only against `trade_journal.db`. |
| `trade_outcomes` (WS5-A) | `ml/datasets/families/trade_outcomes.py` | Derived `won = pnl > 0` label. |
| `market_raw` (WS5-B-PART-1) | `ml/datasets/families/market_raw.py` + `ml/datasets/adapters/{base, csv, bybit_offvm, registry}.py` | **Adopted 2026-05-10 (S-AI-WS5-B-PART-1).** Pluggable adapter framework. CSV adapter live; Bybit off-VM env-gated (`ICT_OFFVM_BUILD_HOST=1`); fetch wiring lands in S-AI-WS5-B-PART-2 PR 2A. |
| `market_features` (WS5-B-PART-2 PR 2B) | `ml/datasets/families/market_features.py` | **Adopted 2026-05-10.** Derives `log_return`, `rolling_log_return_vol`, `vol_bucket`, forward-window stats, and a 3-class `regime_label` from a built `market_raw` dataset. Forward-window labels guarantee no feature/label leakage by construction; `leakage_test_status: passed`. |
| Regime classifier baseline (WS5-B-PART-2 PR 2B) | `ml/trainers/regime_classifier.py`, `ml/evaluators/multiclass_classification.py`, `ml/predictors/{multiclass, per_bucket_multiclass}.py`, `ml/configs/baseline-regime-classifier.yaml` | **Adopted 2026-05-10.** Per-bucket modal multinomial classifier targeting `regime_label`. `MulticlassPredictor` ABC + per-class probabilities; `MulticlassClassificationEvaluator` reports accuracy + per-class precision/recall/f1 + macro/weighted f1. Trainer enforces leakage discipline at `fit(...)` time. |
| `setup_labels` (WS5-C) | `ml/datasets/families/setup_labels.py` | **Adopted 2026-05-10.** Reads CLOSED non-backtest trades with non-empty `setup_type`; emits `r_multiple = pnl_percent / risk_pct` capped at `±r_cap`. Same leakage discipline as `trade_outcomes` (outcome columns excluded by trainer). |
| `setup_candidates` (S-MLOPT-S5) | `ml/datasets/families/setup_candidates.py` + `ml/datasets/labeling/triple_barrier.py` | **Added 2026-06-03 (M14 Phase 1.1).** Manufactures thousands of *synthetic* labeled candidate setups from `market_raw` bar history: de Prado CUSUM event sampling + triple-barrier (TP/SL/timeout) labels sized to local vol, conservative realistic fills. Features past-only, label future-only (entry at next-bar open) → `leakage_test_status: passed` by construction. Carries `is_live_trade` so a model trained on it is evaluated on a REAL-trade holdout (domain-shift discipline). Trainer excludes outcome columns (`label`/`won`/`r_multiple`/`ret`/`barrier_touched`/`holding_bars`). Feeds the S-MLOPT-S6 meta-labeling model. |
| Setup-quality scorer baseline (WS5-C) | `ml/trainers/per_strategy_winrate.py` (extended), `ml/configs/baseline-setup-quality.yaml`, `ml/evaluators/regression.py` | **Adopted 2026-05-10.** Reuses `PerStrategyWinRateTrainer` with new `target_kind: numeric_mean` config knob (per-bucket mean of any numeric target, not just win rate). Pairs with `RegressionEvaluator` (MSE / MAE) targeting `r_multiple`. |
| Training center (WS4) | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/` | YAML manifests + ABCs + filesystem registry + runner + CLI. |
| Predictor abstraction (WS4-FU) | `ml/predictors/` | Decouples evaluators from trainer state shape. |
| Time-aware splitters (WS4-FU; purged WF-CV S-MLOPT-S1) | `ml/experiments/splitters.py` | `holdout` / `time_aware_holdout` / `walk_forward` / `purged_walk_forward` (opt-in multi-fold purged & embargoed CV, de Prado AFML Ch. 7; runner pools per-fold metrics). |
| `compare` CLI (WS4-FU) | `ml/cli.py` | Side-by-side metric diff. |
| First baseline (WS5-A) | `ml/{trainers/per_strategy_winrate, evaluators/classification}.py`, paired manifests | Per-strategy historical winrate + global-only sanity. |
| ML scaffolding (legacy) | `ml/config/`, `ml/src/` | Vestigial. WS10 cleanup. |

### Planned

- Builders for `account_context`, `review_journal`.
- WS5-D onwards (exec quality; post-trade review; prop mission policy).
- Aggregated walk-forward for the plain `walk_forward` strategy (the
  multi-fold averaged path now exists for `purged_walk_forward`, S-MLOPT-S1).
- Per-strategy detail metrics artifact.
- HF publication CLI subcommand.
- Shadow-mode runtime hook (WS7).
- Drift monitoring (WS8).
- Architecture-change checklist + PR template (WS10).
- Migration of live runtime call sites onto WS2 types.
- Registry concurrent-writer locking.

### Forbidden (live-runtime safety floor)

- AI output bypassing risk caps, broker validation, prop-firm
  restrictions, or kill-switch.
- Heavy training jobs running on the Oracle live VM (WS9).
- Heavy dataset builds running on the Oracle live VM. **The
  `bybit_v5_offvm` adapter enforces this with the
  `ICT_OFFVM_BUILD_HOST=1` env-gate (S-AI-WS5-B-PART-1).**
- Live model influence introduced without staged promotion +
  explicit operator approval (WS7).
- Schema / boundary changes shipped without updating this doc.
- Constructing `ExecutionIntent` from a model code path.
- Auto-publication of any dataset to Hugging Face.
- Editing past `StatusEvent` entries in the model registry
  (append-only, S-AI-WS4 rule).
- Promoting a model to `live-approved` or `champion` without
  operator approval recorded in `--by` + `--reason`.
- Consuming outcome columns (`pnl`, `pnl_percent`) as features
  when targeting `won` against the `trade_outcomes` family
  (S-AI-WS5-A leakage discipline).
- **Setting `ICT_OFFVM_BUILD_HOST=1` on the Oracle live VM**
  (S-AI-WS5-B-PART-1 rule). Build hosts only.
- Consuming `regime_label`, `forward_log_return`, or
  `forward_log_return_vol` as features when targeting
  `regime_label` against the `market_features` family
  (S-AI-WS5-B-PART-2 PR 2B leakage discipline). The
  `RegimeClassifierTrainer` enforces this at `fit(...)` time.

## Architecture Update Rule

Review this doc in the same PR when any of: layer boundaries,
stage contracts, dataset families / schemas / leakage discipline,
adapter framework, training-center contracts, deployment stages,
Oracle / HF responsibilities, or anything in `Forbidden` changes.

## Architecture Change Log

| Date | Sprint | Change | Operator impact |
|---|---|---|---|
| 2026-05-10 | S-AI-WS1 | AI-platform doc created. | None. |
| 2026-05-10 | S-AI-WS2 | Stage names locked; typed schemas. | None. |
| 2026-05-10 | S-AI-WS3 | Dataset framework + `backtest_results`. | None. |
| 2026-05-10 | S-AI-WS4 | Training center: manifest + ABCs + registry + runner + CLI. | None. |
| 2026-05-10 | S-AI-WS5-A | First baseline + `trade_outcomes` + leakage rule. | None. |
| 2026-05-10 | S-AI-WS4-FU | Predictor abstraction + splitters + `compare` CLI + global-only sanity baseline + market_raw multi-source design pinned. | None. |
| 2026-05-10 | S-AI-WS5-B-PART-1 | `market_raw` adapter framework + CSV adapter + Bybit off-VM scaffold (env-gated; fetch wiring filed). New Forbidden rule: don't set `ICT_OFFVM_BUILD_HOST=1` on the live VM. | None — additive; Bybit shell raises NotImplementedError until operator wires the fetch. |
| 2026-05-10 | S-AI-WS5-B-PART-2 PR 2B | `market_features` family (rolling vol + 3-class regime label, forward-window leakage discipline) + `RegimeClassifierTrainer` (per-bucket modal) + `MulticlassPredictor` + `MulticlassClassificationEvaluator` + `baseline-regime-classifier.yaml` manifest. New Forbidden rule: don't use forward-window or label columns as features against `regime_label`. | None — additive; research-only baseline. |
| 2026-05-10 | S-AI-WS5-C | `setup_labels` family (CLOSED setup-tagged trades + r_multiple) + `PerStrategyWinRateTrainer` extended with `target_kind: numeric_mean` knob + `baseline-setup-quality.yaml` manifest. Architecture-canonical doc gains an explicit "AI-traders training workflow" section anchored on the `/health-review` skill's per-trade decision grades as labelled feedstock. | None — additive; research-only baseline. |
| 2026-06-03 | S-MLOPT-S1 (M14 Phase 0.1) | Purged & embargoed walk-forward CV: `purged_walk_forward` splitter (de Prado AFML Ch. 7) + reusable two-sided `purge_and_embargo_indices` primitive + opt-in multi-fold runner path (pooled metrics + `cv_folds.json` + full-data refit) + `scripts/ml/eval_split_compare.py`. Master plan: `docs/ml/optimization-roadmap.md`. | None — additive, opt-in; no manifest default eval changed, no live-path file touched. |
| 2026-06-03 | S-MLOPT-S2 (M14 Session 0.2) | Opt-in trainer `sample_weight` knob (`ml/trainers/sample_weights.py`) — recency half-life decay × de Prado average uniqueness, folded into both LightGBM trainers; `scripts/ml/window_recency_sweep.py`. Trainer-VM sweep (#2677) confirmed MB-20260601-001 (5y window dilutes regime `f1_volatile`) and that recency decay recovers it. Adopted on the three `btc-regime-*-lgbm-v2` manifests (1h=90d, 15m=60d, 5m=60d) via Tier-3 PR #2679. | None — additive, opt-in; manifest adoption (#2679) was operator-approved. |
| 2026-06-03 | S-MLOPT-S3 (M14 Session 0.3) | Optuna HPO harness `scripts/ml/hpo_sweep.py` — TPE + MedianPruner over the S-MLOPT-S1 purged WF-CV folds (forces purged folds so HPO can't tune to leakage); searches `lgbm_params`/`n_iter` (+ optional class-weight); emits a best-vs-baseline proposal. Pure CV core unit-tested without Optuna. | None — additive Tier-1 tooling; emits proposals only. Adopting tuned params in a manifest is Tier-3. |
| 2026-06-03 | S-MLOPT-S4 (M14 Session 0.4) | Promotion gates that COMPUTE PASS/FAIL: new `ml/promotion/oos_edge.py` (`compute_oos_edge` — candidate-vs-baseline OOS edge under the S-MLOPT-S1 purged WF-CV, reusing `iter_folds` + the runner's pooled-fold metric; never a holdout). Added the required `oos_edge` gate to `ml/promotion/gates.py`; `drift_clean` now gates on numeric KS ≤ 0.2 / PSI ≤ 0.25. Wired through `gate-check`/`stage-guard` (`--datasets-root`) + the `/ml-review` skill. Trainer-VM run reproduced G4 (lgbm setup-quality LOSES to the constant baseline OOS). Closes G7. | None — Tier-1 compute, read-only; reports a go/no-go packet. Enforcement of the shadow→advisory flip stays Tier-3, operator-gated — no auto-promote, no live-path edit. |
| 2026-06-03 | S-MLOPT-S5 (M14 Session 1.1) | Triple-barrier labeler + dense candidate dataset: new `ml/datasets/labeling/triple_barrier.py` (de Prado CUSUM event sampling + TP/SL/timeout barriers, conservative realistic-fill modeling) and the `setup_candidates` dataset family (`ml/datasets/families/setup_candidates.py`) that manufactures thousands of leak-free labeled candidate setups from `market_raw` bar history. Features past-only, label future-only (entry at next-bar open) → `leakage_test_status: passed` by construction; `is_live_trade` split column reserves the mandatory REAL-trade holdout. Registered in the family registry. | None — additive Tier-1 dataset family + labeler; read-only against built `market_raw` datasets, no live-path file. A trainer/manifest consuming it (S-MLOPT-S6 meta-labeling) is Tier-2/3. |
| 2026-06-03 | S-MLOPT-S6 (M14 Session 1.2) | Meta-labeling decision model: reuses `LightGBMRegressionTrainer` on the binary `won` target + `ClassificationEvaluator` (regress→probability). `setup_candidates` gained a `live_trades_db` source that appends REAL closed trades in the same past-only feature space (`is_live_trade: true`); new `live_holdout` split (`ml/experiments/splitters.py`) trains on synthetic and evaluates on the REAL trades (de-Prado domain-shift discipline). Manifest `ml/configs/setup-candidates-metalabel-v1.yaml` ships at `research_only`. | None for the Tier-1 trainer/family/splitter (additive, read-only). **The manifest + any promotion past `shadow` are Tier-3 (operator-gated)** — the model ships at research_only; gate-check/ml-review produce promotion evidence, the operator decides. |
