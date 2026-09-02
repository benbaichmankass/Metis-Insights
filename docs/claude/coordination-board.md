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

## ⚠️ POST WITH `add_issue_comment`. `issue_write method=update` DESTROYS THE BOARD.

**This has now happened EIGHT times** — twice on 2026-08-20 within four minutes,
again on 2026-08-23, and again on 2026-08-29. `issue_write method=update` **replaces the issue
body** — it is the edit-the-issue tool, not the comment tool — and on #6927 the
body is the pinned protocol header every session's preflight reads. The comment
tool is **`add_issue_comment`**.

**Why it keeps recurring, and why a careful reader still hits it:**

* **The MCP's return value is indistinguishable from a successful comment.** Both
  return an id and a URL. Nothing errors, nothing warns; the session moves on
  believing it posted, and the damage is found later by someone else.
* **Every step below says "post a comment" and none of them named the tool**,
  which left the choice to inference — and `issue_write` is the plausible-looking
  guess for "write to an issue". Naming the tool is the whole fix.
* **The record lived where no future session reads it.** Four of the five were
  written up in the offending session's own sprint log; this doc — the *binding*
  protocol — never carried the warning. That is the written-and-never-read class
  this repo has a CI guard family for, reproduced in the doc that governs the
  board.
* **Nothing enforces it.** `.claude/settings.json` hooks are inert on Claude Code
  on the web (`BL-20260820-PROJECT-HOOKS-INERT-ON-WEB`), so this is self-discipline.

Recorded clobbers: **2026-07-30** (`S-BYBIT-COVERAGE-DEPLOY-VERIFY`) · **2026-08-09**
(`S-M20-LADDER-AND-COLLAPSED-STATES`) · **2026-08-15**
(`S-M20-DISPERSION-ISOLATION-AND-QUEUE` § 33) · **2026-08-19**
(`BL-20260819-BOARD-BODY-OVERWRITTEN-BY-ISSUE-WRITE-UPDATE`) · **2026-08-20 08:52Z**
(`S-LLM-BURST-WORKER-SCHEDULING`) · **2026-08-20 08:56Z**
(`comprehensive-system-audit-p2dlkd`) · **2026-08-23 18:13Z**
(`bracket-expectations-exit-ctjaiq` — restored as a reconstruction the same
minute; the session had not read this section before acting) · **2026-08-29 17:59Z**
(`/system-review 2026-08-29` — restored as an abridged reconstruction within
minutes; ⚠️ **this session HAD read this section earlier in the same hour**).
Another session attempted it on 2026-07-30 and was saved only by a scope denial,
not by knowing better.

⚠️ **Two of these are four minutes apart, by two sessions neither of which could see
the other doing it.** That is the argument that this is a tooling-shaped trap rather
than carelessness, and it is why the fix is here — in the binding doc — rather than in
a sixth sprint log. ⚠️ **The 2026-08-23 recurrence happened WITH this section already
in place**, which narrows the remaining cause: the doc is only load-bearing for a
session that reads it before posting, and nothing on the web prompts that read. The
warning is necessary and is not sufficient.

⚠️ **THE 2026-08-29 RECURRENCE NARROWS IT FURTHER, AND UNCOMFORTABLY: that session
had READ this section, in this file, earlier in the same hour.** So "read the doc
first" is not the missing step either — the gap is between *deciding to post* and
*picking the tool*, and nothing occupies it. Reading a warning minutes earlier does
not survive contact with a routine action that has a plausible-looking wrong tool
and a success-shaped return value. **The remaining fix is mechanical, not
editorial:** a wrapper or an allowlist that makes `issue_write method=update` on
#6927 impossible, so the trap cannot be stepped in rather than merely being
described. Until that exists, every further sentence added here is treating a
tooling defect as a discipline problem — which is what the previous seven entries
already tried.

⚠️ **When restoring, GitHub strips `<…>` as HTML EVEN INSIDE CODE FENCES.** A restore
that pastes this file verbatim silently empties every comment template. Use `{braces}`
in the body, and restore the angle brackets when syncing the body back to this file.

