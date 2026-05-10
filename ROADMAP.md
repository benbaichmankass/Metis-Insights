# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-10 (through S-AI-WS4-FU).
>
> **Canonical authority:**
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)
> 3. This file (`ROADMAP.md`)
> 4. Current sprint log in `docs/sprint-logs/`
>
> **Scope-specific master plans:**
> - M9 + M10 — [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>   AI-scope canonical [`ai-model-platform.md`](docs/architecture/ai-model-platform.md);
>   pipeline types + stage contracts;
>   data layer [`docs/data/`](docs/data/) + [`ml/datasets/`](ml/datasets/);
>   training center [`docs/ml/`](docs/ml/) + [`ml/`](ml/);
>   first specialist baselines under `ml/configs/`.

---

## Core Principles

1. **Lean solutions.**
2. **Stability first.**
3. **Profitability focus.**

---

## M0..M10 Milestone Roadmap

| Milestone | Type | Focus | Status |
|---|---|---|---|
| **M0–M5** | auto-claude | Foundation → Strategy testing | ✅ CLOSED |
| **M6** | auto-claude | Web app UI | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | 🔄 IN PROGRESS — WS1+WS2+WS4+WS5-A+WS4-FU closed 2026-05-10. |
| **M10** | auto-claude | HF / data pipeline | 🔄 IN PROGRESS — WS3 closed; WS9 continuous. |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 + WS4-FU → WS5
> baselines (sub-sprints A..F) → shadow mode (WS7) → WS6 → WS8 + WS10.
> WS9 is continuous from WS3 onwards.

| WS | Title | Status | Sprint plan |
|---|---|---|---|
| **WS1** | Architecture baseline | ✅ DONE | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | ✅ DONE | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | ✅ DONE | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | ✅ DONE (S-AI-WS4 + S-AI-WS4-FU) | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) + [ws4-followups.md](docs/sprint-plans/ai-traders/ws4-followups.md) |
| **WS5** | Baseline models | 🔄 IN PROGRESS (S-AI-WS5-A done; B–F queued) | [ws5-baseline-models.md](docs/sprint-plans/ai-traders/ws5-baseline-models.md) |
| **WS6** | Open-source model layer | 📋 NOT STARTED | [ws6-open-source-models.md](docs/sprint-plans/ai-traders/ws6-open-source-models.md) |
| **WS7** | Deployment tiers | 📋 NOT STARTED | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| **WS8** | Monitoring and feedback loops | 📋 NOT STARTED | [ws8-monitoring-feedback.md](docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| **WS9** | Oracle / Hugging Face runtime split | 🔄 CONTINUOUS | [ws9-runtime-split.md](docs/sprint-plans/ai-traders/ws9-runtime-split.md) |
| **WS10** | Architecture-doc enforcement | 📋 NOT STARTED | [ws10-arch-doc-enforcement.md](docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

**Non-negotiable rules:**

- Do not weaken live trading safety.
- No heavy training on the Oracle live VM.
- No model in live strategy logic without staged promotion +
  operator approval.
- AI output cannot bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code updates the architecture docs in the
  same PR.
- No auto-publishing datasets to HF (S-AI-WS3).
- No editing past `StatusEvent` entries in the registry
  (S-AI-WS4 — append-only).
- No promoting to `live-approved` or `champion` without operator
  approval recorded in `--by` + `--reason` (S-AI-WS4).
- No outcome columns as features against `won` on `trade_outcomes`
  (S-AI-WS5-A).

### Active milestone queue (next 3)

1. **M6 — Web app UI (dashboard repo).**
2. **(M5 P4 closed 2026-05-10).**
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak.

> **AI-traders queue note:** WS1+WS2+WS3+WS4+WS5-A+WS4-FU closed
> 2026-05-10. **Next on AI-traders track is WS5-B** — regime
> classifier; needs operator decision on which `market_raw`
> adapter ships first (CSV / yfinance / off-VM exchange) per the
> design note in `ws5-baseline-models.md`.

### Repo and hosting boundary (MANDATORY)

Dashboard web app **lives in a separate repository**
(`ict-trader-dashboard`) and **runs on Vercel** — NOT on the
Oracle VM.

---

## Historical Sprint Ledger

Full detail preserved in git history. Recent AI-traders sprints:

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-AI-ROADMAP | AI traders models roadmap adopted | ✅ Done (`#693` `1eb59f6`) | M9, M10 |
| S-AI-WS1 | AI traders WS1 — architecture baseline | ✅ Done (`#694` `f453b89`) | M9 |
| S-AI-WS2 | AI traders WS2 — canonical trade pipeline | ✅ Done (`#701` `42a1e6f`) | M9 |
| S-AI-WS3 | AI traders WS3 — data foundation | ✅ Done (`#704` `60807f4`) | M10 |
| S-AI-WS4 | AI traders WS4 — training center | ✅ Done (`#719` `b910fd3`) | M9 |
| S-AI-WS5-A | AI traders WS5-A — outcome probability baseline | ✅ Done (`#730` `6a9f5a0`) | M9 |
| **S-AI-WS4-FU** | **AI traders WS4 follow-ups.** Generic Predictor abstraction (decouples evaluators from trainer state); time-aware + walk-forward splitters; `compare` CLI; global-only sanity baseline manifest; `market_raw` multi-source design pinned. Logged in `docs/sprint-logs/S-AI-WS4-FU.md`. | ✅ Done 2026-05-10 (this PR) | M9 |

> **Sprint number note:** S-067 is in flight as the silent-empty
> audit; AI traders track uses themed `S-AI-*` ids.

---

## Standing / Recurring Sessions

| Type | Cadence | Cap |
|---|---|---|
| Hardening & Stability Audit | Bi-daily | 3h |
| Strategy Improvement Review | Weekly | 4h |
| Model Training & Evaluation | Weekly (HF cron) | 6h (offloaded) |

---

## Items Under Consideration (Not Yet Scheduled)

- Recurring-Session Triggers + `/roadmap` Command.
- Exchange Failover / Multi-Exchange Support.
- Deployment Automation.
- Tier 2 follow-up: live-path migration onto WS2 types.
- Per-family dataset builders (`market_raw` w/ multi-source
  adapters — WS5-B prereq; `market_features`, `setup_labels`,
  `account_context`, `review_journal`).
- `python -m ml.datasets publish` HF subcommand.
- Aggregated walk-forward (averaging metrics across folds).
- Per-strategy detail metrics artifact alongside scalar metrics.
- Registry concurrent-writer locking.

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`. AI-traders workstream sprint
plans live under `docs/sprint-plans/ai-traders/wsN-<slug>.md`.
Themed ids (`S-AI-WSN`, optionally `-A/-B/.../FU` for
sub-sprints / follow-ups) parallel the numeric sequence.

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done | Completed and merged |
| 🔜 Next | Immediate next sprint |
| 🔄 In Progress | Currently being executed |
| ⚠️ Reopened | Verification revealed drift |
| ⛔ Blocked / Scratched | Cannot proceed or cancelled |
| 📋 Backlog | Defined but not yet started |
