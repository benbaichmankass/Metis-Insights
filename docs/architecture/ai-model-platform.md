# AI Model Platform — Architecture

> **Status:** Canonical (AI scope). Adopted in sprint **S-AI-WS1**
> (2026-05-10) per [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md).
>
> **Authority:** This doc is the canonical source of truth for the
> **AI-specific** architecture. The system-wide canonical authority
> remains [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md);
> when this doc and an older note disagree on AI scope, this doc wins.
> When this doc and `ARCHITECTURE-CANONICAL.md` overlap on non-AI
> system design, the canonical doc wins.
>
> **Owns:** ROADMAP.md milestones **M9** (AI / model roadmap) and
> **M10** (HF / data pipeline).
>
> **Companion docs:**
> - [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) — system-wide architecture (trade pipeline, comms, deploy).
> - [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) — AI traders master plan, WS1–WS10 workstreams.
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
| 1. Data | Market, account, news, labels, backtests, post-trade reviews | `runtime_logs/`, `trade_journal.db`, `experiments/`, future `ml/datasets/` |
| 2. Feature / context | Engineered features, regime context, account-state context, prop-firm mission context | future `ml/features/`, future `docs/data/dataset-schema.md` |
| 3. Model | Specialist models | Regime classifier, setup quality scorer, outcome probability, execution quality, post-trade review, prop mission policy assist |
| 4. Orchestration | Combines specialist outputs into a trade candidate or veto | future coordinator extension hooked off `src/core/coordinator.py` |
| 5. Control (deterministic) | Risk rules, hard caps, account restrictions, broker validation, order packaging, audit logs, kill-switch | `src/units/accounts/risk.py`, `src/units/accounts/prop_risk.py`, `src/runtime/risk_counters.py`, `src/runtime/orders.py`, `src/runtime/closed_flat_invariant.py` |

Layer 5 is the immutable safety floor. Layers 1–4 are where model
work lands.

## Current State — audit (verified 2026-05-10)

The trading platform today is **fully deterministic in the live
path**. No model is wired into live decisioning.

### Live (in production)

| Concern | Owner files | Notes |
|---|---|---|
| Trading entrypoint | `src/main.py` → `src/runtime/pipeline.py` | Tick loop + heartbeat |
| Strategy modules | `src/units/strategies/` (`turtle_soup.py`, `vwap.py`, …) | All rule-based |
| Strategy registry | `src/strategy_registry.py` | Driven by `config/strategies.yaml` |
| Coordinator | `src/core/coordinator.py` | Deterministic translator (S-008 9-unit architecture); not a model |
| ICT detection | `src/ict_detection/` | Rule-based signal detection components |
| News veto | `src/news/news_pipeline.py` | Rule-based |
| Risk gating | `src/units/accounts/risk.py`, `src/units/accounts/prop_risk.py`, `src/runtime/risk_counters.py` | Per-account caps; prop-firm rules |
| Order validation | `src/runtime/orders.py::safe_place_order`, `src/runtime/validation.py` | Hard refusal paths for invalid / disallowed orders |
| Closed-flat invariant | `src/runtime/closed_flat_invariant.py` | Alert-only soak (env-gated, default off) |
| Broker execution | `src/units/accounts/execute.py`; connectors `src/exchange/{bybit,binance}_connector.py` | Per-account dry/live via `config/accounts.yaml` |
| Kill-switch | `HALT_FLAG_PATH = /tmp/trader_halt.flag`, consumed in `pipeline.py` | File-based |
| Logging | `runtime_logs/signal_audit.jsonl`, `validation.jsonl`, `status.json`, `heartbeat.txt` | Structured |
| Persistence | `trade_journal.db` (SQLite) — `trades`, `order_packages`, `backtest_results` | M5 writes `backtest_results` |
| Operator control | `src/bot/telegram_query_bot.py`, FastAPI `src/web/api/`, comms artifacts under `comms/` | Tier 1 / 2 / 3 surface per `docs/api-tier-policy.md` |

### Research and validation (experimental)

| Concern | Owner files | Notes |
|---|---|---|
| Backtest harness | `src/backtest/`, `scripts/run_backtest.sh` | Deterministic |
| Multi-symbol / multi-timeframe runs | `experiments/` | Evidence capture |
| Concept generation | `notebooks/` | Colab + local |
| M5 strategy testing flow | `src/bot/test_strategy_consumer.py`, `runtime_logs/validation.jsonl` | Auto-consumed `test_strategy:<name>` requests |
| ML scaffolding | `ml/config/`, `ml/src/collect_data.py`, `ml/src/test_breakout_strategy.py` | **Vestigial.** Inherited from S-004/S-005/S-006; minimal and not wired into the live path. WS4 will rebuild this directory. |

