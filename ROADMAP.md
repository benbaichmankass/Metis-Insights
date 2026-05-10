# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-10.
>
> **Canonical authority (adopted 2026-05-10):**
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)
> 3. This file (`ROADMAP.md`)
> 4. Current sprint log in `docs/sprint-logs/`
>
> **Scope-specific master plans:**
> - M9 + M10 — [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>   AI-scope canonical [`ai-model-platform.md`](docs/architecture/ai-model-platform.md)
>   (S-AI-WS1); pipeline types + stage contracts (S-AI-WS2);
>   data layer [`docs/data/`](docs/data/) +
>   [`ml/datasets/`](ml/datasets/) (S-AI-WS3 + S-AI-WS5-A);
>   training center [`docs/ml/`](docs/ml/) + [`ml/`](ml/) (S-AI-WS4);
>   first specialist baseline
>   [`ml/configs/baseline-trade-outcome-winrate.yaml`](ml/configs/baseline-trade-outcome-winrate.yaml)
>   (S-AI-WS5-A).

---

## Core Principles

1. **Lean solutions.**
2. **Stability first.**
3. **Profitability focus.**

---

## M0..M10 Milestone Roadmap

| Milestone | Type | Focus | Status |
|---|---|---|---|
| **M0** | auto-claude | Workflow foundation | ✅ CLOSED |
| **M1** | auto-claude | Comms infrastructure | ✅ CLOSED 2026-05-08 |
| **M2** | auto-claude | Web app source of truth | ✅ CLOSED 2026-05-08 |
| **M3** | auto-claude | Risk controls foundation | ✅ CLOSED |
| **M4** | auto-claude | Repo hygiene + CI | ✅ CLOSED |
| **M5** | auto-claude | Strategy testing workflow | ✅ CLOSED 2026-05-10 |
| **M6** | auto-claude | Web app UI | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | 🔄 IN PROGRESS — WS1 + WS2 + WS4 + WS5-A closed 2026-05-10. |
| **M10** | auto-claude | HF / data pipeline | 🔄 IN PROGRESS — WS3 closed; WS9 continuous. |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 → WS5 baselines
> (sub-sprints A..F) → shadow mode (WS7) → WS6 → WS8 + WS10.
> WS9 is continuous from WS3 onwards.

| WS | Title | Status | Sprint plan |
|---|---|---|---|
| **WS1** | Architecture baseline | ✅ DONE (S-AI-WS1) | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | ✅ DONE (S-AI-WS2) | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | ✅ DONE (S-AI-WS3) | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | ✅ DONE (S-AI-WS4) | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) |
| **WS5** | Baseline models | 🔄 IN PROGRESS — sub-sprint A closed (S-AI-WS5-A, this PR); B–F queued | [ws5-baseline-models.md](docs/sprint-plans/ai-traders/ws5-baseline-models.md) |
| **WS6** | Open-source model layer | 📋 NOT STARTED — blocked on WS5 progress | [ws6-open-source-models.md](docs/sprint-plans/ai-traders/ws6-open-source-models.md) |
| **WS7** | Deployment tiers | 📋 NOT STARTED — registry tier live (WS4); runtime hook pending | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| **WS8** | Monitoring and feedback loops | 📋 NOT STARTED | [ws8-monitoring-feedback.md](docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| **WS9** | Oracle / Hugging Face runtime split | 🔄 CONTINUOUS | [ws9-runtime-split.md](docs/sprint-plans/ai-traders/ws9-runtime-split.md) |
| **WS10** | Architecture-doc enforcement | 📋 NOT STARTED | [ws10-arch-doc-enforcement.md](docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

**Non-negotiable rules:**

- Do not weaken live trading safety posture.
- Do not run heavy training on Oracle live VM.
- Do not introduce a model into live strategy logic without staged
  promotion + operator approval.
- Do not let AI output bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code must update architecture docs in the
  same PR.
- Do not auto-publish datasets to HF (S-AI-WS3).
- Do not edit past `StatusEvent` entries in the model registry
  (S-AI-WS4 — append-only).
- Do not promote any model to `live-approved` or `champion`
  without operator approval recorded in `--by` + `--reason`
  (S-AI-WS4).
- Do not consume outcome columns as features against the `won`
  target on `trade_outcomes` (S-AI-WS5-A leakage discipline).

### Active milestone queue (next 3)

1. **M6 — Web app UI (dashboard repo).**
2. **(M5 P4 closed 2026-05-10).**
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak.

> **AI-traders queue note:** WS1 + WS2 + WS3 + WS4 + WS5-A closed
> 2026-05-10. **Next on AI-traders track is WS5-B** — regime
> classifier (depends on `market_raw` builder; needs an explicit
> data-acquisition decision per WS9).

### Repo and hosting boundary (MANDATORY)

Dashboard web app **lives in a separate repository**
(`ict-trader-dashboard`) and **runs on Vercel** — NOT on the
Oracle VM.

---

## Historical Sprint Ledger

### Phase 0–4 — abbreviated; full detail preserved in git history

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-000 | Repo hygiene baseline | ✅ Done | M0 |
| S0 | Workflow Foundation | ✅ Done | M0 |
| S-001..S-003 | Telegram + observability + CI | ✅ Done | M1, M3, M4 |
| S-004..S-006 | Training pipeline + registry early work (vestigial) | ✅ Done | M9, M10, M5 |
| S-007..S-010 | Prop manager + coordinator + Colab + risk engine | ✅ Done | M3, M4, M5 |
| S-011..S-012 | Backtesting UI + production wiring | ✅ Done | M3, M4, M5, M6 |
| S-013..S-015 | Web dashboard scaffold (V1 shipped, V2 scratched) | ✅/⛔ | M2, M6 |
| S-017..S-046 | Live activation + audits + spot-margin enablement | ✅ Done | M3, M4 |
| S-047..S-066 | Spot-margin reconciler triad + dashboard build-out arc + Janitor | ✅ Done / ⏸ Deferred | M2, M3, M6, M1 |
| S-CANON-1..-3 | Canonical-docs rebase + branch protection | ✅ Done / 🔄 PARTIAL | Meta/docs, M3, M4 |
| S-AI-ROADMAP | AI traders models roadmap adopted | ✅ Done (`#693` `1eb59f6`) | M9, M10 |
| S-AI-WS1 | AI traders WS1 — architecture baseline | ✅ Done (`#694` `f453b89`) | M9 |
| S-AI-WS2 | AI traders WS2 — canonical trade pipeline | ✅ Done (`#701` `42a1e6f`) | M9 |
| S-AI-WS3 | AI traders WS3 — data foundation | ✅ Done (`#704` `60807f4`) | M10 |
| S-AI-WS4 | AI traders WS4 — training center | ✅ Done (`#719` `b910fd3`) | M9 |
| **S-AI-WS5-A** | **AI traders WS5-A — outcome probability baseline.** New `trade_outcomes` dataset family (CLOSED non-backtest trades, derived `won` label). Per-strategy historical winrate trainer + classification evaluator. Manifest + tests round-trip through the WS4 harness. New non-negotiable: no outcome columns as features against `won`. Logged in `docs/sprint-logs/S-AI-WS5-A.md`. | ✅ Done 2026-05-10 (this PR) | M9 |

> **Sprint number note:** S-067 is in flight as the silent-empty
> audit; the AI traders track uses themed `S-AI-*` ids (with
> sub-sprint suffixes for WS5).

---

## Standing / Recurring Sessions

| Type | Cadence | Cap |
|---|---|---|
| **Hardening & Stability Audit** | Bi-daily | 3h |
| **Strategy Improvement Review** | Weekly | 4h |
| **Model Training & Evaluation** | Weekly (HF cron) | 6h (offloaded) |

---

## Items Under Consideration (Not Yet Scheduled)

- Recurring-Session Triggers + `/roadmap` Command.
- Exchange Failover / Multi-Exchange Support.
- Deployment Automation.
- Tier 2 follow-up: live-path migration onto WS2 types.
- Per-family dataset builders (`market_raw`, `market_features`,
  `setup_labels`, `account_context`, `review_journal`).
- `python -m ml.datasets publish` subcommand.
- WS4 follow-ups: walk-forward / time-aware splitters, generic
  `predict()` interface, `compare` CLI subcommand, registry
  concurrent-writer locking.
- WS5-A follow-ups: per-strategy detail metrics artifact alongside
  scalar registry metrics; compare-against-`ConstantPredictionTrainer`
  global-only sanity baseline.

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`. AI-traders workstream sprint
plans live under `docs/sprint-plans/ai-traders/wsN-<slug>.md`.
Themed ids (`S-AI-WSN`, optionally `-A/-B/...` for sub-sprints)
parallel the numeric sequence.

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done | Completed and merged |
| 🔜 Next | Immediate next sprint |
| 🔄 In Progress / Active / Partial | Currently being executed |
| ⚠️ Reopened | Verification revealed drift |
| ⛔ Blocked / Scratched | Cannot proceed or cancelled |
| 📋 Backlog | Defined but not yet started |
