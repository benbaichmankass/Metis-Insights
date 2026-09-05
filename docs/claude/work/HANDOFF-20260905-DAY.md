# HANDOFF — day manager 2026-09-05 → successor

Written by `session_016e2k4UmsMGgpbrJ5ctqeFv`, which held the manager lease
across 2026-09-05 and was asked by the operator at 11:30Z to wrap up.
**The registers are the state of record — this file is the prompt, not the
state.** Read in this order:

1. `docs/claude/work/MANAGER-CHECKLIST.json` — 128 items, current to 11:36Z
2. `docs/claude/work/MERGE-QUEUE.json` — the queue and what it learned today
3. `docs/claude/work/SESSIONS.json` — 110 rows, refreshed from a LIVE read at 10:53Z
4. `docs/claude/work/OPEN-PRS.json` — 2 open / 43 settled, every settled row dispositioned
5. `docs/claude/OPEN-ITEMS.json` — 54 rows, 43 of them `loud: true` (see MI-133)

⚠️ **CLAIM THE LEASE FIRST** — `python3 scripts/ops/manager_lease.py status`.
Mine was claimed 10:53:03Z with a 90-minute TTL, so it expires ~12:23Z.
Takeover is time-based; you do not need my cooperation.

⚠️ **AND PUSH THE CLAIM IN THE SAME TURN.** My lease expired TWICE today
(354 minutes, then 136) and both were real dead intervals, not accounting
artifacts. `check_manager_scope` reports them as re-claims rather than late
check-ins — which is correct and is NOT absolution.

---

## The two decisions the operator made today, and what they oblige

Both were put as popups and answered. They are recorded as **MI-132** and
**MI-133**; this is the summary, the items are the contract.

- **MI-132 — the manager wake: grant the connector AND fix the permission
  block.** ⚠️ **TWO INDEPENDENT HALVES; DO NOT LET ONE WAIT ON THE OTHER.**
  The POKE half is blocked on the operator alone — `create_trigger` refuses
  the `connectors` parameter org-wide, so a fired session gets no `mcp__*`
  tools. Nothing a session does unblocks it. The DETECTOR half is a
  session's work and can start now.
- **MI-133 — re-grade `OPEN-ITEMS.json` loudness against a stated bar.**
  43 of 54 rows are `loud: true` (79.6%). Define the bar FIRST and grade
  against it; do not pick a target count and trim to fit. Record the basis
  per row so a later reader can tell *assessed and quiet* from *nobody
  looked*. Demoting a row is **not** closing it.

## What is TRUE now that was not this morning

- **The manager wake was TESTED, not asserted.** Five sessions in a row said
  the Routine was "owed to the operator". Half wrong: creation **succeeds**
  without `connectors`. I created one, fired it, and it failed BOTH halves —
  no `mcp__*` tools (cannot poke) **and** it blocked on a `git add`
  permission prompt with no human, landing nothing (verified: no branch on
  origin, no receipt on main). Trigger DELETED at 10:56Z rather than left
  firing 24×/day into nothing, which reads as covered.
