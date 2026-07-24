---
name: research-driver
description: The governance layer for open-ended research/build sessions that don't already map onto a fixed review cadence or a narrower domain skill — how Claude picks what to work on, dispatches to the right existing pipeline before freelancing, keeps moving on other work when a specific item is blocked on a pending Tier-3 decision, pings the operator on a binding hourly cadence, recognizes when a recurring ad hoc pattern should be promoted into its own domain skill, and lands the outcome in the right place in ROADMAP.md's structure. NOT a replacement for /system-review (fixed cadence) or any narrower domain skill (exit-refinement, new-strategy, new-broker, backtesting, model-training, drift-remediation) — it decides when THOSE apply and steps aside once they do. Use when the operator asks for open-ended research, roadmap expansion, "look into X", or any session whose scope isn't already pinned to a fixed review or a domain pipeline.
---

# research-driver — governance for open-ended research/build sessions

Codified 2026-07-22 (operator directive): the repo has strong structure for
its **fixed, repeating** work (the three review skills + `/system-review`,
each with a template, a backlog, a cadence) but had none for the **other
kind of session** — open-ended research/build work that expands the roadmap
(new strategies, new ML experiments, new infra levers, new milestones).
That work landed coherently when a disciplined session happened to follow
good precedent, but consistency depended on rediscovering the pattern each
time — exactly what `docs/CLAUDE-RULES-CANONICAL.md`'s "precedents are not
authoritative" rule warns against. This skill is the binding reference a
new session inherits instead.

## When this skill applies (scope gate)

Load this skill for an open-ended or ambiguous-scope research/roadmap-
expanding directive — "look into X", "improve Y", "what should we build
next", a Claude-initiated deep-dive.

**Hand off immediately, explicitly, when the task already matches:**
- A fixed review cadence (`/health-review`, `/performance-review`,
  `/ml-review`, `/system-review`) — those own their own scope entirely.
- A narrower domain pipeline skill: `exit-refinement`, `new-strategy`,
  `new-broker`, `backtesting`, `model-training`, `drift-remediation`,
  `db-wiring`, `vm-migration`, or any other skill whose description
  matches the task.

State the handoff in your own output ("this is exit-refinement's scope,
invoking it") rather than silently absorbing it under this skill's looser
umbrella — that silent absorption is exactly the drift this skill exists
to prevent.

## Step 1 — Source the work item

Ranked, in order:

1. **Explicit operator directive** — always wins outright.
2. **The "Next — prioritized work plan"** active-build queue in `ROADMAP.md`.
3. **A phased-proposal milestone's next unblocked/gated phase** (the
   `📋 PROPOSED` / `🔄 IN PROGRESS` milestone subsections — check the
   `Phase | Scope | Tier | Gate` table for the next ungated row).
4. **The three review backlogs' proposal arrays** —
   `experiments_proposed[]` (ml-review-backlog), `proposed_tweaks[]`
   (performance-review-backlog), `[refinement]`-tagged items, and
   `exit-refinement-coverage.json`'s `pending` rows.
5. **"Items Under Consideration"** in `ROADMAP.md`.

If none of these hands you a target and the operator hasn't specified one,
say so plainly — don't invent work to look busy.

## Step 2 — Dispatch check

Generation-Discipline Rule 1 (`docs/CLAUDE-RULES-CANONICAL.md` §
Generation Discipline), applied specifically here: before doing ANY
research yourself, scan `.claude/skills/` for a domain pipeline that
already owns this shape of work. If one matches, invoke it and stop —
this skill's own steps below don't apply once you've handed off.

## Step 3 — The shared research-rigor baseline

Every finding produced under this skill (or under a domain skill born
from Step 5) is bound by
[`docs/research/RESEARCH-RIGOR-STANDARD.md`](../../../docs/research/RESEARCH-RIGOR-STANDARD.md).
Read it before running any sweep, backtest, or evidence read. It exists
so this section doesn't have to restate walk-forward discipline,
config-exact harnesses, and truncation-honest counterfactuals every time
a new skill is born — those live in the standard doc and get referenced,
not re-derived.