### Planned (not yet implemented)

- Specialist models (none in production).
- Reproducible dataset builders (none).
- Model registry with promotion stages (none).
- Shadow-mode / advisory-mode execution paths (none).
- Feature drift / outcome drift monitoring (none).
- Hugging Face–integrated training workflow (none).
- Architecture-change checklist + PR template enforcement (none; WS10).

### Forbidden (live-runtime safety floor)

- AI output bypassing risk caps, broker validation, prop-firm
  restrictions, or kill-switch.
- Heavy training jobs running on the Oracle live VM (WS9 rule).
- Live model influence introduced without staged promotion + explicit
  operator approval (WS7 rule).
- Schema / boundary changes shipped without updating this doc.

## Target State

The target architecture extends the existing pipeline with a model
layer and orchestration hook, **without weakening the deterministic
floor**.

### Stage map (current pipeline → target with AI)

Stage names follow `docs/AI-TRADERS-ROADMAP.md` § Workstream 2. The
final stage names + I/O contracts will be locked in WS2; this map
shows the intent.

| Stage | Today (deterministic) | Target (deterministic vs model-assisted) | Owning paths (current + planned) |
|---|---|---|---|
| 1. Market and account ingest | Connectors + market-data helpers | **Deterministic.** No model. | `src/exchange/`, `src/runtime/market_data.py` |
| 2. Normalization | Internal candle / tick representation | **Deterministic.** No model. | `src/runtime/market_data.py` |
| 3. Context assembly | Implicit (per-strategy) | **Deterministic + model-assisted.** Regime + account + mission features assembled here. | future `ml/features/`, `src/units/accounts/` |
| 4. Setup detection | Rule-based strategies | **Deterministic** today. Optional model-assist later (setup quality scorer). | `src/units/strategies/`, `src/ict_detection/` |
| 5. Opportunity scoring | Implicit | **Model-assisted.** Outcome probability + setup quality combine into a candidate score. | future model layer + `src/core/coordinator.py` extension |
| 6. Risk gating | Per-account caps + prop rules + counters | **Deterministic only.** Models cannot influence this stage. | `src/units/accounts/risk.py`, `prop_risk.py`, `src/runtime/risk_counters.py` |
| 7. Execution packaging | Order construction + validation | **Deterministic.** Optional model-assist for execution quality / slippage estimation outside the validation path. | `src/runtime/orders.py`, `src/runtime/validation.py` |
| 8. Broker routing | Per-account dry/live | **Deterministic.** | `src/units/accounts/execute.py`, `src/exchange/` |
| 9. Post-trade capture | Trade journal + audit log | **Deterministic** ingest; model-assisted enrichment downstream. | `trade_journal.db`, `runtime_logs/signal_audit.jsonl` |
| 10. Review and feedback | Manual / ad hoc | **Model-assisted.** Post-trade review model classifies error patterns; feeds back into datasets. | future `ml/reports/`, `runtime_logs/` |

**Invariant:** stages 6, 7, and 8 must remain rejection-capable for
any upstream output regardless of source. Risk gating and broker
validation may reject the output of any model.

### Component diagram (target)

```mermaid
flowchart LR
    subgraph DATA["1. Data layer"]
        D1[market data\n<small>src/exchange, runtime/market_data</small>]
        D2[account state\n<small>src/units/accounts</small>]
        D3[news / events\n<small>src/news</small>]
        D4[backtests\n<small>experiments/</small>]
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
        M6[(optional) prop mission policy assist]
    end

    subgraph ORCH["4. Orchestration layer"]
        O1[coordinator\n<small>src/core/coordinator.py</small>]
    end

    subgraph CTRL["5. Control layer (deterministic, immutable safety floor)"]
        C1[risk gating\n<small>units/accounts/risk.py</small>]
        C2[prop rules\n<small>units/accounts/prop_risk.py</small>]
        C3[order validation\n<small>runtime/orders.py</small>]
        C4[broker routing\n<small>units/accounts/execute.py</small>]
        C5[kill-switch\n<small>HALT_FLAG_PATH</small>]
    end

    DATA --> FEAT
    FEAT --> MODEL
    MODEL --> ORCH
    ORCH -->|trade candidate| CTRL
    CTRL -->|may reject any candidate| ORCH
    CTRL --> EXEC[broker / dry-run]
    EXEC --> D5
    D5 --> D6
    D6 -.feedback.-> DATA
```

