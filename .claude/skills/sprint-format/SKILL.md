---
name: sprint-format
description: Write a sprint log in the canonical format for the ICT bot. Use when closing out a sprint, when the operator says "log this sprint" / "write the sprint log" / "wrap up", or when you need the mandatory section list and where logs live. Wraps docs/SPRINT-LOG-TEMPLATE-CANONICAL.md — every new log goes under docs/sprint-logs/<SPRINT_ID>.md and must report verified reality, not PR intent. NOT for the roadmap status table (that's ROADMAP.md) — the sprint log is the per-session execution record that the roadmap summarizes.
---

# /sprint-format — write a canonical sprint log

A sprint log is the per-session execution record. ROADMAP.md summarizes
it in one row; this file is the detail. The format is mandatory and
uniform so any future session can resume from repo state alone.

**The contract:** logs describe **verified reality, not PR intent.** "Ran
102 tests, all pass" — not "added tests." "Confirmed via diag relay the
new sl_std_mult is live" — not "should be live after deploy." If you
didn't verify something, say so in *Gaps not yet verified*.

## Where it goes

`docs/sprint-logs/<SPRINT_ID>.md`. Copy `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`
and fill it in. Older `docs/sprint-summaries/` and `docs/sprint-plans/`
formats are historical record only — do not add new files there.

## Sprint ID convention

- Numeric: `S-NNN`, monotonic across the whole repo, never reused (a
  cancelled sprint keeps its number; annotate `SUPERSEDED by S-MMM`).
- Themed tracks: `S-AI-WSN` (+ `-A`/`-FU`/`-PART-N` for sub-sprints),
  `S-REFACTOR-SN` (M11), `S-STRAT-IMPROVE-SN` (strategy program). Append
  the date for the strategy/themed logs: `S-STRAT-IMPROVE-S9-2026-05-24.md`.
- Before picking a new numeric id, grep the repo for the highest `S-NNN`
  in use (docs, code, tests) and take +1.

## Mandatory sections (from the canonical template)

1. **Date Range** — Start / End.
2. **Objective** — primary goal + secondary goals.
3. **Tier** — 1 / 2 / 3 + a one-line justification (per CLAUDE-RULES-CANONICAL.md).
4. **Starting Context** — active roadmap items, prior sprint, known risks.
5. **Repo State Checked** — branch/commit reviewed, deployment state,
   canonical docs reviewed.
6. **Files and Systems Inspected** — code, config, deploy files, docs,
   services/timers, GitHub Actions. (The Code-First Verification Rule
   wants this concrete — list real paths.)
7. **Work Completed** — itemized.
8. **Validation Performed** — tests run, dry-runs/staging, manual code
   verification, **gaps not yet verified**.
9. **Documentation Updated** — rules / architecture / TRADE-PIPELINE /
   roadmap / GitHub Actions / subsystem docs / historical-marked.
10. **Contradictions or Drift Found** — including ones you didn't cause.
11. **Risks and Follow-Ups** — technical risks, Tier-3 product decisions,
    blockers.
12. **Deferred Items.**
13. **Next Recommended Sprint** — suggestion + why + required verification.
14. **Wrap-Up Check** — the 7-item checklist (code inspected directly,
    docs reviewed/updated, TRADE-PIPELINE updated if a pipeline stage
    changed, roadmap checked, contradictions recorded, unknowns stated).

## How this composes with session close-out

The sprint log is part of — not a substitute for — the session-end
reconciliation pass (CLAUDE-RULES-CANONICAL.md § Session-end). At close:

1. Write/finish the sprint log here.
2. Update ROADMAP.md (the centralized record) with the sprint's status row.
3. Run the **`doc-freshness`** skill; fix Tier-1 contradictions, log
   minor leftovers to `docs/claude/health-review-backlog.json`.

If the session is closing because it's run long and is handing off to a
fresh session rather than continuing (see **`session-handoff`**), this log
IS the "durable record" that skill's handoff prompt points at — write it
before producing that prompt, not after.

A sprint is **not done** when code lands on `main` — it's done when the
change is active in production (Ship-Autonomously Rule) and the
documentation review (part of the definition of done) is complete.

## Quality bar

- Cite what you actually checked (SHAs, PR numbers, test counts, diag
  outputs, file:line). Vague logs are the landmine the next session
  steps on.
- Don't claim a state you didn't observe. On a live trading system,
  "I need to verify X" in *Gaps* beats a confident wrong "done."
- Reference recent logs as worked examples:
  `docs/sprint-logs/S-STRAT-IMPROVE-S9-2026-05-24.md`,
  `S-AUDIT-PIPELINE-2026-05-17.md`, `S-TRAINER-BT-1.md`.
