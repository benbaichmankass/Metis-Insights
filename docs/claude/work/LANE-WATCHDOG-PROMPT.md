# The lane watchdog — the involuntary half of E20

> **Doc status:** `unknown` · category `unknown` · last verified `never` ·
> registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)
>
> Created 2026-09-22 by the E20 lane (`session_011xpvJ2MQgd4jmKkdfg6gzV`).
>
> ⚠️ **The stamp above is `unknown`, and that is deliberate rather than
> sloppy.** `document_index.py` does not assess this directory — its only
> sibling, `docs/claude/work/README.md`, reads the same — so the generator emits
> `unknown / unknown / never / not-assessed` for this path, and R6 fails any
> hand-written row the generator would not reproduce while R3 fails a header that
> disagrees with the row. Claiming `live` here would make the two surfaces
> disagree, which is the exact failure `DOCUMENT-INDEX.md` exists to stop. The
> honest reading: nobody has assessed this file's status, and what makes it
> trustworthy is the Routine it describes existing, not a stamp.

> **Doc status:** `live` · category `instruction` · last verified `2026-09-22` ·
> registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)
>
> Created 2026-09-22 by the E20 lane (`session_011xpvJ2MQgd4jmKkdfg6gzV`).
>
> ⚠️ Its index row reads `unknown / unknown / never / not-assessed`, and that is
> the generator's own output rather than a contradiction of the line above:
> `document_index.py` does not assess this directory — its only sibling,
> `docs/claude/work/README.md`, reads the same. Said here so the two surfaces do
> not disagree silently, which is the failure `DOCUMENT-INDEX.md` exists to stop.

`scripts/ops/lane_reconcile.py` answers, in one screen, whether every checklist
row claiming a lane has a lane that is breathing, and what the live lanes are
costing. **It has to be RUN**, and that is the weakness its own draft admitted:

> *"asking is still voluntary" is reason (5), the one that killed `DUE.md`.*

## Why the mechanism is a separate scheduled SESSION and not a CI guard

**Two designs were tried and measured before this one.**

1. **A CI guard deriving lane liveness from git.** Flag an `in_flight` row whose
   lane has not committed for N hours. **MEASURED 2026-09-22 against live
   session state: 5 of the 7 lanes in `status_bucket` WORKING had never
   committed anything at all.** Commit recency does not separate a working lane
   from a dead one, so that guard would have redded five healthy lanes. Killed
   on measurement.

2. **A CI guard on the offline half.** `--offline` genuinely runs anywhere, and
   the part of it that matters — *zero rows `in_flight` while unblocked rows are
   queued* — is a fact about **the manager's** behaviour. Failing a
   contributor's PR over it would punish the one actor who cannot fix it, which
   is the reasoning `check_pr_queue_watch.py` records for refusing to fail PRs
   on backlog size, and the way a guard gets deleted rather than fixed.

What is left is the honest answer: **the state lives behind MCP tools
(`list_sessions`), which no CI runner can reach**, so the watcher must be a
session. A Routine firing into **the manager's own session** is what exists
today and is not enough — a manager can skip its own reminder, and that is
exactly reason (5). So the watchdog is a Routine that fires into a **FRESH
session**, on a schedule, whoever is managing. The manager cannot skip it
because the manager is not in the path.

## ⚠️ STATUS: BUILT, CREATED, AND **DISABLED** — IT DOES NOT WORK YET

**Do not read "the Routine exists" as "the watchdog works."** That is the
distinction this repo has paid for more than any other, and it applies to this
file's own subject.

`trig_01MFG5cYsTsS1bci279A2TKi` was created 2026-09-22T13:13Z, **smoke-tested by
firing it once**, and then **DISABLED the same minute**, because the firing
established by observation that it cannot do its job:

| checked | observed on the fired session (`session_01GyB7Zej9GgVoodnfKz4Re5`) |
|---|---|
| a checkout of this repo | **ABSENT** — its `session_context` carries no `sources` at all, so `scripts/ops/lane_reconcile.py` is not on disk and steps 2–3 cannot run |
| the Claude Code Remote MCP tools | **ABSENT** — `create_trigger` returned a warning in terms: *"this trigger stores no MCP connectors, so the sessions it fires will run without connector (mcp__\*) tools"*, so `list_sessions` — the watchdog's whole basis — is unavailable |
| the model | served **`claude-sonnet-5`**, not the `claude-haiku-4-5` passed at creation |

⚠️ **THE ABSENCE IS A REAL READING, NOT A GAP IN THE PROJECTION.** `get_session`
on an ordinary lane *does* return `sources: [{git_repository: …}]` — checked
against this session's own record in the same minute — so the field is emitted
when there is a source, and its absence here means there is none. Two
independent signals agree (the trigger's stored
`session_request.config.sources` is also `[]`).

