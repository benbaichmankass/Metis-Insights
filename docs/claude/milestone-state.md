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
| **Milestone** | M4 — Repo hygiene + CI |
| **Title** | Repo hygiene + CI: Janitor audits, canonical path enforcement, GitHub Actions suite |
| **Type** | roadmap (auto-claude) |
| **Goal** | Complete the M4 gaps: full Janitor audits, canonical path enforcement across all units, complete GitHub Actions suite. S-003 / S-021 / S-035 already covered foundational pieces. **S-044 (2026-05-07) shipped the GitHub Actions CI suite — pytest-collect, secret-scan, repo-inventory, ruff-lint workflows + ci-status-checks.md runbook.** |
| **Status** | 🔄 In progress — CI suite shipped (S-044); Janitor audits + canonical-path enforcement remaining. |
| **Active sprint** | None — open next sprint (S-045 Janitor candidate) against M4 backlog. |
| **Active checkpoint** | None. |
| **Risk tier** | Tier 1 (tests, lint, CI, docs; no live-trading logic). |
| **Definition of done** | Janitor audits + canonical-path enforcement + GitHub Actions suite complete; M4 formally closed with sprint summary + checkpoint. **CI suite ✅ done in S-044; Janitor + canonical-path open.** |

**After M4 closes:** next active milestone is **M5 — Strategy testing workflow**.

**Operator hold (do NOT start M6 / S-015 until hold lifted):**
S-015 Web Client V2 pause/continue Tier 2 decision is pending — the sprint's scope
conflicts with the workplan repo boundary (web UI belongs in the separate Vercel repo).

---

## M0..M10 status table

> Last verified: 2026-05-07 (S-044 close). "Verified" = on-disk artifacts checked
> before accepting any prior "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ✅ CLOSED | S-042 closed 2026-05-06. Pipeline audit passed; smoke-test ping dispatched; telegram-pings.md updated; tests extended. `ict-claude-bridge.service` confirmed active. |
| **M2** | Web app source of truth (backend) | 🔄 PARTIAL | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth) built in this repo. Dashboard consumer side must be built in the separate dashboard repo (Vercel). Not formally closed under M0..M10. |
| **M3** | Risk controls foundation | ✅ CLOSED | S-043 closed 2026-05-06. Order-layer refusal tests now complete (28 new gap-closer tests in `tests/test_s043_order_refusal_paths.py`). Risk engine + kill switch + risk caps + reason-token contract all pinned. |
| **M4** | Repo hygiene + CI | 🔄 IN PROGRESS | S-003 test/CI done; S-035 architecture audit done; S-021 env contract tests done. **S-044 (2026-05-07) shipped the GitHub Actions CI suite — pytest-collect, secret-scan, repo-inventory, ruff-lint + ci-status-checks.md runbook.** Janitor audits + canonical-path enforcement still pending. Not formally closed. |
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
| M3 — Risk controls foundation (S-043) | 2026-05-06 | `CP-2026-05-06-15-s043-complete` | `docs/sprint-summaries/sprint-043-summary.md` |

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In workplan execution order. Each row lists the gating condition to start.

| Order | Milestone | Type | Gating condition |
|---|---|---|---|
| 1 | M4 — Repo hygiene + CI | auto-claude | M3 closed ✅. Ready to start. |
| 2 | M5 — Strategy testing workflow | auto-claude | M3 closed ✅. Can overlap M4. |

> M2 (Web app source of truth) — backend done in this repo. Dashboard consumer needs
> a session in the dashboard repo. Not a blocker for M4/M5.
>
> M6 (Web app UI) — blocked pending S-015 operator hold resolution.
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
