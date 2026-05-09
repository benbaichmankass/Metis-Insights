# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-09 (S-064 closed — Liquidity Maps + Settings
> tabs + two new bot Tier-1 endpoints; S-065 deferred behind operator
> Google Cloud Console setup for OAuth login (option (c) picked over the
> originally-planned shared-secret option (a)); S-066 Janitor pass closing
> out the M1 P2 hygiene cluster honestly. S-061..S-064 closed today form
> sprints A..D of the dashboard build-out arc; S-065 (sprint E) reopens
> when OAuth CREDS land. See `docs/sprints/sprint-061-prompt.md` for the
> parent plan.
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
| **M1** | auto-claude | Comms infrastructure | Repo-based Claude/operator comms, Telegram writeback, dedupe, docs, tests | ✅ CLOSED 2026-05-08 — S-048 fresh re-issue (audit verdict PARTIAL, no P0) closed on `claude/update-roadmap-status-ZnLM9`. Four P1 follow-ups landed: P1-A workplan correction (same-session with audit), P1-D `/new_session`+`/test` commands, P1-B stuck-request recovery alerts (one-time stuck alert + final pre-EXPIRED alert), P1-C auto-hourly snapshot timer (`deploy/ict-hourly-snapshot.{timer,service}`). P2 hygiene cluster filed for a future Janitor sprint. Sources: `docs/audits/M1-comms-audit-2026-05-07-fresh.md` + `docs/audits/M1-comms-audit-followups-fresh.md`. |
| **M2** | auto-claude | Web app source of truth | Read-only dashboard backend and core status data surfaces | ✅ CLOSED 2026-05-08 — S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT) + S-014 dashboard endpoints (`/api/bot/{stats,logs,positions,signals}`) + CORS keyed to `DASHBOARD_ORIGIN` + Vercel rewrite proxy fix (2026-05-07). Backend was effectively complete since S-014; this is the paperwork-only formal close. |
| **M3** | auto-claude | Risk controls foundation | Hard risk caps, kill switch, status controls, order-layer refusal tests | ✅ CLOSED (S-043, CP-2026-05-06-15) |
| **M4** | auto-claude | Repo hygiene + CI | Janitor cleanup, canonical paths, GitHub Actions, test/lint automation | ✅ CLOSED (S-046, 2026-05-07) |
| **M5** | auto-claude | Strategy testing workflow | Telegram-triggered test flow, validation logging, backtest workflow docs | 📋 NOT STARTED — paused behind S-047 T6. The bot-side `/test` dispatch surface is now in place via M1 P1-D; M5 only wires the artifact consumer. |
| **M6** | auto-claude | Web app UI | Dashboard UI for pnl, status, open positions, logs, recent actions | 🔄 IN PROGRESS (dashboard repo) — S-014 V1 SPA shipped in `the-lizardking/ict-trader-dashboard`. **In active session 2026-05-08** on dashboard branch `claude/update-roadmap-status-ZnLM9` — wiring mock-data feeds (equity chart, Active ICT Strategies, Trading Conditions) to live `/api/bot/*` data; positions and signals to follow. |
| **M7** | pm-sprint | Strategy review gate | Review validation results: promote, hold, or kill | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | Parameter review and approval-required strategy changes | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | Model registry, current-model audit, training and performance tracking | 📋 NOT STARTED |
| **M10** | auto-claude | HF / data pipeline | Dataset publishing, artifact packaging, reproducible research workflow | 📋 NOT STARTED |

### Active milestone queue (next 3)

Per `docs/claude/milestone-state.md` "Queued milestones":

1. **S-047 T6 — end-to-end live smoke + runbook (D8)** — Tier 1 docs after smoke. Smoke harness exists from T3. Live smoke needs the Bybit web-UI Spot Margin toggle ON for `bybit_2`.
2. **S-047 T7 — sprint close** — docs-only (milestone-state + bug-log + summary).
3. **M5 — Strategy testing workflow** — Telegram `/test <strategy>` artifact consumer + validation logging + backtest runbook. Bot-side dispatch surface ready via M1 P1-D.

### Repo and hosting boundary (MANDATORY)

The dashboard web app **lives in a separate repository** (`ict-trader-dashboard`) and
**runs on Vercel** — NOT on the Oracle VM. Do not add web-app source code, build
configs, or dashboard UI files to `ict-trading-bot`. This repo publishes a clean data
feed; the dashboard is a pure consumer. See `docs/claude/workplan.md` § "Dashboard apps
— Repo and hosting boundary" for the full rule.

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

### Phase 3.5 — Web UIs *(maps to M2, M6 — built in this repo before the boundary rule)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-011 | Backtesting UI | ✅ Done | M5, M6 (in-repo; boundary note) |
| S-012 | Production Wiring Audit & Full Live Activation | ✅ Done | M3, M4 |