## Step 4 — Recognizing when ad hoc becomes a program

Use `docs/CLAUDE-RULES-CANONICAL.md` § Generation Discipline Rule 1
verbatim: when you notice yourself repeating the same non-trivial
research/build shape a second or third time with no dedicated skill
governing it, **propose a new skill in chat** — low cost, operator
approves, you create it. Don't invent a new numeric recurrence threshold
here; the existing rule already covers it.

The milestone-creation mechanism for a genuine new initiative already
exists too — M23/M24/M25 show it: a `📋 PROPOSED` status blockquote +
`Phase | Scope | Tier | Gate` table in `ROADMAP.md`. Reuse that shape
(Step 6 below) rather than inventing a different one.

## Step 5 — Execute, and keep moving past a Tier-3 block

Normal Tier discipline (`docs/CLAUDE-RULES-CANONICAL.md` § Permission
Tiers) governs execution mechanics unchanged. This skill adds one rule on
top of it:

**Blocked-on-one ≠ blocked-on-all** (operator directive, 2026-07-22). When
a specific action needs Tier-3 sign-off and that approval isn't
immediately available, do NOT halt the whole session waiting on it:

1. Surface the exact proposed change (the same `proposed_tweaks[]` /
   `promotion_recommendations[]` / `experiments_proposed[]` shape the
   review skills already use) so the operator has everything needed to
   decide async.
2. Immediately continue to the next sourced work item from Step 1 rather
   than idling.
3. Revisit the parked Tier-3 item once it's approved, or at the next
   natural check-in (Step 7). Never silently drop it — same discipline
   the review backlogs already enforce for open items.

A session with one pending Tier-3 decision and three other tractable work
items should end having made progress on all three, with the Tier-3 item
clearly flagged and still open — not idle, and not making the Tier-3 call
unilaterally.

## Step 6 — Landing the outcome in ROADMAP.md

Evaluate in order; stop at the first match:

1. **Operator explicitly directed this as a new milestone** → new
   milestone row, `📋 PROPOSED` status blockquote + phase table, mirroring
   M18/M19/M23's exact shape. Next M-number = highest existing M-number in
   the Milestone Roadmap table, +1 (same convention `sprint-format` uses
   for `S-NNN`).
2. **The outcome is the next unblocked phase of an EXISTING milestone's
   phase table** → update that milestone's phase table row + status
   blockquote in place. Never mint a new milestone number for an
   increment of a program that already has one.
3. **A validated, multi-phase (≥3 stage) new initiative that will recur
   across ≥2 future sessions, not operator-named** → propose a new
   milestone (same `📋 PROPOSED` mechanics as #1), but flag in your
   session output that it's *Claude-proposed*, not operator-directed —
   that distinction matters for operator scrutiny.
4. **A validated-but-not-yet-scheduled idea needing scoping/prioritization
   before it earns session time** → an `Items Under Consideration` bullet,
   filed under the existing bucket (`Strategy / research` / `Infra /
   platform` / `Models` — adding a 4th bucket is itself a decision to flag
   in chat, not do silently), one-liner + backlog id, matching the
   current entries' shape. Pruned when it graduates or ships (see the
   2026-07-11 `S-ROADMAP-REVIEW` precedent at the top of that section).
5. **A single bounded finding/follow-up/honest-negative with no
   multi-session structure** → backlog item only, no `ROADMAP.md` touch —
   `doc-freshness`'s existing three-way split (system/pipeline → health;
   strategy/trading → performance; ML/experiment → ml).

