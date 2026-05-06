# Sprint S-041 — Verify-before-trusting-done: workplan reconciliation sweep

**Sprint type:** Roadmap (docs-only) | **Risk tier:** Tier 1 (all self-merge)
**Created:** 2026-05-06 | **Branch:** `claude/reconcile-sprint-workplan-CBUWc`
**Predecessors:** CP-2026-05-06-10-workplan-clarification (PR #429)

## 1. Goal

Bring all planning-surface documents into conformance with `docs/claude/workplan.md`.
The workplan established M0..M10 as the canonical milestone roadmap on 2026-05-06;
three downstream docs still use the old M-S-NNN / Phase 0–4 framing and several
sprint prompts carry stale or unverified "done" status. This sprint audits each
doc against the workplan, verifies on-disk state before accepting any "done" label,
and updates or annotates each doc to match the workplan structure. No `src/`,
`tests/`, `config/`, `deploy/`, or `scripts/` changes — docs-only reconciliation
sprint executed per the verify-before-trusting-done principle.

## 2. Dependencies

- **Sprint dependency:** CP-2026-05-06-10-workplan-clarification merged on `main` ✅
- **Sprint dependency:** `docs/claude/workplan.md` canonical (PRs #428, #429) ✅
- **Operator hold — do NOT open:** S-015 pause/continue Tier 2 decision PR.
- **Operator hold — do NOT open:** 5m/1h timeframe enforcement Tier 3 PR.
- **Infra:** No VM actions required. All work is repo-level docs only.

## 3. Deliverables

1. `docs/sprints/sprint-041-prompt.md` — this file (T0).
2. `docs/claude/milestone-state.md` — rewritten to M0..M10 framing; on-disk state
   verified for each milestone before any "done" label is accepted (T1).
3. `ROADMAP.md` — M0..M10 section added mirroring the workplan table; old Phase 0–4
   sprint ledger preserved as "Historical Sprint Ledger" (T2).
4. Status headers on stale sprint prompts — `sprint-015`, `sprint-017`, `sprint-020`,
   `sprint-021` each annotated with workplan milestone mapping and
   done / in-flight / superseded verdict; no file deleted (T3).
5. `docs/sprint-summaries/sprint-041-summary.md` — PR list, checkpoint IDs,
   deferred items (T4).
6. `docs/claude/checkpoints/CHECKPOINT_LOG.md` — kickoff entry CP-2026-05-06-11 (T0)
   + closing entry CP-2026-05-06-12 (T4).

## 4. Checkpoints

| # | Checkpoint title | What completes by then | Risk class | Wall-clock | Gates |
|---|---|---|---|---|---|
| T0 | Kickoff — sprint prompt + checkpoint | `sprint-041-prompt.md` committed; `CP-2026-05-06-11-s041-kickoff` prepended to CHECKPOINT_LOG; PR self-merged | docs-only | ≤ 30 min | T1 |
| T1 | `milestone-state.md` reconciliation | On-disk state verified for M0..M10; `milestone-state.md` rewritten to M0..M10 framing with accurate status per milestone; operator holds and blockers recorded; PR self-merged | docs-only | ≤ 45 min | T2 |
| T2 | `ROADMAP.md` reconciliation | `ROADMAP.md` restructured — M0..M10 table at the top, old Phase 0–4 sprint ledger preserved under "Historical Sprint Ledger"; PR self-merged | docs-only | ≤ 45 min | T3 |
| T3 | Sprint prompt audit | Status headers added to sprint-015/017/020/021; each annotated with workplan milestone mapping + done/in-flight/superseded verdict; no file deleted; PR self-merged | docs-only | ≤ 30 min | T4 |
| T4 | Sprint close | `docs/sprint-summaries/sprint-041-summary.md` created; `CP-2026-05-06-12-s041-complete` prepended to CHECKPOINT_LOG; PR self-merged | docs-only | ≤ 20 min | — |

### 4b. Unit boundary declaration

| Unit | Role in this sprint |
|---|---|
| `src/units/strategies/` | untouched |
| `src/units/accounts/` | untouched |
| `src/data_layer/` (DB unit) | untouched |
| `src/ui/` | untouched |
| `src/runtime/` | untouched |
| `src/bot/` | untouched |
| `src/core/coordinator.py` | untouched |

**Docs-only sprint. No source files, tests, configs, or scripts touched.**

## 5. Risk class & merge model

| PR | Class | Self-merge? |
|---|---|:-:|
| T0 — kickoff (sprint prompt + checkpoint) | docs-only | ✅ |
| T1 — `milestone-state.md` reconciliation | docs-only | ✅ |
| T2 — `ROADMAP.md` reconciliation | docs-only | ✅ |
| T3 — sprint prompt status headers | docs-only | ✅ |
| T4 — sprint close (summary + final CP) | docs-only | ✅ |

Live-mode invariant check: no live-trading code touched across any PR in this sprint.
`scripts/check_dry_run_in_diff.py` clean for all (docs-only diffs). ✅

## 6. Success criteria

- ✅ `docs/claude/milestone-state.md` active section uses M0..M10 labels; each milestone
  has an on-disk-verified status.
- ✅ `ROADMAP.md` contains an "M0..M10 Milestone Roadmap" section mirroring the workplan
  table; old Phase 0–4 sprint ledger preserved under "Historical Sprint Ledger".
- ✅ `sprint-015-prompt.md`, `sprint-017-prompt.md`, `sprint-020-prompt.md`,
  `sprint-021-prompt.md` each have a `> ⚠️ STATUS NOTE` blockquote with workplan
  milestone mapping and verdict.
- ✅ `CHECKPOINT_LOG.md` gains CP-2026-05-06-11-s041-kickoff (T0) and
  CP-2026-05-06-12-s041-complete (T4).
- ✅ `python scripts/secret_scan.py` clean on every PR.
- ✅ No files deleted — consolidation, not deletion.

## 7. Hard guardrails

1. **Docs-only.** Off-limits this sprint: `src/`, `tests/`, `config/`, `deploy/`,
   `scripts/`, `notebooks/`.
2. **No S-015 pause/continue PR.** The S-015 execution pause is an operator hold.
   Do not open a Tier 2 decision PR for it in this sprint.
3. **No 5m/1h timeframe enforcement PR.** Operator hold.
4. **Consolidation, not deletion.** Sprint prompt files stay; only a status header is
   prepended. Historical sprint ledger in ROADMAP.md stays. Unique content in
   `milestone-state.md` update-protocol section stays.
5. **Pings route on `@claude_ict_comms_bot`.** Post-BUG-059 routing — all pings via
   `runtime_logs/pending_claude_pings/`, not via the trader bot.
6. **Dashboard boundary.** The reconciliation notes the conflict between S-013/S-014/S-015
   web work (built in this repo) and the workplan repo boundary rule (dashboard lives in
   separate Vercel repo) — but does not delete existing code or force a migration. That
   decision belongs to a future operator-approved session after the S-015 hold is lifted.

## 8. Hand-off

When this sprint closes (CP-2026-05-06-12-s041-complete):

- `milestone-state.md` shows M0..M10 with on-disk-verified status per milestone.
- `ROADMAP.md` uses M0..M10 structure at the top, historical ledger preserved below.
- Sprint prompts 015/017/020/021 annotated; no content deleted.
- Next active milestone per updated `milestone-state.md`: **M1 — Comms infrastructure**
  (M0 closed; M1 is the next incomplete milestone in workplan sequence; S-015/M6 is
  blocked pending operator hold resolution).

**Known deferred items (do NOT action in this sprint):**
- S-015 pause/continue decision (operator hold).
- 5m/1h timeframe enforcement (operator hold).
- BUG-057 diagnostic: watch `journalctl` for `BUG-057-DIAG` lines after next live
  VWAP rejection on the VM.
- BUG-058 + BUG-059 VM deployment: fixes on `main`; require operator VM `git pull`
  + `ict-claude-bridge.service` restart.
