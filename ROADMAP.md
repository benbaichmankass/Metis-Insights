# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-10 (S-CANON-1 — canonical doc rebase + audit
> + spurious-file cleanup + stale owner-ref correction across active
> docs/scripts/workflows). See `docs/sprint-logs/S-CANON-1.md`.
>
> **Canonical authority (adopted 2026-05-10):**
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)
> 3. This file (`ROADMAP.md`)
> 4. The current sprint log in `docs/sprint-logs/`
>
> **Scope-specific master plans:**
> - M9 + M10 — [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>   Anchors: AI-scope canonical [`ai-model-platform.md`](docs/architecture/ai-model-platform.md)
>   (S-AI-WS1); pipeline types [`src/pipeline/types.py`](src/pipeline/types.py)
>   + stage contracts [`docs/pipeline/stage-contracts.md`](docs/pipeline/stage-contracts.md)
>   (S-AI-WS2); data layer [`docs/data/`](docs/data/) +
>   [`ml/datasets/`](ml/datasets/) (S-AI-WS3); training center +
>   model registry [`docs/ml/`](docs/ml/) + [`ml/`](ml/) (S-AI-WS4).
>
> Older `docs/claude/workplan.md` and `docs/workplan.md` are kept as
> historical context. When they disagree with the canonical docs above,
> the canonical docs win.

---

## Core Principles

1. **Lean solutions** — smallest change that delivers real value; no over-engineering.
2. **Stability first** — never build features on a shaky foundation.
3. **Profitability focus** — every sprint should move the needle on live trading
   performance or operational safety.

---

## M0..M10 Milestone Roadmap

| Milestone | Type | Focus | Main outcome | Status |
|---|---|---|---|---|
| **M0** | auto-claude | Workflow foundation | Master protocol, session state, logging conventions, handoff rules | ✅ CLOSED |
| **M1** | auto-claude | Comms infrastructure | Repo-based Claude/operator comms, Telegram writeback, dedupe, docs, tests | ✅ CLOSED 2026-05-08 |
| **M2** | auto-claude | Web app source of truth | Read-only dashboard backend and core status data surfaces | ✅ CLOSED 2026-05-08 |
| **M3** | auto-claude | Risk controls foundation | Hard risk caps, kill switch, status controls, order-layer refusal tests | ✅ CLOSED |
| **M4** | auto-claude | Repo hygiene + CI | Janitor cleanup, canonical paths, GitHub Actions, test/lint automation | ✅ CLOSED |
| **M5** | auto-claude | Strategy testing workflow | Telegram-triggered test flow, validation logging, backtest workflow docs | ✅ CLOSED 2026-05-10 |
| **M6** | auto-claude | Web app UI | Dashboard UI for pnl, status, open positions, logs, recent actions | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | Review validation results: promote, hold, or kill | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | Parameter review and approval-required strategy changes | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | Model registry, current-model audit, training and performance tracking. **Expanded by [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md). AI-scope canonical doc: [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md); training center: [`docs/ml/training-center.md`](docs/ml/training-center.md); registry policy: [`docs/ml/model-registry-policy.md`](docs/ml/model-registry-policy.md).** | 🔄 IN PROGRESS — WS1 + WS2 + WS4 closed 2026-05-10. |
| **M10** | auto-claude | HF / data pipeline | Dataset publishing, artifact packaging, reproducible research workflow. **Expanded by [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md). Data layer at [`docs/data/`](docs/data/) + [`ml/datasets/`](ml/datasets/).** | 🔄 IN PROGRESS — WS3 closed 2026-05-10; WS9 continuous. |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
> AI-scope canonical doc:
> [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md).
> Sprint plans:
> [`docs/sprint-plans/ai-traders/`](docs/sprint-plans/ai-traders/).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 → first WS5 baseline →
> shadow mode (WS7) → rest of WS5 → WS6 → WS8 + WS10. WS9 is a
> continuous policy enforced from WS3 onwards.

| WS | Title | Owns | Status | Sprint plan |
|---|---|---|---|---|
| **WS1** | Architecture baseline | M9 | ✅ DONE (S-AI-WS1) | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | M9 | ✅ DONE (S-AI-WS2) | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | M10 | ✅ DONE (S-AI-WS3) | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | M9 | ✅ DONE (S-AI-WS4, this PR) | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) |
| **WS5** | Baseline models | M9 | 🔜 NEXT | [ws5-baseline-models.md](docs/sprint-plans/ai-traders/ws5-baseline-models.md) |
| **WS6** | Open-source model layer | M9 | 📋 NOT STARTED — blocked on first WS5 baseline | [ws6-open-source-models.md](docs/sprint-plans/ai-traders/ws6-open-source-models.md) |
| **WS7** | Deployment tiers | M9 | 📋 NOT STARTED — registry tier system live (WS4); runtime hook pending | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| **WS8** | Monitoring and feedback loops | M9 | 📋 NOT STARTED — schedule after first model in shadow mode | [ws8-monitoring-feedback.md](docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| **WS9** | Oracle / Hugging Face runtime split | M10 | 🔄 CONTINUOUS — policy of record from WS3 onwards | [ws9-runtime-split.md](docs/sprint-plans/ai-traders/ws9-runtime-split.md) |
| **WS10** | Architecture-doc enforcement | M9 | 📋 NOT STARTED — schedule near WS8 / final close | [ws10-arch-doc-enforcement.md](docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

**Non-negotiable rules** (apply to every WS sprint):

- Do not weaken the live trading safety posture.
- Do not run heavy training jobs on the Oracle live VM.
- Do not introduce a model into live strategy logic without staged
  promotion and explicit operator approval.
- Do not let AI output bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code must update the architecture docs in the
  same PR.
- Do not auto-publish datasets to Hugging Face (S-AI-WS3 rule).
- Do not edit past StatusEvent entries in the model registry; the
  promotion history is append-only (S-AI-WS4 rule).
- Do not promote any model to `live-approved` or `champion` without
  operator approval recorded in `--by` + `--reason` (S-AI-WS4 rule).

### Active milestone queue (next 3)

1. **M6 — Web app UI (dashboard repo).**
2. **(M5 P4 closed 2026-05-10).**
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak.

> **AI-traders queue note:** WS1 + WS2 + WS3 + WS4 closed 2026-05-10.
> **Next on the AI-traders track is WS5 (baseline models)** — first
> specialist baseline end-to-end through the WS3 + WS4 paths.
> Recommended first baseline: regime classifier (needs `market_raw`
> dataset family as a prereq) or setup quality scorer.

### Repo and hosting boundary (MANDATORY)

The dashboard web app **lives in a separate repository** (`ict-trader-dashboard`) and
**runs on Vercel** — NOT on the Oracle VM. Do not add web-app source code, build
configs, or dashboard UI files to `ict-trading-bot`.

---

## Historical Sprint Ledger

### Phase 0 — Foundation & Workflow *(maps to M0)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-000 | Repo hygiene, CLAUDE.md hardening, checkpoint system | ✅ Done | M0 |
| S0 | Workflow Foundation | ✅ Done | M0 |

### Phase 1 — Core Stability *(maps to M3, M4)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-001 | Telegram Bot Hardening | ✅ Done | M1, M4 |
| S-002 | System Observability | ✅ Done | M3, M4 |
| S-003 | Test Coverage & CI Hardening | ✅ Done | M4 |

### Phase 2 — Model Pipeline *(maps to M9, M10)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-004 | Automated Training & Backtesting Pipeline | ✅ Done | M10, M5 |
| S-005 | Master Model / Strategy Monitor | ✅ Done | M9 |
| S-006 | Model Registry & Versioning | ✅ Done | M9 |

### Phase 3 — Prop Trading Layer

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-007 | Prop Account Manager | ✅ Done | M3 (partial) |
| S-008 | Coordinator Architecture & Full Unit Rewire | ✅ Done | M4 |
| S-009 | Deferred Wiring: Colab Backtest + App Config | ✅ Done | M5, M4 |
| S-010 | Per-Account Risk Engine | ✅ Done | M3 |

### Phase 3.5 — Web UIs

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-011 | Backtesting UI | ✅ Done | M5, M6 |
| S-012 | Production Wiring Audit & Full Live Activation | ✅ Done | M3, M4 |

### Phase 4 — Secure Web Dashboard

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-013 | Secure Web Dashboard: Backend Scaffold & Home Status | ✅ Done | M2 |
| S-014 | Web Client V1 (Home Dashboard) | ✅ Done | M6 |
| S-015 | Web Client V2 (Component Tabs) | ⛔ SCRATCHED 2026-05-07 | — |

### Ad-hoc sprints (S-017 onwards)

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-017 | Activate live trading + smoke test | ✅ Done | M3 |
| S-021 | BUG-048 hardening | ✅ Done | M3, M4 |
| S-035 | Architecture audit | ✅ Done | M4 |
| S-041 | Workplan reconciliation sweep | ✅ Done | Meta/docs |
| S-042 | M1 close (telegram-bot pipeline audit) | ⚠️ Superseded | M1 |
| S-043 | M3 close (order-layer refusal tests) | ✅ Done | M3 |
| S-044 | M4 step 1 (CI suite) | ✅ Done | M4 |
| S-045 | M4 step 2 | ✅ Done | M4 |
| S-046 | M4 close (Janitor audits) | ✅ Done | M4 |
| S-047 | bybit_2 Spot Margin enablement | ✅ Done | M3 |
| S-048 | M1 comms audit (fresh re-issue) | ✅ Done | M1 (PARTIAL) |
| S-049 | Spot-margin sizer correctness fast-followup | ✅ Done | M3 |
| S-050 | VWAP Phase 2 | ✅ Done (PR #558) | M3, M9 |
| S-058 | Spot-margin dispatch tolerance | ✅ Done (PR #575) | M3 |
| S-059 | Stuck-strategy watchdog | ✅ Done (PR #582) | M3 |
| S-060 | Orphan-position reconciler | ✅ Done (PR #586) | M3 |
| S-061..S-064 | Dashboard build-out arc | ✅ Done | M2, M6 |
| S-065 | Dashboard build-out sprint E | ⏸ Deferred | M3, M6 |
| S-066 | Janitor — M1 P2 hygiene cluster close-out | ✅ Done | M1 |
| S-CANON-1 | Canonical-docs rebase + repo audit | ✅ Done | Meta/docs |
| S-CANON-FU-1 | Mark legacy workplan superseded | ✅ Done | Meta/docs |
| S-CANON-FU-2 | Wire `closed_flat_invariant.check` | 🔄 DRAFT | M3 |
| S-CANON-FU-3 | Enable branch-protection-sync | 🔄 PARTIAL | M4 |
| S-AI-ROADMAP | AI traders models roadmap adopted | ✅ Done (`#693` `1eb59f6`) | M9, M10 |
| **S-AI-WS1** | **AI traders WS1 — architecture baseline.** | ✅ Done (`#694` `f453b89`) | M9 |
| **S-AI-WS2** | **AI traders WS2 — canonical trade pipeline.** | ✅ Done (`#701` `42a1e6f`) | M9 |
| **S-AI-WS3** | **AI traders WS3 — data foundation.** | ✅ Done (`#704` `60807f4`) | M10 |
| **S-AI-WS4** | **AI traders WS4 — training center.** YAML manifest schema (`ml/manifest.py`) + Trainer/Evaluator ABCs (`ml/{trainers,evaluators}/`) + filesystem model registry with state machine + transition log (`ml/registry/`) + experiments runner (`ml/experiments/`) + umbrella CLI `python -m ml ...` (`ml/{cli,__main__}.py`). Demo `ConstantPredictionTrainer` + `RegressionEvaluator` round-trips train+evaluate+register against `backtest_results` family. Docs: `docs/ml/{training-center,model-registry-policy}.md`. Tests in `tests/ml/test_*.py`. Live runtime untouched. Logged in `docs/sprint-logs/S-AI-WS4.md`. | ✅ Done 2026-05-10 (this PR) | M9 |

> **Sprint number note:** S-067 is in flight as the silent-empty
> audit (`docs/sprints/sprint-067-prompt.md`); the AI traders track
> uses themed `S-AI-*` ids in parallel.

---

## Standing / Recurring Sessions

Full spec: [`docs/claude/recurring-sessions.md`](docs/claude/recurring-sessions.md).

| Type | Cadence | Prompt | Cap | Purpose |
|---|---|---|---|---|
| **Hardening & Stability Audit** | Bi-daily | [`docs/sprints/recurring-hardening-prompt.md`](docs/sprints/recurring-hardening-prompt.md) | 3h | E2E health check; deep-dive prioritized subsystem |
| **Strategy Improvement Review** | Weekly | [`docs/sprints/recurring-strategy-improvement-prompt.md`](docs/sprints/recurring-strategy-improvement-prompt.md) | 4h | Compare live vs backtest; propose param adjustments (Tier 3) |
| **Model Training & Evaluation** | Weekly (HF cron) | [`docs/sprints/recurring-model-training-prompt.md`](docs/sprints/recurring-model-training-prompt.md) | 6h (offloaded) | Train candidate; evaluate vs incumbent; propose promote/reject |

---

## Items Under Consideration (Not Yet Scheduled)

- **Recurring-Session Triggers + `/roadmap` Command.**
- **Exchange Failover / Multi-Exchange Support.**
- **Deployment Automation.**
- **Tier 2 follow-up: live-path migration onto WS2 types.**
- **Per-family dataset builders** (S-AI-WS3 follow-ups).
- **`python -m ml.datasets publish` subcommand** (S-AI-WS3 follow-up).
- **WS4 follow-ups:** walk-forward / time-aware splitters,
  generic `predict()` interface, `compare` CLI subcommand,
  registry concurrent-writer locking.

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`

AI-traders workstream sprint plans live under
`docs/sprint-plans/ai-traders/wsN-<slug>.md`. Themed ids (`S-AI-WSN`)
parallel the numeric sequence.

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done | Sprint/milestone completed and merged |
| 🔜 Next | Planned as the immediate next sprint |
| 🔄 In Progress / Active / Partial | Currently being executed or partial |
| ⚠️ Reopened | Previously closed; subsequent verification revealed drift or new spec |
| ⛔ Blocked / Scratched | Cannot proceed without a decision/dependency, or cancelled outright |
| 📋 Backlog | Defined but not yet started |
| 💬 Discussion | Idea raised, not yet broken into tasks |
