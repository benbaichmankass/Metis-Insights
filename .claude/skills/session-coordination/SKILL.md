---
name: session-coordination
description: >
  Binding cross-session workflow governance — the session preflight (read the
  rules + know your tool/capability limits), the MANDATORY live coordination
  board (GitHub issue #6927 — post updates + questions, NOT gated on merging),
  and the multi-session MERGE PROTOCOL that serializes PRs so concurrent sessions
  don't race a merge and force each other into behind-rebase retest churn. Use at
  the START of every session and BEFORE every merge. Owns docs/claude/session-board.json
  + docs/claude/coordination-board.md. Composes with git-actions (dispatch),
  doc-freshness (session end), full-system-audit, and delegate-work (the "how to
  split + run a big task" half — this skill is the "how concurrent sessions don't
  collide" half).
---

# session-coordination — preflight + the multi-session merge protocol

This skill exists because (a) sessions repeatedly act/commit before reading the
canonical rules or knowing what tools they actually have, and (b) two sessions
merging at the same time keep forcing each other "behind" `main` → branch
protection require-up-to-date → a full re-run of CI on every PR (observed twice
on 2026-06-28). It is **binding**: a session that skips it is the failure mode.

This skill owns **two** coordination surfaces, and both are mandatory:

- **The live coordination board — GitHub issue [#6927](https://github.com/benbaichmankass/ict-trading-bot/issues/6927)**
  ("🤖 Claude Coordination Board"). Live comms **not gated on merging** — post
  work-start / questions / answers / done, read at session start. Because it's an
  issue, every session sees a comment the instant it's posted (a committed file
  only propagates through a merge + pull — too late to prevent a collision). Full
  protocol: **`docs/claude/coordination-board.md`**.
- **The merge queue — `docs/claude/session-board.json`.** Honour-system
  last-writer-wins, not a hard lock; the hard safety net is GitHub
  branch-protection (require-up-to-date). Its job is to serialize the *one* merge
  slot + mirror session intent durably.

The board makes concurrent work *visible in real time*; the JSON serializes the
merge and records intent durably. Use both.

## 1. Session-start preflight (before your FIRST substantive tool call)

The `SessionStart` hook (`.claude/settings.json`) already emits the read-first
contract + generation discipline. This skill adds the two missing halves:

1. **Read-first (non-negotiable, hook clause 1).** Read in full:
   `docs/CLAUDE-RULES-CANONICAL.md`, root `CLAUDE.md`, and the `SKILL.md` of the
   skill covering your task. A context-compaction RESUME is a new session — it
   does NOT waive this.
2. **Know your capabilities BEFORE you reach for a tool you don't have.** On
   Claude Code on the web / PM-side sessions (see `CLAUDE.md` § "PM-side session
   capabilities"): `run_workflow` 403s — drive workflows via **labelled issues**
   (the diag/system-action relays). Direct VM egress is usually firewalled —
   live-VM reads go through the **`vm-diag-snapshot` relay** (`/api/diag/*` only)
   or `trainer-vm-diag`. The hosted GitHub MCP **drops intermittently** — retry
   with backoff (2s/4s/8s/16s), never treat the first failure as an expired token
   or hand off to the operator. There is **no `create_label`** — labels come from
   `bootstrap-labels.yml`.
3. **READ the live coordination board FIRST, then POST your START.** This is the
   mandatory live-comms step (`docs/claude/coordination-board.md`). Before your
   first substantive change: (a) `issue_read method=get_comments` on **#6927** to
   see what every other live session is touching + answer any open question you
   can; (b) post a `▶️ START` comment (session id, branch, **which files /
   subsystems / PRs you're about to touch**, one-line intent). The board is not
   gated on merging, so this is the claim that actually reaches other live
   sessions in time to prevent a collision. Post `❓ QUESTION` comments whenever
   your work might overlap another's, answer questions you can, and post `✅ DONE`
   when you wrap.
4. **Register on the merge queue.** Add yourself to `active_sessions` in
   `docs/claude/session-board.json` (session_id, branch, one-line intent) and read
   the existing entries + `merge_slot` so you know who else is live. (This is the
   durable record; the board in step 3 is the live signal — do both.)
5. **Claim your work BEFORE starting it — the anti-collision step.** When you're
   one of several sessions in a multi-session program (e.g. an M17 `S-AUDIT-*`
   sprint, an M14 ML sprint), FIRST check both the **live open-PR list**
   (`list_pull_requests state=open`) and the program's sprint table in
   `ROADMAP.md`. **If another session already has an open PR (or a pushed
   `claude/*` branch) for the same sprint/workstream, do NOT start a parallel
   one** — pick an unclaimed sprint, or narrow yours to a non-overlapping slice
   and say so in your PR. Two sessions landing in the same workstream blind to
   each other (2026-06-28: two sessions both re-did Workstream-B → duplicate
   env-gate + `NEWS_VETO` PRs #4939/#4941, one closed) is the failure this step
   exists to stop. The open-PR list is the authoritative "who's doing what" — the
   `active_sessions` array is a best-effort mirror, not the source of truth (and
   because every session edits that one JSON, treat conflicts on it as expected
   and resolve by union, never by clobbering another session's entry).

## 2. The merge protocol (BEFORE every `merge_pull_request`)

Run these in order. This is the part that stops the retest churn.

1. **List open PRs** (`list_pull_requests state=open`) — the authoritative
   real-time signal. If another session's PR is `mergeable_state: behind`/ready
   and clearly mid-merge, or holds `merge_slot` on the board, **wait** (watch it
   via a Monitor poll on its state; merge yours after it lands).
2. **Claim the slot.** Set `merge_slot` in the board to
   `{held_by, branch, pr, claimed_at}`. If the slot is already held by a live
   session, do not merge — wait or coordinate.
3. **Sync to `main` LAST, right before merging** — `git fetch origin main &&
   git merge origin/main` (or rebase) so your branch is up-to-date at merge time,
   not minutes before. Push; let CI go green on the synced head.
4. **Merge on green.** Confirm all required checks pass on the *synced* head SHA
   (a Monitor poll on `commits/<sha>/check-runs` is the clean wait), then
   `merge_pull_request`. Squash unless the history matters.
5. **Release the slot** — clear `merge_slot` back to nulls immediately after the
   merge resolves (merged OR aborted). A held-but-abandoned slot blocks everyone.

Corollary: **one PR = one concern.** Never add unrelated work to a branch that
already has an open PR — it pollutes the PR and invalidates its CI run (and a new
head SHA strands any merge-gate watcher). Start a fresh branch off `main` for a
distinct deliverable, even mid-session.

## 3. Tiering still applies

This skill governs *coordination*, not *authority*. Tier-3 changes (strategy /
risk / sizing / account-mode / live promotion / real-money order routing) still
need explicit operator approval before merge — claiming the slot does not grant
that. The slot only serializes WHEN you merge, not WHETHER you may.

## 4. Session end

- **Post a `✅ DONE` comment on the coordination board (#6927)** — release your
  claim so the next session knows your area is clear.
- Run **`doc-freshness`** (the canonical session-end check).
- **Prune your `active_sessions` entry** and ensure `merge_slot` is not left held
  by you.
- Anything you couldn't finish → the right review backlog
  (`docs/claude/{health,performance,ml}-review-backlog.json`).

## Honesty

The board is advisory. If you couldn't acquire the slot cleanly (e.g. the MCP was
dropping), say so and fall back to the real-time open-PR list + branch-protection
rather than asserting a clean claim you didn't make.
