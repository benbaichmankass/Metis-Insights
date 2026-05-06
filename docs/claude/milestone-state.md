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
| **Milestone ID** | M-S-014 |
| **Title** | Web Client V1 (Home Dashboard) |
| **Type** | roadmap (Phase 4 — Secure Web Dashboard) |
| **Goal** | Land a read-only home dashboard the operator can open from the `/webapp` Telegram link: login → home view showing per-account live/dry status, overall + per-account P&L, active strategies, uptime, git SHA, and a 7-day equity sparkline. Live trader uptime preserved end-to-end. |
| **Status** | 🔄 in progress |
| **Active sprint** | S-014 — Web Client V1 (Home Dashboard) — **resumed** 2026-05-06 after a 6-day pause for hardening + BUG-056. |
| **Active checkpoint** | M0 + M1 + M3 PR #1 + M3 PR #2 already shipped on 2026-04-30 (PRs #183, #192, #193, #195, #196 — see `CP-2026-04-30-09`). **Remaining**: M3 PR #3 (equity sparkline — autonomous Tier 2), M2 PR #1 + M2 PR #2 (login flow — PM-review-gated), M4 PR #1 (sprint close). M3 PR #3 is the next concrete deliverable — it depends only on the already-shipped `/api/pnl/history` endpoint. |
| **Risk tier** | Tier 2 mostly (web client + JWT-protected backend); Tier 1 for docs/static. No live-trading code path touched. M2 PRs are PM-review per the sprint prompt. |
| **Definition of done** | Operator can open `/webapp` link, log in, and see the home dashboard rendering live data from S-013 backend + `/api/pnl/history` endpoint (status panel + P&L panel + equity sparkline + logout). Loopback-only hosting (public exposure deferred to S-014.5). See `docs/sprints/sprint-014-prompt.md` § Definition of Done. |

### Sprint backlog inside this milestone

| # | PR group | Status |
|---|---|---|
| M0 PR #1 | `GET /api/pnl/history` (#183) | ✅ merged 2026-04-30 |
| M1 PR #1 | Frontend scaffold (templates + vendored JS) (#192) | ✅ merged 2026-04-30 |
| M1 PR #2 | FastAPI mounts + UI router (#193) | ✅ merged 2026-04-30 |
| M2 PR #1 | Login form wired (PM REVIEW) | ⏳ pending |
| M2 PR #2 | Auth-aware HTMX requests (PM REVIEW) | ⏳ pending |
| M3 PR #1 | `/ui/fragments/status` (#195) | ✅ merged 2026-04-30 |
| M3 PR #2 | `/ui/fragments/pnl` (#196) | ✅ merged 2026-04-30 |
| M3 PR #3 | Equity sparkline JS (autonomous Tier 2) | 🔄 in progress 2026-05-06 |
| M4 PR #1 | Sprint summary + ROADMAP + close checkpoint | ⏳ pending |

---

## Recently closed milestones

> Maintained as a rolling window. Older entries are pruned to `ROADMAP.md` and
> `docs/sprint-summaries/`.

| Milestone | Closed | Final checkpoint | Summary doc |
|---|---|---|---|
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
| 1 | S-015 — Web Client V2 (Component Tabs) | roadmap | S-014 merged; backend extended for component data. |
| 2 | S-016 — Secure API Key Management | roadmap | S-015 merged; encrypted vault design approved (Tier 2 ping). |
| 3 | S-014.5 — Web Client public exposure (reverse proxy + TLS) | roadmap | S-014 merged + loopback dashboard validated by operator. |

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