- **MI-103 RECURRED and is now proven to regenerate.** It was
  `landed_unproven`; today was instance 2. Instance 1 (#10780, 09-03) closed
  8 stacked PRs; instance 2 (#10921, today) closed 5. Same per-instance
  workaround both times, which makes that workaround the established
  non-fix. A reconciler **cannot** supply a disposition — it answers *why a
  human closed a PR*, and 'superseded' / 'the operator refused it' / 'the
  author gave up' are opposite next actions.
- **The merge queue is unjammed.** The blocker was one missing sentence —
  `#10921: terminal is 'closed_unmerged' and there is no disposition` — and
  the missing reason was mine.
- **Seven backlog rows filed, zero items taken.**

## What I got WRONG today, because you will hit the same edges

- **`cp`/round-trip writes reformat registers.** Two whole-file reformats
  (3,518 and 2,276 lines) on 13-row and 1-row changes. **Probe the exact
  serialization before writing** — `OPEN-ITEMS.json` does not round-trip at
  ANY `json.dumps` setting and must be patched surgically; the others are
  `indent=2, ensure_ascii=False`. Only the backlogs have a safe writer.
- **Auto-merge fires on the FIRST green commit.** #11049 merged at 10:59:17Z
  while I was writing a second commit at 11:00:45Z, stranding it on a branch
  whose PR was then closed. **After any manager PR merges, check the content
  actually reached main.**
- **I truncated a backlog id for the fourth time.** The guard caught all
  four. The row I filed about it asks me to remember, which is why it is not
  a fix — flagged as needing a real mechanism.
- **I re-diagnosed a live checklist item from scratch** (MI-103), because
  `backlog_append`'s duplicate probe reads the three backlogs and NOT
  `MANAGER-CHECKLIST.json`. The backlogs hold what is not being worked; the
  checklist holds what is. Filed.

## What is BLOCKED, and do not force any of it

| | blocker |
|---|---|
| **#10895** (MI-83, merge-ping observability) | Actionable now — MI-125 measured its R7 face and it CLEARS. What remains is a merge conflict plus an unattributed R2 commit. Do not except it and do not rewrite another session's commit. |
| **#10398** (econ-calendar PIT) | Open since 08-29 and its disposition is RECORDED: MI-131 took the one unique file by value in #11047 (merged 08:55Z). The other two files would REWIND main by 7 days. `state: dispositioned_take_by_value_close_owed` — the close is owed, the analysis is not. **Do not re-derive it.** |
| **MI-103** (reconcile generator) | Owner `unassigned` — needs one. The instance is fixed; the mechanism is not, and it re-jams on the next PR closed without merging. |

## The three things I would do first

1. **Close the five superseded reconcile PRs** (#11041 #11046 #11051 #11056
   #11058) once #11062 lands. Their register content is byte-identical to
   what #11062 carries, plus the disposition only a manager can supply.
   ⚠️ Verify #11062 is ON MAIN first — see the auto-merge lesson above.
2. **MI-133 (loudness)** — it is bounded, needs no fleet reads, and every
   status update pays for it until it is done.
3. **MI-132's DETECTOR half** — startable without the operator, and until
   it works no wake can record anything even if the connector lands.

## What I did NOT do, and why

- **Spawned nothing.** Zero sessions were running against a cap of 3 when
  the operator said to wrap up. MI-132 and MI-133 are queued with owner
  `unassigned` — honestly, rather than assigned to a session that does not
  exist.
- **Took no items.** Filing is the act of not taking them.

---

## Two things found at the very end — read these before touching the queue

**1. A reconcile PR cut before a disposition lands will ERASE it on merge,
and the diff gives you nothing to notice.** A sixth reconcile PR (#11063,
cut 11:17:47Z) read **identical** to `main` — `open_prs [10895, 10398]`,
`settled_prs 43` — while carrying `#10921` with a **blank** `disposition`.
Merging it would have overwritten the recorded reason with an empty one and
re-jammed the guard. Row counts match, the file is generated so churn is
expected, and the title is the same as every other reconcile PR. The only
signal is reading that one field. **Any hand-supplied field is exposed this
way to any register PR already open when it is written.** Filed as
`BL-20260905-A-RECONCILE-PR-CUT-BEFORE-A-DISPOSITION-LANDS-WILL-ERASE-IT-ON-MERGE-WITH-IDENTICAL-ROW-COUNTS`.
All six reconcile PRs are closed with the reason posted on each.

**2. `OI-20260902-DECISION-DRAIN-…` rested on a premise that is FALSE.** It
said *"Nothing in the repo can create it — a Routine is made from the web UI
or /schedule in an interactive CLI session."* `create_trigger` **succeeds**
from an ordinary session; I disproved this by accident building the wake.
What is refused is the `connectors` parameter, org-wide. The row is
corrected in place. ⚠️ **The verdict did not change — the REASON did**, and
that is why it mattered: a session reading the old sentence concludes no
Routine can exist and never looks at the real obstacle, which is that a
Routine-fired session has no tools and cannot answer a permission prompt.
⚠️ **Creating that drain today would be actively harmful** — it would
deliver nothing while reading as armed, which is the failure that row
itself exists to warn about.

## One procedural note for the queue

`claude-pr-automerge` opens PRs as `github-actions[bot]`, and GitHub fires
no workflows for `GITHUB_TOKEN` pushes — so a relay-opened PR can sit at
`mergeable_state: blocked` with **only** its own `open-and-automerge` check
and none of the four required ones. #11066 sat like that for 13 minutes.
**Push one ordinary commit yourself to arm CI.** Do not read zero checks as
"CI hasn't started"; read `mergeable_state` — `blocked` is this, `dirty` is
a merge conflict.

---

## The gate says `not_ready`. Read why before you act on it.

Run at 12:01Z with all three observations supplied
(`--session-id`, `--live-sessions`, `--open-prs`):

```
[PASS] live_registry          3 live session(s) registered (observed 14)
[PASS] checklist_owners       72 owner mention(s) across 128 items resolve
[FAIL] lease                  you do not hold the lease (state=released)
[PASS] manager_state_pushed   registry, checklist and lease match origin/main
[PASS] pending_spawns         no unconfirmed spawn_pending rows
[PASS] open_prs               all 2 open PRs have a row; no row names a closed one
[PASS] pr_decisions           all rows carry a typed decision
[PASS] settled_prs            43 rows, every non-merged one says why
readiness=not_ready
```

**The single FAIL is a sequencing error I made, not state a successor
loses.** The contract is hold → check → release; I released at 11:55:16Z and
ran the check afterwards, so it correctly reports that I am handing over
something I no longer hold. Every check that measures whether anything is
LOST passes.

⚠️ **I deliberately did NOT re-claim the lease to turn it green.** Claiming a
lease for the sole purpose of passing a check, then releasing it again, is
manufacturing a pass — the "cheaper to game than to satisfy" failure this
repo's guards exist to prevent. A released lease is strictly better for you:
you claim immediately instead of waiting out the 90-minute TTL.

**What this means for you:** run the gate yourself once you hold the lease.
It should read `ready`. If it does not, the failing check names a real loss —
mine did not.

⚠️ **And note the incentive this check creates**, filed as
`BL-20260905-HANDOFF-CHECK-CANNOT-GRADE-READY-AFTER-THE-RELEASE-THAT-IS-THE-CORRECT-FINAL-ACT`:
releasing is the right last act, the check cannot pass after it, and the
tool's advice is "fix them, then re-run" — which for this one check means
re-claim. Nothing enforces the order, so the natural sequence produces a red
that invites the wrong repair.
