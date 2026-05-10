# AI Traders Models Roadmap

> **Status:** Master plan adopted 2026-05-10.
>
> Canonical authority order:
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md)
> 3. [`ROADMAP.md`](../ROADMAP.md)
> 4. The current sprint log in [`docs/sprint-logs/`](sprint-logs/)
>
> **AI-scope canonical doc:**
> [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
> (S-AI-WS1). **Stage contracts:**
> [`docs/pipeline/stage-contracts.md`](pipeline/stage-contracts.md)
> (S-AI-WS2). **Pipeline types:**
> [`src/pipeline/types.py`](../src/pipeline/types.py) (S-AI-WS2).
> **Data layer:** [`docs/data/`](data/) +
> [`ml/datasets/`](../ml/datasets/) (S-AI-WS3, S-AI-WS5-A).
> **Training center + registry:**
> [`docs/ml/`](ml/) + [`ml/`](../ml/) (S-AI-WS4).
> **First specialist baseline:**
> [`ml/configs/baseline-trade-outcome-winrate.yaml`](../ml/configs/baseline-trade-outcome-winrate.yaml)
> (S-AI-WS5-A).

---

## Mission

Evolve from a deterministic live bot into a structured AI-assisted
trading platform with reproducible datasets, specialist models,
deployment gates, and an auditable training center.

One orchestration layer coordinating multiple specialist models;
deterministic risk controls remain outside the AI layer.

---

## Operating assumptions

- Hugging Face is the primary external AI platform.
- Oracle VM hosts the live trader + light support compute only;
  not heavy training.
- No model bypasses deterministic risk guards, broker validation,
  account restrictions, or kill-switch.

---

## Strategic principles

1. Keep the live trading path safe.
2. Specialist models, not one master model.
3. Baselines before advanced families.
4. Reproducible datasets and training.
5. Promotion gates before live influence.
6. Doc updates are part of DoD.

---

## Workstreams

| WS | Title | M | Status | Sprint plan |
|---|---|---|---|---|
| WS1 | Architecture baseline | M9 | ✅ DONE 2026-05-10 (S-AI-WS1) | [ws1-architecture-baseline.md](sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| WS2 | Canonical trade pipeline | M9 | ✅ DONE 2026-05-10 (S-AI-WS2) | [ws2-canonical-pipeline.md](sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| WS3 | Data foundation | M10 | ✅ DONE 2026-05-10 (S-AI-WS3) | [ws3-data-foundation.md](sprint-plans/ai-traders/ws3-data-foundation.md) |
| WS4 | Training center | M9 | ✅ DONE 2026-05-10 (S-AI-WS4) | [ws4-training-center.md](sprint-plans/ai-traders/ws4-training-center.md) |
| WS5 | Baseline models | M9 | 🔄 IN PROGRESS — sub-sprint A (outcome probability) closed 2026-05-10 (S-AI-WS5-A); B–F queued | [ws5-baseline-models.md](sprint-plans/ai-traders/ws5-baseline-models.md) |
| WS6 | Open-source model layer | M9 | 📋 Not started — blocked on WS5 progress | [ws6-open-source-models.md](sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | 📋 Not started — registry tier system live; runtime hook pending | [ws7-deployment-tiers.md](sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | 📋 Not started | [ws8-monitoring-feedback.md](sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | 🔄 Continuous | [ws9-runtime-split.md](sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | 📋 Not started | [ws10-arch-doc-enforcement.md](sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

---

### Workstream 5 — Baseline models

**In progress.** WS5 is decomposed into per-baseline sub-sprints.
Full table in
[`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](sprint-plans/ai-traders/ws5-baseline-models.md).

| Sub-sprint | Baseline | Dataset prereq | Status |
|---|---|---|---|
| **S-AI-WS5-A** | Outcome probability (per-strategy historical winrate) | `trade_outcomes` (live as of S-AI-WS5-A) | ✅ DONE 2026-05-10 |
| S-AI-WS5-B | Regime classifier | `market_raw` (pending) | 📋 queued |
| S-AI-WS5-C | Setup quality scorer | `setup_labels` (pending) | 📋 queued |
| S-AI-WS5-D | Execution quality | `trade_outcomes` + execution metadata | 📋 queued |
| S-AI-WS5-E | Post-trade review | `review_journal` (pending) | 📋 queued |
| S-AI-WS5-F | Prop mission policy | `account_context` (pending) | 📋 queued |

---

## Recommended implementation order

1. WS1 — architecture audit. **✅ done (S-AI-WS1).**
2. WS2 — canonical trade pipeline. **✅ done (S-AI-WS2).**
3. WS3 — dataset taxonomy + first builder. **✅ done (S-AI-WS3).**
4. WS4 — training center. **✅ done (S-AI-WS4).**
5. WS5-A — first specialist baseline (outcome probability).
   **✅ done (S-AI-WS5-A).**
6. WS5-B onwards — remaining baselines (regime classifier next).
   **🔜 next.**
7. Shadow-mode deployment path (WS7).
8. Open-source model inventory + first HF workflow (WS6).
9. Monitoring, retraining, architecture-enforcement (WS8 + WS10).

---

## Non-negotiable rules

- Do not weaken live trading safety.
- Do not run heavy training on the Oracle live VM.
- Do not introduce a model into live strategy logic without staged
  promotion + explicit approval.
- Do not let AI output bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Do not ship architecture-changing code without updating the
  architecture docs.
- Do not auto-publish datasets to HF (S-AI-WS3).
- Do not edit past `StatusEvent` entries in the model registry
  (S-AI-WS4 — append-only).
- Do not promote any model to `live-approved` or `champion`
  without operator approval recorded in `--by` + `--reason`
  (S-AI-WS4).
- Do not consume outcome columns (`pnl`, `pnl_percent`) as
  features when the target is `won` against `trade_outcomes`
  (S-AI-WS5-A leakage discipline).

---

## Definition of Done (roadmap-level)

Unchanged. Marked ✅ across all 10 bullets when WS1–WS10 close.

---

## Final instruction to Claude

Small, reviewable PRs. Live-trader stability over model ambition.
Doc updates are part of the product.

---

## Change log

| Date | Sprint | Change | Files | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-AI-ROADMAP | Master plan adopted; M9 + M10 expanded into WS1–WS10. | `docs/AI-TRADERS-ROADMAP.md`, `ROADMAP.md`, `docs/sprint-plans/ai-traders/*` | None. |
| 2026-05-10 | S-AI-WS1 | WS1 complete. Canonical AI-scope doc created. | + cross-links. | None. |
| 2026-05-10 | S-AI-WS2 | WS2 complete. Stage names locked; typed schemas. | `src/pipeline/*`, `tests/pipeline/*`, `docs/pipeline/stage-contracts.md`, + cross-links. | None. |
| 2026-05-10 | S-AI-WS3 | WS3 complete. Reproducible dataset framework + `backtest_results` builder. | `ml/datasets/*`, `tests/ml/datasets/*`, `docs/data/*`, `docs/integrations/huggingface-datasets.md`, + cross-links. | None — additive; live runtime untouched. |
| 2026-05-10 | S-AI-WS4 | WS4 complete. Training factory: manifest + Trainer/Evaluator ABCs + filesystem registry + experiments runner + umbrella CLI. | `ml/{manifest, cli, __main__}.py`, `ml/{trainers, evaluators, experiments, registry, promotion, configs}/`, `tests/ml/test_*.py`, `docs/ml/*`, + cross-links. | None. |
| 2026-05-10 | S-AI-WS5-A | WS5 first sub-sprint. New `trade_outcomes` dataset family (CLOSED non-backtest trades; derived `won` label). Per-strategy historical winrate trainer + classification evaluator. Round-trips through WS4 harness. New non-negotiable: no outcome columns as features against `won`. | `ml/datasets/families/trade_outcomes.py`, `ml/datasets/registry.py`, `ml/trainers/per_strategy_winrate.py`, `ml/evaluators/classification.py`, `ml/configs/baseline-trade-outcome-winrate.yaml`, `tests/ml/{datasets/test_trade_outcomes,test_per_strategy_winrate}.py`, `docs/data/*`, `docs/architecture/ai-model-platform.md`, sprint plan + log, this doc. | None — additive; live runtime untouched. |
