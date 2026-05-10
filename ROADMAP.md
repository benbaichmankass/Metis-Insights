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
> **Scope-specific master plans** (owned by individual milestones):
> - M9 + M10 — [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md)
>   (AI traders models roadmap; expanded into WS1–WS10 below). The
>   AI-scope canonical doc is
>   [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md)
>   (S-AI-WS1); pipeline stage contracts at
>   [`docs/pipeline/stage-contracts.md`](docs/pipeline/stage-contracts.md)
>   (S-AI-WS2); pipeline types at
>   [`src/pipeline/types.py`](src/pipeline/types.py); data layer
>   under [`docs/data/`](docs/data/) +
>   [`ml/datasets/`](ml/datasets/) (S-AI-WS3).
>
> Older `docs/claude/workplan.md` and `docs/workplan.md` are kept as
> historical context. When they disagree with the canonical docs above,
> the canonical docs win.
>
> Prior history: S-061..S-064 closed the dashboard build-out arc (sprints
> A..D); S-065 deferred behind operator Google Cloud Console setup for
> OAuth login (option (c)); S-066 Janitor pass closed out the M1 P2
> hygiene cluster.

---

## Core Principles

1. **Lean solutions** — smallest change that delivers real value; no over-engineering.
2. **Stability first** — never build features on a shaky foundation.
3. **Profitability focus** — every sprint should move the needle on live trading
   performance or operational safety.

---

## M0..M10 Milestone Roadmap

> Canonical milestone sequence from `docs/claude/workplan.md` (adopted 2026-05-06).
> This section is the living roadmap. The Historical Sprint Ledger below is the
> record of what was actually built.

| Milestone | Type | Focus | Main outcome | Status |
|---|---|---|---|---|
| **M0** | auto-claude | Workflow foundation | Master protocol, session state, logging conventions, handoff rules | ✅ CLOSED (S0, CP-2026-05-06-S0-02) |
| **M1** | auto-claude | Comms infrastructure | Repo-based Claude/operator comms, Telegram writeback, dedupe, docs, tests | ✅ CLOSED 2026-05-08 |
| **M2** | auto-claude | Web app source of truth | Read-only dashboard backend and core status data surfaces | ✅ CLOSED 2026-05-08 |
| **M3** | auto-claude | Risk controls foundation | Hard risk caps, kill switch, status controls, order-layer refusal tests | ✅ CLOSED (S-043, CP-2026-05-06-15) |
| **M4** | auto-claude | Repo hygiene + CI | Janitor cleanup, canonical paths, GitHub Actions, test/lint automation | ✅ CLOSED (S-046, 2026-05-07) |
| **M5** | auto-claude | Strategy testing workflow | Telegram-triggered test flow, validation logging, backtest workflow docs | ✅ CLOSED 2026-05-10 |
| **M6** | auto-claude | Web app UI | Dashboard UI for pnl, status, open positions, logs, recent actions | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | Review validation results: promote, hold, or kill | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | Parameter review and approval-required strategy changes | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | Model registry, current-model audit, training and performance tracking. **Expanded by [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md) into WS1, WS2, WS4, WS5, WS6, WS7, WS8, WS10.** AI-scope canonical doc: [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md); stage contracts: [`docs/pipeline/stage-contracts.md`](docs/pipeline/stage-contracts.md). | 🔄 IN PROGRESS — WS1 + WS2 closed 2026-05-10. |
| **M10** | auto-claude | HF / data pipeline | Dataset publishing, artifact packaging, reproducible research workflow. **Expanded by [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md) into WS3 + WS9.** Data layer at [`docs/data/`](docs/data/) + [`ml/datasets/`](ml/datasets/). | 🔄 IN PROGRESS — WS3 closed (S-AI-WS3, 2026-05-10); WS9 is continuous policy. |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
> AI-scope canonical doc:
> [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md).
> Pipeline stage contracts:
> [`docs/pipeline/stage-contracts.md`](docs/pipeline/stage-contracts.md).
> Data layer: [`docs/data/`](docs/data/) +
> [`ml/datasets/`](ml/datasets/).
> Sprint plans: [`docs/sprint-plans/ai-traders/`](docs/sprint-plans/ai-traders/).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 → first WS5 baseline →
> registry+promotion (WS4/WS7) → shadow mode (WS7) → rest of WS5 → WS6
> → WS8 + WS10. WS9 is a continuous policy enforced from WS3 onwards.

