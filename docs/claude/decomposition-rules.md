# Milestone → sprint → checkpoint decomposition rules

> **Purpose:** the normative contract for how work is decomposed in this
> repo. Every roadmap item, every ad-hoc request, and every recurring
> auto-task lands as a milestone, breaks into session-sized sprints, and
> each sprint breaks into checkpoints. This file defines the shape and
> the boundaries.
>
> **Authority:** if a sprint prompt or operator request can't be expressed
> in this shape, the prompt is wrong, not the rules. Revise the prompt
> first.

---

## 1. The three layers

```
Milestone   ── one goal, one DoD, owns 1..N sprints
   └── Sprint   ── one Claude session, one PR-sized stack, owns 1..N checkpoints
          └── Checkpoint   ── one resumable step, ends with a handoff entry
```

Each layer has a single owner, a single artifact, and a single closing
ceremony. They do not overlap.

| Layer | Owner artifact | Closing ceremony |
|---|---|---|
| Milestone | Roadmap row + Definition of Done in `ROADMAP.md` | Sprint summary doc under `docs/sprint-summaries/` + milestone-state.md update |
| Sprint | Sprint prompt in `docs/sprints/sprint-NNN-prompt.md` | Final checkpoint with `COMPLETE` / `WRAPPED` in title |
| Checkpoint | Handoff entry in `CHECKPOINT_LOG.md` | Append handoff + session-complete Telegram ping |

---

## 2. Milestones

### 2.1 What a milestone is

A milestone is a coherent body of work that:

- Has a **single goal** expressible in one sentence.
- Has a **single Definition of Done** (a checklist a person can verify after
  the last sprint merges).
- Has a **single risk tier** (the highest tier of any sprint inside it).
- Lives as exactly one row in `ROADMAP.md` and exactly one block in
  `docs/claude/milestone-state.md` while active.

### 2.2 Milestone types

Three types, defined in `docs/workplan.md`:

| Type | Trigger | Sprint count | Where it appears |
|---|---|---|---|
| **Roadmap** | Planned by PM + Tech Lead | 1..N | `ROADMAP.md` phase tables |
| **Ad-hoc** | Operator request, urgent bug, incident | 1..3 typically | Inserted at top of milestone-state.md, retroactively logged in `ROADMAP.md` if it was non-trivial |
| **Auto-task** | Recurring auto-trigger (cron, Telegram command) | 1 (the recurring sprint) | `docs/claude/recurring-sessions.md` |

All three types use the same milestone → sprint → checkpoint shape. They
differ only in **how they enter the queue**, not in how they execute.

### 2.3 Milestone sizing

A milestone is too big if:

- Its sprint backlog has > ~10 sprints (split it into sub-milestones).
- Its goal sentence needs an "and" (split into two milestones).
- Its DoD references hardware / external dependencies that aren't yet
  resolved (split off the dependency-resolution work as its own milestone).

A milestone is too small if:

- It would be a single PR with no sprint structure (just file the PR; don't
  invent a milestone for it).
- Its DoD is "fix bug X" with no broader context (use a one-sprint ad-hoc
  milestone instead, or fold it into the next active milestone if related).

### 2.4 Milestone closure

Closing a milestone requires:

1. Every sprint inside it has its summary doc under
   `docs/sprint-summaries/sprint-NNN-summary.md`.
2. The milestone row in `ROADMAP.md` flips to ✅ Done.
3. `docs/claude/milestone-state.md` moves the milestone from **Active** to
   **Recently closed milestones** and pulls the next queued milestone into
   **Active**.
4. The closing checkpoint references the milestone by ID and notes
   `MILESTONE COMPLETE: M-NNN` in its title so the Telegram ping fires
   with high priority.

---

## 3. Session-sized sprints

### 3.1 What a sprint is

A sprint is a **single Claude session** worth of work. Its prompt lives at
`docs/sprints/sprint-NNN-prompt.md` and must satisfy
`docs/claude/sprint-planning.md` before work starts.

### 3.2 Sprint sizing rules

| Rule | Why |
|---|---|
| ≤ 1 Claude session of wall-clock work | Anything longer can't be planned at the checkpoint level. |
| ≤ 5 PRs landing in the sprint | More means the sprint is actually a milestone. |
| Each PR is risk-tier-uniform | Mixed-risk PRs split (per `sprint-planning.md` § 5). |
| One unit-boundary declaration | Sprint prompt declares which units it touches (per `CLAUDE.md` § *Architecture rules*). |

### 3.3 Mandatory sprint-prompt sections

Per `docs/claude/sprint-planning.md`:

1. Goal (one paragraph).
2. Dependencies.
3. Deliverables.
4. Checkpoints (table).
4b. Unit boundary declaration.
5. Risk class & merge model.
6. Success criteria.
7. Hard guardrails.
8. Hand-off.

A sprint that ships without one of these sections is a process bug — file it
in `docs/claude/bug-log.md` and revise the prompt before opening any PR.

### 3.4 Sprint closure

Closing a sprint requires the seven-step Sprint Completion Checklist in
`CLAUDE.md` § *Sprint Completion Checklist*:

