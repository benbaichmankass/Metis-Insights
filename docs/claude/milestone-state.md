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
| **Milestone** | M5 — Strategy testing workflow |
| **Title** | Strategy testing workflow: Telegram-triggered test flow, validation logging, backtest workflow docs |
| **Type** | roadmap (auto-claude) |
| **Goal** | Build the operator-triggered strategy validation workflow: a `/test <strategy_name>` Telegram command that writes a structured request to the repo, validation logging (signals + decisions + outcomes per workplan § Required logs), and a `docs/claude/backtest-workflow.md` runbook per workplan § Backtesting sessions. |
| **Status** | 📋 Not started — open next sprint (S-047) against M5 backlog. |
| **Active sprint** | None — next sprint to file is **S-047**. |
| **Active checkpoint** | None. |
| **Risk tier** | Tier 1 expected (tests, docs, bot command wiring). Tier 2 if validation logging touches the runtime pipeline. |
| **Definition of done** | `/test <strategy_name>` Telegram command lands a structured request to the repo, picked up by the next session; validation logging schema defined; `docs/claude/backtest-workflow.md` runbook filed; M5 formally closed with sprint summary + checkpoint. |

---

## M0..M10 status table

> Last verified: 2026-05-07 (intra-session handoff). "Verified" = on-disk artifacts checked
> before accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ✅ CLOSED | S-042 closed 2026-05-06. Pipeline audit passed; smoke-test ping dispatched; telegram-pings.md updated; tests extended. `ict-claude-bridge.service` confirmed active. |
| **M2** | Web app source of truth (backend) | 🔄 PARTIAL | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth) built in this repo. S-014 added `/api/bot/{stats,logs,positions,signals}` for the Vercel dashboard + CORS middleware keyed to `DASHBOARD_ORIGIN`. Dashboard reachability fix landed 2026-05-07 (Vercel rewrite proxies `/api/bot/*` to the bot, defeats HTTPS→HTTP mixed-content block). Backend side considered effectively complete; not formally closed under M0..M10 because formal close-out paperwork was never filed. |
| **M3** | Risk controls foundation | ✅ CLOSED | S-043 closed 2026-05-06. Order-layer refusal tests now complete (28 new gap-closer tests in `tests/test_s043_order_refusal_paths.py`). Risk engine + kill switch + risk caps + reason-token contract all pinned. |
| **M4** | Repo hygiene + CI | ✅ CLOSED | S-044 (CI suite) ✅; S-045 (conftest + pytest-collect blocking + ruff default) ✅; post-S-045 follow-up (auto-sync branch protection workflow) ✅; S-046 (2026-05-07) closed the three Janitor audits — 8 dead files removed, `src/ui/` shim consolidated into `src/units/ui/`, missing-test gap closed for `src/units/db/data_loader.py`. M4 formally closed. |
| **M5** | Strategy testing workflow | 📋 NOT STARTED | Telegram-triggered test flow, validation logging, backtest workflow docs not yet built. **Active milestone — S-047 will kick it off.** |
| **M6** | Web app UI | 🔄 IN PROGRESS (dashboard repo) | S-014 V1 React/Vite SPA shipped 2026-05-07 in `the-lizardking/ict-trader-dashboard`. S-015 V2 plan **scratched 2026-05-07** per operator. Connection bug fix landed (dashboard PR #2) the same day — Vercel rewrite proxies `/api/bot/*` to the bot VPS, dashboard now reaches the API and renders live status. **Next webapp sprint focus (file in `ict-trader-dashboard`, not here):** (1) replace mock data feeds with live `/api/bot/*` data — the equity chart, `Active ICT Strategies` list, `Trading Conditions` panel are all hard-coded; positions and signals endpoints exist but are not yet wired into the UI; (2) build out functionalities per workplan § "AI Trader Bot — Operator commands" — Forced Stop button, killswitch, close-all-positions, account live/dry-run toggle. Phase order is read-only data first, then interactive controls (workplan § "Dashboard build order"). |
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
| S-041 — workplan reconciliation sweep (M1 prep) | 2026-05-06 | `CP-2026-05-06-12-s041-complete` | `docs/sprint-summaries/sprint-041-summary.md` |
| M1 — Comms infrastructure (S-042) | 2026-05-06 | `CP-2026-05-06-14-s042-complete` | `docs/sprint-summaries/sprint-042-summary.md` |
| M3 — Risk controls foundation (S-043) | 2026-05-06 | `CP-2026-05-06-15-s043-complete` | `docs/sprint-summaries/sprint-043-summary.md` |
| S-044 — M4 CI suite | 2026-05-07 | `CP-2026-05-07-03-s044-complete` | `docs/sprint-summaries/sprint-044-summary.md` |
| S-045 — M4 step 2: conftest + pytest-collect-blocking + ruff default | 2026-05-07 | `CP-2026-05-07-05-s045-complete` | `docs/sprint-summaries/sprint-045-summary.md` |
| **M4 — Repo hygiene + CI (S-046)** | **2026-05-07** | `CP-2026-05-07-NN-s046-complete` | `docs/sprint-summaries/sprint-046-summary.md` |

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In workplan execution order. Each row lists the gating condition to start.

| Order | Milestone | Type | Gating condition |
|---|---|---|---|
| 1 | M5 — Strategy testing workflow | auto-claude | M4 closed ✅. Active — S-047 to file next. |
| 2 | M6 — Web app UI (dashboard repo) | auto-claude | Bot side endpoints + connectivity confirmed 2026-05-07. Next session opens against `the-lizardking/ict-trader-dashboard`, not this repo. Focus order: live data feed first, then operator-control functionalities. |
| 3 | M9 — AI / model roadmap | auto-claude | Independent of M5/M6. Could run in parallel. |
| 4 | M10 — HF / data pipeline | auto-claude | Independent of M5/M6. Could run in parallel. |

> M2 (Web app source of truth) — backend essentially done; formal close-out
> deferred. Not a blocker for M5 or M6.
>
> M7–M10 follow the workplan sequence.

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

> **Resolved 2026-05-07:** S-015 pause/continue Tier 2 hold — operator scratched
> S-015 entirely. M6 now proceeds in the dashboard repo per workplan boundary.

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