**Tie-break (#2 vs #4):** default to #4 (Items Under Consideration) when
ambiguous — silently expanding an existing phase table with an ungated
phase is worse than under-filing; a future session can deliberately
graduate it.

**Orthogonal, always check regardless of which of #1–5 applied:** was the
picked item already listed in "Next" / a research-week `WS` row / a
numbered ML-program pick? If so, also annotate *that* entry's status
inline (the doc's existing convention) — not mutually exclusive with the
landing above.

**Always, if the milestone's overall status changed:** update the
Milestone Roadmap table's one-line Status cell too. This refines —
cross-references, doesn't duplicate — `doc-freshness` step 5's existing
"Milestone/sprint completed or status-changed → required" row; that row
still governs whether a landing is required at all, this tree only answers
*where*.

## Step 7 — Status cadence (mandatory, operator directive 2026-07-22)

A `research-driver` session pings the operator **at least once an hour**,
unconditionally — including a cycle with no progress to report. A
heartbeat is itself informative: it confirms the session is alive and
still working, distinct from going silent because it's stuck on an
unattended prompt.

Two ping shapes, both via the same `send-ping` system-action
(`docs/claude/telegram-pings.md`):

- **Routine heartbeat** — what's in flight, one line, low priority. Fires
  even with nothing new to report; the cadence itself is the signal.
- **Milestone/result ping** — a phase gate hit, a finding landed, a Tier-3
  item now needs a decision. Normal/high priority per the existing
  telegram-pings convention.

This mirrors the pattern already used by autonomous roadmap sessions
("Autonomous roadmap session started... hourly status pings from here")
— this section makes it a binding requirement of `research-driver`
specifically, not an ad hoc habit some sessions happen to adopt.

### The wake mechanism — how the cadence ACTUALLY fires (mandatory, 2026-07-24)

A ping cadence is not self-executing. A Claude Code (web/remote) session
**ends its turn and stops** — it does not stay running between operator
messages. So "ping at least once an hour" is unactionable on its own: with
no scheduled wake, the session simply goes silent after its last reply and
the operator has to keep pushing it to continue. That silent stop is the
exact failure this whole section exists to prevent (operator directive,
2026-07-24: *"you need to be sending me a ping once an hour even if there's
no progress, and set yourself alarms/timers so you continue when you're
ready and not just stop and wait for me to keep pushing you"*).

So **at session start (Step 0), before doing substantive work, arm a
recurring self-wake** — this is the required companion to Step 0's
permission front-loading:

- Create a recurring **self-bound Routine** with the Claude Code Remote
  `create_trigger` MCP tool: `cron_expression` for hourly (`0 * * * *` —
  the server anchors it to the creation minute), fired into **this
  persistent session** (the default self-bind — do NOT set
  `create_new_session_on_fire`, so the fire resumes the same context and
  its MCP connectors reconnect on resume). The trigger's `prompt` re-enters
  this loop: check in-flight state (open PRs → merge when green; dispatched
  workflow-issues → read the verdict; queued runs), record any landed
  verdicts, continue the next Step-1 work item, and post the status update
  (that reply IS the heartbeat).
- **On the FIRST fire, verify the tools you need are actually present**
  (`get_me` as a github-MCP liveness probe). If a fired session comes up
  without a connector it needs, adapt (git-CLI-only build work still
  works: branch/commit/push, pytest, ruff) and flag the gap — do not go
  silent.
- **Tear the Routine down** (`delete_trigger`) when the session's work is
  genuinely complete or handed off (Step 8) — a self-wake that outlives
  its work is its own alarm-fatigue bug.
- `send_later` (one-shot, minute-granularity) is the right tool for a
  *nearer-than-an-hour* check (e.g. a dispatched workflow that finishes in
  ~10 min); the recurring `create_trigger` is the durable hourly backbone.
  Use both when a run needs a prompt check sooner than the next hourly tick.

Without this, Step 7's mandate is words with no motor. A `research-driver`
session that has NOT armed a self-wake is misconfigured — arm it first,
then work.

## Step 0 (session start) — front-load permissions

Before doing substantive work, identify anything this session will need
that requires an unattended approval mid-flight — a tool/permission grant,
an operator-gated action whose ack you'll want later, access to a resource
not yet wired — and ask for all of it **upfront**, in one batch, at session
start. An autonomous session that hits a silent permission prompt hours in
has no way to surface it (operator directive, 2026-07-22: "it dies out
because I don't see it") — the failure is invisible by construction, so it
must be front-loaded rather than discovered mid-session. This composes
with Step 7's hourly cadence as a second layer of defense (a session that
DOES get stuck despite front-loading still surfaces via the next heartbeat
missing), not a replacement for it.

**WHY this is the whole game (2026-07-24 directive — read this before you
rationalize a mid-flight ask):** the operator being **away** is the
*expected, normal* state of an autonomous session, not an exception. That
is what "autonomous" means. So:

1. **Do not ask for a mid-flight "sanity"/permission ack at all.** If you
   catch yourself wanting one after work has started, that is a Step-0
   miss — you should have front-loaded it. Batch it, or make the safe
   default call yourself within your tier and proceed. Reaching for a
   mid-flight approval is the anti-pattern this step exists to kill.
2. **Silence is NOT denial. An unanswered prompt is the away-operator
   case, not a "no."** Never infer rejection, disapproval, or a changed
   directive from a non-response — and never let an unanswered prompt
   halt autonomous work. If you find yourself having concluded "the
   operator rejected X" or "the operator steered me away from Y" and the
   only evidence is that a prompt went unanswered, that conclusion is
   **wrong by construction** — discard it immediately and continue. (This
   rule exists because a session did exactly this: it fired a scheduling
   prompt, got no reply because the operator was away working autonomously
   as intended, misread the silence as a 3×-repeated rejection, baked that
   false inference into its own running context, and then *perpetuated* it
   across turns instead of catching it — the precise inversion this step
   forbids.)
3. **Catch and reverse a bad inference the moment you notice it.** A wrong
   conclusion about operator intent is not a fact to preserve for
   consistency — the instant you realize an assumption rests on silence or
   a misread, state the correction and act on the corrected understanding.
   Perpetuating a known-shaky inference "to stay consistent" is itself the
   bug.

## Step 8 — Know when to hand off instead of continuing

This skill's own sessions are exactly the class most likely to run long
(open-ended, naturally multi-hour). At every checkpoint from Step 6 (an
outcome landed) or Step 7 (an hourly ping), also check
**`session-handoff`**'s triggers: has a context-compaction already fired
this session, and does the next sourced work item (Step 1) share no context
with what's already loaded? If so, close out per that skill instead of
starting the next unrelated item here — finish/checkpoint current work, no
loose ends, then hand the operator a concrete prompt to continue in a fresh
session. This is distinct from Step 7's hourly heartbeat (that's a status
ping while continuing; this is the decision to stop).

## Composes with

`sprint-format` (write-up format for any sprint log this session
produces), `doc-freshness` (doc-vs-doc contradiction scanning; step 5's
decision-landing table cross-references this skill's Step 6 for *where* in
`ROADMAP.md`), `session-coordination` (concurrency/collision safety — run
in full: board post, merge protocol), `delegate-work` (decomposition/
parallelization mechanics for big-scope work), `session-handoff` (Step 8
above — when to stop and hand off instead of continuing), the domain skills
(dispatch targets per Step 2), `workplan-vs-architecture` (periodic whole-repo
retrospective audit — not this skill's job, see below).

## What this skill does NOT own

Explicit contract, to avoid scope creep:

- Sprint-log format/content (`sprint-format` owns the write-up; this
  skill only decides where the *roadmap-level* record goes).
- Doc-vs-doc contradiction scanning (`doc-freshness`).
- Periodic whole-repo retrospective audits (`workplan-vs-architecture`).
- Concurrency/collision safety (`session-coordination` — still run in
  full, this skill doesn't relax it).
- Decomposition/parallelization mechanics for big-scope work
  (`delegate-work`).
- The four fixed review cadences.
- Execution mechanics of any domain pipeline once dispatched (Step 2).
- Tier/approval authority — comes from `docs/CLAUDE-RULES-CANONICAL.md`
  alone; this skill grants nothing beyond what that doc already allows.
