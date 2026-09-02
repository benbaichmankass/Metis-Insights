---
name: session-coordination
description: >
  Binding cross-session workflow governance — the session preflight (read the
  rules + know your tool/capability limits), the MANDATORY live coordination
  board (GitHub issue #6927 — post updates + questions, NOT gated on merging),
  the multi-session MERGE PROTOCOL that serializes PRs so concurrent sessions
  don't race a merge and force each other into behind-rebase retest churn, and
  CROSS-SESSION RESOURCE OPTIMIZATION — route CPU-heavy work to free GitHub runners
  (not the scarce 1-core trainer VM), serialize the VM with a board FIFO lane, and
  flag any dead run loudly (docs/claude/vm-resource-management.md). Use at
  the START of every session, BEFORE every merge, and BEFORE dispatching heavy VM
  work. Owns docs/claude/session-board.json
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
on 2026-06-28, and again across four cycles on 2026-08-09/10). **That specific
re-run churn is GONE as of 2026-08-10** — `require-up-to-date` was unticked
(`strict: false`, operator-directed), so being `behind` no longer blocks a merge.
The rest of this skill stands: two sessions merging at once still collide on
shared files, still duplicate work, and still need the claim. It is **binding**:
a session that skips it is the failure mode.

This skill owns **two** coordination surfaces, and both are mandatory:

- **The live coordination board — GitHub issue [#6927](https://github.com/benbaichmankass/ict-trading-bot/issues/6927)**
  ("🤖 Claude Coordination Board"). Live comms **not gated on merging** — post
  work-start / questions / answers / done, read at session start. Because it's an
  issue, every session sees a comment the instant it's posted (a committed file
  only propagates through a merge + pull — too late to prevent a collision). Full
  protocol: **`docs/claude/coordination-board.md`**.
- **The merge queue — `docs/claude/session-board.json`.** Honour-system
  last-writer-wins, not a hard lock; the hard safety net is GitHub
  branch-protection **required status checks** (`require-up-to-date` is off since
  2026-08-10). Its job is to **mirror** session intent durably (the
  `active_sessions` array). ⚠️ `merge_slot` is *structurally* unwritable for a
  claim on the PR that carries it — see
  `BL-20260810-MERGE-SLOT-MIRROR-UNWRITABLE-PRE-MERGE`; the authoritative claim
  is the #6927 comment.

