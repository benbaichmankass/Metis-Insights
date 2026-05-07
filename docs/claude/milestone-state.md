# Milestone & session state

> **Purpose:** single quick-glance answer to "where is the program right now?"
> for future Claude sessions. Read this **after** `checkpoints/CHECKPOINT_LOG.md`
> (which tells you where to resume tactically) but **before** opening any sprint plan.
>
> **Authority:** `docs/claude/workplan.md` is the decider. This file tracks execution
> state against the workplan's M0..M10 roadmap. When this file conflicts with the
> workplan, the workplan wins.
>
> **Update rule:** the closing checkpoint of every sprint updates this file.
> If the file is stale, the resuming session should refresh it before doing
> any other work.

---

## How to read this file

1. **Active milestone** — the one milestone currently being worked.
2. **M0..M10 status table** — on-disk-verified status for every milestone.
3. **Recently closed milestones** — last three closed milestones.
4. **Queued milestones** — what's lined up next in workplan order.
5. **Standing / recurring sessions** — auto-task milestones on a cadence.
6. **Open blockers** — anything the operator owes the program.

When opening a session:

- If the **Active milestone** points at a sprint with an open checkpoint, resume
  that checkpoint per `checkpoint-workflow.md`.
- If the **Active milestone** has no open sprint, start the next sprint in its backlog.
- If a **Blocker** is listed, follow the ping-PR pattern in `telegram-pings.md`.

---

## Active milestone

| Field | Value |
|---|---|
| **Milestone** | M5 — Strategy testing workflow (paused while S-047 runs; M1 audit queued behind S-047 close) |
| **Title** | Strategy testing workflow: Telegram-triggered test flow, validation logging, backtest workflow docs |
| **Type** | roadmap (auto-claude) |
| **Goal** | Build the operator-triggered strategy validation workflow: a `/test <strategy_name>` Telegram command that writes a structured request to the repo, validation logging (signals + decisions + outcomes per workplan § Required logs), and a `docs/claude/backtest-workflow.md` runbook per workplan § Backtesting sessions. |
| **Status** | 📋 Not started — paused while **S-047** (live-trading priority) finishes, then **S-048 / M1 deep audit** runs (M1 reopened — see M0..M10 table). M5 resumes after both. |
| **Active sprint** | **S-047** (ad-hoc, live-trading priority) — bybit_2 Spot Margin enablement so VWAP can take true longs + shorts against USDT collateral. Plan: `docs/sprint-plans/S-047-bybit2-spot-margin.md`. |
| **Active checkpoint** | ~~T0~~ DELETED in PR #455 (margin-agnostic correction). ~~T1~~ shipped 2026-05-07 (PR #456 operator-merged). ~~T2~~ shipped 2026-05-07 (**PR #459 operator-merged 2026-05-07 13:28 UTC** — `feat(risk): spot-margin sizing — collateral, liquidation, borrow fees`). **T3 ready to start** — `feat(exec): route spot-margin orders via isLeverage=1` + `feat(coordinator): direction-aware balance for spot-margin accounts` (D4 + D5 land together — one diff is incoherent without the other). Operator-cleared 2026-05-07 to be filed in its own dedicated session per operating-protocol § 2.2 "one task per session". |
| **Risk tier (S-047 average)** | Tier 2 / Tier 3 — touches strategy sizing, live order routing, reconciler. |
| **Definition of done (S-047)** | A sell-side VWAP signal on `bybit_2` opens a true short via `category=spot, isLeverage=1`; risk manager sizes from USDT collateral with liquidation/borrow-fee parameters; `vwap.py::monitor()` closes on TP/SL/VWAP-cross; reconciler agrees with the trade journal at the end of each cycle. |

**S-047 operator action remaining: none.**
The system operates margin-agnostic. The operator clicks Enable Spot Margin in the Bybit web UI on their own schedule; until then, every `isLeverage=1` order returns retCode 110007 server-side and is logged via the existing `report_api_failure` path. After the click, orders flow through. No notebook, no parameter capture, no PR thread.

---

## M0..M10 status table

