---
name: delegate-work
description: How to DELEGATE and PARALLELIZE a big-scope or long-running task across sub-agents and sub-sessions so it runs correctly and efficiently instead of as one slow serial slog. Pull this the moment a task is large (many files / multiple subsystems / all three repos / a broad sweep, audit, migration, or refactor) or a session is long-running — BEFORE diving in head-first. Covers when to delegate vs do it inline, how to decompose into independent units, the three parallelization modes (in-context parallel tool calls · background Agent fan-out with structured findings · operator-spawned parallel sessions), the single-writer consolidation rule, the sub-session spawn-prompt template, and running efficiently. Composes with session-coordination (the merge serialization + board) and full-system-audit (its Phase 2 IS this skill). NOT for small sequential tasks — delegation overhead isn't worth it there.
---

# delegate-work — decompose, delegate, and run a big task efficiently

Pull this skill **before** starting a task that is big-scope (many files,
several subsystems, all three repos, a broad sweep / audit / migration /
refactor) or expected to be long-running. The failure mode it prevents: grinding
a large job **serially in one context** until you run out of room — slow,
expensive, and prone to dying mid-way with no resumable record. The fix is to
**decompose → delegate → serialize the merges**, deliberately, from the start.

## Step 1 — Decide: delegate, or do it inline?

Delegate when the work **decomposes into independent units** (slices that don't
need each other's intermediate state) AND there's enough of it to amortize the
overhead. Do it inline when it's small, inherently sequential, or a single-file
change — delegation overhead (spawning, consolidating, coordinating) isn't free.

Often the right move is **hybrid**: scout inline first to *discover the
work-list* (list the files, find the endpoint families, scope the diff), then
delegate the pipeline over that list. You don't need to know the shape before
the *task* — only before the *delegation step*.

## Step 2 — Decompose into independent units

Carve the scope into units that can run **without sharing live state**:
directory ranges, endpoint families, per-repo slices, per-symbol/strategy, per
subsystem. Name them (the audit convention: `S-AUDIT-A`, `S-AUDIT-B`, …). Write
the unit list into a **durable shared plan** (a findings doc, a ROADMAP
breakdown, the session board) so the work survives a context window dying — a
fresh session resumes from the repo, not from your memory.

## Step 3 — Pick the parallelization mode (cheapest that fits)

| Mode | Use when | Mechanism |
|---|---|---|
| **A. In-context parallel tool calls** | independent reads/searches/greps you need *now* | issue them in ONE message (multiple tool_use blocks) so they run concurrently — never serially. The default for "I need to look at N independent things." |
| **B. Background Agent fan-out** | a read-heavy sweep too big for your context (audit, broad search, "read every file under X") | spawn `Agent` sub-agents over the unit slices, each returning **structured findings** (give it a schema/shape to return, not a file dump). You keep the conclusions, not the raw bytes. |
| **C. Operator-spawned parallel sessions** | write-heavy workstreams that each need their own context + their own PR(s) | hand each a self-contained spawn prompt (template below); they coordinate through the session board. |

Modes compose: scout inline (hybrid) → fan out reads with B → consolidate →
spin C sessions for the write-heavy workstreams.

**Mode B sub-agents that WRITE still need board coordination — you post it, not
them.** Mode B is framed above as read-heavy, but a Mode-B sub-agent can end up
committing, pushing, dispatching a live-VM/trainer-VM action, or opening a PR
(e.g. running a full sub-review that drains a backlog and commits the result).
A spawned `Agent` sub-agent has **no session identity of its own** to post to
the live coordination board (issue #6927) with — it is invisible to every other
concurrent session unless YOU, the orchestrator, post the `▶️ START` covering
its full scope **before** launching it. (Found the hard way 2026-07-22: a
`/system-review` session fanned out three sub-review sub-agents — one of which
dispatched trainer-VM diag requests — without ever checking or posting to
#6927, while a real concurrent session was mid-trainer-VM-work at the same
time. Pure luck, not process, is why nothing collided.) See
`docs/CLAUDE-RULES-CANONICAL.md` § "Multi-session coordination" step 0.

## Step 4 — Single-writer consolidation (the rule that prevents churn)

When multiple agents/sessions feed one result, **one writer (you, the lead)
makes the edits and PRs.** Parallel writers to the same shared append-files (the
session board, a findings doc, the review backlogs, a roadmap row) collide and
churn. So: agents *return findings*; the lead *writes*. One workstream = one
session = its own focused PR(s); never one cross-workstream PR.

## Step 5 — Serialize the merges (compose `session-coordination`)

Parallel work, **serial merges.** This is owned by **`session-coordination`** +
`docs/claude/session-board.json` — invoke it. The essentials: claim the single
`merge_slot` before merging, sync your branch to `main` **last** (so it's not
`behind` → branch-protection require-up-to-date → everyone re-runs CI), merge on
green, release the slot. **No cron / no polling loop to force merges** — merge
deliberately. (Racing concurrent merges off the same base is the exact churn
this prevents.)

## The sub-session spawn-prompt template (Mode C)

Give a spawned session everything it needs to run correctly *alone*:

> You own **\<unit id> — \<one-line scope>**. Files/area: \<exact paths>.
> START by reading `docs/CLAUDE-RULES-CANONICAL.md` + root `CLAUDE.md` + the
> SKILL.md of the skill covering this work + the shared plan (\<findings doc /
> roadmap row>) + `docs/claude/session-board.json`. Register in the board's
> `active_sessions`. Do the work; **raise findings by tier** — Tier-3
> (strategy/risk/sizing/order-path/account-mode/live-promotion) is
> propose-and-operator-approve, never self-merge; Tier-1 you ship. Coordinate
> merges via `session-coordination` (claim the slot, sync to `main` last).
> Append your coverage/findings to the shared plan as you go. On exit: write a
> sprint log (`sprint-format`), prune your board entry, leave no loose PR/issue.

## Run efficiently (the habits)

- **Batch independent calls** — many tool_use blocks in one message, not a
  serial chain (Mode A is the default, not the exception).
- **Return structure, not dumps** — a sub-agent's value is its conclusion +
  evidence, give it the shape to return so you don't re-read its raw output.
- **Don't re-do delegated work** — once you've fanned out a search, wait for the
  result; don't also run it yourself.
- **Durable plan over memory** — the shared plan (findings doc / board /
  roadmap) is what lets a dead or compacted context resume. Update it as units
  complete, not at the end.
- **Report coverage honestly** — each unit logs what it did AND did not reach;
  silent partial coverage reads as "all done" when it isn't.

## When NOT to delegate

Small, sequential, single-file, or tightly-coupled work where each step needs
the previous step's result. A trivial fix delegated is slower than done. The
test is Step 1: *does it decompose into independent units, and is there enough
of it?* If no → inline.

## Composes with

- **`session-coordination`** — Step 5 merge serialization + the session board
  (the "how concurrent sessions don't collide" half; this skill is the "how to
  split and run the work" half).
- **`full-system-audit`** — its Phase 2 (multi-session delegation) IS this skill
  applied to the audit program.
- **`sprint-format`** — each spawned session's exit record.
- **`doc-freshness`** — each session's session-end decision-landing.
- **`session-handoff`** — the time-axis counterpart: this skill decomposes a
  big task **up front** across **parallel** sessions/agents; `session-handoff`
  is for when a **single serial thread** organically grows too long and needs
  to close out + continue in a **fresh session later**, not in parallel.
