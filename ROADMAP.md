# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-07 (workplan-status-review session — M1 reopened per operator;
> S-047 T2 merged; S-015 scratched; M6 moved to dashboard repo)
> **Canonical authority:** `docs/claude/workplan.md` (the decider). When this file
> conflicts with the workplan, the workplan wins.

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
| **M1** | auto-claude | Comms infrastructure | Repo-based Claude/operator comms, Telegram writeback, dedupe, docs, tests | ⚠️ REOPENED 2026-05-07 — S-042 closed M1 against the pre-reconciliation workplan; new workplan adopted later same day via S-041. Per workplan § "Verify-before-trusting-done", the on-disk telegram-bot implementation has not been audited against the new workplan § "Telegram bots" spec. **S-048 (M1 comms audit) queued behind S-047 close** — see `docs/sprints/sprint-048-prompt.md`. |
| **M2** | auto-claude | Web app source of truth | Read-only dashboard backend and core status data surfaces | 🔄 PARTIAL — S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT). S-014 added `/api/bot/{stats,logs,positions,signals}` + CORS keyed to `DASHBOARD_ORIGIN`. Vercel rewrite proxy fix landed 2026-05-07. Backend effectively complete; formal close-out deferred. |
| **M3** | auto-claude | Risk controls foundation | Hard risk caps, kill switch, status controls, order-layer refusal tests | ✅ CLOSED (S-043, CP-2026-05-06-15) |
| **M4** | auto-claude | Repo hygiene + CI | Janitor cleanup, canonical paths, GitHub Actions, test/lint automation | ✅ CLOSED (S-046, 2026-05-07) |
| **M5** | auto-claude | Strategy testing workflow | Telegram-triggered test flow, validation logging, backtest workflow docs | 📋 NOT STARTED — paused behind S-047 close + S-048 (M1 audit) |
| **M6** | auto-claude | Web app UI | Dashboard UI for pnl, status, open positions, logs, recent actions | 🔄 IN PROGRESS (dashboard repo) — S-014 V1 SPA shipped in `the-lizardking/ict-trader-dashboard`; **S-015 V2 plan scratched 2026-05-07** per operator. Next session opens in dashboard repo with focus order: live data feed first, then operator-control functionalities (Forced Stop, killswitch, close-all-positions, account live/dry-run toggle) per workplan § "Dashboard build order". |
| **M7** | pm-sprint | Strategy review gate | Review validation results: promote, hold, or kill | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | Parameter review and approval-required strategy changes | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | Model registry, current-model audit, training and performance tracking | 📋 NOT STARTED |
| **M10** | auto-claude | HF / data pipeline | Dataset publishing, artifact packaging, reproducible research workflow | 📋 NOT STARTED |

### Active milestone queue (next 3)

Per workplan priority: **system hardening and operational visibility before expansion.**

1. **S-047 T3 close** — Tier 2/3 ad-hoc live-trading sprint. PR #459 (T2) merged
   2026-05-07; T3 ready (D4 isLeverage=1 routing in `execute.py` + D5 direction-aware
   balance for spot-margin accounts in `coordinator.py`). Per operating-protocol § 2.2
   "one task per session" T3 runs in its own session.
2. **S-048 — M1 comms audit (telegram-bot deep dive)** — Tier 1 docs-only audit
   reopening M1 per operator directive 2026-05-07. Prompt:
   `docs/sprints/sprint-048-prompt.md`. Produces a gap-list comparing on-disk
   telegram-bot implementation against the new workplan's § "Telegram bots" + § "Required
   logs" + § "Repeatable operator-triggered workflows". Code follow-ups filed as
   their own sprints.
3. **M5 — Strategy testing workflow** — Telegram `/test <strategy>` command,
   validation logging, backtest runbook. Begins after S-048 closes (M1 audit may
   surface dependencies M5 inherits).

### Repo and hosting boundary (MANDATORY)

The dashboard web app **lives in a separate repository** (`ict-trader-dashboard`) and
**runs on Vercel** — NOT on the Oracle VM. Do not add web-app source code, build
configs, or dashboard UI files to `ict-trading-bot`. This repo publishes a clean data
feed; the dashboard is a pure consumer. See `docs/claude/workplan.md` § "Dashboard apps
— Repo and hosting boundary" for the full rule.

> ⚠️ **Known conflict:** S-013, S-014, and (cancelled) S-015 were planned in this repo
> before the Vercel boundary rule was codified (2026-05-06). The data-feed backend
> (S-013, S-014 backend additions) is correctly placed here. S-014 V1 web client moved
> to the dashboard repo where M6 now continues. S-015 V2 scratched 2026-05-07.

---

## Historical Sprint Ledger

> Sprints S-000 through S-040 completed under the old Phase 0–4 / M-S-NNN roadmap
> framing. This ledger is preserved for traceability. Status "Done" was accepted from
> prior sessions — use verify-before-trusting-done on any sprint before relying on its
> artifacts. Each sprint maps to one or more M0..M10 milestones.

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

### Phase 3 — Prop Trading Layer *(maps to M3; prop infra deferred per workplan)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-007 | Prop Account Manager | ✅ Done | M3 (partial; prop infra deferred) |
| S-008 | Coordinator Architecture & Full Unit Rewire | ✅ Done | M4 |
| S-009 | Deferred Wiring: Colab Backtest + App Config | ✅ Done | M5, M4 |
| S-010 | Per-Account Risk Engine | ✅ Done | M3 |

