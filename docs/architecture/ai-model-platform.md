# AI Model Platform — Architecture

> **Status:** Canonical (AI scope). Adopted in sprint **S-AI-WS1**
> (2026-05-10) per [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md).
>
> **Authority:** This doc is the canonical source of truth for the
> **AI-specific** architecture. The system-wide canonical authority
> remains [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md);
> when this doc and an older note disagree on AI scope, this doc wins.
>
> **Owns:** ROADMAP.md milestones **M9** (AI / model roadmap) and
> **M10** (HF / data pipeline).
>
> **Companion docs:**
> - [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) — system-wide architecture (trade pipeline, comms, deploy).
> - [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) — AI traders master plan, WS1–WS10 workstreams.
> - [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md) — per-stage I/O, owner files, logging requirements (WS2).
> - [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md), [`dataset-schema.md`](../data/dataset-schema.md), [`versioning-policy.md`](../data/versioning-policy.md) — data layer (WS3).
> - [`docs/integrations/huggingface-datasets.md`](../integrations/huggingface-datasets.md) — HF publishing workflow (WS3).
> - [`docs/ml/training-center.md`](../ml/training-center.md), [`docs/ml/model-registry-policy.md`](../ml/model-registry-policy.md) — training factory + registry (WS4).
> - [`docs/sprint-plans/ai-traders/`](../sprint-plans/ai-traders/) — per-workstream sprint plans.

## Purpose

Single source of truth for how AI models fit into the trading
platform. Names what is live today, what is experimental, what is
planned, and what is forbidden. Defines the architectural position of
models relative to the deterministic safety controls already enforced
by the live trader.

## Architectural principles (AI-specific)

1. Live trading stability takes precedence over feature growth.
2. Use specialist models, not one opaque master model.
3. Start with baselines before advanced model families.
4. Make datasets and training reproducible.
5. Require promotion gates before any live influence.
6. Update this doc whenever model boundaries, data schemas, or
   deployment stages change — it is part of the Definition of Done.

## Architectural position

**No “master model to rule them all.”** The target system is one
orchestration layer that consumes outputs from specialist models and
deterministic rules. The orchestrator may rank, combine, or veto
opportunities, but the final live system remains inspectable and
modular so any model can be replaced without redesigning the whole
platform.

**Deterministic risk controls are outside the AI layer and cannot be
bypassed by model output.** Risk gating, broker validation, account
restrictions, kill-switch, and order packaging remain enforced by code
that does not depend on model availability or model decisions. A
model-unavailable state must degrade to deterministic behavior, never
to a permissive bypass.

## Five-layer model

| Layer | Owns | Examples |
|---|---|---|
| 1. Data | Market, account, news, labels, backtests, post-trade reviews | `runtime_logs/`, `trade_journal.db`, `experiments/`, `ml/datasets/` (WS3) |
| 2. Feature / context | Engineered features, regime context, account-state context, prop-firm mission context | future `ml/features/`, [`docs/data/dataset-schema.md`](../data/dataset-schema.md) |
| 3. Model | Specialist models | Regime classifier, setup quality scorer, outcome probability, execution quality, post-trade review, prop mission policy assist; **trainer / evaluator framework live (WS4)** |
| 4. Orchestration | Combines specialist outputs into a trade candidate or veto | future coordinator extension hooked off `src/core/coordinator.py`; **model registry live (WS4)** |
| 5. Control (deterministic) | Risk rules, hard caps, account restrictions, broker validation, order packaging, audit logs, kill-switch | `src/units/accounts/risk.py`, `src/units/accounts/prop_risk.py`, `src/runtime/risk_counters.py`, `src/runtime/orders.py`, `src/runtime/closed_flat_invariant.py` |

Layer 5 is the immutable safety floor. Layers 1–4 are where model
work lands.

## Current State — audit (verified 2026-05-10)

The trading platform today is **fully deterministic in the live
path**. No model is wired into live decisioning.

### Live (in production)

Unchanged since WS1 audit. Trading entrypoint: `src/main.py` →
`src/runtime/pipeline.py`. Strategies in `src/units/strategies/`,
risk gating in `src/units/accounts/risk.py`, order validation in
`src/runtime/orders.py`, broker execution in
`src/units/accounts/execute.py`, persistence in
`trade_journal.db`. Operator surface: `src/bot/telegram_query_bot.py`,
FastAPI `src/web/api/`, comms artifacts under `comms/`.

### Research and validation (experimental, WS1–WS4 additions)

