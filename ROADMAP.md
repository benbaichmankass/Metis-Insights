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
>   [`src/pipeline/types.py`](src/pipeline/types.py).
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
| **M1** | auto-claude | Comms infrastructure | Repo-based Claude/operator comms, Telegram writeback, dedupe, docs, tests | ✅ CLOSED 2026-05-08 — S-048 fresh re-issue (audit verdict PARTIAL, no P0) closed on `claude/update-roadmap-status-ZnLM9`. Four P1 follow-ups landed: P1-A workplan correction (same-session with audit), P1-D `/new_session`+`/test` commands, P1-B stuck-request recovery alerts (one-time stuck alert + final pre-EXPIRED alert), P1-C auto-hourly snapshot timer (`deploy/ict-hourly-snapshot.{timer,service}`). P2 hygiene cluster filed for a future Janitor sprint. Sources: `docs/audits/M1-comms-audit-2026-05-07-fresh.md` + `docs/audits/M1-comms-audit-followups-fresh.md`. |
| **M2** | auto-claude | Web app source of truth | Read-only dashboard backend and core status data surfaces | ✅ CLOSED 2026-05-08 — S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT) + S-014 dashboard endpoints (`/api/bot/{stats,logs,positions,signals}`) + CORS keyed to `DASHBOARD_ORIGIN` + Vercel rewrite proxy fix (2026-05-07). Backend was effectively complete since S-014; this is the paperwork-only formal close. |
| **M3** | auto-claude | Risk controls foundation | Hard risk caps, kill switch, status controls, order-layer refusal tests | ✅ CLOSED (S-043, CP-2026-05-06-15) |
| **M4** | auto-claude | Repo hygiene + CI | Janitor cleanup, canonical paths, GitHub Actions, test/lint automation | ✅ CLOSED (S-046, 2026-05-07) |
| **M5** | auto-claude | Strategy testing workflow | Telegram-triggered test flow, validation logging, backtest workflow docs | ✅ CLOSED 2026-05-10 — P1 #637 (consumer + validation log + dispatch guard), P2 #639 (subprocess + timeout + lock + env gate), P3 #640 (runbook + close-out docs), P4 #689 (`GET /api/bot/backtests` Tier-1 read) + dashboard `#12` (Backtests tab). End-to-end shipped: operator dispatch → consumer → validation log → runbook → dashboard surface. Runbook: [`docs/runbooks/strategy-testing.md`](docs/runbooks/strategy-testing.md). |
| **M6** | auto-claude | Web app UI | Dashboard UI for pnl, status, open positions, logs, recent actions | 🔄 IN PROGRESS (dashboard repo) — S-014 V1 SPA shipped in `benbaichmankass/ict-trader-dashboard` (originally cut on the legacy `the-lizardking/...` namespace). Active wiring of mock-data feeds (equity chart, Active ICT Strategies, Trading Conditions) to live `/api/bot/*` data; positions and signals to follow. |
| **M7** | pm-sprint | Strategy review gate | Review validation results: promote, hold, or kill | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | Parameter review and approval-required strategy changes | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | Model registry, current-model audit, training and performance tracking. **Expanded by [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md) into WS1, WS2, WS4, WS5, WS6, WS7, WS8, WS10 (see table below). AI-scope canonical doc: [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md); stage contracts: [`docs/pipeline/stage-contracts.md`](docs/pipeline/stage-contracts.md).** | 🔄 IN PROGRESS — WS1 + WS2 complete (S-AI-WS1, S-AI-WS2, 2026-05-10). |
| **M10** | auto-claude | HF / data pipeline | Dataset publishing, artifact packaging, reproducible research workflow. **Expanded by [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md) into WS3 + WS9 (see table below).** | 📋 NOT STARTED |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
> AI-scope canonical doc:
> [`docs/architecture/ai-model-platform.md`](docs/architecture/ai-model-platform.md).
> Pipeline stage contracts:
> [`docs/pipeline/stage-contracts.md`](docs/pipeline/stage-contracts.md).
> Sprint plans: [`docs/sprint-plans/ai-traders/`](docs/sprint-plans/ai-traders/).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 → first WS5 baseline →
> registry+promotion (WS4/WS7) → shadow mode (WS7) → rest of WS5 → WS6
> → WS8 + WS10. WS9 is a continuous policy enforced from WS3 onwards.

| WS | Title | Owns | Status | Sprint plan |
|---|---|---|---|---|
| **WS1** | Architecture baseline | M9 | ✅ DONE (S-AI-WS1, `f453b89`) | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | M9 | ✅ DONE (S-AI-WS2, this PR) | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | M10 | 🔜 NEXT | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | M9 | 📋 NOT STARTED — blocked on WS3 | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) |
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

### Active milestone queue (next 3)

Per `docs/claude/milestone-state.md` "Queued milestones":

1. **M6 — Web app UI (dashboard repo)** — Vercel SPA wiring of mock-data feeds (equity chart, Active ICT Strategies, Trading Conditions) to live `/api/bot/*` data; positions and signals to follow.
2. **(M5 P4 closed 2026-05-10)** — bot #689 + dashboard `#12` shipped the backtest-history surface end-to-end.
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak (started 2026-05-10).

