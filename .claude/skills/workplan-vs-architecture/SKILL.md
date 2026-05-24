---
name: workplan-vs-architecture
description: Reconcile what the project INTENDED to build (the operator's workplan/goals + ROADMAP milestones) against what is ACTUALLY built (ARCHITECTURE-CANONICAL.md + the code/config on disk). Use when the operator asks "are we building what we set out to build?", "what drifted from the plan?", "does the architecture match the roadmap?", or during a periodic governance/audit pass. Produces a drift report: intent items with no implementation, implemented subsystems the plan never described, and stale doc claims. NOT a code review (use `review`) and NOT a runtime health check (use `health-review`).
---

# /workplan-vs-architecture — reconcile intent against reality

This skill answers one question: **does what we're building match what we
set out to build?** It compares three layers and reports the gaps.

| Layer | Source of truth | What it tells you |
|---|---|---|
| **Intent** | the operator's goals + ROADMAP.md milestones/principles; historical `docs/claude/workplan.md` for original framing | what we *meant* to build and the rules for building it |
| **Design** | `docs/ARCHITECTURE-CANONICAL.md` (+ `docs/architecture/*`) | how the system is *described* to be built |
| **Reality** | the code/config on disk (`src/`, `config/`, `deploy/`, `.github/workflows/`) | what is *actually* built and running |

Drift is any disagreement between two adjacent layers. The fix direction
follows the precedence rules below — you never "fix reality to match a
stale doc."

## Authority & precedence (do not get this backwards)

Per CLAUDE.md instruction hierarchy and CLAUDE-RULES-CANONICAL.md
§ Document Priority:

1. `docs/CLAUDE-RULES-CANONICAL.md`
2. `docs/ARCHITECTURE-CANONICAL.md`
3. `ROADMAP.md` (the centralized milestone/sprint record)
4. current sprint log
5. skills
6. `CLAUDE.md`
7. `docs/claude/*` and historical notes

**`docs/claude/workplan.md` is historical** (superseded 2026-05-10 by the
canonical set). Read it for the original *intent* and operating
principles (safety-before-expansion, repo-as-truth, visibility,
autonomy), but it is **not** authoritative on sequencing or policy — the
canonical docs win. If the historical workplan and a canonical doc
disagree, the canonical doc is right; do not "reconcile toward" the
workplan.

**Field beats comment** (CLAUDE-RULES-CANONICAL.md § Documentation
Hygiene): when a YAML field / config constant / code symbol disagrees
with surrounding prose or a non-canonical note, the field is the truth —
fix the prose, never flip the field on inference. (This is the PR #1358
lesson.) The one exception: a doc explicitly marked canonical outranks an
ordinary inline comment.

## The reconciliation pass

1. **Pull intent.** From ROADMAP.md: the milestone table (M0..M11), the
   active queue, the non-negotiable rules, the core principles. From the
   historical workplan: the original goal (portfolio targeting 1–2%
   weekly; safety/visibility/auditability) and operating principles.
2. **Pull design.** From ARCHITECTURE-CANONICAL.md: subsystem boundaries,
   the trade pipeline, the comms pipeline, the two-VM topology, the Mode
   Mutation Contract, the Change log, and the **Known gaps** section.
3. **Pull reality.** Spot-check the code/config the design names — does
   `config/strategies.yaml` carry the roster the roadmap claims? Does the
   execution gate (`mode:` + `execution:`) match the Prime Directive? Do
   the services in `deploy/` match the architecture's service list?
4. **Diff and classify each finding:**
   - **Intent → no design/reality** — a planned milestone/principle with
     no implementation (e.g. a deferred feature). Expected for
     `NOT STARTED`/`IN PROGRESS` rows; flag only if marked done.
   - **Reality → no intent** — a shipped subsystem the plan never
     described. Either the roadmap is stale (update it) or scope crept
     (raise it).
   - **Design ≠ reality (stale doc)** — the architecture describes
     something the code no longer does, or vice versa. Per the rules, the
     code is truth — update the doc (Tier-1) unless the doc is canonical
     and the code is the bug (then it's a Tier-2/3 finding for the
     operator).
   - **Intent ≠ design (governance drift)** — the roadmap says one thing,
     the architecture another. This is the highest-priority class:
     surface it for an explicit decision; don't silently pick a side.

## Output — the drift report

A short structured report, not prose sprawl:

- **Aligned:** the milestones/subsystems where intent, design, and
  reality agree (one line each — confirms the spine is sound).
- **Drift:** each finding with its class (above), the two sources that
  disagree, the file/line evidence, and the fix direction + tier.
- **Recommended actions:** Tier-1 doc fixes you'll make in this pass;
  Tier-2/3 items to raise with the operator; backlog items to log to
  `docs/claude/health-review-backlog.json`.

## Fix what you can, log the rest

This skill is allowed to make **Tier-1 doc reconciliations in place**
(update a stale architecture/roadmap line to match verified reality).
Anything that needs a code/config change, or that is a governance
decision between intent and design, is flagged for the operator — not
actioned. Close the pass by running the **`doc-freshness`** skill to
catch any contradiction the manual diff missed, and log minor leftovers
to the health-review backlog so a future review drains them.
