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
| **Milestone** | S-047 T3 — Bybit Spot Margin live-order routing wire-up |
| **Title** | feat(exec): route spot-margin orders via isLeverage=1 + feat(coordinator): direction-aware balance for spot-margin accounts |
| **Type** | ad-hoc (live-trading). Tier 2/3. |
| **Goal** | Land the two co-dependent diffs that close S-047. D4 routes spot-margin orders via `isLeverage=1`; D5 makes the coordinator return direction-aware available balance for spot-margin accounts. The two diffs are incoherent independently — must land together. |
| **Status** | 🔄 ACTIVE — opens after S-048 close. Tier 2/3 — will pause at the operator-merge gate (PR opens as draft, requires explicit approval). |
| **Active sprint** | **S-047 T3.** Plan: `docs/sprint-plans/S-047-bybit2-spot-margin.md` § T3. |
| **Active checkpoint** | T3 D4 — `feat(exec): route spot-margin orders via isLeverage=1`. |
| **Risk tier** | Tier 2 / Tier 3 (live order path). Operator approval required before merge. |
| **Definition of done (S-047 T3)** | D4 + D5 land together; tests green; smoke-test plan executed; sprint summary at `docs/sprint-summaries/sprint-047-summary.md`; bybit_2 spot-margin ready for first live VWAP signal. |

**Operator action remaining when S-047 T3 lands a PR: merge / hold gate** (Tier 2/3 — see operating-protocol § 4). PR opens as draft per § 4.4.

**M1 status update (S-048 closed 2026-05-07):** audit verdict = 🔄 PARTIAL. No P0 surfaced. Seven P1 follow-ups + one P2 cluster filed in `docs/audits/M1-comms-audit-followups.md` to be re-queued after S-047 T3 closes. M1 stays open until at least the relocation, merge-review-buttons, recovery-alert, and unification sprints land. Per § 8 hand-off: next sprint after S-048 close = S-047 T3 (default; no P0 override).

---

## M0..M10 status table

> Last verified: 2026-05-07 (workplan-status-review session — operator-directed M1 reopen,
> S-047 T2 merge confirmation, S-015 scratch + M6 status update). "Verified" = on-disk
> artifacts checked before accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | 🔄 PARTIAL (S-048 audited 2026-05-07) | S-048 audited the on-disk telegram-bot implementation against the new workplan and produced `docs/audits/M1-comms-audit-2026-05-07.md` + `docs/audits/M1-comms-audit-followups.md`. Verdict: **🔄 PARTIAL**, no P0. Operator-control trio (`/halt`, `/closeall`, `/toggle`) and 7 of 8 information menus on `@bict_trading_bot` are present and tested. S-027's repo-driven request/response system is on disk and tested — but installed on the **trader bot** (`telegram_query_bot.py:2955`) instead of `@claude_ict_comms_bot` per the workplan. Seven P1 follow-ups filed: relocate comms to ClaudeBot; merge/hold inline buttons; stuck-request recovery alerts; auto-hourly snapshot timer; `/new-session` + `/test` operator commands; unify `pending-pings.jsonl` and `comms/requests/`; correct S-042 doc drift. M1 stays open until the relocation + merge-review + recovery-alert + unification sprints land. |
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
| ~~M1 — Comms infrastructure (S-042)~~ | ~~2026-05-06~~ | ~~`CP-2026-05-06-14-s042-complete`~~ | ~~`docs/sprint-summaries/sprint-042-summary.md`~~ — **REOPENED 2026-05-07; S-048 audit produced PARTIAL verdict; M1 stays open behind P1 follow-ups (see M0..M10 table).** |
| M3 — Risk controls foundation (S-043) | 2026-05-06 | `CP-2026-05-06-15-s043-complete` | `docs/sprint-summaries/sprint-043-summary.md` |
| S-044 — M4 CI suite | 2026-05-07 | `CP-2026-05-07-03-s044-complete` | `docs/sprint-summaries/sprint-044-summary.md` |
| S-045 — M4 step 2: conftest + pytest-collect-blocking + ruff default | 2026-05-07 | `CP-2026-05-07-05-s045-complete` | `docs/sprint-summaries/sprint-045-summary.md` |
| **M4 — Repo hygiene + CI (S-046)** | **2026-05-07** | `CP-2026-05-07-NN-s046-complete` | `docs/sprint-summaries/sprint-046-summary.md` |
| **S-048 — M1 comms audit (deep dive vs new workplan)** | **2026-05-07** | `CP-2026-05-07-NN-s048-complete` | `docs/sprint-summaries/sprint-048-summary.md` (Tier 1 self-merge; M1 stays open per audit verdict — see M0..M10 row) |

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In execution order. Each row lists the gating condition to start. **S-048 closed 2026-05-07 with PARTIAL verdict; no P0 surfaced; default hand-off → S-047 T3 per sprint-048-prompt § 8.**

| Order | Milestone / sprint | Type | Gating condition |
|---|---|---|---|
| 1 | **S-047 T3 close** (close S-047 cleanly: D4 + D5 + sprint-summary) | ad-hoc (live-trading) | None — ready to start. Plan: `docs/sprint-plans/S-047-bybit2-spot-margin.md` § T3. |
| 2 | **M1 comms followups** — relocate comms to ClaudeBot, merge-review buttons, recovery alerts, auto-hourly timer, `/new-session` + `/test`, surface unification, S-042 doc correction | roadmap | S-047 T3 closes. Highest-priority entry from `docs/audits/M1-comms-audit-followups.md` runs first. M5 inherits the `/test <strategy>` deliverable from this queue. |
| 3 | M5 — Strategy testing workflow | auto-claude | M1 `/test <strategy>` follow-up closes (M5 dispatch surface lives there). |
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