| Concern | Owner files | Notes |
|---|---|---|
| Backtest harness | `src/backtest/`, `scripts/run_backtest.sh` | Deterministic |
| Multi-symbol / multi-timeframe runs | `experiments/` | Evidence capture |
| Concept generation | `notebooks/` | Colab + local |
| M5 strategy testing flow | `src/bot/test_strategy_consumer.py`, `runtime_logs/validation.jsonl` | Auto-consumed `test_strategy:<name>` requests |
| ML scaffolding (legacy) | `ml/config/`, `ml/src/collect_data.py`, `ml/src/test_breakout_strategy.py` | Vestigial S-004/S-005/S-006; not wired into anything WS1–WS4. WS10 cleanup or per-family rebuild. |
| Pipeline types (WS2) | `src/pipeline/types.py`, `tests/pipeline/test_types.py` | Frozen-dataclass `TradeCandidate`, `ExecutionIntent`, `StageDecision`, `StageName`, `Direction`, `RejectionSource`. Live path migration deferred to a Tier 2 sprint. |
| Dataset framework (WS3) | `ml/datasets/{metadata, builder, validate, cli}`, `families/backtest_results`, `tests/ml/datasets/` | Stdlib-only reproducible builders; first family `backtest_results` reads `trade_journal.db` read-only. Append-only versioning. |
| Training center (WS4) | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/`, `tests/ml/test_{training_manifest, model_registry, experiments_runner}.py` | **Adopted 2026-05-10 (S-AI-WS4).** YAML training manifests + Trainer/Evaluator ABCs + filesystem registry with status state machine + experiments runner that round-trips train+evaluate+register. First demo trainer + evaluator (`ConstantPredictionTrainer` / `RegressionEvaluator`) ship as the WS4 acceptance proof. |

### Planned (not yet implemented)

- Specialist models with real predictive value (none in production).
- Builders for the remaining six dataset families.
- Hugging Face publication CLI subcommand (manual flow documented).
- Shadow-mode / advisory-mode execution paths in the live trader
  (registry tier exists; runtime hook does not yet).
- Feature drift / outcome drift monitoring (WS8).
- Architecture-change checklist + PR template enforcement (WS10).
- Migration of live runtime call sites onto the WS2 types.
- Walk-forward / time-aware dataset splitters (current splitter
  is a stable holdout suffix).
- Generic `predict()` interface decoupling trainer state from
  evaluator.

### Forbidden (live-runtime safety floor)

- AI output bypassing risk caps, broker validation, prop-firm
  restrictions, or kill-switch.
- Heavy training jobs running on the Oracle live VM (WS9 rule).
- Heavy dataset builds running on the Oracle live VM.
- Live model influence introduced without staged promotion + explicit
  operator approval (WS7 rule).
- Schema / boundary changes shipped without updating this doc.
- Constructing `ExecutionIntent` from a model code path. Only the
  execution-packaging code may emit one (`src/runtime/orders.py`).
- Auto-publication of any dataset to Hugging Face. Publication is
  always an explicit operator action.
- Editing past `StatusEvent` entries in the model registry. The
  promotion history is append-only (S-AI-WS4 rule).
- Promoting a model to `live-approved` or `champion` without
  operator-issued approval recorded in `--by` + `--reason`.

## Target State

The target architecture extends the existing pipeline with a model
layer and orchestration hook, **without weakening the deterministic
floor**. Stage names are locked in
[`src/pipeline/types.py`](../../src/pipeline/types.py); per-stage I/O
in [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md);
datasets in [`ml/datasets/`](../../ml/datasets/); training factory in
[`ml/`](../../ml/) per
[`training-center.md`](../ml/training-center.md).

### Stage map

| # | Stage (`StageName`) | Today (deterministic) | Target (deterministic vs model-assisted) | Owning paths (current + planned) |
|---|---|---|---|---|
| 1 | `INGEST` | Connectors + market-data helpers | Deterministic | `src/exchange/`, `src/runtime/market_data.py` |
| 2 | `NORMALIZE` | Internal candle / tick representation | Deterministic | `src/runtime/market_data.py` |
| 3 | `CONTEXT` | Implicit (per-strategy) | Deterministic + model-assisted | future `ml/features/`, `src/units/accounts/` |
| 4 | `SETUP` | Rule-based strategies | Deterministic today; optional model-assist later | `src/units/strategies/`, `src/ict_detection/` |
| 5 | `SCORE` | Implicit | Model-assisted | future model layer + `src/core/coordinator.py` extension |
| 6 | `RISK` | Per-account caps + prop rules + counters | Deterministic only | `src/units/accounts/risk.py`, `prop_risk.py`, `src/runtime/risk_counters.py` |
| 7 | `PACKAGE` | Order construction + validation | Deterministic | `src/runtime/orders.py`, `src/runtime/validation.py` |
| 8 | `ROUTE` | Per-account dry/live | Deterministic | `src/units/accounts/execute.py`, `src/exchange/` |
| 9 | `CAPTURE` | Trade journal + audit log | Deterministic ingest; model-assisted enrichment | `trade_journal.db`, `runtime_logs/signal_audit.jsonl` |
| 10 | `REVIEW` | Manual / ad hoc | Model-assisted | future `ml/reports/`, `runtime_logs/` |

**Invariant:** stages 6, 7, and 8 must remain rejection-capable for
any upstream output regardless of source.

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
        M3[outcome probability]
        M4[execution quality]
        M5[post-trade review]
        TRAINER[Trainer / Evaluator + Registry\n<small>ml/{trainers,evaluators,registry}</small>]
    end

    subgraph ORCH["4. Orchestration layer"]
        O1[coordinator\n<small>src/core/coordinator.py</small>\n<small>+ TradeCandidate (src/pipeline/types)</small>]
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

- **Legacy `ml/config/` and `ml/src/`.** Vestigial S-004/S-005/S-006
  scaffolding. `ml/datasets/`, `ml/trainers/`, `ml/evaluators/`,
  `ml/registry/`, `ml/promotion/`, `ml/experiments/`, `ml/configs/`
  are live (WS3 + WS4); the legacy dirs are not wired into anything
  and are flagged for WS10 cleanup or per-family rebuild.
- **No specialist models** with real predictive value. WS5 lands
  the first baselines.
- **Most dataset families are scaffolded but not buildable.** Only
  `backtest_results` is live as of WS3.
- **Hugging Face publication is manual.** A CLI subcommand for
  `python -m ml.datasets publish ...` is filed for a follow-up.
- **No shadow-mode runtime hook.** The registry has a tier system
  (WS4); the live trader does not yet consume any model signal in
  any tier. WS7 introduces the runtime hook.
- **No feature / outcome drift monitoring.** WS8 owns this.
- **Existing `docs/architecture.md` is partly stale.** Not
  blocking; flagged for cleanup alongside WS10.
- **WS2 types not yet adopted by the live path.** Strategies still
  emit `OrderPackage`; the coordinator path still routes those.
  Migration onto `TradeCandidate` / `ExecutionIntent` is filed as a
  Tier 2 follow-up.
- **Architecture-change checklist + PR template not yet enforced.**
  WS10 owns this.
- **Holdout splitter is order-preserving suffix only.** Walk-forward
  / time-aware splitters land in a follow-up to WS4.
- **Trainer↔evaluator coupling is hard.** `RegressionEvaluator`
  reads `model_state["constant"]` directly; a generic `predict()`
  interface is filed for a follow-up.

## Architecture Update Rule

This document must be reviewed and updated in the **same PR** when
any of the following change:

- Layer boundaries (data, feature/context, model, orchestration,
  control).
- Stage names or stage I/O contracts.
- Dataset families or schemas.
- Versioning / retention policy.
- Hugging Face publishing workflow.
- **Training-center directory layout, manifest schema, runner
  pipeline, or the model-registry status state machine /
  promotion gates.**
- Deployment stage list (research → candidate → backtest-approved
  → shadow → advisory → limited live → live-approved).
- Oracle vs Hugging Face runtime responsibilities.
- Anything tagged `Forbidden` above.

Omissions are an architecture defect and should be filed as a Janitor
follow-up.

## Architecture Change Log

| Date | Sprint | Change | Files | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-AI-WS1 (WS1) | Doc created. Records the AI-specific architecture, current-state audit, target state, stage map, Mermaid diagram, and known gaps. Linked from `ARCHITECTURE-CANONICAL.md`. | `docs/architecture/ai-model-platform.md` (new), + WS1 cross-links. | None — doc-only. |
| 2026-05-10 | S-AI-WS2 (WS2) | Stage names locked. Typed schemas in `src/pipeline/types.py`. Per-stage I/O at `docs/pipeline/stage-contracts.md`. | `src/pipeline/*`, `tests/pipeline/*`, `docs/pipeline/stage-contracts.md`, this doc. | None — additive types. |
| 2026-05-10 | S-AI-WS3 (WS3) | Data foundation lands. Reproducible dataset framework under `ml/datasets/`; first family `backtest_results` reads `trade_journal.db` read-only. | `ml/datasets/*`, `tests/ml/datasets/*`, `docs/data/*`, `docs/integrations/huggingface-datasets.md`, this doc. | None — additive; live runtime untouched. |
| 2026-05-10 | S-AI-WS4 (WS4) | Training center lands. YAML manifest schema + Trainer/Evaluator ABCs + filesystem model registry (state machine + transition log) + experiments runner + umbrella CLI (`python -m ml`). First demo trainer/evaluator (`ConstantPredictionTrainer` / `RegressionEvaluator`) round-trips train+evaluate+register against a `backtest_results` dataset. Layer 3 + Layer 4 (registry portion) move from "planned" to "experimental". | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/`, `tests/ml/test_{training_manifest, model_registry, experiments_runner}.py`, `docs/ml/{training-center, model-registry-policy}.md`, this doc. | None — additive; live runtime untouched. |