1. Run full tests.
2. Run secret scan.
3. Create summary PR under `docs/sprint-summaries/sprint-NNN-summary.md`.
4. Self-merge summary PR (docs-only).
5. Propose 1–2 `CLAUDE.md` improvements for the next sprint.
6. Telegram `/sprintlet_complete S-NNN`.
7. Append final checkpoint to `CHECKPOINT_LOG.md`.

If the sprint is the **last sprint of a milestone**, the closure also
performs the milestone-closure steps in § 2.4.

---

## 4. Checkpoints

### 4.1 What a checkpoint is

A checkpoint is the **smallest resumable unit** of work. It ends with a
handoff entry in `CHECKPOINT_LOG.md`; it does **not** need to end with a
merged PR. The next session reads the latest entry and resumes from there.

### 4.2 Checkpoint ID convention

`CP-<sprint-date>-<NN>` — e.g. `CP-2026-04-28-03`. Increment monotonically
within the sprint. Never reuse an ID.

For sprints whose ID is `S-NNN` rather than a date, prefer the form
`CP-YYYY-MM-DD-S-NNN-NN` so the date sorts and the sprint context is
visible.

### 4.3 What a checkpoint must contain

Per `HANDOFF_TEMPLATE.md`, exactly five fields:

1. Completed.
2. Files changed.
3. Tests run.
4. Remaining.
5. Next checkpoint.

Plus the metadata header (session date, sprint, current sprint phase, last
completed checkpoint, next checkpoint, Telegram sent, alerts, blockers).

### 4.4 Checkpoint sizing rules

| Rule | Why |
|---|---|
| Atomically resumable | Next session reads the entry and knows exactly what to do without context. |
| Tests passing or skipped with explanation | Half-broken checkpoints poison the next session. |
| Working tree committed or cleanly stashable | No half-edited files dangling. |
| One concern per checkpoint | Don't bundle a fix and a refactor — they want different reviews. |

### 4.5 Partial checkpoints

If limits hit mid-checkpoint:

- **Stop at the first safe sub-state** (tests passing on the partial diff,
  files saved, branch pushable).
- Write the handoff with **exact partial step described** in the entry.
- The next session does **not** restart the checkpoint — it continues from
  the partial step.

### 4.6 Checkpoint anti-patterns

- ❌ Re-reading the full sprint plan and restarting from the top.
- ❌ Bundling two milestones into one PR because "they're related".
- ❌ Skipping the handoff entry because "the diff speaks for itself".
- ❌ Pasting Telegram tokens into the log to make notifications work.
- ❌ Blocking on a human merge before the next checkpoint.

---

## 5. The decomposition flowchart

```
operator request / roadmap row / cron trigger
                 │
                 ▼
   ┌──── is the work > 1 PR? ───── no ──▶ ship as a single PR; no milestone
   │ yes
   ▼
   draft milestone:
     - one-sentence goal
     - DoD checklist
     - risk tier
     - rough sprint count (1..N)
                 │
                 ▼
   ┌──── does it fit in one session? ──── yes ──▶ one sprint inside the milestone
   │ no
   ▼
   split into N session-sized sprints; order them
   in milestone-state.md queue or ROADMAP.md table
                 │
                 ▼
   for each sprint:
     write docs/sprints/sprint-NNN-prompt.md per sprint-planning.md template
                 │
                 ▼
   for each sprint, when scheduled:
     break into checkpoints; each checkpoint atomically resumable
                 │
                 ▼
   execute checkpoint → handoff → next checkpoint → ... → final checkpoint
                 │
                 ▼
   sprint summary doc → milestone-state.md update → next sprint (or milestone close)
```

---

## 6. Worked example: M-S0 (this milestone)

To make the rules concrete, here is how M-S0 (Workflow Foundation) maps:

| Layer | Artifact |
|---|---|
| **Milestone** | M-S0 — Workflow Foundation. Goal: "establish repo as the source of truth for Claude's operating workflow." DoD: workflow source of truth in repo; milestone/sprint/checkpoint definitions explicit; future sessions can resume from repo state; no live trading path changed. Risk tier: Tier 1. Sprint count: 1. |
| **Sprint** | S0 — Workflow Foundation (this session). |
| **Checkpoints** | `CP-2026-05-06-S0-01` — workflow foundation docs landed (this session's deliverable). Future session will pick up `CP-2026-05-06-S0-02` if any follow-up is needed; otherwise the milestone closes here. |

This is the smallest possible milestone (1 sprint, 1 checkpoint). It exists
to prove the decomposition contract by **using** it on the very session
that creates it.

---

## 7. Cross-references

- `docs/workplan.md` — master workplan; defines the three milestone types.
- `docs/claude/milestone-state.md` — current active milestone + queue.
- `docs/claude/operating-protocol.md` — session shape, merge tiers.
- `docs/claude/sprint-planning.md` — binding sprint-prompt template.
- `docs/claude/checkpoint-workflow.md` — resume + end-of-session handoff.
- `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` — checkpoint format.
- `ROADMAP.md` — roadmap milestones table.
- `docs/claude/recurring-sessions.md` — auto-task milestones spec.
