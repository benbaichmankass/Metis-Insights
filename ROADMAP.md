# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-06 (S-041 workplan reconciliation — M0..M10 framing adopted)
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
| **M1** | auto-claude | Comms infrastructure | Repo-based Claude/operator comms, Telegram writeback, dedupe, docs, tests | 🔄 IN PROGRESS (auto-ping + routing fixed; writeback loop pending) |
| **M2** | auto-claude | Web app source of truth | Read-only dashboard backend and core status data surfaces | 🔄 PARTIAL — data-feed backend built S-013; dashboard consumer needs separate repo |
| **M3** | auto-claude | Risk controls foundation | Hard risk caps, kill switch, status controls, order-layer refusal tests | 🔄 IN PROGRESS (risk engine S-010 done; refusal tests partial) |
| **M4** | auto-claude | Repo hygiene + CI | Janitor cleanup, canonical paths, GitHub Actions, test/lint automation | 🔄 IN PROGRESS (S-003, S-035, S-021 done; full CI suite pending) |
| **M5** | auto-claude | Strategy testing workflow | Telegram-triggered test flow, validation logging, backtest workflow docs | 📋 NOT STARTED |
| **M6** | auto-claude | Web app UI | Dashboard UI for pnl, status, open positions, logs, recent actions | ⛔ BLOCKED — workplan boundary requires separate Vercel repo; S-015 under operator hold |
| **M7** | pm-sprint | Strategy review gate | Review validation results: promote, hold, or kill | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | Parameter review and approval-required strategy changes | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | Model registry, current-model audit, training and performance tracking | 📋 NOT STARTED |
| **M10** | auto-claude | HF / data pipeline | Dataset publishing, artifact packaging, reproducible research workflow | 📋 NOT STARTED |

### Active milestone queue (next 3)

Per workplan priority: **system hardening and operational visibility before expansion.**

1. **M1 — Comms infrastructure** — Complete the repo-based comms loop. BUG-058/BUG-059
   fixed and on `main`; structured writeback loop is the remaining gap.
2. **M3 — Risk controls foundation** — Formalize hard risk caps, complete order-layer
   refusal tests, close open risk-path gaps.
3. **M4 — Repo hygiene + CI** — Complete Janitor audits and full GitHub Actions
   lint/test automation.

### Repo and hosting boundary (MANDATORY)

The dashboard web app **lives in a separate repository** (`ict-trader-dashboard`) and
**runs on Vercel** — NOT on the Oracle VM. Do not add web-app source code, build
configs, or dashboard UI files to `ict-trading-bot`. This repo publishes a clean data
feed; the dashboard is a pure consumer. See `docs/claude/workplan.md` § "Dashboard apps
— Repo and hosting boundary" for the full rule.

> ⚠️ **Known conflict:** S-013, S-014, and S-015 were built in this repo before the
> Vercel boundary rule was codified (2026-05-06). The data-feed backend (S-013) is
> correctly placed here. The web client code (S-014 templates/static) and S-015's
> planned tabs are in the wrong repo. Resolution is pending the S-015
> pause/continue operator hold decision.

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

### Phase 4 — Secure Web Dashboard *(maps to M2, M6 — ⚠️ boundary note)*

> S-013 FastAPI backend belongs here (data-feed publisher = correct repo). S-014 and
> S-015 web client code is in the wrong repo per the workplan boundary rule.

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-013 | Secure Web Dashboard: Backend Scaffold & Home Status | ✅ Done | M2 (data-feed publisher — correct repo) |
| S-014 | Web Client V1 (Home Dashboard) | ✅ Done | M6 (⚠️ in this repo; boundary violation) |
| S-015 | Web Client V2 (Component Tabs) | ⛔ Blocked — T0 kickoff 2026-05-06; pause/continue under operator hold | M6 (⚠️ boundary violation + hold) |
| S-014.5 | Web Client public exposure (reverse proxy + TLS + CSP) | 📋 Backlog — gated on S-015 resolution | M6 (⚠️ boundary note) |

### Phase 5 — Web Dashboard Ops Layer *(maps to M6 — ⚠️ boundary note)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-016 | Secure API Key Management | 📋 Backlog | M6 (⚠️ boundary note) |

### Ad-hoc sprints (S-017 onwards)

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-017 | Activate live trading + smoke test | ✅ Done — all PRs on `main`; CP-2026-04-30-14 | M3 |
| S-018 | Fix Telegram pings + auto-install systemd units | ✅ Done | M1 |
| S-019 | Bot-side ping inbox | ✅ Done (partial; deferred auto-ping to S-020) | M1 |
| S-020 | Fix auto-ping path | ✅ Done — CP-2026-04-30-17; BUG-018/022 closed | M1 |
| S-021 | BUG-048 hardening: config-drift contract + boot-time observability | ✅ Done — CP-2026-05-04-04; 59 tests | M3, M4 |
| S-035 | Architecture audit | ✅ Done | M4 |
| S-041 | Workplan reconciliation sweep (this sprint) | 🔄 In Progress | Meta/docs |

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
| ⛔ Blocked | Cannot proceed without a decision or dependency |
| 📋 Backlog | Defined but not yet started |
| 💬 Discussion | Idea raised, not yet broken into tasks |