| WS | Title | Owns | Status | Sprint plan |
|---|---|---|---|---|
| **WS1** | Architecture baseline | M9 | ✅ DONE (S-AI-WS1, `f453b89`) | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | M9 | ✅ DONE (S-AI-WS2, `42a1e6f`) | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | M10 | ✅ DONE (S-AI-WS3, this PR) | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | M9 | 🔜 NEXT | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) |
| **WS5** | Baseline models | M9 | 📋 NOT STARTED — blocked on WS4 | [ws5-baseline-models.md](docs/sprint-plans/ai-traders/ws5-baseline-models.md) |
| **WS6** | Open-source model layer | M9 | 📋 NOT STARTED — blocked on first WS5 baseline | [ws6-open-source-models.md](docs/sprint-plans/ai-traders/ws6-open-source-models.md) |
| **WS7** | Deployment tiers | M9 | 📋 NOT STARTED — overlaps WS4 registry work | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
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

### Active milestone queue (next 3)

Per `docs/claude/milestone-state.md` "Queued milestones":

1. **M6 — Web app UI (dashboard repo)** — Vercel SPA wiring of mock-data feeds (equity chart, Active ICT Strategies, Trading Conditions) to live `/api/bot/*` data; positions and signals to follow.
2. **(M5 P4 closed 2026-05-10)** — bot #689 + dashboard `#12` shipped the backtest-history surface end-to-end.
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak (started 2026-05-10).

> **AI-traders queue note:** WS1 + WS2 + WS3 closed 2026-05-10.
> **Next on the AI-traders track is WS4 (training center)** — repo-native
> training factory under `ml/` (`trainers/`, `evaluators/`,
> `experiments/`, `registry/`, `promotion/`, `configs/`, `reports/`).
> Doc-heavy + light scaffold, no live-runtime risk.

### Repo and hosting boundary (MANDATORY)

The dashboard web app **lives in a separate repository** (`ict-trader-dashboard`) and
**runs on Vercel** — NOT on the Oracle VM. Do not add web-app source code, build
configs, or dashboard UI files to `ict-trading-bot`. This repo publishes a clean data
feed; the dashboard is a pure consumer.

---

## Historical Sprint Ledger

### Phase 0 — Foundation & Workflow *(maps to M0)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-000 | Repo hygiene, CLAUDE.md hardening, checkpoint system | ✅ Done | M0 |
| S0 | Workflow Foundation — master protocol, session state, milestone-state file | ✅ Done | M0 |

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
| S-007 | Prop Account Manager | ✅ Done | M3 (partial; prop infra deferred) |
| S-008 | Coordinator Architecture & Full Unit Rewire | ✅ Done | M4 |
| S-009 | Deferred Wiring: Colab Backtest + App Config | ✅ Done | M5, M4 |
| S-010 | Per-Account Risk Engine | ✅ Done | M3 |

### Phase 3.5 — Web UIs

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-011 | Backtesting UI | ✅ Done | M5, M6 (in-repo; boundary note) |
| S-012 | Production Wiring Audit & Full Live Activation | ✅ Done | M3, M4 |

