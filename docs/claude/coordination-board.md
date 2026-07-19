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
  protection. You still claim `merge_slot` in `session-board.json` before merging
  (see `.claude/skills/session-coordination/SKILL.md` § 2).
- The board grants **no authority**. Tier-3 changes still need explicit operator
  approval before merge; a START comment is a heads-up, not a go-ahead.
- **Honesty applies.** If the GitHub MCP was dropping and you couldn't post
  cleanly, say so in your work rather than asserting a coordination you didn't do.
  Fall back to the live open-PR list (`list_pull_requests state=open`) as the
  real-time truth.

## If the board is ever missing

If #6927 is closed or unreachable, do **not** silently proceed uncoordinated:
recreate it (`issue_write method=create`, same title, this doc's body), update
the number here + in the `session-coordination` skill + the `SessionStart` hook
echo, and post a `⚠️` note. Then continue.