**One source of truth for the merge claim: the live board (#6927), not the JSON.**
The 2026-07-20 lapse (BL-20260720-MERGE-PROTOCOL-LAPSE — 3 claim-less merges raced
`behind` 3× in one day, each costing a branch refresh + a full ~10-min CI rerun,
while the 2 merges that DID post a claim comment merged cleanly first try) showed
the two surfaces had drifted: sessions treated `merge_slot` in the JSON as the
claim, but it went untouched all day while the *board comment* claims were what
actually prevented collisions. So: the **`🔒 MERGE SLOT CLAIM` / `🔓 MERGE SLOT
RELEASE` board comments are the authoritative live claim** (they reach other
sessions instantly); the JSON `merge_slot` is a best-effort durable mirror, not
the claim itself. The board makes concurrent work *visible in real time*; the JSON
records intent durably. Use both, but the board comment is the one that gates a
merge.

## 1. Session-start preflight (before your FIRST substantive tool call)

The `SessionStart` hook (`.claude/settings.json`) already emits the read-first
contract + generation discipline. This skill adds the two missing halves:

1. **Read-first (non-negotiable, hook clause 1).** Read in full:
   `docs/CLAUDE-RULES-CANONICAL.md`, root `CLAUDE.md`, and the `SKILL.md` of the
   skill covering your task. A context-compaction RESUME is a new session — it
   does NOT waive this.
2. **Know your capabilities BEFORE you reach for a tool you don't have.** On
   Claude Code on the web / PM-side sessions (see `CLAUDE.md` § "PM-side session
   capabilities"): **`run_workflow` WORKS** — this line read "`run_workflow`
   403s" until 2026-09-02 and that is stale in the direction that COSTS you a
   capability. It 403'd when checked 2026-06-11; the 2026-08 MCP added it, and
   it was exercised again 2026-09-02 (`actions_run_trigger method=run_workflow`
   on `replay-pregate-nightly.yml` against a feature ref → 204, run #4390).
   Labelled issues (the diag/system-action relays) remain the fallback, and are
   still the only path for workflows that expose no `workflow_dispatch`. Direct VM egress is firewalled for a raw
   `http://IP:port` but **not** for the Caddy HTTPS hostname (measured
   2026-08-20 at default-`Trusted`) — try `https://ict-bot.duckdns.org/api/diag/*`
   first, then the **`vm-diag-snapshot` relay**; the trainer is relay-only
   (`trainer-vm-diag`), it has no HTTP diag surface. The hosted GitHub MCP **drops intermittently** — retry
   with backoff (2s/4s/8s/16s), never treat the first failure as an expired token
   or hand off to the operator. There is **no `create_label`** — labels come from
   `bootstrap-labels.yml`.
3. **Claim the WORK, not just the files.** `docs/claude/work/` is the state of
   record for what is being worked and under which intent, and it is the only
   surface a session arriving COLD can read. Before you start: if your task
   corresponds to a work object, say which in your board `START`; if it does
   not, that is worth noticing rather than skipping past. ⚠️ **Two sessions can
   claim disjoint FILES and still be the same WORK** — the file list cannot show
   that, and the object id can. If you are MANAGING, the lease
   (`scripts/ops/manager_lease.py status`) and the sub-session registry
   (`docs/claude/work/SESSIONS.json`) are separate obligations; see `CLAUDE.md`.

> ⚠️ **EVERY board post goes through `add_issue_comment`. NEVER `issue_write
> method=update` — that REPLACES the issue body and destroys the board's pinned
> protocol header.** It has happened six times (2026-07-30, 08-09, 08-15, 08-19,
> and twice on 08-20) and the MCP's return value is **indistinguishable** from a successful
> comment, so nothing tells you — twice on 2026-08-20 alone, four minutes apart, by
> two sessions neither of which could see the other do it. Full account + the restore
> procedure:
> [`docs/claude/coordination-board.md`](../../../docs/claude/coordination-board.md)
> § "POST WITH `add_issue_comment`".

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

## 2. The merge protocol — a PER-MERGE precondition (BEFORE every `merge_pull_request`)

> **The GitHub native merge queue is UNAVAILABLE on this repo, so the manual
> protocol below is the SOLE serializer** (see
> [`docs/runbooks/merge-queue.md`](../../../docs/runbooks/merge-queue.md),
> BL-20260726, resolved 2026-08-02): the native queue is an **organization-only**
> GitHub feature and `benbaichmankass/Metis-Insights` is **user-owned** — there is
> no "Require merge queue" checkbox to enable, and the dormant `merge_group:`
> triggers on the workflows never fire. So there is **no** auto-sync-and-serialize
> path; the `🔒 CLAIM` / sync-immediately-before / `🔓 RELEASE` steps below are the
> real serializer, not belt-and-suspenders. Expect the rebase-race (a PR goes
> `behind` while its checks run) — **resolved 2026-08-10**: "Require branches
> up to date" is now unticked on `main`, so a `behind` PR merges on green. Moving
> the repo into an org (for the native queue) remains the only route to real
> serialization. The board (#6927) is unchanged and
> still MANDATORY for **work coordination** — `▶️ START` / `✅ DONE` / questions /
> `active_sessions` registration — which the queue does not do. Until the queue
> is enabled on this repo, the manual protocol below is the sole serializer and
> is fully binding.

**This is not a session-start ritual — it is a precondition on the
`merge_pull_request` call itself.** Every merge, every time, no matter how busy the
repo is (the 07-20 lapse happened precisely because the claim lived in session
memory and got skipped under time pressure while racing an approved chain). If
you are about to call `merge_pull_request`, you run steps 1–2 in the *same turn*
first. Run all of these in order — this is the part that stops the retest churn:

1. **Read the board tail + open PRs FIRST.** `issue_read method=get_comments` on
   **#6927** (read the *newest* comments — an open `🔒 MERGE SLOT CLAIM` with no
   matching `🔓 RELEASE` means another session holds it: **wait**) AND
   `list_pull_requests state=open` (the authoritative real-time signal — a PR
   `mergeable_state: behind`/ready and clearly mid-merge means wait). Not reading
   the board before merging is root-cause (2) of the 07-20 lapse.

   ⚠️ **ASSERT that the page you read IS the last page before concluding "no open
   🔒"** (`BL-20260817-BOARD-TAIL-READ-CANNOT-ASSERT-IT-REACHED-THE-END`).
   `get_comments` pages ascending with no `is_last` field, so a full page and the
   real tail are **byte-indistinguishable**. Request `perPage=N`; **N items back
   proves nothing**, a **short page (< N) is the proof**. Find the end by probing
   `perPage=1` at a high `page` for `[]`, then bisect. A session skipped this on
   2026-08-17, read a page 46 minutes stale, and merged inside another session's
   open claim on the same file — see `docs/claude/coordination-board.md` §
   "The protocol" step 1 for the full account. **`list_pull_requests state=open`
   has no such failure mode and is the cheap cross-check** — if it shows another
   session's PR ready-and-green while your board read shows no claim, believe the
   PR list and re-read the board.
2. **Post your `🔒 MERGE SLOT CLAIM` comment on #6927** (session id, branch, PR #).
   This board comment is the live claim that reaches other sessions in time; also
   mirror it into `session-board.json::merge_slot` (`{held_by, branch, pr,
   claimed_at}`) as the durable record. If a live session already holds the claim,
   do not merge — wait or coordinate.
3. **Sync only when you need `main`'s content.** Since 2026-08-10 a `behind`
   branch merges fine, so a reflexive re-sync just buys another full CI cycle.
   Sync when your change depends on something newly on `main`, or when GitHub
   returns `405 merge conflicts` (a real textual conflict — still yours to
   resolve). Otherwise let CI go green on the head you have.
4. **Merge on green.** Confirm all required checks pass on the *synced* head SHA
   (a Monitor poll on `commits/<sha>/check-runs` is the clean wait), then
   `merge_pull_request`. Squash unless the history matters.
5. **Release the slot** — post a `🔓 MERGE SLOT RELEASE` comment on #6927 AND clear
   `session-board.json::merge_slot` back to nulls immediately after the merge
   resolves (merged OR aborted). A held-but-abandoned claim blocks everyone.

Mnemonic: **read board → 🔒 CLAIM → sync → merge on green → 🔓 RELEASE**, on every
`merge_pull_request`.

**Enforcement is RUNTIME-DEPENDENT, and on Claude Code on the web there is
NONE — do not read a merge that went through as proof the protocol ran**
(measured 2026-08-20, `BL-20260819-MERGE-SLOT-GUARD-DOES-NOT-FIRE`). The web
runtime never loads project hooks at all: `/tmp/claude-code.log` carries
**1,379 consecutive `Hooks: Found 0 total hooks in registry` lines** from
2026-08-18 to 2026-08-20 and **not one** line reporting a non-zero count, and no
`/tmp/.claude-board-nudge-*` or `/tmp/.claude-merge-claim-*` marker exists across
~120 sessions of `/tmp` history. This is the same class as *"Claude Code on the
web doesn't honour project `.mcp.json`"* (root `CLAUDE.md` § PM-side session
capabilities) — nothing in this repo can change it. The guard SCRIPT is correct
and denies exactly as documented when invoked (`tests/test_merge_slot_guard.py`
runs the shipping command against synthetic stdin and asserts the deny, the
per-PR scoping, and the 20-minute staleness bound), so it does fire in runtimes
that honour project hooks — CLI and desktop. **On the web the protocol is
self-discipline: run it because it is the contract, not because something will
stop you.** Three sessions have now merged without it — 5 merges on 2026-08-18,
1 on 2026-08-19, 10 on 2026-08-20 — and every one of those was noticed only
because the session volunteered it.

The guard as written (2026-07-27): a `PreToolUse` guard in
`.claude/settings.json` **denies** `merge_pull_request` **and**
`enable_pr_auto_merge` until this session has run the protocol for the specific
PR and set a fresh (< 20 min) per-PR marker `/tmp/.claude-merge-claim-<sid>-<pr>`
(you `touch` it in step 4, after posting the `🔒 CLAIM` and syncing). The deny
message restates steps 1–5. The marker is a speed-bump proving you went through
the motions for *that* PR — it is **not** the claim; the `🔒 CLAIM` comment on
#6927 is what other sessions see, so post it for real. Rationale + the incident
that forced it: `docs/claude/coordination-board.md` § "Enforcement: the hard
merge-guard". (Because hooks load at session start, a session that *edits*
`settings.json` mid-run still follows the protocol manually for its own PRs.)

Corollary: **one PR = one concern.** Never add unrelated work to a branch that
already has an open PR — it pollutes the PR and invalidates its CI run (and a new
head SHA strands any merge-gate watcher). Start a fresh branch off `main` for a
distinct deliverable, even mid-session.

## 2b. Cross-session resource optimization + the VM-lane protocol (BEFORE dispatching heavy VM work)

Binding contract: **[`docs/claude/vm-resource-management.md`](../../../docs/claude/vm-resource-management.md)**
(operator-directed 2026-07-28 — "cross-session resource optimization needs to be the
core"). The essentials every session applies:

1. **Route to the cheapest sufficient resource.** GitHub-hosted runners are **free
   ($0 on this public repo), abundant, and parallel across sessions** (4 vCPU, up to
   ~5.5h). The trainer VM is a **single core — scarce and serialized.** So
   **CPU-heavy work that needs NO VM-resident state** (a public-feed fetch + a
   backtest/k-fold/validation over it) belongs on a **runner** (the
   `research-exit-head-build.yml` pattern — fetch from a public archive: Binance-
   vision keyless, Dukascopy for FX/metals; Bybit geoblocks US runners), **NOT** the
   `trainer-vm-diag` SSH relay. Reserve the trainer VM for work that genuinely needs
   its on-box state (dataset cache, registry, on-box services, GPU). The deep XAU/MGC
   re-validation that kept dying at the trainer relay's job cap (#7829) is the worked
   example: it was CPU-only and belonged on a runner.
2. **Serialize the scarce VM with the board FIFO — running is never preempted.**
   Before dispatching a **heavy/exclusive** VM job (a backtest/dataset-build/training
   cycle on the VM), read the board tail; if a `🔒 VM-LANE CLAIM · <vm>` is open with
   no `🔓 RELEASE`, **wait** (post `🕓 VM-LANE QUEUED`); else post
   `🔒 VM-LANE CLAIM · <vm> · <session> · <task> · ETA <min>`, dispatch, and post
   `🔓 VM-LANE RELEASE` the instant it ends. FIFO; newest never wins; one documented
   `⚡ VM-LANE OVERRIDE` escape hatch for real priority / provable non-contention.
   **Quick read-only pulls need no claim** (parallel-safe). Format:
   `docs/claude/coordination-board.md` § "The VM-lane queue". This is a **board**
   FIFO, not a GitHub concurrency group (which can't queue depth > 1).
   **Hard-enforced (2026-07-28):** a `PreToolUse` guard (`.claude/hooks/vm_lane_guard.sh`)
   denies an `issue_write` carrying the `trainer-vm-heavy-request` label unless a fresh
   `/tmp/.claude-vm-lane-claim-<sid>` marker exists — so a HEAVY trainer job uses that
   label + claims first; quick `trainer-vm-diag-request` reads (and everything else) are
   never blocked. The marker is a speed-bump; the `🔒 VM-LANE CLAIM` comment is the claim.
3. **A dead run flags loudly + immediately.** `claude-run-failure-alert.yml` pings
   the operator the moment a watched VM/relay/research run fails/cancels/times-out,
   and each workflow posts an **honest** failure/cancelled comment (a `cancelled`
   relay run is the **job time budget** or a manual cancel — **never** a sibling
   preemption). **Never wait indefinitely on a dispatched run** — poll its issue, and
   treat a failure/cancelled comment as terminal (re-diagnose, don't blindly re-open).
   Any new Claude-driven workflow a session can BLOCK on MUST post honest terminal
   comments AND be added to `claude-run-failure-alert.yml`.

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

## Composes with

- **`session-handoff`** — this skill's § 4 "Session end" (board `✅ DONE`,
  `doc-freshness`, pruning your board entry) is exactly Step 2 of that
  skill's wrap-up checklist. Reuse it rather than re-deriving it: this skill
  owns *how* to release your claim; `session-handoff` owns *when* to decide
  the session is over and *how* to hand off to a fresh one.
