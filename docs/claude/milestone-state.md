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
| **Milestone** | S-047 — bybit_2 Spot Margin enablement (live-trading priority sprint) |
| **Title** | S-047 — VWAP true longs + shorts on bybit_2 via Bybit V5 Spot Margin |
| **Type** | ad-hoc (live-trading priority); interleaves M1 audit and M5 per `operating-protocol.md` § 3. |
| **Goal** | Per `docs/sprint-plans/S-047-bybit2-spot-margin.md` § 1 — VWAP takes both long + short BTCUSDT positions on `bybit_2` via Bybit V5 Spot Margin, with the RiskManager + dispatch + monitor + reconciler all treating Spot-Margin sells as borrowed-coin shorts. |
| **Status** | 🔄 ACTIVE — **T1 / T2 / T3 / T4 / T5 shipped + S-049 fast-followup shipped**; **T6 queued**. Operator merged PR #477 (T5) on 2026-05-07 ~17:23 UTC. |
| **Active sprint** | **S-047 T6 — end-to-end live smoke + runbook.** Plan row: `docs/sprint-plans/S-047-bybit2-spot-margin.md` § T6 + D8. |
| **Active checkpoint** | T6 — `docs(bug-log + runbook): spot-margin remediation cross-references` + live smoke (0.0005 BTC short on `bybit_2` mainnet through full open → monitor → close cycle; trade journal + reconciler agree). |
| **Risk tier (T6)** | Tier 1 (docs after smoke succeeds). Smoke harness `scripts/sprint047/spot_margin_smoke.py` already exists from T3. |
| **Definition of done (T6)** | D8 merged: bug-log close entries link BUG-046/048/049 to S-047 as the structural fix; `docs/runbooks/spot-margin.md` written; live smoke recorded with PnL/borrow-fee analysis; CI green. |

**S-048 (M1 comms audit) status:** ✅ CLOSED (fresh re-issue) on `claude/update-roadmap-status-ZnLM9` — see `docs/audits/M1-comms-audit-2026-05-07-fresh.md`.

**M1 P1-A..D follow-ups status:** ✅ CLOSED 2026-05-08 on `claude/review-roadmap-hIO75`. P1-A (workplan correction) was already landed pre-branch on `update-roadmap-status-ZnLM9`; P1-D (`/new_session` + `/test`), P1-B (stuck-request recovery alerts), P1-C (auto-hourly snapshot timer) all shipped here. P2 hygiene cluster remains filed for a future Janitor sprint per `docs/audits/M1-comms-audit-followups-fresh.md`. **M1 → ✅ CLOSED.**

---

## M0..M10 status table

