# Milestone & session state

> **Purpose:** single quick-glance answer to "where is the program right now?"
> for future Claude sessions. Read this **after** `checkpoints/CHECKPOINT_LOG.md`
> (which tells you where to resume tactically) but **before** opening any
> sprint plan.
>
> **Update rule:** the closing checkpoint of every sprint updates this file.
> If the file is stale, the resuming session should refresh it before doing
> any other work.

---

## How to read this file

1. **Active milestone** — the one milestone currently being worked. Only one
   at a time. Includes its sprint backlog, the active sprint, and the active
   checkpoint pointer.
2. **Recently closed milestones** — last three closed milestones with their
   final summary checkpoint IDs. Older history lives in `ROADMAP.md` and the
   sprint summaries directory.
3. **Queued milestones** — what's lined up next, in order, with the gating
   condition for each.
4. **Standing / recurring sessions** — auto-task milestones that run on a
   cadence and don't appear in the linear queue.
5. **Open blockers** — anything the operator owes the program (a key, a
   merge, a decision). Empty when the program is unblocked.

When opening a session:

- If the **Active milestone** points at a sprint with an open checkpoint,
  resume that checkpoint per `checkpoint-workflow.md`.
- If the **Active milestone** has no open sprint, start the next sprint in
  its backlog.
- If a **Blocker** is listed, follow the ping-PR pattern in
  `telegram-pings.md` rather than working around it.

---

## Active milestone

| Field | Value |
|---|---|
| **Milestone ID** | M-S-015 |
| **Title** | Web Client V2 (Component Tabs) |
| **Type** | roadmap (Phase 4 — Secure Web Dashboard) |
| **Goal** | Extend the S-014 home dashboard with operator-iterable tabs: Strategies, Accounts, Model Metrics, Runtime Logs & Bugs. Backend extends with the per-tab data endpoints; web client adds tab navigation + per-tab fragments. Live trader uptime preserved end-to-end. |
| **Status** | 🔄 Active (kickoff PR in flight) |
| **Active sprint** | **S-015 T0 (kickoff)** — `docs/sprints/sprint-015-prompt.md` rewritten 2026-05-06 to match the M-S-015 scope and the eight-section template in `docs/claude/sprint-planning.md`. Next code work is T1 (M0 PR #1 — Tab nav scaffold). |
| **Active checkpoint** | `CP-2026-05-06-S-015-01` — S-015 kickoff. |
| **Risk tier** | Tier 2 mostly (web client + new API surfaces, JWT-protected backend); Tier 1 for docs/static. No live-trading code path touched. |
| **Definition of done** | Each of the four tabs (Strategies, Accounts, Model Metrics, Runtime Logs & Bugs) renders with live data from the backend; navigation between tabs preserves auth state; loopback-only hosting; live trader uptime preserved. See `docs/sprints/sprint-015-prompt.md`. |

### Sprint backlog inside this milestone

Per `docs/sprints/sprint-015-prompt.md` § 4 (one row per checkpoint):

| #   | Checkpoint                              | Risk class | Wall-clock |
|-----|-----------------------------------------|------------|------------|
| T0  | Kickoff (this session)                  | docs-only  | ≤ 30 min   |
| T1  | M0 PR #1 — Tab nav scaffold             | infra      | ≤ 60 min   |
| T2  | M1 PR #1 — Strategies tab               | infra      | ≤ 90 min   |
| T3  | M2 PR #1 — Accounts tab                 | infra      | ≤ 90 min   |
| T4  | M3 PR #1 — Model Metrics tab            | infra      | ≤ 90 min   |
| T5  | M4 PR #1 — Runtime Logs & Bugs tab      | infra      | ≤ 90 min   |
| T5b | Mid-sprint checkpoint (after T3)        | docs-only  | ≤ 15 min   |
| T6  | M5 PR #1 — Sprint close                 | docs-only  | ≤ 60 min   |

All PRs self-merge after CI green (no PM-review gate this sprint —
the security-critical client behaviour from S-014 M2 already lives on
`main`, and S-015 only adds new endpoints behind the same auth gate).

---

## Recently closed milestones

> Maintained as a rolling window. Older entries are pruned to `ROADMAP.md` and
> `docs/sprint-summaries/`.

| Milestone | Closed | Final checkpoint | Summary doc |
|---|---|---|---|
| M-S-014 — Web Client V1 (Home Dashboard) | 2026-05-06 | `CP-2026-05-06-S-014-COMPLETE` | `docs/sprint-summaries/sprint-014-summary.md` |
| M-S0 — Workflow Foundation | 2026-05-06 | `CP-2026-05-06-S0-02` | `docs/sprint-summaries/sprint-S0-summary.md` |

Pre-existing roadmap progress (S-000 through S-013) is captured in `ROADMAP.md`
and is **not** retro-actively migrated into this file. From M-S0 forward, every
closed milestone gets a row here.

---

## Queued milestones

In execution order. Each row lists the gating condition that must be true
before the milestone can start.

| Order | Milestone | Type | Gating condition |
|---|---|---|---|
| 1 | S-014.5 — Web Client public exposure (reverse proxy + TLS + CSP) | roadmap | S-014 merged (✅) + loopback dashboard validated by operator (smoke test in `docs/audit/sprint-013-deployment-runbook.md` § "S-014 web client smoke test"). |
| 2 | S-016 — Secure API Key Management | roadmap | S-015 merged; encrypted vault design approved (Tier 2 ping). |
| 3 | _next strategy/model improvement sprint per recurring cadence_ | auto-task | runs on its own trigger. |

(See `ROADMAP.md` for the full backlog and phase grouping. This table holds
only the next 1–3 milestones so it stays readable.)

---

## Standing / recurring sessions

These are **auto-task milestones**. They run on a cadence and produce their own
checkpoints, but they do not move the linear roadmap forward — they keep the
system healthy.

| Cadence | Session | Prompt |
|---|---|---|
| Bi-daily | Hardening & Stability Audit | `docs/sprints/recurring-hardening-prompt.md` |
| Weekly | Strategy Improvement Review | `docs/sprints/recurring-strategy-improvement-prompt.md` |
| Weekly (HF cron) | Model Training & Evaluation | `docs/sprints/recurring-model-training-prompt.md` |

Full spec: `docs/claude/recurring-sessions.md`.

---

## Open blockers

| Blocker | Owner | Opened | Link | Resolution path |
|---|---|---|---|---|
| _(none)_ | — | — | — | — |

When a session is blocked waiting on the operator, append a row here **and**
follow the ping-PR pattern in `docs/claude/telegram-pings.md` (commit
`[BLOCKED-PM] <question>`, open a draft PR titled `BLOCKED: <question>`, open
a tiny ping-PR on `claude/ping-<slug>` and self-merge that to fire Telegram).

---

## Update protocol

The closing checkpoint of every sprint must:

1. Refresh **Active milestone** (status, active sprint, active checkpoint).
2. If the milestone closed, move it from **Active** to **Recently closed
   milestones** and bring the next queued milestone into **Active**.
3. Refresh the **Queued milestones** rolling window (1–3 ahead).
4. Add or remove **Open blockers** rows as state changes.
5. Commit this file alongside the `CHECKPOINT_LOG.md` append in the same PR
   so the program's state moves atomically.

If a session discovers this file is out of date relative to
`CHECKPOINT_LOG.md`, the first action of the session is to reconcile the two.
