# Sprint S0 Summary — Workflow Foundation (M-S0)

**Date:** 2026-05-06
**Branch:** `claude/workflow-foundation-40FN3` (work) + `claude/plan-next-sprint-wCr1L` (closure)
**Milestone:** M-S0 — Workflow Foundation (Phase 0 — Foundation & Workflow)
**Checkpoints:** `CP-2026-05-06-S0-01` (work landed) → `CP-2026-05-06-S0-02` (milestone closure)

---

## PRs

| PR | Title | Status |
|---|---|---|
| #412 | S0: Workflow Foundation — workplan, operating protocol, decomposition rules | Merged |
| (closure) | M-S0 closure: sprint summary + ROADMAP/milestone-state flip | This PR |

---

## Deliverables

| File / Doc | Change | Purpose |
|---|---|---|
| `docs/workplan.md` | new | Master workplan: goal, current priorities (system hardening + visibility, web app near-term, prop-trading deferred), six core operating principles, milestone/session system definition, three-tier merge authority, VM/operator action rules with pre-filled canonical values. |
| `docs/claude/milestone-state.md` | new | Central milestone/session state file. Single quick-glance answer to "where is the program right now?" — Active milestone, Recently closed, Queued, Standing/recurring, Open blockers, Update protocol. |
| `docs/claude/operating-protocol.md` | new | Consolidated Claude operating protocol: four standing principles, session shape (start/middle/end), three-tier merge authority, live-mode invariant, ping-PR vs work-PR separation, VM/operator-action rules, compute-delegation table. |
| `docs/claude/decomposition-rules.md` | new | Normative milestone → sprint → checkpoint contract. Three layers, milestone types/sizing/closure, sprint sizing/mandatory sections/closure, checkpoint ID convention/contents/sizing, decomposition flowchart, worked example (M-S0 itself). |
| `README.md` | updated | Added "Workflow source of truth" table near the top, linking the seven foundational docs in read order. |
| `docs/claude/INDEX.md` | updated | Added "Workflow foundation (M-S0, 2026-05-06)" section at the top of the file list. |
| `ROADMAP.md` | updated | Added S0 row under Phase 0; flipped to ✅ Done in this closure PR. |
| `docs/claude/checkpoints/CHECKPOINT_LOG.md` | updated | `CP-2026-05-06-S0-01` (work) + `CP-2026-05-06-S0-02` (closure) handoff entries. |

---

## Tests added

None — docs-only sprint. Verification:

- `python scripts/secret_scan.py` → pass.
- `python scripts/repo_inventory.py` → pass.
- `PYTHONPATH=. pytest --collect-only -q tests` → 1728 tests collected; 45 pre-existing collection errors (`ModuleNotFoundError: No module named 'yaml'`) unrelated to this docs-only patch.

No production / runtime code touched.

---

## Architecture note

This sprint produced **the rules everything else now follows**, not new code. All four new docs are normative — future sprint prompts that don't conform get revised, not the rules. The decomposition contract (M-S0 itself was the worked example) makes the milestone → sprint → checkpoint shape explicit so future sessions can resume from repo state without ambiguity.

The milestone-state file is the new "where are we?" pointer that sits **between** `CHECKPOINT_LOG.md` (tactical resume) and `ROADMAP.md` (long-arc view). Closing checkpoints update it; opening sessions read it.

---

## Milestone closure (M-S0)

Per `docs/claude/decomposition-rules.md` § 2.4:

1. ✅ Sprint summary doc — this file.
2. ✅ ROADMAP row flipped to ✅ Done (Phase 0).
3. ✅ Milestone-state.md: M-S0 moved Active → Recently closed; S-014 (Web Client V1) pulled into Active.
4. ✅ Closing checkpoint titled `CP-2026-05-06-S0-02 — MILESTONE COMPLETE: M-S0`.

---

## Deferred items

None for M-S0. Two `CLAUDE.md` improvements proposed for the next sprint (see *Lessons learned* below + closing checkpoint).

---

## Lessons learned

1. **Foundation docs land cleaner as their own milestone, not folded into a feature sprint.** Splitting M-S0 from S-014 (Web Client V1) meant the rules everyone follows didn't compete with feature LOC for review attention. Future PMs: when foundational docs are needed, give them their own milestone slot.
2. **The closing checkpoint is the right place to flip ROADMAP + milestone-state, not the work checkpoint.** Keeping `CP-S0-01` (work) and `CP-S0-02` (closure) as separate PRs gave the foundation docs a clean reviewable diff and the closure ceremony its own atomic state move.
3. **`milestone-state.md` answers "what's next?" faster than scanning the checkpoint log.** First read for any new session: `CHECKPOINT_LOG.md` top entry → `milestone-state.md` Active block. The two together replace the previous practice of re-deriving sprint context from the sprint plan + recent commits.

---

## Next milestone

**S-014 — Web Client V1 (Home Dashboard)** — Phase 4. Browser client over the S-013 backend (FastAPI + JWT). Stack: HTMX + Jinja2 + Chart.js (no Node toolchain). Spec: `docs/sprints/sprint-014-prompt.md`. Eight PRs ≤ 400 LOC each, loopback-only hosting, read-only home dashboard.