> Last verified: 2026-05-08 (S-048 fresh re-issue session — operator-directed
> M1 audit redo with corrections baked in; P1 follow-ups landing same-session
> per operator directive). "Verified" = on-disk artifacts checked before
> accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ✅ CLOSED | S-048 fresh audit closed on `claude/update-roadmap-status-ZnLM9`. Audit verdict: PARTIAL, no P0. P1-A (workplan correction) landed there same-session. P1-B (stuck-request recovery alerts), P1-C (auto-hourly snapshot timer), P1-D (`/new_session` + `/test` commands) closed 2026-05-08 on `claude/review-roadmap-hIO75`. P2 hygiene cluster filed for a future Janitor sprint per `docs/audits/M1-comms-audit-followups-fresh.md`. |
| **M2** | Web app source of truth (backend) | ✅ CLOSED | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth). S-014 added `/api/bot/{stats,logs,positions,signals}` for the Vercel dashboard + CORS middleware keyed to `DASHBOARD_ORIGIN`. Dashboard reachability fix landed 2026-05-07 (Vercel rewrite proxies `/api/bot/*` to the bot, defeats HTTPS→HTTP mixed-content block). M2 formally closed 2026-05-08 alongside the M1 P1-A..D follow-ups — backend work was already shipped, this close-out is paperwork-only (no new code). The diagnostic surface (`/api/diag/*`) is a separate workstream and stays out of M2 scope. |
| **M3** | Risk controls foundation | ✅ CLOSED | S-043 closed 2026-05-06. Order-layer refusal tests now complete (28 new gap-closer tests in `tests/test_s043_order_refusal_paths.py`). Risk engine + kill switch + risk caps + reason-token contract all pinned. |
| **M4** | Repo hygiene + CI | ✅ CLOSED | S-044 (CI suite) ✅; S-045 (conftest + pytest-collect blocking + ruff default) ✅; post-S-045 follow-up (auto-sync branch protection workflow) ✅; S-046 (2026-05-07) closed the three Janitor audits. M4 formally closed. |
| **M5** | Strategy testing workflow | 📋 NOT STARTED (paused) | Telegram-triggered test flow, validation logging, backtest workflow docs not yet built. **Paused** behind S-047 T6. The bot-side dispatch surface for `/test <strategy>` is now in place via M1 P1-D — M5 only needs to wire the artifact consumer. |
| **M6** | Web app UI | 🔄 IN PROGRESS (dashboard repo) | S-014 V1 React/Vite SPA shipped 2026-05-07 in `the-lizardking/ict-trader-dashboard`. **S-015 V2 plan scratched 2026-05-07** per operator. Dashboard connection fix (Vercel rewrite of `/api/bot/*` to bot VPS) landed the same day. **In active session 2026-05-08** on dashboard branch `claude/update-roadmap-status-ZnLM9` — wiring mock-data feeds (equity chart, Active ICT Strategies, Trading Conditions) to live `/api/bot/*` data; positions and signals to follow. |
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
| ~~M1 — Comms infrastructure (S-042)~~ | ~~2026-05-06~~ | ~~`CP-2026-05-06-14-s042-complete`~~ | ~~`docs/sprint-summaries/sprint-042-summary.md`~~ — **REOPENED 2026-05-07; audited fresh 2026-05-08 → 🔄 PARTIAL via `CP-2026-05-07-17-s048-fresh-m1-audit`** |
| M3 — Risk controls foundation (S-043) | 2026-05-06 | `CP-2026-05-06-15-s043-complete` | `docs/sprint-summaries/sprint-043-summary.md` |
| **M4 — Repo hygiene + CI (S-046)** | **2026-05-07** | `CP-2026-05-07-NN-s046-complete` | `docs/sprint-summaries/sprint-046-summary.md` |
| **S-048 — M1 comms audit (fresh re-issue)** | **2026-05-08** | `CP-2026-05-07-17-s048-fresh-m1-audit` | `docs/sprint-summaries/sprint-048-summary.md` |
| **M1 P1-A..D follow-ups + M2 close-out** | **2026-05-08** | (this PR's checkpoint) | `docs/sprint-summaries/m1-p1-followups-and-m2-close-summary.md` |
| **M2 — Web app source of truth (backend)** | **2026-05-08** | (this PR's checkpoint) | (paperwork-only close — work already shipped under S-013 + S-014) |
| **2026-05-08 all-models training run + S-050 (VWAP Phase 2)** | **2026-05-09** | `CP-2026-05-09-01-all-models-training` | `experiments/2026-05-08-all-models-training/RECOMMENDATIONS.md` (PR #558 squashed as `9a7bdf3`) |

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In execution order. Each row lists the gating condition to start.

| Order | Milestone / sprint | Type | Gating condition |
|---|---|---|---|
| 1 | **S-047 T6 — end-to-end live smoke + runbook** (D8) | ad-hoc (live-trading) | None — ready to start. Live smoke needs Bybit web-UI Spot Margin toggle ON for `bybit_2`. |
| 2 | **S-047 T7 — sprint close** (milestone-state + bug-log + summary) | docs-only (Tier 1) | T6 closes. |
| 3 | M5 — Strategy testing workflow | auto-claude | S-047 closes. `/test <strategy>` bot-side dispatch surface now in place via M1 P1-D; M5 wires the artifact consumer. |
| 4 | M6 — Web app UI (dashboard repo) | auto-claude | **In active session 2026-05-08** in `the-lizardking/ict-trader-dashboard`. |
| 5 | M9 — AI / model roadmap | auto-claude | Independent of M5/M6. Could run in parallel. |
| 6 | M10 — HF / data pipeline | auto-claude | Independent of M5/M6. Could run in parallel. |
| 7 | **S-050-followup — Phase-3 HTF reference 4h → 1h EMA-200** (Tier 2, PM-review) | strategy-improvement | ≥ 30 days of Phase-2 live metrics on the HTF gate (S-050 shipped 2026-05-09). Expected +0.4 Sharpe lift on top of Phase-2 per V3 in `experiments/2026-05-08-all-models-training/`. |

> M2 (Web app source of truth) — closed 2026-05-08 (paperwork-only;
> backend had already shipped under S-013 + S-014). Not a blocker for
> any queued milestone.
>
> S-050 (VWAP Phase 2 HTF gate) — shipped early on 2026-05-09 via PR
> #558 after the 2026-05-08 all-models training run showed the
> 38-month baseline was structurally unprofitable (Sharpe -0.39).
> The originally-gated "≥ 30 days live metrics" condition was
> waived by operator decision — Phase-2 was no longer a quality
> lift but the difference between profitable and not. The 30-day
> gate now applies to the Phase-3 follow-up instead (HTF reference
> 4h → 1h EMA-200).

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

> **Resolved 2026-05-07/08:**
> - **S-047 T1..T5 + S-049 fast-followup** all operator-merged 2026-05-07.
> - **PR #463 (stale S-048 audit) + PR #467 (contradictory S-047 T3 close-checkpoint)** — both closed 2026-05-07/08 in favour of the fresh S-048 re-issue on `claude/update-roadmap-status-ZnLM9`.

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