### Phase 3.5 — Web UIs *(maps to M2, M6 — ⚠️ built in this repo; see boundary note above)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-011 | Backtesting UI | ✅ Done | M5, M6 (in-repo; boundary note) |
| S-012 | Production Wiring Audit & Full Live Activation | ✅ Done | M3, M4 |

### Phase 4 — Secure Web Dashboard *(maps to M2, M6)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-013 | Secure Web Dashboard: Backend Scaffold & Home Status | ✅ Done | M2 (data-feed publisher — correct repo) |
| S-014 | Web Client V1 (Home Dashboard) — moved to `ict-trader-dashboard` | ✅ Done | M6 (dashboard repo) |
| S-015 | Web Client V2 (Component Tabs) | ⛔ SCRATCHED 2026-05-07 per operator | — |
| S-014.5 | Web Client public exposure (reverse proxy + TLS + CSP) | 📋 Backlog (dashboard repo) | M6 |

### Phase 5 — Web Dashboard Ops Layer *(maps to M6 — dashboard repo)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-016 | Secure API Key Management | 📋 Backlog (dashboard repo) | M6 |

### Ad-hoc sprints (S-017 onwards)

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-017 | Activate live trading + smoke test | ✅ Done — all PRs on `main`; CP-2026-04-30-14 | M3 |
| S-018 | Fix Telegram pings + auto-install systemd units | ✅ Done | M1 |
| S-019 | Bot-side ping inbox | ✅ Done (partial; deferred auto-ping to S-020) | M1 |
| S-020 | Fix auto-ping path | ✅ Done — CP-2026-04-30-17; BUG-018/022 closed | M1 |
| S-021 | BUG-048 hardening: config-drift contract + boot-time observability | ✅ Done — CP-2026-05-04-04; 59 tests | M3, M4 |
| S-035 | Architecture audit | ✅ Done | M4 |
| S-041 | Workplan reconciliation sweep | ✅ Done — CP-2026-05-06-12 | Meta/docs |
| S-042 | M1 close (telegram-bot pipeline audit) | ⚠️ DONE — but **superseded** by S-048 audit per operator 2026-05-07 (closed before new workplan adopted) | M1 |
| S-043 | M3 close (order-layer refusal tests) | ✅ Done — CP-2026-05-06-15 | M3 |
| S-044 | M4 step 1 (CI suite) | ✅ Done — CP-2026-05-07-03 | M4 |
| S-045 | M4 step 2 (conftest + pytest-collect blocking + ruff default) | ✅ Done — CP-2026-05-07-05 | M4 |
| S-046 | M4 close (Janitor audits) | ✅ Done | M4 |
| S-047 | bybit_2 Spot Margin enablement | 🔄 IN PROGRESS (T0 deleted; T1 ✅ PR #456; T2 ✅ PR #459 merged 2026-05-07; T3 ready; T4–T7 queued) | M3 (live-trading priority) |
| S-048 | M1 comms audit (telegram-bot deep dive) | 📋 QUEUED behind S-047 close — see `docs/sprints/sprint-048-prompt.md` | M1 reopen |

> **Sprint number note:** S-036..S-040 include the burned range (see
> `docs/claude/workplan.md` § "Sprint and checkpoint numbering"). Full sprint history
> in git log and `docs/claude/checkpoints/CHECKPOINT_LOG.md`.

---

## S-008 Sprint Record

**Completed:** 2026-04-29 | **Checkpoint:** `CP-2026-04-29-58`
**PRs merged:** #120–#128 (9 PRs) | **Tests added:** 178

| Unit | Key File | Tests |
|---|---|---|
| Coordinator (TRANSLATOR) | `src/core/coordinator.py` | — |
| Strategies | `src/units/strategies/{ict,vwap,breakout_confirmation,killzone}.py` | 27 |
| Accounts | `src/units/accounts/{risk,execute}.py` | 23 |
| Dashboards | `src/units/dashboards/{alerts,stats}.py` | 25 |
| Telegram Bot rewired | `src/bot/telegram_query_bot.py` | 19 |
| Trading School | `src/units/trading_school/validator.py` | 23 |
| Integration Tests | `tests/test_coordinator_flow.py` | 25 |

**Deferred to S-009:** `trigger_backtest()` Colab wiring; App unit config operations.

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
  `/improve_strategy`, `/train_model`, `/roadmap`. Required to operationalize the
  recurring-session program.
- **Exchange Failover / Multi-Exchange Support** — resilience via secondary exchange.
- **Deployment Automation** — CI/CD pipeline for deploying approved code to Oracle VM.

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done | Sprint/milestone completed and merged |
| 🔜 Next | Planned as the immediate next sprint |
| 🔄 In Progress | Currently being executed |
| ⚠️ Reopened | Previously closed; subsequent verification revealed drift or new spec |
| ⛔ Blocked / Scratched | Cannot proceed without a decision/dependency, or cancelled outright |
| 📋 Backlog | Defined but not yet started |
| 💬 Discussion | Idea raised, not yet broken into tasks |
