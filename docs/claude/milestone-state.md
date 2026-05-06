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
| **Milestone** | M3 — Risk controls foundation |
| **Title** | Risk controls foundation: order-layer refusal tests + kill switch hardening |
| **Type** | roadmap (auto-claude) |
| **Goal** | Close the remaining gap in M3: order-layer refusal tests (partial per S-021); risk engine and kill switch already done. |
| **Status** | 📋 Queued — ready to start. No blockers. |
| **Active sprint** | None — open next sprint against M3 backlog. |
| **Active checkpoint** | None. |
| **Risk tier** | Tier 1 / Tier 2 (tests + risk-path changes; assess per PR) |
| **Definition of done** | Order-layer refusal tests complete; M3 formally closed with sprint summary + checkpoint. |

**After M3 closes:** next active milestone is **M4 — Repo hygiene + CI**.

**Operator hold (do NOT start M6 / S-015 until hold lifted):**
S-015 Web Client V2 pause/continue Tier 2 decision is pending — the sprint's scope
conflicts with the workplan repo boundary (web UI belongs in the separate Vercel repo).

---

## M0..M10 status table

> Last verified: 2026-05-06 (S-042 close). "Verified" = on-disk artifacts checked
> before accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ✅ CLOSED | S-042 closed 2026-05-06. Pipeline audit passed; smoke-test ping dispatched; telegram-pings.md updated; tests extended. `ict-claude-bridge.service` confirmed active. |
| **M2** | Web app source of truth (backend) | 🔄 PARTIAL | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth) built in this repo. Dashboard consumer side must be built in the separate dashboard repo (Vercel). Not formally closed under M0..M10. |
| **M3** | Risk controls foundation | 🔄 IN PROGRESS | S-010 per-account risk engine done; `/halt` kill switch live; hard risk caps in RiskManager; S-021 config-drift contract tests done. Order-layer refusal tests partial. Not formally closed under M0..M10. |
| **M4** | Repo hygiene + CI | 🔄 IN PROGRESS | S-003 test/CI done; S-035 architecture audit done; S-021 env contract tests done. Full Janitor audits, canonical path enforcement, complete GitHub Actions suite pending. Not formally closed. |
| **M5** | Strategy testing workflow | 📋 NOT STARTED | Telegram-triggered test flow, validation logging, backtest workflow docs not yet built. |
| **M6** | Web app UI | ⛔ BLOCKED | S-014 (Web Client V1) built UI in this repo; S-015 kickoff done 2026-05-06. Workplan boundary requires UI in separate Vercel dashboard repo. S-015 pause/continue under operator hold. |
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

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In workplan execution order. Each row lists the gating condition to start.

| Order | Milestone | Type | Gating condition |
|---|---|---|---|
| 1 | M3 — Risk controls foundation | auto-claude | M1 closed ✅. Ready to start. |
| 2 | M4 — Repo hygiene + CI | auto-claude | M1 closed ✅. Can overlap M3. |

> M2 (Web app source of truth) — backend done in this repo. Dashboard consumer needs
> a session in the dashboard repo. Not a blocker for M3/M4.
>
> M6 (Web app UI) — blocked pending S-015 operator hold resolution.
>
> M5 and M7–M10 follow the workplan sequence.

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
| S-015 pause/continue (Tier 2) | Operator | 2026-05-06 | Workplan boundary requires web UI in separate Vercel repo; pause/continue is an operator hold. Do not execute T1+ of S-015 until hold lifted. |
| BUG-057 diagnostic review | VM logs | 2026-05-06 | Diagnostic logging shipped PR #424. Awaiting next live VWAP rejection with `BUG-057-DIAG` log lines in `journalctl`. |

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
