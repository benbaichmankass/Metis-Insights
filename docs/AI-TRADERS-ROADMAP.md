# AI Traders Models Roadmap

> **Status:** Master plan adopted 2026-05-10. Through S-AI-WS5-B-PART-1.
>
> **AI-scope canonical doc:**
> [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).
> **Stage contracts:** [`docs/pipeline/stage-contracts.md`](pipeline/stage-contracts.md).
> **Pipeline types:** [`src/pipeline/types.py`](../src/pipeline/types.py).
> **Data layer:** [`docs/data/`](data/) + [`ml/datasets/`](../ml/datasets/).
> **`market_raw` adapters:**
> [`docs/ml/market-raw-adapters.md`](ml/market-raw-adapters.md).
> **Training center + registry + Predictor + splitters + compare:**
> [`docs/ml/`](ml/) + [`ml/`](../ml/).
> **First specialist baseline:**
> `ml/configs/baseline-trade-outcome-{winrate,global}.yaml`.

---

## Workstreams

| WS | Title | M | Status | Sprint plan |
|---|---|---|---|---|
| WS1 | Architecture baseline | M9 | ✅ DONE 2026-05-10 (S-AI-WS1) | [ws1-architecture-baseline.md](sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| WS2 | Canonical trade pipeline | M9 | ✅ DONE 2026-05-10 (S-AI-WS2) | [ws2-canonical-pipeline.md](sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| WS3 | Data foundation | M10 | ✅ DONE 2026-05-10 (S-AI-WS3) | [ws3-data-foundation.md](sprint-plans/ai-traders/ws3-data-foundation.md) |
| WS4 | Training center | M9 | ✅ DONE 2026-05-10 (S-AI-WS4 + S-AI-WS4-FU) | [ws4-training-center.md](sprint-plans/ai-traders/ws4-training-center.md) + [ws4-followups.md](sprint-plans/ai-traders/ws4-followups.md) |
| WS5 | Baseline models | M9 | 🔄 IN PROGRESS — sub-sprints A + B-PART-1 closed 2026-05-10 | [ws5-baseline-models.md](sprint-plans/ai-traders/ws5-baseline-models.md) |
| WS6 | Open-source model layer | M9 | 📋 Not started | [ws6-open-source-models.md](sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | 📋 Not started | [ws7-deployment-tiers.md](sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | 📋 Not started | [ws8-monitoring-feedback.md](sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | 🔄 Continuous | [ws9-runtime-split.md](sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | 📋 Not started | [ws10-arch-doc-enforcement.md](sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

---

## Recommended implementation order

1. WS1–WS4 + WS4-FU — ✅ done.
2. WS5-A (outcome probability) — ✅ done.
3. **WS5-B-PART-1 (`market_raw` adapter framework) — ✅ done
   2026-05-10.**
4. **WS5-B-PART-2 (regime classifier + Bybit off-VM fetch wiring) —
   🔜 next.**
5. WS5-C..F (other baselines).
6. Shadow mode (WS7) → WS6 → WS8 + WS10.

---

## Non-negotiable rules

- Live trading safety > feature growth.
- No heavy training on the Oracle live VM (WS9).
- No model in live strategy logic without staged promotion +
  operator approval.
- AI output cannot bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code updates the architecture docs in
  the same PR.
- No auto-publishing datasets to HF (S-AI-WS3).
- No editing past `StatusEvent` entries in the registry
  (S-AI-WS4 — append-only).
- No promoting to `live-approved` or `champion` without operator
  approval recorded in `--by` + `--reason` (S-AI-WS4).
- No outcome columns as features against `won` on
  `trade_outcomes` (S-AI-WS5-A).
- **No `ICT_OFFVM_BUILD_HOST=1` on the Oracle live VM**
  (S-AI-WS5-B-PART-1).

---

## Change log

| Date | Sprint | Change | Operator impact |
|---|---|---|---|
| 2026-05-10 | S-AI-ROADMAP | Master plan adopted. | None. |
| 2026-05-10 | S-AI-WS1 | WS1: AI-platform doc. | None. |
| 2026-05-10 | S-AI-WS2 | WS2: stage names locked; typed schemas. | None. |
| 2026-05-10 | S-AI-WS3 | WS3: dataset framework + `backtest_results`. | None. |
| 2026-05-10 | S-AI-WS4 | WS4: training center. | None. |
| 2026-05-10 | S-AI-WS5-A | WS5-A: outcome probability + `trade_outcomes`. | None. |
| 2026-05-10 | S-AI-WS4-FU | WS4 follow-ups: Predictor + splitters + `compare` + global-only baseline. | None. |
| 2026-05-10 | S-AI-WS5-B-PART-1 | WS5-B Part 1: `market_raw` multi-source adapter framework (CSV adapter live; Bybit off-VM scaffold env-gated; fetch wiring filed for operator). New non-negotiable: no `ICT_OFFVM_BUILD_HOST=1` on the live VM. | None — additive; Bybit shell raises NotImplementedError until operator wires the fetch. |
