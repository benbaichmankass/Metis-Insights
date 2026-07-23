---
name: session-handoff
description: Recognize when a session has run long enough that continuing to a NEW unrelated work item in the SAME context window is wasting compute (repeated context-compaction, cross-subsystem thrash), then close the current unit of work cleanly with no loose ends and hand off with a concrete, self-contained prompt for a fresh session to continue. Use at every natural checkpoint (a PR merged, an investigation resolved, before starting a new unrelated item) — especially in research-driver / full-system-audit / any open-ended or multi-hour session. NOT for splitting a big task across PARALLEL sessions up front (that's delegate-work) and NOT for concurrent-session collision safety (that's session-coordination) — this is the SERIAL/temporal counterpart: one thread of work, continued across TIME via a fresh context instead of one ballooning one.
---

# session-handoff — stop cleanly, hand off crisply

Codified 2026-07-23 (operator directive): long-running sessions that grind
through many unrelated tasks in one context window waste compute — every
context-compaction is a lossy, costly re-derivation, and a session that never
closes the books leaves the next session to rediscover state from chat
scrollback instead of the repo. The fix is symmetric to `delegate-work`'s
up-front decomposition, but on the **time** axis instead of the **space**
axis: recognize the cut point, close cleanly, hand off crisply.

## The core distinction (read this first)

- **`delegate-work`** — a big task is decomposed **before** starting, run
  **in parallel** across sub-agents/sessions.
- **`session-handoff`** (this skill) — a single serial thread of work has
  **organically grown too long**; it gets closed out and **continued in a
  fresh session later**, one thing after another in time, not in parallel.
- **`session-coordination`** — concurrent sessions not colliding *right now*.
  This skill reuses its board-`✅ DONE` step but is otherwise orthogonal.

All three compose. A long `research-driver` session might use `delegate-work`
mid-flight for a big sub-task, then still need this skill to close out at the
end of the day.

## Step 1 — Check the triggers at every natural checkpoint

