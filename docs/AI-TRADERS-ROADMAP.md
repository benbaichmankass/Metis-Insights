# AI Traders Models Roadmap

> **Status:** Master plan adopted 2026-05-10.
> **Scope:** Implementation plan for the AI trader model system in this repo.
> **Maps to:** ROADMAP.md milestones **M9 (AI / model roadmap)** and **M10 (HF / data pipeline)**.
>
> Canonical authority order is unchanged:
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md)
> 3. [`ROADMAP.md`](../ROADMAP.md)
> 4. The current sprint log in [`docs/sprint-logs/`](sprint-logs/)
>
> This doc is the **scope-specific master plan** for the AI / ML lifecycle
> work and is owned by M9 + M10. When it disagrees with the canonical docs
> above, the canonical docs win.
>
> **AI-scope canonical doc:**
> [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
> (created in S-AI-WS1, 2026-05-10) is the canonical AI architecture
> reference and the durable artifact of WS1.

---

## Mission

Evolve the project from a live trading bot with hard safety controls into a
structured AI-assisted trading platform with reproducible datasets,
specialist models, deployment gates, and an auditable training center.

The target system is **one orchestration layer coordinating multiple
specialist models**, while deterministic risk controls remain outside the AI
layer and cannot be bypassed by model output.

---

## Operating assumptions

- Claude Code is doing the implementation work.
- Hugging Face is the primary external AI platform for open models, datasets,
  artifacts, and training workflows.
- The Oracle VM remains the live runtime first and only hosts lightweight
  support compute, not serious training workloads. Always Free resources are
  limited and contention with the live trader is an operational risk.
- Google AI Studio is optional for ad hoc research or model comparison; it is
  not a required part of the production architecture.
- No model may bypass deterministic risk guards, broker validation, account
  restrictions, or operator kill-switch logic already enforced by the live
  trader.

---

## Strategic principles

1. Keep the live trading path safe.
2. Use specialist models, not one opaque master model.
3. Start with baselines before advanced model families.
4. Make datasets and training reproducible.
5. Require promotion gates before any live influence.
6. Update the architecture docs as part of the Definition of Done for every
   major milestone.

---

## Target architecture

Five layers:

1. **Data layer** — market data, account data, news/event data, labels,
   backtest outputs, post-trade review data.
2. **Feature and context layer** — engineered features, regime context,
   account-state context, prop-firm mission context, reusable feature
   definitions.
3. **Model layer** — specialist models for regime detection, setup scoring,
   execution quality, narrative analysis, portfolio selection, post-trade
   review.
4. **Orchestration layer** — combines specialist outputs into a trade
   candidate or veto decision; **does not own hard risk policy**.
5. **Control layer** — deterministic risk rules, hard caps, account
   restrictions, broker validation, order packaging, audit logs, kill-switch
   behavior.

Full layer + stage description with current-state audit and Mermaid
diagram lives in
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

### Architectural position

Do **not** build one “master model to rule them all.” Use one coordinator
that consumes outputs from specialist models and deterministic rules. The
coordinator may rank, combine, or veto opportunities, but the final live
system must remain inspectable and modular so any model can be replaced
without redesigning the whole platform.

---

## What success looks like

The repo can produce a full AI model lifecycle on demand: build a dataset,
version it, train a candidate, evaluate it, compare it to a champion,
register it, stage it in shadow or advisory mode, and promote it only after
passing explicit gates. Architecture documentation stays aligned with reality
and no AI work weakens the existing safety posture of the live trader.

---

## Workstreams

The ten workstreams below are the unit of planning. Each one ships as one or
more sprints under M9 or M10 in `ROADMAP.md`. Sprint plans live under
[`docs/sprint-plans/ai-traders/`](sprint-plans/ai-traders/).

| WS | Title | M | Status | Sprint plan |
|---|---|---|---|---|
| WS1 | Architecture baseline | M9 | ✅ DONE 2026-05-10 (S-AI-WS1) — see [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md) | [ws1-architecture-baseline.md](sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| WS2 | Canonical trade pipeline | M9 | 📋 Not started | [ws2-canonical-pipeline.md](sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| WS3 | Data foundation | M10 | 📋 Not started | [ws3-data-foundation.md](sprint-plans/ai-traders/ws3-data-foundation.md) |
| WS4 | Training center | M9 | 📋 Not started | [ws4-training-center.md](sprint-plans/ai-traders/ws4-training-center.md) |
| WS5 | Baseline models | M9 | 📋 Not started | [ws5-baseline-models.md](sprint-plans/ai-traders/ws5-baseline-models.md) |
| WS6 | Open-source model layer | M9 | 📋 Not started | [ws6-open-source-models.md](sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | 📋 Not started | [ws7-deployment-tiers.md](sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | 📋 Not started | [ws8-monitoring-feedback.md](sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | 🔄 Continuous (policy of record from WS3 onwards) | [ws9-runtime-split.md](sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | 📋 Not started | [ws10-arch-doc-enforcement.md](sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

Workstream details are inlined below; sprint plans hold the executable task
lists.

---

### Workstream 1 — Architecture baseline

**Objective.** Create a clear source of truth for the current and target AI
trading architecture before building new model infrastructure.

**Tasks**

- Audit the current repo: live trading path, strategy modules, runtime
  pipeline, risk-manager path, broker adapters, bot-control path, deployment
  flow, current research / backtest utilities.
- Create or update the canonical architecture doc at
  `docs/architecture/ai-model-platform.md`.
- Add a `Current State` section, a `Target State` section, a Mermaid
  component diagram, and a stage-ownership table.
- Add an `Architecture Change Log` section that must be updated whenever
  model boundaries, data schemas, or deployment stages change.
- Update the main architecture index / README so the doc is discoverable.

**Acceptance**

- A single architecture doc reflects current reality and desired future
  state.
- The doc names which parts are live, staged, planned, forbidden.
- The doc explicitly states that deterministic risk controls are outside the
  AI layer.
- The doc is linked from the main repo docs navigation.

**Closed:** S-AI-WS1, 2026-05-10. Acceptance criteria all met. See the
sprint plan for the deliverable list and the change log row below.

---

### Workstream 2 — Canonical trade pipeline

**Objective.** Turn the trade lifecycle into a formally defined pipeline so
model work can plug in cleanly.

**Default pipeline stages**

1. Market and account ingest
2. Normalization
3. Context assembly
4. Setup detection
5. Opportunity scoring
6. Risk gating
7. Execution packaging
8. Broker routing
9. Post-trade capture
10. Review and feedback

**Tasks**

- Typed schemas (dataclass / pydantic) for stage inputs and outputs.
- Canonical `TradeCandidate` and `ExecutionIntent` objects.
- Define where deterministic rule checks happen and where model scores may
  influence decisions.
- Strict rule: risk and broker validation may reject outputs from any model.
- Per-stage logging requirements.
- Test scaffolding for stage contracts.

**Acceptance**

- Every pipeline stage has a documented owner and I/O contract.
- Repo contains shared types or schemas for trade candidates and execution
  intents.
- Explicit rule: models cannot place or modify orders outside the validated
  execution-packaging path.

---

### Workstream 3 — Data foundation

**Objective.** Build a dataset and feature system that is versioned,
reproducible, and Hugging Face friendly.

**Dataset families**

- `market_raw` — bars, ticks, order-book-derived snapshots.
- `market_features` — engineered features from raw data.
- `setup_labels` — labels for pattern or setup quality.
- `trade_outcomes` — realized trade results tied to signals or execution
  intents.
- `backtest_results` — outputs of simulation runs.
- `account_context` — account state, funding phase, prop-firm restrictions,
  mission state, active day rules, related metadata.
- `review_journal` — post-trade reviews, mistake tagging, narrative
  annotations.

**Tasks**

- Dataset schema document.
- Naming conventions for datasets and versions.
- Mandatory metadata: source, timezone, symbol scope, timeframe, generation
  script commit SHA, label version, leakage-test status, notes.
- Dataset builders so all datasets reproduce from code.
- Hugging Face dataset repos or a documented publishing workflow.
- Schema-compliance + missing-field validation scripts.
- Retention / versioning policy so old datasets remain traceable.

**Acceptance**

- Documented dataset taxonomy.
- At least one dataset family generated and published repeatably.
- Dataset metadata carries enough lineage to reproduce a training run later.

---

### Workstream 4 — Training center

**Objective.** Repo-native training center that behaves like a repeatable
factory for training, evaluation, registration, and promotion.

**Required structure** (adapt to existing `ml/` layout)

```text
ml/
  datasets/
  features/
  labels/
  trainers/
  evaluators/
  experiments/
  registry/
  promotion/
  configs/
  reports/
```

**Tasks**

- Training-manifest format (YAML): model family, dataset version, feature
  set, label spec, objective, evaluation suite, target deployment stage.
- CLI / Make entry points: `build-dataset`, `train`, `evaluate`, `compare`,
  `register`, `promote`.
- Experiment-tracking metadata, file-based first version is fine.
- Model-registry file or folder with statuses: `candidate`, `champion`,
  `paper`, `advisory`, `live-approved`.
- Promotion checklist: leakage checks, walk-forward, transaction-cost-aware
  evaluation, rollback notes.
- All training artifacts tied to a specific dataset version and code
  revision.

**Acceptance**

- Documented training center exists.
- At least one model trains and evaluates via a repeatable command path.
- Model-registry metadata supports promotion-state tracking.

---

### Workstream 5 — Baseline models

**Objective.** Prove the system with simple, strong baselines before
introducing advanced open-source model families.

**Baseline tasks**

- **Regime classifier** — market state, volatility regime, trend.
- **Setup quality scorer** — quality of a detected setup.
- **Outcome probability model** — probability of favorable trade outcome.
- **Execution quality model** — slippage / fill quality risk.
- **Post-trade review model** — error patterns / trade quality.
- **Prop mission policy layer** — deterministic first; model assistance is
  optional and follows later. Evaluation-account rules are safety-sensitive
  and rule-heavy.

**Tasks**

- Clear labels for each baseline task.
- Leakage checks per task.
- Evaluate by regime, symbol group, timeframe where relevant.
- Compare every baseline to a simple heuristic / rule baseline.
- Publish run summaries into the repo.

**Acceptance**

- Each baseline task has a dataset, trainer, evaluator, summary report.
- No advanced model family is introduced before a baseline exists for the
  same task.
- Each baseline produces decision-useful metrics, not only generic ML
  metrics.

---

### Workstream 6 — Open-source model layer

**Objective.** Add open-source models through Hugging Face only after
baseline systems are stable.

**Tasks**

- `model-inventory.md` listing candidate open-source models by task.
- Separate model families by use case:
  - text models for news, journaling, operator notes,
  - embedding models for retrieval / similarity,
  - tabular / time-series models for market prediction,
  - optional larger reasoning models only for research or offline
    orchestration support.
- Prefer PEFT, LoRA, adapter-style tuning before full fine-tuning.
- Approval criteria before adopting a new model family: measurable gain over
  baseline, manageable latency, acceptable infra cost, safe rollback path.
- Keep the training interface provider-agnostic even if Hugging Face is the
  first implementation target.

**Acceptance**

- Documented open-source model inventory.
- Every added model family has a defined task and measurable success
  criteria.
- No model is added only because it is fashionable or large.

---

### Workstream 7 — Deployment tiers

**Objective.** All models move through staged influence levels instead of
jumping from training to live trading.

**Stages**

1. Research only
2. Candidate
3. Backtest approved
4. Shadow mode
5. Advisory mode
6. Limited live influence
7. Live approved

**Tasks**

- Stage metadata in the model registry.
- Shadow-mode execution path: model scores opportunities without affecting
  live execution.
- Advisory mode: model outputs annotate or veto only if the operator chooses
  that stage.
- Explicit approval before any model influences strategy behavior in live
  mode.
- Deterministic fallback behavior when a model is unavailable.
- Logs for model version, score, final decision path.

**Acceptance**

- A new model can run in shadow mode without changing live trading behavior.
- Stage promotion requires documented evidence.
- Every live-influencing model has a fallback / disable path.

---

### Workstream 8 — Monitoring and feedback loops

**Objective.** Trading-specific observability and post-deployment review.

**Tasks**

- Log model version and config for each scored opportunity.
- Track model confidence, downstream decision, realized outcome, veto
  impact.
- Feature drift and outcome drift monitoring.
- Strategy / model attribution: was the move from the strategy, the model,
  the filter, or execution conditions?
- Retraining trigger policy based on drift, stale data, degraded business
  metrics.
- Post-trade review workflow that feeds back into datasets.

**Acceptance**

- Logging is sufficient to reconstruct why a model influenced or did not
  influence a trade.
- Defined policy for retraining, rollback, review.
- Monitoring focuses on trading outcomes, not only offline ML metrics.

---

### Workstream 9 — Oracle / Hugging Face runtime split

**Objective.** Protect the live runtime while using Hugging Face for the
heavier AI lifecycle work.

**Rules**

- Oracle VM hosts the live trader, bot, scheduler, small ETL, light
  preprocessing, inference, and only very small CPU-safe experiments.
- Oracle VM **must not** host heavy training, large backtests, or any
  long-running job that could starve the live process.
- Hugging Face hosts datasets, open-source model workflows, artifacts, model
  storage, and heavier training-related operations.
- Jobs that are unpredictable in memory or CPU usage do not belong on the
  Oracle live box.

**Acceptance**

- Architecture doc explicitly distinguishes live runtime from training
  infrastructure.
- Repo contains a short operational policy for what may and may not run on
  Oracle.

---

### Workstream 10 — Architecture-doc enforcement

**Objective.** Make documentation maintenance mandatory so the architecture
doc does not drift away from the codebase.

**Tasks**

- Add `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`.
- Require any PR changing data schemas, model boundaries, pipeline stages,
  deployment stages, or runtime responsibilities to update the architecture
  docs.
- Add a PR-template checkbox for architecture updates.
- Add a changelog table inside the architecture doc capturing date, change,
  files touched, operator impact.
- Add a `Known Gaps` section so incomplete work is visible rather than
  implied.

**Acceptance**

- Repo has an enforceable architecture-update workflow.
- A reviewer can see architecture impact directly in the PR or linked docs.

---

## Recommended repo deliverables

- `docs/architecture/ai-model-platform.md` — ✅ created in S-AI-WS1.
- `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` — WS10.
- `docs/architecture/model-inventory.md` — WS6.
- `docs/data/dataset-taxonomy.md` — WS3.
- `docs/data/dataset-schema.md` — WS3.
- `docs/ml/training-center.md` — WS4.
- `docs/ml/model-registry-policy.md` — WS4.
- `ml/configs/`, `ml/datasets/`, `ml/features/`, `ml/trainers/`,
  `ml/evaluators/`, `ml/registry/`, `ml/reports/` — WS4.

Exact paths may be adjusted to match the real repo structure (the existing
`ml/` tree currently only contains `ml/config/` and `ml/src/`); WS1 records
this fact and WS4 owns rebuilding the directory.

---

## Recommended implementation order

1. WS1 — architecture audit + canonical architecture doc. **✅ done
   (S-AI-WS1).**
2. WS2 — canonical trade pipeline + stage contracts. **🔜 next.**
3. WS3 — dataset taxonomy, schema docs, first reproducible dataset builder.
4. WS4 — training center structure + command path.
5. First baseline model end to end (subset of WS5).
6. Model registry + promotion stages (subset of WS4 / WS7).
7. Shadow-mode deployment path (subset of WS7).
8. Additional baseline models (rest of WS5).
9. Open-source model inventory + first Hugging Face integrated workflow
   (WS6).
10. Monitoring, retraining policy, architecture-enforcement workflow (WS8 +
    WS10).

---

## Non-negotiable rules

- Do **not** weaken the live trading safety posture while building AI
  infrastructure.
- Do **not** run heavy training jobs on the Oracle live VM.
- Do **not** introduce a model directly into live strategy logic without
  staged promotion and explicit approval.
- Do **not** let AI output bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Do **not** ship architecture-changing code without updating the
  architecture docs.

---

## Definition of Done (roadmap-level)

This roadmap is successfully implemented when:

- The repo has an up-to-date architecture document describing both current
  and target AI trading platform.
- The trade pipeline is formally defined with stage contracts.
- Datasets can be generated reproducibly and published / stored with
  lineage.
- The training center exists and can train at least one model end to end.
- Baseline models exist for core tasks.
- A model registry and promotion workflow exist.
- Shadow or advisory mode exists before any live model influence.
- Monitoring and feedback loops exist.
- Oracle and Hugging Face roles are clearly separated.
- Documentation is enforced as part of the workflow.

---

## Final instruction to Claude

Implement this plan as a sequence of small, reviewable PRs. Keep the live
trader stable, prefer deterministic safety over model ambition, and treat
architecture documentation as part of the product, not optional cleanup.
Every milestone should end with updated docs, clear acceptance evidence, and
a clean next step.

---

## Change log

| Date | Sprint | Change | Files | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-AI-ROADMAP | Master plan adopted; M9 + M10 expanded into WS1–WS10; sprint-plan stubs seeded for WS1–WS10 (WS1–WS4 expanded, WS5–WS10 stub-only). | `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-plans/ai-traders/*` | None at this stage — docs only; live runtime untouched. |
| 2026-05-10 | S-AI-WS1 | WS1 complete. New canonical AI-scope doc at `docs/architecture/ai-model-platform.md` (current-state audit + target state + 5-layer model + stage map + Mermaid diagram + Architecture Change Log + Known Gaps). Linked from `ARCHITECTURE-CANONICAL.md`. WS1 sprint plan updated with sprint id and acceptance check-offs. Sprint log filed at `docs/sprint-logs/S-AI-WS1.md`. | `docs/architecture/ai-model-platform.md` (new), `docs/ARCHITECTURE-CANONICAL.md`, `docs/sprint-plans/ai-traders/ws1-architecture-baseline.md`, `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-logs/S-AI-WS1.md` (new) | None — doc-only. |