> Last verified: 2026-05-07 (workplan-status-review session — operator-directed M1 reopen,
> S-047 T2 merge confirmation, S-015 scratch + M6 status update). "Verified" = on-disk
> artifacts checked before accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ⚠️ REOPENED 2026-05-07 — workflow drift | S-042 closed M1 on 2026-05-06 against the pre-reconciliation workplan; the new canonical workplan was adopted later that same day via S-041 (workplan-reconciliation sweep). Per workplan § "Verify-before-trusting-done", the on-disk telegram-bot implementation has **not** been audited against the new workplan § "Telegram bots" spec (two-bot model: AI Trader Bot operator commands + info menus; ClaudeBot repo-driven comms loop with merge-review buttons / sprint pings / writeback / recovery). Operator directive 2026-05-07: M1 needs a deep audit covering the entire telegram-bot functionality vs. the new workplan. **Comms-cleanup audit sprint S-048 is the next session after S-047 closes.** |
| **M2** | Web app source of truth (backend) | 🔄 PARTIAL | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth). S-014 added `/api/bot/{stats,logs,positions,signals}` for the Vercel dashboard + CORS middleware keyed to `DASHBOARD_ORIGIN`. Dashboard reachability fix landed 2026-05-07 (Vercel rewrite proxies `/api/bot/*` to the bot, defeats HTTPS→HTTP mixed-content block). Backend side considered effectively complete; not formally closed under M0..M10 because the close-out paperwork was never filed. |
| **M3** | Risk controls foundation | ✅ CLOSED | S-043 closed 2026-05-06. Order-layer refusal tests now complete (28 new gap-closer tests in `tests/test_s043_order_refusal_paths.py`). Risk engine + kill switch + risk caps + reason-token contract all pinned. |
| **M4** | Repo hygiene + CI | ✅ CLOSED | S-044 (CI suite) ✅; S-045 (conftest + pytest-collect blocking + ruff default) ✅; post-S-045 follow-up (auto-sync branch protection workflow) ✅; S-046 (2026-05-07) closed the three Janitor audits — 8 dead files removed, `src/ui/` shim consolidated into `src/units/ui/`, missing-test gap closed for `src/units/db/data_loader.py`. M4 formally closed. |
| **M5** | Strategy testing workflow | 📋 NOT STARTED (paused) | Telegram-triggered test flow, validation logging, backtest workflow docs not yet built. **Paused** behind S-047 close + S-048 (M1 audit). Resumes after both. |
| **M6** | Web app UI | 🔄 IN PROGRESS (dashboard repo) | S-014 V1 React/Vite SPA shipped 2026-05-07 in `the-lizardking/ict-trader-dashboard`. **S-015 V2 plan scratched 2026-05-07** per operator. Dashboard connection fix (Vercel rewrite of `/api/bot/*` to bot VPS) landed the same day; dashboard now renders live status. **Next webapp sprint focus (file in `ict-trader-dashboard`, not here):** (1) replace mock data feeds with live `/api/bot/*` data — equity chart, `Active ICT Strategies` list, `Trading Conditions` panel are hard-coded; positions and signals endpoints exist but are not yet wired into the UI; (2) operator functionalities per workplan § "AI Trader Bot — Operator commands" — Forced Stop button, killswitch, close-all-positions, account live/dry-run toggle. Phase order: read-only data first, then interactive controls (workplan § "Dashboard build order"). |
| **M7** | Strategy review gate | 📋 NOT STARTED | |
| **M8** | Strategy tuning | 📋 NOT STARTED | |
| **M9** | AI / model roadmap | 📋 NOT STARTED | S-005 (model monitor) and S-006 (model registry) built under old framing; formal M9 not started. |
| **M10** | HF / data pipeline | 📋 NOT STARTED | S-004 (training pipeline) built under old framing; formal M10 not started. |

---

## Recently closed milestones

> Rolling window. Older entries pruned to `ROADMAP.md` and `docs/sprint-summaries/`.

