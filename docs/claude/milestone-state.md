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
| **Milestone** | M1 — Comms infrastructure (REOPENED 2026-05-07; deep audit required against new workplan) |
| **Title** | M1 comms infrastructure deep audit (telegram-bot vs new workplan) |
| **Type** | roadmap (auto-claude). M1 reopen. |
| **Goal** | Produce a structured gap-list comparing on-disk telegram-bot implementation against `docs/claude/workplan.md` § "Telegram bots" + § "Required logs" + § "Repeatable operator-triggered workflows". Output is a prioritized backlog of follow-up sprints, not a code change. |
| **Status** | 🔄 ACTIVE — next session opens **S-048** per `docs/sprints/sprint-048-prompt.md`. Tier 1 docs-only audit. **S-047 T3 is queued behind S-048 close** (operator directive 2026-05-07: comms cleanup runs first). |
| **Active sprint** | **S-047 T4 — VWAP monitor close logic** (queued behind T3 close 2026-05-07). Plan: `docs/sprint-plans/S-047-bybit2-spot-margin.md` § T4. |
| **Active checkpoint** | T4 — `feat(vwap): close on TP/SL/VWAP-cross instead of only break-even-SL`. T3 (D4 + D5 spot-margin exec+coordinator wiring) merged 2026-05-07 (PR #464). |
| **Risk tier (T4)** | Tier 3 (strategy logic). Draft work-PR + ping-PR + operator merge gate. |
| **Definition of done (T4)** | D6 merged. `tests/units/strategies/test_vwap_monitor_close.py` covers TP-cross, SL-cross, VWAP-cross, time-decay, no-action paths. Operator review required. § 4.4 5-bullet compliance check passes. |

**Operator action remaining for S-047 (overall): none — toggle on, T1+T2+T3 all merged.** The remaining checkpoints (T4 / T5 / T6 / T7) are autonomous Claude work; each opens its own draft work-PR and pauses at the operator-merge gate per Tier 3 protocol. M1 audit (S-048) deferred — S-047 finishes its remaining checkpoints first.

---

## M0..M10 status table

> Last verified: 2026-05-07 (workplan-status-review session — operator-directed M1 reopen,
> S-047 T2 merge confirmation, S-015 scratch + M6 status update). "Verified" = on-disk
> artifacts checked before accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ⚠️ REOPENED 2026-05-07 — workflow drift (active milestone) | S-042 closed M1 on 2026-05-06 against the pre-reconciliation workplan; the new canonical workplan was adopted later that same day via S-041 (workplan-reconciliation sweep). Per workplan § "Verify-before-trusting-done", the on-disk telegram-bot implementation has **not** been audited against the new workplan § "Telegram bots" spec (two-bot model: AI Trader Bot operator commands + info menus; ClaudeBot repo-driven comms loop with merge-review buttons / sprint pings / writeback / recovery). Operator directive 2026-05-07: M1 needs a deep audit covering the entire telegram-bot functionality vs. the new workplan. **S-048 is the active sprint** — see `docs/sprints/sprint-048-prompt.md`. |
| **M2** | Web app source of truth (backend) | 🔄 PARTIAL | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth). S-014 added `/api/bot/{stats,logs,positions,signals}` for the Vercel dashboard + CORS middleware keyed to `DASHBOARD_ORIGIN`. Dashboard reachability fix landed 2026-05-07 (Vercel rewrite proxies `/api/bot/*` to the bot, defeats HTTPS→HTTP mixed-content block). Backend side considered effectively complete; not formally closed under M0..M10 because the close-out paperwork was never filed. |
| **M3** | Risk controls foundation | ✅ CLOSED | S-043 closed 2026-05-06. Order-layer refusal tests now complete (28 new gap-closer tests in `tests/test_s043_order_refusal_paths.py`). Risk engine + kill switch + risk caps + reason-token contract all pinned. |
| **M4** | Repo hygiene + CI | ✅ CLOSED | S-044 (CI suite) ✅; S-045 (conftest + pytest-collect blocking + ruff default) ✅; post-S-045 follow-up (auto-sync branch protection workflow) ✅; S-046 (2026-05-07) closed the three Janitor audits — 8 dead files removed, `src/ui/` shim consolidated into `src/units/ui/`, missing-test gap closed for `src/units/db/data_loader.py`. M4 formally closed. |
| **M5** | Strategy testing workflow | 📋 NOT STARTED (paused) | Telegram-triggered test flow, validation logging, backtest workflow docs not yet built. **Paused** behind S-048 (M1 audit) → S-047 T3 (close) → then resumes. |
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

In execution order. Each row lists the gating condition to start. **Operator directive 2026-05-07: M1 comms cleanup (S-048) runs before S-047 T3.**

| Order | Milestone / sprint | Type | Gating condition |
|---|---|---|---|
| 1 | **S-047 T4** — VWAP monitor close logic (D6) | ad-hoc (live-trading) | None — T3 merged 2026-05-07. Ready to start. |
| 2 | **S-047 T5** — reconciler spot-margin awareness (D7) | ad-hoc (live-trading) | T4 closes. |
| 3 | **S-047 T6** — end-to-end live smoke + runbook (D8) | ad-hoc (live-trading) | T5 closes. |
| 4 | **S-047 T7** — sprint close (milestone-state, ROADMAP, summary) | ad-hoc (docs-only) | T6 closes. |
| 5 | **S-048 — M1 comms audit (telegram-bot deep dive)** | auto-claude (M1 reopen) | S-047 T7 closes. Prompt: `docs/sprints/sprint-048-prompt.md`. |
| 6 | M5 — Strategy testing workflow | auto-claude | S-048 closes (M1 audit may surface M5 dependencies). |
| 7 | M6 — Web app UI (dashboard repo) | auto-claude | Independent — opened against `the-lizardking/ict-trader-dashboard`, not this repo. Focus order: live data feed first, then operator-control functionalities. |
| 8 | M9 — AI / model roadmap | auto-claude | Independent of M5/M6. Could run in parallel. |
| 9 | M10 — HF / data pipeline | auto-claude | Independent of M5/M6. Could run in parallel. |

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
> - **S-047 T2 (PR #459) operator-merge gate** — operator merged PR #459 at 2026-05-07 13:28 UTC. T3 unblocked; queued behind S-048 per operator directive. CHECKPOINT_LOG.md CP-12 entry's reference to PR #459 as DRAFT is now stale (operator merged it 6 minutes after CP-12 landed); the next checkpoint entry will reflect the merged state correctly.

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