> **AI-traders queue note:** WS1 + WS2 closed 2026-05-10. **Next on
> the AI-traders track is WS3 (data foundation)** — dataset taxonomy,
> schema doc, first reproducible builder; doc-heavy + light scaffold,
> no live-runtime risk.

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
| S-047 | bybit_2 Spot Margin enablement (T1 routing + T2 sizing + T3 wiring + T4 VWAP monitor + T5 reconciler + T6 runbook + BUG-066 + T7 close) | ✅ Done 2026-05-10 (T6 PR #686, T7 this PR) | M3 (live-trading priority) |
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
| **S-CANON-1** | **Canonical-docs rebase + repo audit + stale owner-ref correction.** New canonical set: `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `docs/github-actions-workflows.md`. Removed 9 spurious tracked files at repo root from PR #658 (`<sqlite3.Connection object at 0x…>`). Updated `the-lizardking` → `benbaichmankass` in active code/docs/workflows; preserved historical sprint summaries unchanged. Logged in `docs/sprint-logs/S-CANON-1.md`. | ✅ Done 2026-05-10 (this PR) | Meta/docs |
| **S-CANON-FU-1** | **Mark legacy `docs/claude/workplan.md` and `docs/workplan.md` superseded by the S-CANON-1 canonical doc set.** Top-of-file banner on both; updates to `milestone-state.md`, `ci-status-checks.md`, `next-session-prompt.md`, `bug-log-pending/README.md` to cite the canonical rules doc instead of the workplan. Historical body text preserved intact. Logged in `docs/sprint-logs/S-CANON-FU-1-workplan-superseded.md`. | ✅ Done 2026-05-10 (this PR) | Meta/docs |
| **S-CANON-FU-2** | **Wire `closed_flat_invariant.check` into `run_monitor_tick`** behind `CLOSED_FLAT_INVARIANT_ENABLED` env (default false). Applies the documented 3-line patch from `docs/claude/closed-flat-invariant-phase2-wiring.md`; adds `tests/test_closed_flat_wiring_call_site.py` to pin the call-site behavior. Tier 2 — DRAFT pending operator ack; env stays unset on the VM until the operator flips it for the 7-day alert-only soak. Logged in `docs/sprint-logs/S-CANON-FU-2-cfi-wiring.md`. | 🔄 DRAFT 2026-05-10 (this PR, awaiting operator ack) | M3 |
| **S-CANON-FU-3** | **Enable branch-protection-sync on `main`.** Workflow file is correct and `REQUIRED_CONTEXTS=["pytest-collect","secret-scan","ruff-lint","dry-run-guard"]` matches the actual job IDs (verified). Operator-gated work remaining: create the fine-grained PAT, add `BRANCH_PROTECTION_TOKEN` secret, dispatch one run, open a trivial-doc test PR. Doc updates: stale owner-ref fix in `ci-status-checks.md` § Verify + new status subsection. Logged in `docs/sprint-logs/S-CANON-FU-3-branch-protection.md`. | 🔄 PARTIAL 2026-05-10 (this PR) — operator-gated steps pending | M4 |
| **S-AI-ROADMAP** | **AI traders models roadmap adopted.** New master plan at `docs/AI-TRADERS-ROADMAP.md` expands M9 + M10 into WS1–WS10. Seeds sprint-plan files for each workstream under `docs/sprint-plans/ai-traders/`. Doc-only; live runtime untouched. | ✅ Done 2026-05-10 (`#693` squash `1eb59f6`) | M9, M10 |
| **S-AI-WS1** | **AI traders WS1 — architecture baseline.** New canonical AI-scope doc at `docs/architecture/ai-model-platform.md` (current-state audit + target state + 5-layer model + stage map + Mermaid diagram + Architecture Change Log + Known Gaps). Linked from `ARCHITECTURE-CANONICAL.md`. WS1 sprint plan updated with sprint id and acceptance check-offs. Sprint id is `S-AI-WS1` (not S-067) because S-067 is in flight as the silent-empty audit (`docs/sprints/sprint-067-prompt.md`); themed id matches `S-CANON-*` / `S-AI-ROADMAP` precedent. Logged in `docs/sprint-logs/S-AI-WS1.md`. | ✅ Done 2026-05-10 (`#694` squash `f453b89`) | M9 |
| **S-AI-WS2** | **AI traders WS2 — canonical trade pipeline.** Stage names locked in `src/pipeline/types.py` (`StageName` enum, 10 stages). Frozen-dataclass `TradeCandidate`, `ExecutionIntent`, `StageDecision` with `__post_init__` invariant checks. `RejectionSource.DETERMINISTIC` (immutable) vs `RejectionSource.MODEL` (advisory) distinction. Per-stage I/O + owner files + logging spec at `docs/pipeline/stage-contracts.md`. Test coverage in `tests/pipeline/test_types.py`. AI-platform doc stage map + Known Gaps + Change Log refreshed. Additive; no live-runtime call site rewired — migration onto these types is filed as a Tier 2 follow-up. Logged in `docs/sprint-logs/S-AI-WS2.md`. | ✅ Done 2026-05-10 (this PR) | M9 |

> **Sprint number note:** S-036..S-040 burned per
> `docs/claude/workplan.md` § "Sprint and checkpoint numbering".
> S-049 ad-hoc fast-followup landed mid-S-047. S-050 VWAP Phase 2
> shipped early (PR #558, 2026-05-09). S-051..S-057 used by hardening
> work between 2026-05-08 and 2026-05-09. S-058..S-060 ship the
> spot-margin reconciler triad on 2026-05-09. S-067 is in flight as
> the silent-empty audit (`docs/sprints/sprint-067-prompt.md`); the
> AI traders track uses themed `S-AI-*` ids in parallel.

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

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`

AI-traders workstream sprint plans live under
`docs/sprint-plans/ai-traders/wsN-<slug>.md`. When a workstream is
scheduled as one or more concrete sprints, record the `S-<id>` mapping in
the workstream file. Themed ids (`S-AI-WSN`) parallel the numeric
sequence and are used when the next free numeric id is already taken.

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