**Why it is DISABLED rather than left running.** A watcher that fires every six
hours, cannot reach its subject, and reports nothing is *exactly* the defect
class E20 is about — a check whose subject it cannot see, still counted as
coverage — and it would bill a session per firing to produce it. Leaving it
enabled would be worse than not having it. **The invariant is therefore still
voluntary**, which this file exists to record rather than let lapse quietly.

**WHAT THE OPERATOR MUST DO to make it work** (this is the only part that needs a
human, and it needs one because connectors on a Routine cannot be granted from a
session that has none to pass through):

1. Open the Routines UI on claude.ai, find `lane-watchdog`, and **attach the
   Claude Code Remote connector** and **this repository as a source**.
2. Re-enable it.
3. Fire it once and confirm the fired session reports either a reconciliation or
   an explicit *could not look* — never a silent pass.

Tracked in `docs/claude/work/PIPELINE.jsonl` as
`PI-20260922-THE-LANE-WATCHDOG-ROUTINE-FIRES-SESSIONS-WITH-NO-REPO-AND-NO-MCP-SO-IT-IS-DISABLED-AND-THE-INVARIANT-IS-STILL-VOLUNTARY`,
with a rerun that checks the Routine's enabled state rather than taking this
paragraph's word for it.

Until then: **run `scripts/ops/lane_reconcile.py --sessions <dump>` by hand at
every manager check-in**, as `.claude/skills/manager/SKILL.md` now requires.

## The Routine as configured

Created 2026-09-22 with `create_trigger`:

- **fresh session per firing** (`create_new_session_on_fire: true`) — so it is
  independent of whoever holds the manager role
- **`claude-haiku-4-5-20251001`** — the work is one dump, one script run and one
  ping. This is the `Sweep dispatch, log reads, extraction` row of the manager
  skill's model table.
- **every 6 hours**. Not 2: the conditions it watches (a walled lane, a finished
  lane still open) persist for **days** when nobody looks — the measured instance
  sat 72.8 hours — so a 6-hour cadence catches them at a fraction of the cost,
  and a desensitising alarm is its own P1 in
  `docs/CLAUDE-RULES-CANONICAL.md`.
- **it reports only when there is a finding.** A quiet run says nothing. An
  `accruing`-equivalent that pinged on every pass would be the alarm fatigue
  that rule already names.

## The prompt (verbatim — this file is the source of record)

It lives here rather than only inside the Routine so it is reviewable in the
diff, reproducible if the Routine is lost, and changeable through a PR:

```text
You are the LANE WATCHDOG for benbaichmankass/Metis-Insights. You are NOT the
manager and you do not do items. One job, then stop.

1. Read docs/claude/work/LANE-WATCHDOG-PROMPT.md and this repo's CLAUDE.md
   section "How work is organised".
2. Call list_sessions(mine=true, limit=60) and write the JSON to a file.
3. Run: python3 scripts/ops/lane_reconcile.py --sessions <that file>
4. If it exits 0, STOP. Say nothing, ping nobody, commit nothing — a quiet run
   is the expected state and an alarm on every pass is the alarm fatigue
   docs/CLAUDE-RULES-CANONICAL.md calls a P1 in its own right.
5. If it exits 1, do exactly these, in order, and nothing else:
   a. Post the reconciliation to the operator via the send-ping system-action
      (open a `system-action` labelled issue, `action: send-ping`). Lead with
      the loudest line: a WALLED lane, then a lane over ceiling, then a
      finished-but-open lane.
   b. For any row whose lane is dead while the row still says `in_flight`,
      append ONE pipeline row via docs/claude/work/PIPELINE.jsonl naming the
      row, the lane, the lane's own post_turn_summary, and a due_when — unless
      a row for that lane already exists, in which case add nothing.
   c. STOP. Do NOT dispatch a lane, do NOT clear a permission prompt (only a
      human may — firing a trigger at a blocked lane answers the prompt on the
      operator's behalf), do NOT archive another session, and do NOT fix
      whatever the dead lane was working on. Surfacing it IS the job.
6. Exit code 2 means the read did not happen. Say THAT — it is not "all clear".
```

## What this does NOT do

- **It does not supervise.** It reports the record of supervision, the same
  honest limit `check_manager_scope.py` R6 states about itself.
- **It does not clear a wall.** A `BLOCKED` lane needs a human click.
  `fire_trigger` is refused there and that refusal is correct.
- **It does not archive anything.** Archiving another manager's lane is the
  manager's call; the watchdog's job ends at making it impossible to miss.

## How to stop it

`list_triggers` to find it by name (`lane-watchdog`), then `delete_trigger`, or
`update_trigger(enabled: false)` to pause it. If it is ever deleted, the
invariant goes back to being voluntary — which is the state this file exists to
record, so say so rather than letting it lapse quietly.
