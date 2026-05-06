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
| **Milestone ID** | M-S0 |
| **Title** | Workflow Foundation |
| **Type** | roadmap (Phase 0 — Foundation & Workflow) |
| **Goal** | Establish repo as the source of truth for Claude's operating workflow: master workplan, milestone-state, operating protocol, decomposition rules, and visible documentation index. |
| **Status** | 🔄 in progress |
| **Active sprint** | S0 — Workflow Foundation (this session) |
| **Active checkpoint** | `CP-2026-05-06-S0-01` — workflow foundation docs landed; next session picks up `CP-2026-05-06-S0-02` per the next-checkpoint pointer in `CHECKPOINT_LOG.md`. |
| **Risk tier** | Tier 1 (docs-only, no live-trading code touched). |
| **Definition of done** | Workflow source of truth in repo; milestones, session-sized sprints, and checkpoints explicitly defined; future sessions can resume from repo state without ambiguity; no live trading path changed. |

### Sprint backlog inside this milestone

| # | Sprint | Status |
|---|---|---|
| 1 | S0 — Workflow Foundation | 🔄 in progress |

(M-S0 is intentionally a single session-sized sprint; on close, it becomes the
foundation that every future milestone uses.)

---

## Recently closed milestones

> Maintained as a rolling window. Older entries are pruned to `ROADMAP.md` and
> `docs/sprint-summaries/`.

| Milestone | Closed | Final checkpoint | Summary doc |
|---|---|---|---|
| _(none yet — M-S0 is the first formally tracked milestone in this state file)_ | — | — | — |

Pre-existing roadmap progress (S-000 through S-013) is captured in `ROADMAP.md`
and is **not** retro-actively migrated into this file. From M-S0 forward, every
closed milestone gets a row here.

---

## Queued milestones

In execution order. Each row lists the gating condition that must be true
before the milestone can start.

| Order | Milestone | Type | Gating condition |
|---|---|---|---|
| 1 | S-014 — Web Client V1 (Home Dashboard) | roadmap | M-S0 closed; S-013 backend on `main` (already merged). |
| 2 | S-015 — Web Client V2 (Component Tabs) | roadmap | S-014 merged; backend extended for component data. |
| 3 | S-016 — Secure API Key Management | roadmap | S-015 merged; encrypted vault design approved (Tier 2 ping). |

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