Check this **whenever a unit of work just finished** (a PR merged, an
investigation answered, a review completed) and before starting the next
unrelated one — never mid-flight on unfinished work (see Step 2's ordering).

1. **A context-compaction has already fired this session.** A resumed-from-
   summary conversation is a visible, checkable signal (the harness's own
   "conversation grows long → summarized" behavior, or a session that opened
   with "this session is being continued from a previous conversation that
   ran out of context"). One compaction already happening is tolerable —
   normal on a real workstream. The trigger is what happens **next**: don't
   volunteer to start a THIRD unrelated workstream that would risk a SECOND
   compaction. Each compaction compounds fidelity loss and burns real tokens
   re-deriving context that a fresh session gets for free by reading the
   durable record (roadmap/sprint-log/backlog) instead.
2. **The next candidate work item shares no context with what's already
   loaded** — different subsystem, different repo, an unrelated
   investigation. This is `delegate-work`'s "does it decompose into
   independent units?" test applied backwards: if the answer is yes, the
   independent unit belongs in its own session, not appended to this one —
   nothing carries over except the token cost of holding it all.
3. **This session has already shipped multiple independent, fully-closed
   units** (merged PRs / resolved investigations / a completed review) and
   is about to start yet another unrelated one. A soft proxy for #2, not a
   hard numeric gate — don't invent a precise count threshold; if #2 says
   yes, this usually already agrees.
4. **Explicit operator ask** ("wrap this up", "let's continue in a new
   session", "hand this off") — wins immediately, skip straight to Step 2.

None of these block finishing the **current** unit of work — they only gate
whether to *start the next one* here. Never hand off mid-flight on something
unfinished (a half-written edit, an uncommitted diff, an unresolved operator
question) — reach a safe checkpoint first (Step 2), even if that means
finishing the current small thing before closing out.

## Step 2 — Close the current unit with no loose ends

1. **Finish or checkpoint** the in-flight unit: commit, push, open the PR (a
   draft with a clear description is a valid checkpoint — a stray local
   branch with uncommitted changes is not).
2. **No loose ends** — verify, don't assume:
   - `git status` clean, or every remaining diff explained in the handoff.
   - Every branch you pushed has an open PR (Multi-session coordination's
     "one PR = one concern" still applies — don't leave an orphan branch).
   - If you posted a `▶️ START` on the coordination board (issue #6927),
     post `✅ DONE` now — see `session-coordination`.
   - Any Tier-3 proposal still awaiting operator approval is **logged to the
     right backlog** (health/performance/ml, per `doc-freshness`'s split),
     never silently dropped — same discipline `research-driver` Step 5
     already applies to a blocked-on-one item.
   - Any open question you asked the operator that hasn't been answered yet
     gets carried into the handoff prompt (Step 3), not lost.
3. **Write the record**, sized to the work:
   - Sprint-scale work → a full sprint log (`sprint-format`).
   - A smaller session → at minimum, a compact closing note (what shipped,
     what's next) wherever this session's work is already being tracked —
     don't force full sprint-log overhead onto a short session.
4. **Run `doc-freshness`** if any canonical doc might now contradict the
   code or another doc.

## Step 3 — Produce the handoff prompt (the actual deliverable)

This is what the operator asked for and it is a **required output of this
skill**, not an optional nicety: end the session by giving the operator a
concrete, paste-ready prompt for a fresh session. Adapted from
`delegate-work`'s sub-session spawn-prompt template, but for **serial**
continuation of the same thread rather than a parallel independent unit:

> Continuing from a prior session on **\<repo(s) : branch(es)>**.
> **Just completed** (verified, not intent): \<1–3 sentences, cite PR
> numbers / commit SHAs / what was actually confirmed — the same
> verified-reality bar `sprint-format` holds logs to>.
> **Durable record**: \<sprint log path> / \<ROADMAP.md row> / \<backlog item
> id> — read that for full detail; this prompt POINTS at the record, it does
> not re-derive it (durable plan over memory, same principle
> `delegate-work` uses for parallel units).
> **Next**: \<the specific next work item + where it was sourced from — an
> explicit operator ask, the ROADMAP "Next" queue, a review-backlog item id,
> or `research-driver` Step 1's ranking>.
> **Outstanding**: \<any parked Tier-3 item awaiting approval; any open
> question the operator hasn't answered yet> — never silently drop these.
> Start with the normal session-start read (root `CLAUDE.md` →
> `docs/CLAUDE-RULES-CANONICAL.md` → `ROADMAP.md` → the coordination board)
> before touching anything — a resumed-from-summary session is still a new
> session for that purpose.

Keep it short — the durable record carries the detail; a bloated handoff
prompt just re-creates the compaction problem one level up. Deliver this
**as the session's final chat message**, not buried in a file only — the
operator needs to be able to copy it straight into a new session.

## What this skill does NOT do

- It does not invent a fake token-budget number to poll against — this
  harness gives no reliable token-count introspection to the main loop, so
  the triggers in Step 1 are structural/observable signals, not a counter.
- It does not override an explicit operator instruction to keep going in
  this session — Step 1's triggers are a recommendation this skill surfaces
  proactively; an operator "keep going" overrides them for that session.
- It does not replace `sprint-format`, `doc-freshness`, or
  `session-coordination` — it orchestrates calling them at the right moment,
  it doesn't restate their content.
- It does not decompose a large task up front — that's `delegate-work`.

## Composes with

- **`delegate-work`** — the parallel/space-axis counterpart; see "The core
  distinction" above.
- **`session-coordination`** — reuse its board `✅ DONE` step in Step 2.
- **`sprint-format`** — the write-up format for the closing record.
- **`doc-freshness`** — session-end contradiction sweep, run in Step 2.
- **`research-driver`** — the class of session most likely to trigger this
  (open-ended, naturally long); its Step 7 hourly-ping cadence and this
  skill's Step 1 triggers are complementary, not the same check.
- **`full-system-audit`** — already explicitly multi-session; this skill is
  the mechanism for handing off between its phases/workstreams.
