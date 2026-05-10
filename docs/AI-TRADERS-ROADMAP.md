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
> (S-AI-WS1). **Stage contracts:**
> [`docs/pipeline/stage-contracts.md`](pipeline/stage-contracts.md)
> (S-AI-WS2). **Pipeline types:**
> [`src/pipeline/types.py`](../src/pipeline/types.py) (S-AI-WS2).
> **Data layer:** [`docs/data/dataset-taxonomy.md`](data/dataset-taxonomy.md),
> [`dataset-schema.md`](data/dataset-schema.md),
> [`versioning-policy.md`](data/versioning-policy.md) +
> [`ml/datasets/`](../ml/datasets/) (S-AI-WS3).

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

Five layers, full description in
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).
Per-stage I/O in
[`docs/pipeline/stage-contracts.md`](pipeline/stage-contracts.md). Data
layer in [`docs/data/`](data/).

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

| WS | Title | M | Status | Sprint plan |
|---|---|---|---|---|
| WS1 | Architecture baseline | M9 | ✅ DONE 2026-05-10 (S-AI-WS1) | [ws1-architecture-baseline.md](sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| WS2 | Canonical trade pipeline | M9 | ✅ DONE 2026-05-10 (S-AI-WS2) | [ws2-canonical-pipeline.md](sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| WS3 | Data foundation | M10 | ✅ DONE 2026-05-10 (S-AI-WS3) — see [`data/`](data/) + [`ml/datasets/`](../ml/datasets/) | [ws3-data-foundation.md](sprint-plans/ai-traders/ws3-data-foundation.md) |
| WS4 | Training center | M9 | 🔜 Next | [ws4-training-center.md](sprint-plans/ai-traders/ws4-training-center.md) |
| WS5 | Baseline models | M9 | 📋 Not started | [ws5-baseline-models.md](sprint-plans/ai-traders/ws5-baseline-models.md) |
| WS6 | Open-source model layer | M9 | 📋 Not started | [ws6-open-source-models.md](sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | 📋 Not started | [ws7-deployment-tiers.md](sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | 📋 Not started | [ws8-monitoring-feedback.md](sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | 🔄 Continuous | [ws9-runtime-split.md](sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | 📋 Not started | [ws10-arch-doc-enforcement.md](sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

---

### Workstream 1 — Architecture baseline

**Closed:** S-AI-WS1, 2026-05-10.
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

---

### Workstream 2 — Canonical trade pipeline

**Closed:** S-AI-WS2, 2026-05-10. Stage names locked in
[`src/pipeline/types.py`](../src/pipeline/types.py); per-stage I/O
in [`docs/pipeline/stage-contracts.md`](pipeline/stage-contracts.md).
Live-path migration filed as a Tier 2 follow-up.

---

### Workstream 3 — Data foundation

**Closed:** S-AI-WS3, 2026-05-10. Reproducible dataset framework under
[`ml/datasets/`](../ml/datasets/) with `DatasetMetadata` + `DatasetBuilder`
+ `validate_dataset` + CLI. First buildable family
([`backtest_results`](../ml/datasets/families/backtest_results.py)) reads
`trade_journal.db` read-only and emits versioned JSONL + metadata.
Manual HF publication flow documented in
[`docs/integrations/huggingface-datasets.md`](integrations/huggingface-datasets.md);
no `huggingface_hub` dep added.

**Acceptance** (all met)

- [x] Documented dataset taxonomy.
- [x] At least one dataset family generated and published
  repeatably.
- [x] Dataset metadata carries enough lineage to reproduce a
  training run later.

**Buildable families:** `backtest_results` (live as of S-AI-WS3).
**Scaffolded but not yet buildable:** `market_raw`,
`market_features`, `setup_labels`, `trade_outcomes`,
`account_context`, `review_journal` — each filed as a follow-up so
leakage tests can be designed per family.

---

### Workstream 4 — Training center

**Objective.** Repo-native training center that behaves like a repeatable
factory for training, evaluation, registration, and promotion.

**Required structure** (adapt to existing `ml/` layout)

```text
ml/
  datasets/      # ✅ S-AI-WS3
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
  `register`, `promote`. (`build-dataset` is already provided by
  `python -m ml.datasets build` from S-AI-WS3.)
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
  optional and follows later.

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
- `docs/pipeline/stage-contracts.md` — ✅ created in S-AI-WS2.
- `src/pipeline/types.py` — ✅ created in S-AI-WS2.
- `docs/data/dataset-taxonomy.md`, `dataset-schema.md`,
  `versioning-policy.md` — ✅ created in S-AI-WS3.
- `docs/integrations/huggingface-datasets.md` — ✅ created in
  S-AI-WS3.
- `ml/datasets/` (framework + first family) — ✅ created in
  S-AI-WS3.
- `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` — WS10.
- `docs/architecture/model-inventory.md` — WS6.
- `docs/ml/training-center.md` — WS4.
- `docs/ml/model-registry-policy.md` — WS4.
- `ml/configs/`, `ml/features/`, `ml/trainers/`, `ml/evaluators/`,
  `ml/registry/`, `ml/reports/` — WS4.

---

## Recommended implementation order

1. WS1 — architecture audit + canonical architecture doc. **✅ done
   (S-AI-WS1).**
2. WS2 — canonical trade pipeline + stage contracts. **✅ done
   (S-AI-WS2).**
3. WS3 — dataset taxonomy, schema docs, first reproducible dataset builder.
   **✅ done (S-AI-WS3).**
4. WS4 — training center structure + command path. **🔜 next.**
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
- Do **not** auto-publish datasets to Hugging Face. Publication is
  always an operator-driven action (S-AI-WS3 rule).

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
| 2026-05-10 | S-AI-ROADMAP | Master plan adopted; M9 + M10 expanded into WS1–WS10; sprint-plan stubs seeded for WS1–WS10. | `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-plans/ai-traders/*` | None — docs only. |
| 2026-05-10 | S-AI-WS1 | WS1 complete. Canonical AI-scope doc at `docs/architecture/ai-model-platform.md`; linked from `ARCHITECTURE-CANONICAL.md`. | `docs/architecture/ai-model-platform.md`, `docs/ARCHITECTURE-CANONICAL.md`, `docs/sprint-plans/ai-traders/ws1-architecture-baseline.md`, `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-logs/S-AI-WS1.md` | None — doc-only. |
| 2026-05-10 | S-AI-WS2 | WS2 complete. Stage names locked in `src/pipeline/types.py`; per-stage I/O + owners + logging at `docs/pipeline/stage-contracts.md`; tests in `tests/pipeline/test_types.py`. | `src/pipeline/*`, `tests/pipeline/*`, `docs/pipeline/stage-contracts.md`, `docs/architecture/ai-model-platform.md`, `docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md`, `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-logs/S-AI-WS2.md` | None — additive types. |
| 2026-05-10 | S-AI-WS3 | WS3 complete. Reproducible dataset framework under `ml/datasets/` (metadata + builder ABC + registry + validator + CLI); first buildable family `backtest_results` reads `trade_journal.db` read-only via `mode=ro` URI. Append-only versioning policy. Manual HF publication flow. Stdlib only; no new deps. | `ml/datasets/*`, `tests/ml/datasets/*`, `docs/data/{dataset-taxonomy,dataset-schema,versioning-policy}.md`, `docs/integrations/huggingface-datasets.md`, `docs/architecture/ai-model-platform.md`, `docs/sprint-plans/ai-traders/ws3-data-foundation.md`, `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-logs/S-AI-WS3.md` | None — additive; live runtime untouched. Builder is read-only against `trade_journal.db`. |