**If you clobber it anyway:** say so on the board immediately, then restore the
body from this file (it is the board's **body of record**) and label the result a
reconstruction — do not pass a rebuild off as the original. GitHub keeps the real
text in the **"edited"** dropdown on the issue body, which is GraphQL-only
(`userContentEdits`) and therefore unreachable from a web session, so a verbatim
restore needs the operator's one click.

## The protocol (binding)

1. **At session start — READ the board first.** `issue_read method=get_comments`
   on #6927 (newest last). See what every other live session is touching; answer
   any open question you can. This tells you whether your intended work collides
   with someone else's *before* you start it.

   ⚠️ **PROVE YOU REACHED THE END — a full page is not the tail**
   (`BL-20260817-BOARD-TAIL-READ-CANNOT-ASSERT-IT-REACHED-THE-END`). `get_comments`
   pages **ascending** with no `is_last` field and no newest-first option, so a page
   of comments looks **identical** whether or not it is the last one. Request
   `perPage=N`: getting back **N** items proves *nothing* about reaching the end;
   a **short page (fewer than N) is the proof**. To find the end at all, probe
   `perPage=1` at a high `page` for an empty `[]` and bisect down.

   **This is not hypothetical and it is not a matter of being careful.** On
   2026-08-17 a session read a page whose newest entry was 13:35Z, reported *"no
   open 🔒"*, and merged inside another session's open 14:13:47Z claim — two
   comments had landed past its page boundary. Both had edited the same table in
   the same file. **Demonstrated live minutes later:** `perPage=3, page=345`
   returned a short page of 2 (valid proof of the end); two comments later the
   **same query** returned a full page of 3 whose newest was stale. A page number
   silently stops being the tail. Reading *"the tail"* is not the same as reading
   the tail — never write *"no open 🔒"* off a page you have not shown to be last.

2. **POST a `▶️ START` comment (via `add_issue_comment`, NEVER `issue_write`) before your first substantive change** — session
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

### ⚠️ THERE IS NO DIRECT SESSION-TO-SESSION CHANNEL, IN EITHER DIRECTION

**This is why the board is mandatory rather than merely encouraged**, and it is the
one thing a session most often assumes it can route around.

`SendMessage` between a manager and a sub-session **fails both ways**:

| direction | result | measured |
|---|---|---|
| manager → sub-session | `{"success": false, "No agent named ... is reachable"}` | 2026-09-01, `WO-20260901-PHASE-E` |
| **sub-session → its own manager** | **the same error**, on its own `parent_session_id` | **2026-09-02, MI-70** |

⚠️ **The downward half was recorded first and was read as the whole constraint.**
That phrasing — *"a manager cannot message a sub-session"* — invites the reading
that a sub-session can at least **report upward**. It cannot. `ListAgents` shows no
reachable cloud peers in either direction, and there is no `send_message` or
`list_events` tool in either surface.

What follows, and it is not a detail:

- **A sub-session with a finding, a blocker, or a question has exactly two channels
  to its manager: THIS BOARD, and its PR.** Nothing else reaches. A sub-session that
  "tells the manager" in its final message is talking to a transcript nobody polls.
- **A spawn prompt must therefore carry every rule the sub-session will need**, and
  must direct upward-bound information to the board or the PR rather than to the
  manager. A rule remembered after the spawn cannot be delivered.
- **A manager must not wait for a sub-session to report.** It polls `get_session`
  and reads the board and the branch; there is no inbox on either side.

**The handoff is cheap precisely because of this.** There is no live connection to
transfer, so taking over is purely a KNOWLEDGE problem — and knowledge goes in
files: `SESSIONS.json`, `MANAGER-CHECKLIST.json`, `OPEN-PRS.json`, and this board.

### If `add_issue_comment` returns 403 — USE THE RELAY, don't skip the board

A PM-side session's MCP can be **read-only for issues and PRs**:
`add_issue_comment` returns `403 Resource not accessible by integration` while
`issue_read` on the same issue succeeds. That is a **write-scope boundary, not**
the transient hosted-MCP drop this repo also documents (which a cheap retry
clears in seconds) — so retrying with backoff will not fix it, and neither will
`gh` (not installed in the sandbox) or `curl` to `api.github.com` (403 at the
proxy).

**There is a relay for exactly this, and you do not have to skip the board:**

```
automation/board-posts/<name>.md     →  .github/workflows/board-post.yml
```

Write your comment as that file's **entire contents**, push it on a `claude/**`
branch, and the runner posts it to #6927 with its own `GITHUB_TOKEN`. Read the
outcome back:

```
git fetch origin <branch>
git show origin/<branch>:automation/board-results/<name>.txt
```

An empty body is **refused**, and a failed post **fails the run** — deliberately
louder than its `pr-opener` sibling, because a session that believes it claimed
the board and did not is invisible to every other session *and to itself*.

The two siblings, for the same 403: `automation/pr-requests/<name>.json` →
`pr-opener.yml` opens a PR (fresh filename per PR — a reused one is a silent
no-op), and `.github/pr-automerge-requests/<branch>.txt` →
`claude-pr-automerge.yml` enables auto-merge.

⚠️ **This paragraph exists because the relay was undiscoverable from the
documents a session actually reads.** `board-post.yml` shipped 2026-08-20
specifically so a 403-scoped session could comply with a binding rule — and the
strings `board-post` and `pr-opener` appeared **zero times** in this file,
`CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` and the `session-coordination`
skill (measured 2026-09-01, positive control: `claude-pr-automerge` appears 3×
in `CLAUDE.md`). A session hit the 403 on 2026-09-01, read the docs, correctly
concluded no board path existed, and found both relays only by reading
`.github/workflows/` after the documented paths had failed. A capability that is
built but unreachable from the surface its user reads is, for that user,
identical to no capability at all
(`BL-20260901-COORDINATION-BOARD-WRITES-403-FROM-THIS-SESSION-WHILE-READS-SUCCEED`).

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

⚠️ **THE ENFORCEMENT DOES NOT EXIST ON CLAUDE CODE ON THE WEB, WHICH IS WHERE
MOST OF THESE SESSIONS RUN** (measured 2026-08-20, closing the diagnosis half of
`BL-20260819-MERGE-SLOT-GUARD-DOES-NOT-FIRE`). Three sessions merged without
ever claiming a slot — 5 merges 2026-08-18, 1 on 2026-08-19, 10 on 2026-08-20 —
and the cause is neither the matcher nor MCP namespacing nor a hook error, the
three candidates this row originally listed. **The web runtime loads no project
hooks whatsoever:** `/tmp/claude-code.log` shows **1,379 consecutive
`Hooks: Found 0 total hooks in registry` lines** (2026-08-18 → 2026-08-20T08:12,
never once a non-zero count), and no marker file from ANY hook in
`.claude/settings.json` exists in `/tmp` across ~120 sessions — not the merge
claim, not the board nudge. So the SessionStart contract echoes, the board
nudge, this guard, and the VM-lane guard are **all inert on the web**; the same
class as *"Claude Code on the web doesn't honour project `.mcp.json`"* (root
`CLAUDE.md`), and not fixable from this repo.

**What this means in practice, stated plainly because the old wording invited
the opposite inference:** a merge going through is **not** evidence the protocol
ran. The guard was described as a *"physical precondition of merging"*; on the
web it is a description of a precondition that nothing checks. A backstop
believed to exist and absent is worse than a known-absent one — it is the
desensitized-alarm failure inverted, and it is why the merge-without-claim went
unremarked for three days across three sessions.

**The script itself is correct** and is now held to that by
[`tests/test_merge_slot_guard.py`](../../tests/test_merge_slot_guard.py), which
extracts the shipping command out of `settings.json` (never a copy, which would
drift) and asserts against synthetic stdin: no marker → deny naming the PR and
issue #6927; fresh marker → allow; a marker for a *different* PR → still deny;
a >20-minute marker → deny; and no stray shell diagnostics. That last one caught
a real defect the same day — `` `behind` `` sat inside a double-quoted string, so
bash ran command substitution, printed `behind: command not found`, and **deleted
the word from the deny message**, leaving *"so  no longer blocks a merge"*. Fixed.

So it does fire where project hooks load (CLI, desktop). Where they do not, run
the protocol as self-discipline. The claim is enforced (in those runtimes) by a
**`PreToolUse` guard** in
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