The arrow from CTRL back to ORCH is the rejection path: deterministic
rules can veto a candidate even if every model approved it.

### Where each workstream lands

| Workstream | Affects layer(s) | Sprint plan |
|---|---|---|
| WS1 | Cross-cutting (this doc) | [ws1-architecture-baseline.md](../sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| WS2 | Stage contracts across all five layers | [ws2-canonical-pipeline.md](../sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| WS3 | Layer 1 + 2 | [ws3-data-foundation.md](../sprint-plans/ai-traders/ws3-data-foundation.md) |
| WS4 | Layer 3 + meta (training center) | [ws4-training-center.md](../sprint-plans/ai-traders/ws4-training-center.md) |
| WS5 | Layer 3 (specialist baselines) | [ws5-baseline-models.md](../sprint-plans/ai-traders/ws5-baseline-models.md) |
| WS6 | Layer 3 (open-source families) | [ws6-open-source-models.md](../sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Layer 4 + 5 boundary (deployment tiers) | [ws7-deployment-tiers.md](../sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Cross-cutting (monitoring + feedback) | [ws8-monitoring-feedback.md](../sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Runtime split (Oracle vs HF) | [ws9-runtime-split.md](../sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Doc enforcement | [ws10-arch-doc-enforcement.md](../sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

## Known Gaps (as of 2026-05-10)

- **`ml/` is vestigial.** Only `ml/config/` plus two scripts under
  `ml/src/`. The master plan target structure (`ml/datasets/`,
  `ml/features/`, `ml/labels/`, `ml/trainers/`, `ml/evaluators/`,
  `ml/experiments/`, `ml/registry/`, `ml/promotion/`, `ml/configs/`,
  `ml/reports/`) is not yet present. WS4 owns rebuilding this tree.
- **No model registry.** `S-006 Model Registry & Versioning` is
  marked done in the historical ledger but no registry artifact
  exists in the current repo. WS4 / WS7 deliver the canonical
  registry.
- **No reproducible dataset builders.** Backtest evidence lives in
  `experiments/<sprint>/results/*.json` but is not generated by a
  versioned, schema-validated dataset pipeline. WS3 owns this.
- **No shadow-mode path.** All model influence is currently
  hypothetical. WS7 introduces shadow / advisory tiers before any
  live influence.
- **No feature / outcome drift monitoring.** WS8 owns this.
- **Existing `docs/architecture.md` is partly stale** (the S-008
  9-unit translator section is current; the “Target Structure” block
  predates the canonical doc). Not blocking; flagged for cleanup
  alongside WS10.
- **Stage names not yet locked.** This doc uses the master-plan
  10-stage list while `ARCHITECTURE-CANONICAL.md` documents an
  8-step pipeline. WS2 reconciles and lands typed stage contracts.
- **Architecture-change checklist + PR template not yet enforced.**
  WS10 owns this.

## Architecture Update Rule

This document must be reviewed and updated in the **same PR** when
any of the following change:

- Layer boundaries (data, feature/context, model, orchestration,
  control).
- Stage names or stage I/O contracts (also touches WS2 artifacts).
- Dataset families or schemas (also touches
  `docs/data/dataset-schema.md` once it exists).
- Model registry status categories or promotion rules (also touches
  `docs/ml/model-registry-policy.md` once it exists).
- Deployment stage list (research → candidate → backtest-approved
  → shadow → advisory → limited live → live-approved).
- Oracle vs Hugging Face runtime responsibilities.
- Anything tagged `Forbidden` above.

Omissions are an architecture defect and should be filed as a Janitor
follow-up.

## Architecture Change Log

| Date | Sprint | Change | Files | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-AI-WS1 (WS1) | Doc created. Records the AI-specific architecture, current-state audit, target state, stage map, Mermaid diagram, and known gaps. Linked from `ARCHITECTURE-CANONICAL.md`. | `docs/architecture/ai-model-platform.md` (new), `docs/ARCHITECTURE-CANONICAL.md` (link added), `docs/sprint-plans/ai-traders/ws1-architecture-baseline.md` (status → in progress, sprint id S-AI-WS1), `docs/AI-TRADERS-ROADMAP.md` (change log + WS1 status), `ROADMAP.md` (WS1 status row + S-AI-WS1 ledger entry) | None at this stage — doc-only; live runtime untouched. |