### Phase 4 — Secure Web Dashboard *(maps to M2, M6)*

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-013 | Secure Web Dashboard: Backend Scaffold & Home Status | ✅ Done | M2 (data-feed publisher) |
| S-014 | Web Client V1 (Home Dashboard) — moved to `ict-trader-dashboard` | ✅ Done | M6 (dashboard repo) |
| S-015 | Web Client V2 (Component Tabs) | ⛔ SCRATCHED 2026-05-07 per operator | — |

### Ad-hoc sprints (S-017 onwards)

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-017 | Activate live trading + smoke test | ✅ Done | M3 |
| S-021 | BUG-048 hardening: config-drift contract + boot-time observability | ✅ Done | M3, M4 |
| S-035 | Architecture audit | ✅ Done | M4 |
| S-041 | Workplan reconciliation sweep | ✅ Done | Meta/docs |
| S-042 | M1 close (telegram-bot pipeline audit) | ⚠️ Superseded — closed pre-reconciliation; see S-048 below | M1 |
| S-043 | M3 close (order-layer refusal tests) | ✅ Done | M3 |
| S-044 | M4 step 1 (CI suite) | ✅ Done | M4 |
| S-045 | M4 step 2 (conftest + pytest-collect blocking + ruff default) | ✅ Done | M4 |
| S-046 | M4 close (Janitor audits) | ✅ Done | M4 |
| S-047 | bybit_2 Spot Margin enablement | 🔄 IN PROGRESS (T1–T5 ✅ + S-049 fast-followup ✅; T6 active) | M3 (live-trading priority) |
| S-048 | M1 comms audit (fresh re-issue) | ✅ Done 2026-05-08 — `CP-2026-05-07-17-s048-fresh-m1-audit` | M1 (PARTIAL) |
| S-049 | Spot-margin sizer correctness fast-followup (UTA availableBalance + buy-side fee buffer) | ✅ Done | M3 |
| S-050 | VWAP Phase 2 — HTF gate (Sharpe lift on top of 38-month baseline) | ✅ Done 2026-05-09 (PR #558) | M3, M9 |
| S-058 | Spot-margin dispatch tolerance (totalEquity fallback so non-USDT residue does not brick dispatch) | ✅ Done 2026-05-09 (PR #575) | M3 |
| S-059 | Stuck-strategy watchdog respects exchange-side position state | ✅ Done 2026-05-09 (PR #582) | M3 |
| S-060 | Orphan-position reconciler — auto-liquidate stranded base-coin balances back to USDT | ✅ Done 2026-05-09 (PR #586) | M3 |
| S-061 | Dashboard build-out sprint A — close #556 data-contract gap (vmHealth + signal pattern/confidence null-on-missing) + dashboard nullable types | ✅ Done 2026-05-09 (dashboard PR #7 + bot PR #590 squash `a8eaad4`) | M2, M6 |
| S-062 | Dashboard build-out sprint B — Models tab + Time & Price tab | ✅ Done 2026-05-09 (dashboard PR #8 squash `06ca19c`) | M6 |
| **S-063** | **Dashboard build-out sprint C — Performance tab + persistent equity history; bot drops JWT gate on `/api/pnl/history` (option (a)), flattens to `PnlHistoryPoint[]`, files `docs/api-tier-policy.md`** | ✅ Done 2026-05-09 (dashboard PR #9 squash `be85d10`; bot PR #595 squash `87d5ee1`) | M2, M6 |
| **S-064** | **Dashboard build-out sprint D — Liquidity Maps + Settings (read-only); ships two new Tier-1 bot endpoints `/api/bot/liquidity` (reads per-tick `runtime_logs/liquidity_state.json` written by the prereq pipeline hook) + `/api/bot/config` (redacted YAML view + runtime live/dry overlay)** | ✅ Done 2026-05-09 (bot prereq PR #597 squash `1eb816b`; bot main PR #601 squash `14fe5d7a`; dashboard PR #10 squash `b7963b26`) | M2, M6 |
| S-065 | Dashboard build-out sprint E — controls phase 1 (halt + live/dry toggle, Tier 2/3) + minimal session/login flow | ⏸ Deferred 2026-05-09 — login scope escalated to Google OAuth (option (c)); blocked on operator-side Google Cloud Console setup | M3, M6 |
| **S-066** | **Janitor — M1 P2 hygiene cluster close-out (docs only). Reconciled `M1-comms-audit-followups-fresh.md` § P2 against ground truth: 3 items already done, 2 carved out (schema-drift envelope + command-name cosmetics) for explicit follow-up.** | ✅ Done 2026-05-09 (this PR) | M1 |

> **Sprint number note:** S-036..S-040 burned per
> `docs/claude/workplan.md` § "Sprint and checkpoint numbering".
> S-049 ad-hoc fast-followup landed mid-S-047. S-050 VWAP Phase 2
> shipped early (PR #558, 2026-05-09). S-051..S-057 used by hardening
> work between 2026-05-08 and 2026-05-09. S-058..S-060 ship the
> spot-margin reconciler triad on 2026-05-09. **The next available
> sprint number after S-065 is S-066.**

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

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`

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
