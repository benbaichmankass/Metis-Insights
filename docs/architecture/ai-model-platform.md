# AI Model Platform — Architecture

> **Status:** Canonical (AI scope). Adopted **S-AI-WS1**
> (2026-05-10). Refreshed through **S-AI-WS5-B-PART-1**.
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
| 2. Feature / context | Engineered features, regime / account / mission context | future `ml/features/` |
| 3. Model | Specialist models | trainer/evaluator/predictor framework live (WS4 + WS4-FU); first baseline (WS5-A) |
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
| `market_raw` (WS5-B-PART-1) | `ml/datasets/families/market_raw.py` + `ml/datasets/adapters/{base, csv, bybit_offvm, registry}.py` | **Adopted 2026-05-10 (S-AI-WS5-B-PART-1).** Pluggable adapter framework. CSV adapter live; Bybit off-VM scaffold env-gated (`ICT_OFFVM_BUILD_HOST=1`); actual exchange call filed for the operator to wire on a non-VM host. |
| Training center (WS4) | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/` | YAML manifests + ABCs + filesystem registry + runner + CLI. |
| Predictor abstraction (WS4-FU) | `ml/predictors/` | Decouples evaluators from trainer state shape. |
| Time-aware splitters (WS4-FU) | `ml/experiments/splitters.py` | `holdout` / `time_aware_holdout` / `walk_forward`. |
| `compare` CLI (WS4-FU) | `ml/cli.py` | Side-by-side metric diff. |
| First baseline (WS5-A) | `ml/{trainers/per_strategy_winrate, evaluators/classification}.py`, paired manifests | Per-strategy historical winrate + global-only sanity. |
| ML scaffolding (legacy) | `ml/config/`, `ml/src/` | Vestigial. WS10 cleanup. |

### Planned

- Builders for `market_features`, `setup_labels`, `account_context`,
  `review_journal`.
- WS5-B-PART-2 onwards (regime classifier + actual Bybit off-VM
  fetch wiring; setup quality scorer; exec quality; post-trade
  review; prop mission policy).
- Aggregated walk-forward.
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