| Milestone | Closed | Final checkpoint | Summary doc |
|---|---|---|---|
| M0 — Workflow Foundation (≈ S0) | 2026-05-06 | `CP-2026-05-06-S0-02` | `docs/sprint-summaries/sprint-S0-summary.md` |
| S-041 — workplan reconciliation sweep | 2026-05-06 | `CP-2026-05-06-12-s041-complete` | `docs/sprint-summaries/sprint-041-summary.md` |
| ~~M1 — Comms infrastructure (S-042)~~ | ~~2026-05-06~~ | ~~`CP-2026-05-06-14-s042-complete`~~ | ~~`docs/sprint-summaries/sprint-042-summary.md`~~ — **REOPENED 2026-05-07** (audit pending against new workplan; see M0..M10 table) |
| M3 — Risk controls foundation (S-043) | 2026-05-06 | `CP-2026-05-06-15-s043-complete` | `docs/sprint-summaries/sprint-043-summary.md` |
| S-044 — M4 CI suite | 2026-05-07 | `CP-2026-05-07-03-s044-complete` | `docs/sprint-summaries/sprint-044-summary.md` |
| S-045 — M4 step 2: conftest + pytest-collect-blocking + ruff default | 2026-05-07 | `CP-2026-05-07-05-s045-complete` | `docs/sprint-summaries/sprint-045-summary.md` |
| **M4 — Repo hygiene + CI (S-046)** | **2026-05-07** | `CP-2026-05-07-NN-s046-complete` | `docs/sprint-summaries/sprint-046-summary.md` |

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In workplan execution order. Each row lists the gating condition to start.

| Order | Milestone / sprint | Type | Gating condition |
|---|---|---|---|
| 1 | **S-047 T3 close** (close S-047 cleanly: D4 + D5 + sprint-summary) | ad-hoc (live-trading) | PR #459 merged ✅. Ready — file T3 in its own session per operating-protocol § 2.2. |
| 2 | **S-048 — M1 comms audit (telegram-bot deep dive)** | auto-claude (M1 reopen) | S-047 closes. Prompt: `docs/sprints/sprint-048-prompt.md`. |
| 3 | M5 — Strategy testing workflow | auto-claude | S-048 closes (M1 audit may produce dependencies M5 inherits). |
| 4 | M6 — Web app UI (dashboard repo) | auto-claude | Independent — opened against `the-lizardking/ict-trader-dashboard`, not this repo. Focus order: live data feed first, then operator-control functionalities. |
| 5 | M9 — AI / model roadmap | auto-claude | Independent of M5/M6. Could run in parallel. |
| 6 | M10 — HF / data pipeline | auto-claude | Independent of M5/M6. Could run in parallel. |

> M2 (Web app source of truth) — backend essentially done; formal close-out
> deferred. Not a blocker for any queued milestone.
>
> M7–M10 follow the workplan sequence after M5.

---

## Standing / recurring sessions

| Cadence | Session | Prompt |
|---|---|---|
| Bi-daily | Hardening & Stability Audit | `docs/sprints/recurring-hardening-prompt.md` |
| Weekly | Strategy Improvement Review | `docs/sprints/recurring-strategy-improvement-prompt.md` |
| Weekly (HF cron) | Model Training & Evaluation | `docs/sprints/recurring-model-training-prompt.md` |

Full spec: `docs/claude/recurring-sessions.md`.

---

## Open blockers

| Blocker | Owner | Opened | Notes |
|---|---|---|---|
| BUG-057 diagnostic review | VM logs | 2026-05-06 | Diagnostic logging shipped PR #424. Awaiting next live VWAP rejection with `BUG-057-DIAG` log lines in `journalctl`. |

> **Resolved 2026-05-07:**
> - **S-015 pause/continue Tier 2 hold** — operator scratched S-015 entirely. M6 now proceeds in the dashboard repo per workplan boundary.
> - **S-047 T2 (PR #459) operator-merge gate** — operator merged PR #459 at 2026-05-07 13:28 UTC. T3 unblocked and ready to file in its own session. CHECKPOINT_LOG.md CP-12 entry's reference to PR #459 as DRAFT is now stale (operator merged it 6 minutes after CP-12 landed); the next checkpoint entry will reflect the merged state correctly.

---

## Update protocol

The closing checkpoint of every sprint must:

1. Refresh **Active milestone** (status, active sprint, active checkpoint).
2. If the milestone closed, move it to **Recently closed milestones** and advance
   the next queued milestone into **Active**.
3. Update **M0..M10 status table** for any changed milestones.
4. Refresh the **Queued milestones** rolling window (1–3 ahead).
5. Add or remove **Open blockers** rows as state changes.
6. Commit this file alongside the `CHECKPOINT_LOG.md` append in the same PR so
   the program's state moves atomically.

If a session discovers this file is out of date relative to `CHECKPOINT_LOG.md`,
the first action of the session is to reconcile the two.