### Phase 4 — Secure Web Dashboard

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-013 | Secure Web Dashboard: Backend Scaffold & Home Status | ✅ Done | M2 |
| S-014 | Web Client V1 (Home Dashboard) — moved to `ict-trader-dashboard` | ✅ Done | M6 |
| S-015 | Web Client V2 (Component Tabs) | ⛔ SCRATCHED 2026-05-07 per operator | — |

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
| S-047 | bybit_2 Spot Margin enablement | ✅ Done 2026-05-10 | M3 |
| S-048 | M1 comms audit (fresh re-issue) | ✅ Done 2026-05-08 | M1 (PARTIAL) |
| S-049 | Spot-margin sizer correctness fast-followup | ✅ Done | M3 |
| S-050 | VWAP Phase 2 — HTF gate | ✅ Done 2026-05-09 (PR #558) | M3, M9 |
| S-058 | Spot-margin dispatch tolerance | ✅ Done 2026-05-09 (PR #575) | M3 |
| S-059 | Stuck-strategy watchdog respects exchange-side position state | ✅ Done 2026-05-09 (PR #582) | M3 |
| S-060 | Orphan-position reconciler | ✅ Done 2026-05-09 (PR #586) | M3 |
| S-061 | Dashboard build-out sprint A | ✅ Done 2026-05-09 | M2, M6 |
| S-062 | Dashboard build-out sprint B | ✅ Done 2026-05-09 | M6 |
| S-063 | Dashboard build-out sprint C | ✅ Done 2026-05-09 | M2, M6 |
| S-064 | Dashboard build-out sprint D | ✅ Done 2026-05-09 | M2, M6 |
| S-065 | Dashboard build-out sprint E | ⏸ Deferred 2026-05-09 | M3, M6 |
| S-066 | Janitor — M1 P2 hygiene cluster close-out | ✅ Done 2026-05-09 | M1 |
| S-CANON-1 | Canonical-docs rebase + repo audit | ✅ Done 2026-05-10 | Meta/docs |
| S-CANON-FU-1 | Mark legacy workplan superseded | ✅ Done 2026-05-10 | Meta/docs |
| S-CANON-FU-2 | Wire `closed_flat_invariant.check` into `run_monitor_tick` | 🔄 DRAFT | M3 |
| S-CANON-FU-3 | Enable branch-protection-sync on `main` | 🔄 PARTIAL | M4 |
| S-AI-ROADMAP | AI traders models roadmap adopted | ✅ Done 2026-05-10 (`#693` squash `1eb59f6`) | M9, M10 |
| **S-AI-WS1** | **AI traders WS1 — architecture baseline.** Canonical AI-scope doc at `docs/architecture/ai-model-platform.md`. | ✅ Done 2026-05-10 (`#694` squash `f453b89`) | M9 |
| **S-AI-WS2** | **AI traders WS2 — canonical trade pipeline.** Stage names locked in `src/pipeline/types.py`. Per-stage I/O at `docs/pipeline/stage-contracts.md`. Frozen-dataclass `TradeCandidate`, `ExecutionIntent`, `StageDecision`. Tests in `tests/pipeline/`. Additive; no live-runtime call site rewired. | ✅ Done 2026-05-10 (`#701` squash `42a1e6f`) | M9 |
| **S-AI-WS3** | **AI traders WS3 — data foundation.** Reproducible dataset framework under `ml/datasets/` (metadata + builder ABC + registry + validator + CLI). First buildable family `backtest_results` reads `trade_journal.db::backtest_results` read-only via SQLite `mode=ro` URI. Append-only versioning. Manual HF publication (no auto-push, no `huggingface_hub` dep). Stdlib only. Docs: `docs/data/{dataset-taxonomy,dataset-schema,versioning-policy}.md` + `docs/integrations/huggingface-datasets.md`. Tests in `tests/ml/datasets/` against synthetic SQLite. Live runtime untouched. Logged in `docs/sprint-logs/S-AI-WS3.md`. | ✅ Done 2026-05-10 (this PR) | M10 |

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

- **Recurring-Session Triggers + `/roadmap` Command** — Telegram commands `/audit`,
  `/improve_strategy`, `/train_model`, `/roadmap`. Already implemented on ClaudeBot.
- **Exchange Failover / Multi-Exchange Support** — resilience via secondary exchange.
- **Deployment Automation** — CI/CD pipeline for deploying approved code to Oracle VM.
- **Tier 2 follow-up: live-path migration onto WS2 types.** Wire
  `TradeCandidate` / `ExecutionIntent` through the existing
  coordinator path. Operator-ack required.
- **Per-family dataset builders (S-AI-WS3 follow-ups).**
  `market_raw`, `market_features`, `setup_labels`, `trade_outcomes`,
  `account_context`, `review_journal` — one sprint per family so
  leakage tests can be designed individually.
- **`python -m ml.datasets publish` subcommand.** Wraps
  `huggingface-cli upload`. Filed for a follow-up to S-AI-WS3.

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`

AI-traders workstream sprint plans live under
`docs/sprint-plans/ai-traders/wsN-<slug>.md`. Themed ids (`S-AI-WSN`)
parallel the numeric sequence and are used when the next free
numeric id is already taken.

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
