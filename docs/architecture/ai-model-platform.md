# AI Model Platform — Architecture

> **Status:** Canonical (AI scope). Adopted in **S-AI-WS1**
> (2026-05-10).
>
> **Authority:** Canonical for AI-specific architecture. System-wide
> canonical: [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md).
>
> **Owns:** ROADMAP.md milestones **M9** + **M10**.
>
> **Companion docs:**
> - [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (master plan).
> - [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md) (WS2).
> - [`docs/data/{dataset-taxonomy,dataset-schema,versioning-policy}.md`](../data/) (WS3).
> - [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md) (WS3).
> - [`docs/ml/{training-center,model-registry-policy}.md`](../ml/) (WS4).
> - [`docs/sprint-plans/ai-traders/`](../sprint-plans/ai-traders/).

## Purpose

Single source of truth for how AI models fit into the trading
platform. Names what is live, experimental, planned, forbidden.

## Architectural principles (AI-specific)

1. Live trading stability takes precedence over feature growth.
2. Use specialist models, not one opaque master model.
3. Start with baselines before advanced model families.
4. Make datasets and training reproducible.
5. Require promotion gates before any live influence.
6. Update this doc as part of the DoD when boundaries / schemas /
   stages change.

## Architectural position

**No “master model to rule them all.”** One orchestration layer
consumes specialist outputs + deterministic rules; the
orchestrator may rank, combine, or veto, but the live system stays
inspectable and modular.

**Deterministic risk controls are outside the AI layer.** Risk
gating, broker validation, account restrictions, kill-switch, and
order packaging must not depend on model availability or model
decisions. Model-unavailable degrades to deterministic, never to
permissive bypass.

## Five-layer model

| Layer | Owns | Examples |
|---|---|---|
| 1. Data | Market, account, news, labels, backtests, post-trade reviews | `runtime_logs/`, `trade_journal.db`, `experiments/`, `ml/datasets/` (WS3 + WS5-A) |
| 2. Feature / context | Engineered features, regime / account / mission context | future `ml/features/` |
| 3. Model | Specialist models | trainer/evaluator framework live (WS4); first baseline (outcome probability) live (WS5-A) |
| 4. Orchestration | Combines specialist outputs into a candidate / veto | future coordinator extension hooked off `src/core/coordinator.py`; model registry live (WS4) |
| 5. Control (deterministic) | Risk rules, hard caps, account restrictions, broker validation, order packaging, audit logs, kill-switch | `src/units/accounts/risk.py`, `src/units/accounts/prop_risk.py`, `src/runtime/risk_counters.py`, `src/runtime/orders.py`, `src/runtime/closed_flat_invariant.py` |

Layer 5 is the immutable safety floor.

## Current State — audit (verified 2026-05-10)

**The live trading path is fully deterministic.** No model wired
into live decisioning.

### Live (in production)

Unchanged since WS1: `src/main.py` → `src/runtime/pipeline.py`,
strategies in `src/units/strategies/`, risk gating in
`src/units/accounts/risk.py`, broker execution in
`src/units/accounts/execute.py`, persistence in
`trade_journal.db`. Operator surface: `src/bot/telegram_query_bot.py`,
FastAPI `src/web/api/`, comms artifacts under `comms/`.

### Research and validation (experimental, WS1–WS5-A additions)

| Concern | Owner files | Notes |
|---|---|---|
| Backtest harness | `src/backtest/`, `scripts/run_backtest.sh` | Deterministic |
| Multi-symbol / multi-timeframe runs | `experiments/` | Evidence capture |
| M5 strategy testing flow | `src/bot/test_strategy_consumer.py`, `runtime_logs/validation.jsonl` | Auto-consumed `test_strategy:<name>` |
| ML scaffolding (legacy) | `ml/config/`, `ml/src/{collect_data,test_breakout_strategy}.py` | Vestigial S-004/S-005/S-006. WS10 cleanup. |
| Pipeline types (WS2) | `src/pipeline/types.py`, `tests/pipeline/` | Frozen-dataclass `TradeCandidate`, `ExecutionIntent`, `StageDecision`. Live-path migration deferred. |
| Dataset framework (WS3) | `ml/datasets/{metadata, builder, validate, cli}`, `tests/ml/datasets/` | Stdlib-only reproducible builders; first family `backtest_results` reads `trade_journal.db` read-only. Append-only versioning. |
| Training center (WS4) | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/`, `tests/ml/test_*` | YAML manifest + Trainer/Evaluator ABCs + filesystem registry with state machine + experiments runner + umbrella CLI. |
| Outcome-probability baseline (WS5-A) | `ml/datasets/families/trade_outcomes.py`, `ml/trainers/per_strategy_winrate.py`, `ml/evaluators/classification.py`, `ml/configs/baseline-trade-outcome-winrate.yaml`, `tests/ml/test_per_strategy_winrate.py` | **Adopted 2026-05-10 (S-AI-WS5-A).** First specialist baseline. `trade_outcomes` family reads CLOSED non-backtest trades read-only and emits `won = pnl > 0` label. Per-strategy winrate trainer + classification evaluator (accuracy/precision/recall/f1/brier). Round-trips through the WS4 harness. |

### Planned (not yet implemented)

- Builders for `market_raw`, `market_features`, `setup_labels`,
  `account_context`, `review_journal`.
- WS5-B onwards (regime classifier, setup quality scorer, exec
  quality, post-trade review, prop mission policy).
- Hugging Face publication CLI subcommand (manual flow today).
- Shadow-mode / advisory-mode runtime hook (registry tier exists;
  runtime consumer doesn't — WS7).
- Feature drift / outcome drift monitoring (WS8).
- Architecture-change checklist + PR template (WS10).
- Migration of live runtime call sites onto WS2 types.
- Walk-forward / time-aware dataset splitters.
- Generic `predict()` interface decoupling trainer state from
  evaluator.

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
- **Consuming outcome columns (`pnl`, `pnl_percent`) as features
  when the target is `won` against the `trade_outcomes` family**
  (S-AI-WS5-A leakage discipline).

## Target State

The target architecture extends the existing pipeline with a model
layer and orchestration hook, **without weakening the deterministic
floor**. Stage names locked in `src/pipeline/types.py`; per-stage
I/O in `docs/pipeline/stage-contracts.md`; datasets in
`ml/datasets/`; training factory in `ml/`.

### Stage map

| # | Stage (`StageName`) | Today | Target | Owning paths |
|---|---|---|---|---|
| 1 | `INGEST` | Connectors + market-data helpers | Deterministic | `src/exchange/`, `src/runtime/market_data.py` |
| 2 | `NORMALIZE` | Internal candle / tick representation | Deterministic | `src/runtime/market_data.py` |
| 3 | `CONTEXT` | Implicit | Deterministic + model-assisted | future `ml/features/`, `src/units/accounts/` |
| 4 | `SETUP` | Rule-based strategies | Deterministic; optional model-assist later | `src/units/strategies/`, `src/ict_detection/` |
| 5 | `SCORE` | Implicit | Model-assisted | future model layer + `src/core/coordinator.py` extension |
| 6 | `RISK` | Per-account caps + prop rules + counters | Deterministic only | `src/units/accounts/risk.py`, `prop_risk.py`, `src/runtime/risk_counters.py` |
| 7 | `PACKAGE` | Order construction + validation | Deterministic | `src/runtime/orders.py`, `src/runtime/validation.py` |
| 8 | `ROUTE` | Per-account dry/live | Deterministic | `src/units/accounts/execute.py`, `src/exchange/` |
| 9 | `CAPTURE` | Trade journal + audit log | Deterministic ingest; model-assisted enrichment | `trade_journal.db`, `runtime_logs/signal_audit.jsonl` |
| 10 | `REVIEW` | Manual | Model-assisted | future `ml/reports/`, `runtime_logs/` |

**Invariant:** stages 6, 7, and 8 must remain rejection-capable
for any upstream output regardless of source.

### Component diagram (target)

```mermaid
flowchart LR
    subgraph DATA["1. Data layer"]
        D1[market data\n<small>src/exchange, runtime/market_data</small>]
        D2[account state\n<small>src/units/accounts</small>]
        D3[news / events\n<small>src/news</small>]
        D4[backtests + datasets\n<small>experiments/, ml/datasets/</small>]
        D5[trade journal\n<small>trade_journal.db</small>]
        D6[review journal\n<small>future docs/ml/</small>]
    end

    subgraph FEAT["2. Feature / context layer"]
        F1[market features\n<small>future ml/features</small>]
        F2[regime context]
        F3[account / mission context]
    end

    subgraph MODEL["3. Model layer (specialists)"]
        M1[regime classifier]
        M2[setup quality scorer]
        M3[outcome probability\n<small>WS5-A</small>]
        M4[execution quality]
        M5[post-trade review]
        TRAINER[Trainer / Evaluator + Registry\n<small>ml/{trainers,evaluators,registry}</small>]
    end

    subgraph ORCH["4. Orchestration layer"]
        O1[coordinator\n<small>src/core/coordinator.py</small>\n<small>+ TradeCandidate</small>]
        REG[Model registry\n<small>ml/registry-store/</small>]
    end

    subgraph CTRL["5. Control layer (deterministic, immutable safety floor)"]
        C1[risk gating]
        C2[prop rules]
        C3[order validation → ExecutionIntent]
        C4[broker routing]
        C5[kill-switch]
    end

    DATA --> FEAT
    FEAT --> MODEL
    MODEL --> ORCH
    REG -.tier metadata.-> ORCH
    ORCH -->|TradeCandidate| CTRL
    CTRL -->|StageDecision VETO\nDETERMINISTIC| ORCH
    CTRL --> EXEC[broker / dry-run]
    EXEC --> D5
    D5 --> D6
    D6 -.feedback.-> DATA
```

## Known Gaps (as of 2026-05-10)

- **Legacy `ml/config/` and `ml/src/`.** Vestigial. WS3+WS4+WS5-A
  paths are live; legacy dirs need cleanup (WS10) or per-family
  rebuild.
- **Most dataset families are not yet buildable.** Live:
  `backtest_results` (WS3), `trade_outcomes` (WS5-A). Pending:
  `market_raw`, `market_features`, `setup_labels`,
  `account_context`, `review_journal`.
- **WS5-A baseline is intentionally trivial.** Per-strategy mean
  is the simplest non-degenerate model. Real specialist models
  follow in WS5-B onwards.
- **Hugging Face publication is manual.** CLI subcommand filed.
- **No shadow-mode runtime hook.** Registry tier exists; live
  trader doesn't consume any model signal. WS7.
- **No feature / outcome drift monitoring.** WS8.
- **`docs/architecture.md` partly stale.** Cleanup with WS10.
- **WS2 types not adopted by the live path.** Strategies still
  emit `OrderPackage`. Tier 2 follow-up.
- **Architecture-change checklist + PR template not enforced.**
  WS10.
- **Holdout splitter is order-preserving suffix only.** Walk-
  forward / time-aware splitters filed.
- **Trainer↔evaluator coupling is hard.** Generic `predict()`
  interface filed.
- **Per-strategy detail metrics are not surfaced** in registry
  entries (scalar headlines only). Future evaluation_detail.json
  artifact filed.

## Architecture Update Rule

Review and update this doc in the **same PR** when any of:

- Layer boundaries change.
- Stage names or stage I/O contracts change.
- Dataset families, schemas, or leakage discipline change.
- Versioning / retention policy changes.
- Hugging Face publishing workflow changes.
- Training-center directory layout, manifest schema, runner
  pipeline, or the model-registry status state machine /
  promotion gates change.
- Deployment stage list changes.
- Oracle vs Hugging Face runtime responsibilities change.
- Anything tagged `Forbidden` above changes.

## Architecture Change Log

| Date | Sprint | Change | Files | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-AI-WS1 | Doc created. AI-specific architecture, current-state audit, target state, stage map, Mermaid, known gaps. | + WS1 cross-links. | None — doc-only. |
| 2026-05-10 | S-AI-WS2 | Stage names locked. Typed schemas in `src/pipeline/types.py`. | `src/pipeline/*`, `tests/pipeline/*`, `docs/pipeline/stage-contracts.md`, this doc. | None — additive types. |
| 2026-05-10 | S-AI-WS3 | Reproducible dataset framework + `backtest_results` builder. | `ml/datasets/*`, `tests/ml/datasets/*`, `docs/data/*`, `docs/integrations/huggingface-datasets.md`, this doc. | None — additive; live runtime untouched. |
| 2026-05-10 | S-AI-WS4 | Training center: YAML manifest + Trainer/Evaluator ABCs + filesystem registry + experiments runner + umbrella CLI. Demo round-trip. | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/`, `tests/ml/test_*.py`, `docs/ml/{training-center, model-registry-policy}.md`, this doc. | None — additive; live runtime untouched. |
| 2026-05-10 | S-AI-WS5-A | First specialist baseline + dataset prereq. New `trade_outcomes` family (reads CLOSED non-backtest trades read-only, derives `won = pnl > 0`). New per-strategy historical winrate trainer + classification evaluator. Round-trips through the WS4 harness. New `Forbidden` rule: no outcome columns as features against the `won` target. | `ml/datasets/families/trade_outcomes.py`, `ml/datasets/registry.py`, `ml/trainers/per_strategy_winrate.py`, `ml/evaluators/classification.py`, `ml/configs/baseline-trade-outcome-winrate.yaml`, `tests/ml/datasets/test_trade_outcomes.py`, `tests/ml/test_per_strategy_winrate.py`, `docs/data/{dataset-taxonomy,dataset-schema}.md`, this doc, sprint plan + log. | None — additive; live runtime untouched. Builder reads `trade_journal.db` read-only. |
