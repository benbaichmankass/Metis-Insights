# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-04-29 (S-010 complete; prop sprints deferred; Text Milestones promoted to S-011)
> **Maintained by:** PM (Ben) + Tech Lead (Perplexity)
> **Sprint prompt files:** `docs/sprints/sprint-NNN-prompt.md`

---

## Core Principles

1. **Lean solutions** — smallest change that delivers real value; no over-engineering.
2. **Stability first** — never build features on a shaky foundation. Hardening sprints precede feature sprints.
3. **Profitability focus** — every sprint should move the needle on live trading performance or operational safety.

---

## Workflow

- Roadmap items are discussed between PM and Tech Lead and broken into **sprints**.
- Before a sprint starts, a sprint prompt file is created at `docs/sprints/sprint-NNN-prompt.md`.
- Claude Code executes the sprint autonomously, merges PRs independently, and posts a checkpoint summary.
- After each sprint, we review, discuss, and update this file to reflect progress and re-prioritise.

---

## Roadmap Overview

### Phase 0 — Foundation & Workflow
**Goal:** Establish clean process before accelerating feature work.

| Sprint | Title | Status |
|--------|-------|--------|
| S-000 | Repo hygiene, CLAUDE.md hardening, checkpoint system | ✅ Done |

---

### Phase 1 — Core Stability
**Goal:** Make the live system robust, observable, and maintainable before scaling.

| Sprint | Title | Status |
|--------|-------|--------|
| S-001 | **Telegram Bot Hardening** — decouple bot from hardcoded config, make it dynamically reflect the live system state | ✅ Done |
| S-002 | **System Observability** — structured logging, error alerting, runtime health metrics pipeline | ✅ Done |
| S-003 | **Test Coverage & CI Hardening** — expand test suite, enforce linting/type checking in CI | ✅ Done |

---

### Phase 2 — Model Pipeline
**Goal:** Build a robust, repeatable process for training, evaluating, and iterating on models.

| Sprint | Title | Status |
|--------|-------|--------|
| S-004 | **Automated Training & Backtesting Pipeline** — scheduled Colab/HF jobs for periodic retraining, standardised metrics output | ✅ Done |
| S-005 | **Master Model / Strategy Monitor** — periodic task that reviews all strategy performance, flags underperformers, generates structured improvement report | ✅ Done |
| S-006 | **Model Registry & Versioning** — track model versions, associate them with strategy configs, enable rollback | ✅ Done |

---

### Phase 3 — Prop Trading Layer
**Goal:** Enable trading on funded/prop accounts safely with isolated risk management per account.

| Sprint | Title | Status |
|--------|-------|--------|
| S-007 | **Prop Account Manager** — upload API key, associate with a strategy, isolated execution layer | ✅ Done |
| S-008 | **Coordinator Architecture & Full Unit Rewire** — Translator/Coordinator pattern, unit rewire (strategies, accounts, dashboards, trading school), Telegram bot rewired, 178 tests across 9 PRs (#120–#128) | ✅ Done |
| S-009 | **Deferred Wiring: Colab Backtest + App Config** — `trigger_backtest()` Colab wiring, App unit config operations (carried over from S-008) | ✅ Done |
| S-010 | **Per-Account Risk Engine** — `TradingAccount`, `RiskManager`, `Integrator`, multi-account execution, Telegram risk commands, 62 tests (PRs #135–#139) | ✅ Done |
| — | **Prop Account Model** — lightweight breach-avoidance model per account (probability scoring, position adjustment) | 📋 Backlog — Deferred until prop accounts ready |

---

### Phase 3.5 — Text Milestones (Web UIs)
**Goal:** PM-iterable visibility into backtests and strategy config — no mobile app required.

| Sprint | Title | Status |
|--------|-------|--------|
| S-011 | **Backtesting UI** — Streamlit web view for historical results, equity curve, strategy comparison; `/accounts` dry/live toggle | 🔜 Next |
| S-012 | **Strategy Config UI** — Edit risk %, timeframe, symbols via Streamlit + `config/strategies.yaml`; `/reload_strats` Telegram command | 📋 Backlog |

---

### Phase 4 — Mobile App V1 (Dashboard)
**Goal:** Read-only mobile dashboard giving full visibility into the system.

| Sprint | Title | Status |
|--------|-------|--------|
| S-013 | **App Scaffold & Home Dashboard** — React Native or Flutter scaffold, home tab with overall P&L, system status, active strategies | 📋 Backlog |
| S-014 | **Component Tabs** — tabs for Strategies, Accounts, Model Metrics, Runtime Logs & Bugs; left-side tab panel | 📋 Backlog |

---

### Phase 5 — Mobile App V2 (Ops)
**Goal:** Allow operational tasks from the app — primarily secure key management.

| Sprint | Title | Status |
|--------|-------|--------|
| S-015 | **Secure API Key Management** — add/store/rotate API keys through the app, encrypted vault, eliminates need to manually edit master-secrets file | 📋 Backlog |

---

## S-008 Sprint Record

**Completed:** 2026-04-29 | **Checkpoint:** `CP-2026-04-29-58` in `CHECKPOINT_LOG.md`
**PRs merged:** #120–#128 (9 PRs) | **Tests added:** 178

| Unit | Key File | Tests |
|------|----------|-------|
| Coordinator (TRANSLATOR) | `src/core/coordinator.py` | — |
| Strategies | `src/units/strategies/{ict,vwap,breakout_confirmation,killzone}.py` | 27 |
| Accounts | `src/units/accounts/{risk,execute}.py` | 23 |
| Dashboards | `src/units/dashboards/{alerts,stats}.py` | 25 |
| Telegram Bot rewired | `src/bot/telegram_query_bot.py` | 19 |
| Trading School | `src/units/trading_school/validator.py` | 23 |
| Workflows + Docs | `docs/workflows/`, `docs/architecture.md` | — |
| Integration Tests | `tests/test_coordinator_flow.py` | 25 |

**Deferred to S-009:**
- `trigger_backtest()` Colab wiring
- App unit config operations

---

## Items Under Consideration (Not Yet Scheduled)

These are suggested additions for discussion — they are not committed sprints yet:

- **Exchange Failover / Multi-Exchange Support** — add resilience by supporting a secondary exchange in case Bybit has issues.
- **Notification Centre** — structured trade, error, and performance notifications beyond Telegram (push to mobile app).
- **Audit Log / Trade Journal** — persistent, queryable record of all trade decisions with reasoning for review.
- **Paper Trading Mode** — ability to run any strategy in simulated mode against live data without real orders, useful for validating new models.
- **Deployment Automation** — CI/CD pipeline for deploying approved code to the Oracle VM automatically after sprint merges.

---

## Sprint File Naming Convention

Sprint prompt files live in `docs/sprints/` and follow this pattern:

```
docs/sprints/sprint-NNN-prompt.md
```

Example: `docs/sprints/sprint-001-prompt.md`

Each file contains:
- Sprint goal and scope
- Ordered task list with acceptance criteria
- Files Claude is permitted to modify
- Merge and handoff instructions

---

## Status Key

| Symbol | Meaning |
|--------|---------| 
| ✅ Done | Sprint completed and merged |
| 🔜 Next | Planned as the immediate next sprint |
| 🔄 In Progress | Currently being executed by Claude Code |
| 📋 Backlog | Defined but not yet started |
| 💬 Discussion | Idea raised, not yet broken into tasks |
