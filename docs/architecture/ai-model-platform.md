# AI Model Platform — Architecture

> **Status:** Canonical (AI scope). Adopted in **S-AI-WS1**
> (2026-05-10). Refreshed through S-AI-WS4-FU.
>
> **Authority:** Canonical for AI-specific architecture. System-wide
> canonical: [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md).
>
> **Owns:** ROADMAP.md milestones **M9** + **M10**.
>
> **Companion docs:**
> - [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (master plan).
> - [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md) (WS2).
> - [`docs/data/{dataset-taxonomy,dataset-schema,versioning-policy}.md`](../data/) (WS3 + WS5-A).
> - [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md) (WS3).
> - [`docs/ml/{training-center,model-registry-policy}.md`](../ml/) (WS4 + WS4-FU).
> - [`docs/sprint-plans/ai-traders/`](../sprint-plans/ai-traders/).

## Purpose

Single source of truth for how AI models fit into the trading
platform.

## Architectural principles

1. Live trading stability over feature growth.
2. Specialist models, not one master model.
3. Baselines before advanced families.
4. Reproducible datasets and training.
5. Promotion gates before any live influence.
6. Doc updates are part of DoD.

## Architectural position

**No “master model.”** One orchestration layer consumes specialist
outputs + deterministic rules; orchestrator may rank, combine, or
veto, but the live system stays inspectable and modular.

**Deterministic risk controls are outside the AI layer.** Risk
gating, broker validation, account restrictions, kill-switch, and
order packaging must not depend on model availability or model
decisions. Model-unavailable degrades to deterministic, never to
permissive bypass.

## Five-layer model

| Layer | Owns | Examples |
|---|---|---|
| 1. Data | Market, account, news, labels, backtests, post-trade reviews | `ml/datasets/` (WS3 + WS5-A) |
| 2. Feature / context | Engineered features, regime / account / mission context | future `ml/features/` |
| 3. Model | Specialist models | trainer/evaluator framework live (WS4 + WS4-FU); Predictor abstraction live (WS4-FU); first baseline live (WS5-A) |
| 4. Orchestration | Combines specialist outputs | future coordinator extension; model registry live (WS4) |
| 5. Control (deterministic) | Risk rules, hard caps, broker validation, kill-switch | `src/units/accounts/risk.py`, `src/runtime/orders.py`, etc. |

Layer 5 is the immutable safety floor.

## Current State — audit (verified 2026-05-10)

**Live trading path is fully deterministic.** No model wired into
live decisioning.

### Live (in production)

Unchanged since WS1. Trading entrypoint: `src/main.py` →
`src/runtime/pipeline.py`. See `ARCHITECTURE-CANONICAL.md` for the
full live-runtime audit.

### Research and validation (experimental)

| Concern | Owner files | Notes |
|---|---|---|
| Backtest harness | `src/backtest/` | Deterministic |
| Pipeline types (WS2) | `src/pipeline/types.py` | Frozen-dataclass `TradeCandidate`, `ExecutionIntent`. Live-path migration deferred. |
| Dataset framework (WS3 + WS5-A) | `ml/datasets/` | Two buildable families: `backtest_results`, `trade_outcomes`. |
| Training center (WS4) | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/` | YAML manifests + Trainer/Evaluator ABCs + filesystem registry + experiments runner + umbrella CLI. |
| Predictor abstraction (WS4-FU) | `ml/predictors/` | `ConstantPredictor`, `PerGroupPredictor`. Decouples evaluators from trainer state shape. |
| Time-aware splitters (WS4-FU) | `ml/experiments/splitters.py` | `holdout`, `time_aware_holdout`, `walk_forward` dispatched via `evaluator_config.split_strategy`. |
| `compare` CLI (WS4-FU) | `ml/cli.py` | Side-by-side metric diff over two registry entries. |
| First baseline (WS5-A) | `ml/trainers/per_strategy_winrate.py`, `ml/evaluators/classification.py`, `ml/configs/baseline-trade-outcome-{winrate,global}.yaml` | Per-strategy historical winrate + classification metrics; paired global-only sanity baseline (WS4-FU). |
| ML scaffolding (legacy) | `ml/config/`, `ml/src/` | Vestigial S-004/S-005/S-006. WS10 cleanup. |

### Planned (not yet implemented)

- Builders for `market_raw` (multi-source adapter design pinned
  for WS5-B), `market_features`, `setup_labels`, `account_context`,
  `review_journal`.
- WS5-B onwards (regime classifier, setup quality scorer, exec
  quality, post-trade review, prop mission policy).
- Aggregated walk-forward (averaging metrics across folds).
- Per-strategy detail metrics artifact alongside scalar registry
  metrics.
- Hugging Face publication CLI subcommand.
- Shadow-mode / advisory-mode runtime hook (WS7).
- Feature drift / outcome drift monitoring (WS8).
- Architecture-change checklist + PR template (WS10).
- Migration of live runtime call sites onto WS2 types.
- Registry concurrent-writer locking.

### Forbidden (live-runtime safety floor)

- AI output bypassing risk caps, broker validation, prop-firm
  restrictions, or kill-switch.
- Heavy training jobs running on the Oracle live VM (WS9).
- Heavy dataset builds running on the Oracle live VM.
- Live model influence introduced without staged promotion +
  explicit operator approval (WS7).
- Schema / boundary changes shipped without updating this doc.
- Constructing `ExecutionIntent` from a model code path. Only
  `src/runtime/orders.py` may.
- Auto-publication of any dataset to Hugging Face.
- Editing past `StatusEvent` entries in the model registry
  (append-only, S-AI-WS4 rule).
- Promoting a model to `live-approved` or `champion` without
  operator approval recorded in `--by` + `--reason`.
- Consuming outcome columns (`pnl`, `pnl_percent`) as features
  when targeting `won` against the `trade_outcomes` family
  (S-AI-WS5-A leakage discipline).

## Target State

Unchanged from S-AI-WS4-FU. Stage names locked in
`src/pipeline/types.py`; per-stage I/O in
`docs/pipeline/stage-contracts.md`; datasets in `ml/datasets/`;
training factory in `ml/`. Component diagram unchanged from
S-AI-WS4 (Mermaid in earlier revisions; preserved in git history).

## Known Gaps (as of 2026-05-10)

- **Legacy `ml/config/` and `ml/src/` are vestigial.** WS10 cleanup.
- **Most dataset families not buildable.** Live: `backtest_results`,
  `trade_outcomes`. Pending: `market_raw`, `market_features`,
  `setup_labels`, `account_context`, `review_journal`.
- **WS5-A baseline is intentionally trivial.** Real specialist
  models follow in WS5-B onwards.
- **HF publication is manual.**
- **No shadow-mode runtime hook.** WS7.
- **No feature / outcome drift monitoring.** WS8.
- **`docs/architecture.md` partly stale.** WS10.
- **WS2 types not adopted by live path.** Tier 2 follow-up.
- **Architecture-change checklist not enforced.** WS10.
- **Aggregated walk-forward not implemented.** Splitter framework
  (WS4-FU) returns folds via `split_walk_forward(...)`; the runner
  uses single-split form via `split(...)`. Aggregation requires
  runner + metrics-format changes; filed.
- **Per-strategy detail metrics artifact not surfaced.** Scalar
  headlines only.
- **Registry concurrent-writer locking absent.**
- **`market_raw` multi-source adapter framework not yet
  implemented.** Design pinned in `ws5-baseline-models.md`
  (operator directive 2026-05-10).

## Architecture Update Rule

Review and update this doc in the same PR when any of:

- Layer boundaries change.
- Stage names or I/O contracts change.
- Dataset families, schemas, or leakage discipline change.
- Versioning / retention policy changes.
- HF publishing workflow changes.
- Training-center directory layout, manifest schema, runner
  pipeline, predictor abstraction, split strategies, or registry
  state machine / promotion gates change.
- Deployment stage list changes.
- Oracle vs HF runtime responsibilities change.
- Anything tagged `Forbidden` above changes.

## Architecture Change Log

| Date | Sprint | Change | Operator impact |
|---|---|---|---|
| 2026-05-10 | S-AI-WS1 | AI-platform doc created. | None. |
| 2026-05-10 | S-AI-WS2 | Stage names locked; typed schemas. | None. |
| 2026-05-10 | S-AI-WS3 | Dataset framework + `backtest_results` builder. | None. |
| 2026-05-10 | S-AI-WS4 | Training center: manifest + ABCs + registry + runner + CLI. | None. |
| 2026-05-10 | S-AI-WS5-A | First baseline + `trade_outcomes` family + leakage discipline rule. | None. |
| 2026-05-10 | S-AI-WS4-FU | Predictor abstraction (decouples evaluators from trainer state); time-aware + walk-forward splitters; `compare` CLI subcommand; global-only sanity baseline manifest; `market_raw` multi-source design pinned. Refactor: existing evaluators now use `_resolve_predictor` instead of reading state-specific keys. Backward-compatible: `state['trainer']` qualname is already populated by every trainer's `fit()`. | None — additive code; refactor preserves WS4 + WS5-A behavior. |
