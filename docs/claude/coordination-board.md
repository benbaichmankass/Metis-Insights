# The Claude Coordination Board (live cross-session comms)

> **The board is GitHub issue [#6927](https://github.com/benbaichmankass/ict-trading-bot/issues/6927)** —
> "🤖 Claude Coordination Board". Standing/pinned, never closed. Discover it by
> that number, by `search_issues in:title Coordination Board`, or by the
> `claude-coordination` label.

## Why this exists

Multiple Claude sessions run in parallel and **still collide** even with the
merge queue. The merge queue (`docs/claude/session-board.json` + branch
protection) only serializes the *one merge slot* — it does nothing to stop two
sessions independently editing the same file, re-doing the same workstream blind
to each other, or blocking on a question only another live session can answer.

The gap is **live comms that are not gated on merging.** A committed file
(`session-board.json`) only propagates through a merge + a pull, so a session's
"I'm working on X" note is invisible to everyone else until it lands — too late
to prevent the collision. A **GitHub issue's comments are visible to every
session immediately** via the API, with zero branch/merge involvement. That is
the board.

## Two tools, both mandatory, different jobs

| | **Coordination Board** (issue #6927) | **`session-board.json`** (merge queue) |
|---|---|---|
| Purpose | Live comms — updates, questions, answers, heads-ups | Merge serialization — the single `merge_slot` + `active_sessions` intent mirror |
| Gated on merging? | **No** — instant, API-visible | Yes — a committed file |
| Medium | Issue comments | Repo JSON |
| When | Continuously, during work | At session start, and around each merge |
| Owner skill | `session-coordination` | `session-coordination` |

Using the board is **MANDATORY for every session**, including every review
sub-session (`/health-review`, `/performance-review`, `/ml-review`,
`/system-review`). It is the **first framing** the `SessionStart` hook emits.

## The protocol (binding)

1. **At session start — READ the board first.** `issue_read method=get_comments`
   on #6927 (newest last). See what every other live session is touching; answer
   any open question you can. This tells you whether your intended work collides
   with someone else's *before* you start it.

2. **POST a `▶️ START` comment before your first substantive change** — session
   id, branch, and **specifically which files / subsystems / PRs you're about to
   touch**. This is the claim that lets other sessions steer clear. (You still
   *also* register in `session-board.json::active_sessions` — the board is the
   live signal, the JSON is the durable record + merge slot.)

3. **POST a `❓ QUESTION` comment** the moment your work might overlap, block, or
   depend on another session's — and **ANSWER (`💬 REPLY`) questions you can**.
   Coordinate *before* the collision, not after. Sessions poll the board; there
   is no @-mention delivery, so keep questions self-contained.

4. **POST a `✅ DONE` comment when you wrap** (merged / handed off / stopping) so
   your claim is released and the next session knows the area is clear.

5. **`⚠️` heads-up comments** for anything other sessions need to know now: a
   shared file you just changed, a live-VM action in flight, a red guard on
   `main`, a deploy about to run.

Keep comments short and skimmable — one comment per event, lead with the emoji
tag + your short session id.

### Comment format

```
▶️ START · <short-session-id> · branch <branch>
Repo: <ict-trading-bot | ict-trader-dashboard | ict-trader-android>
Touching: <files / subsystems / PR #>
Intent: <one line>
```
```
❓ QUESTION · <short-session-id>
<self-contained question>
```
```
✅ DONE · <short-session-id> · branch <branch>
Shipped: <PR # / what merged> — area now clear.
```

## Scope + limits

- **One board for all three repos** (`ict-trading-bot`, `ict-trader-dashboard`,
  `ict-trader-android`) — every session has cross-repo access; post here whatever
  repo you're in and name the repo in the comment.
- The board **complements, does not replace**, the merge queue and branch
  protection. The **per-merge claim is a `🔒 MERGE SLOT CLAIM` comment on THIS
  board** (mirrored into `session-board.json::merge_slot` as the durable record) —
  post it as a precondition on every `merge_pull_request`, read the board tail
  first, and post `🔓 MERGE SLOT RELEASE` after (see
  `.claude/skills/session-coordination/SKILL.md` § 2 — the board comment is the
  authoritative live claim; the JSON is its mirror, per
  BL-20260720-MERGE-PROTOCOL-LAPSE).
- The board grants **no authority**. Tier-3 changes still need explicit operator
  approval before merge; a START comment is a heads-up, not a go-ahead.
- **Honesty applies.** If the GitHub MCP was dropping and you couldn't post
  cleanly, say so in your work rather than asserting a coordination you didn't do.
  Fall back to the live open-PR list (`list_pull_requests state=open`) as the
  real-time truth.

## The VM-lane queue (scarce-VM FIFO — running is never preempted)

The trainer VM is a **single core** shared by every session; concurrent heavy jobs
starve each other. The board carries a **FIFO queue** for it (and for any exclusive
heavy live-VM action), mirroring the merge slot. **Full contract + the routing rule
that keeps most work off the VM entirely: [`docs/claude/vm-resource-management.md`](vm-resource-management.md).**

- **First, route (don't queue).** Before claiming, ask: does this work need
  VM-resident state? If it's CPU-only (a public-feed fetch + a backtest over it),
  run it on a **free GitHub-hosted runner** (`research-exit-head-build.yml` pattern)
  — no VM, no lane, no contention, $0. Most heavy work does not belong on the VM.
- **Claim only for a heavy/exclusive VM job** (a backtest/dataset-build/training
  cycle that must run on the VM). Quick read-only pulls (log tail, `cat`,
  `systemctl status`) need **no** claim — they're parallel-safe.
- **FIFO, running-never-preempted:**
  1. Read the board tail for an open `🔒 VM-LANE CLAIM · <vm>` with no `🔓 RELEASE`.
  2. Lane **free** → post `🔒 VM-LANE CLAIM · <vm> · <session> · <task> · ETA <min>`, dispatch.
  3. Lane **held** → post `🕓 VM-LANE QUEUED · <vm> · <session> · behind <holder>`, **wait**. When the holder releases, the earliest-queued claims next. Newest never wins.
  4. Post `🔓 VM-LANE RELEASE · <vm> · <session>` the instant the job ends or is abandoned.
  5. Override (real priority OR provable non-contention only): `⚡ VM-LANE OVERRIDE · <vm> · <session> · reason: <…>`.

```
🔒 VM-LANE CLAIM · trainer · <session-id> · <task> · ETA <min>
🕓 VM-LANE QUEUED · trainer · <session-id> · behind <holder> · <task>
🔓 VM-LANE RELEASE · trainer · <session-id>
```

**Hard-enforced (2026-07-28), mirroring the merge guard.** A `PreToolUse` guard in
[`.claude/settings.json`](../../.claude/settings.json) (`.claude/hooks/vm_lane_guard.sh`)
**denies** an `issue_write` that carries the **`trainer-vm-heavy-request`** label
unless a fresh (< 30 min) `/tmp/.claude-vm-lane-claim-<session_id>` marker exists —
so a heavy trainer dispatch can't skip the claim under load. The guard is
narrowly scoped and **fail-open**: quick `trainer-vm-diag-request` reads,
system-actions, prop-reports, and every other issue are never matched. The marker
is a speed-bump proving the protocol ran; the `🔒 VM-LANE CLAIM` comment on #6927 is
the claim other sessions actually see. Same session-start caveat as the merge guard
(a session that edits `settings.json` mid-run protects the *next* session onward).

## If the board is ever missing

If #6927 is closed or unreachable, do **not** silently proceed uncoordinated:
recreate it (`issue_write method=create`, same title, this doc's body), update
the number here + in the `session-coordination` skill + the `SessionStart` hook
echo, and post a `⚠️` note. Then continue.

## Enforcement: the hard merge-guard (2026-07-27)

The per-merge slot claim used to be **announced** (SessionStart contract, this
doc, the `session-coordination` skill) but not **enforced** — so a session could
read the contract and still auto-merge PR after PR without ever claiming a slot.
On 2026-07-27 exactly that happened: a full M36 Track-D session auto-merged its
whole PR chain with only `START`/`DONE` posted, never a `🔒 MERGE SLOT CLAIM`,
and raced a concurrent Track-C session into repeated behind-rebase churn. The
operator's directive: make the claim a **physical precondition of merging**, at
session start, not a gate bolted on at the end.

So the claim is now hard-enforced by a **`PreToolUse` guard** in
[`.claude/settings.json`](../../.claude/settings.json) on
`mcp__github__merge_pull_request` **and** `mcp__github__enable_pr_auto_merge`:

- The guard **denies** the merge/auto-merge call unless a per-PR marker
  `/tmp/.claude-merge-claim-<session_id>-<pr>` exists and is **fresh (< 20 min)**.
- The deny message is the runbook: (1) list OPEN PRs (real-time truth — is
  another session mid-merge?); (2) post a `🔒 MERGE SLOT CLAIM` on #6927 naming
  the PR; (3) sync THIS branch to `origin/main` **only if you need to** — your
  change depends on something newly on `main`, or GitHub returns `405 merge
  conflicts` (a real textual conflict, still yours to resolve); (4) `touch` the
  marker and RETRY the call; (5) post `🔓 MERGE SLOT RELEASE` on #6927 after it
  merges.
  <br>Step 3 read *"sync IMMEDIATELY before merging"* until 2026-08-10, when
  **require-up-to-date was unticked** (`branch-protection-sync.yml::STRICT=false`).
  `behind` no longer blocks a merge, so a reflexive re-sync buys another full CI
  cycle and serializes nothing. `merge-queue.md` and the `session-coordination`
  skill were corrected the same day; this file and the hook's own deny message
  were **missed in that sweep** and said the opposite for an hour — which is why
  the phrasing is now pinned by `canonical-doc-coherence`'s declared-values check.
- The marker is a **speed-bump, not the claim** — setting it without posting the
  board comment defeats the purpose and is a coordination failure. The board
  comment is the claim other sessions actually see; the marker just proves this
  session went through the motions for *this specific PR*.
- The guard is **per-PR and time-boxed** so one claim can't blanket-authorise a
  whole session's merges — every distinct PR re-runs the protocol, and a stale
  marker (> 20 min) forces a re-claim + re-sync (the sync is the part that
  actually prevents the behind-rebase churn).

Because hooks load at **session start**, a session that edits `settings.json`
mid-run does **not** pick up the new guard itself — it must still follow the
protocol manually for its own PRs; the guard protects the *next* session onward.
Keep the guard, this section, the `session-coordination` skill, and
`docs/CLAUDE-RULES-CANONICAL.md` § "Multi-session coordination" in sync.
