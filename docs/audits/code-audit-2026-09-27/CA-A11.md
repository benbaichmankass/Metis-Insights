# CA-A11 — Code audit Wave A: scripts/ops part 1

Lane: CA-A11 (dispatched by the manager, session_01KAQRJxRbRwpYTkPgBjNVyQ, under the
operator's 2026-09-27 code-audit decision, checklist row `CA`). Model: Sonnet.
Ceiling: $40.

**Scope, as stated on the checklist row:** "scripts/ops/ files 1–166 in `ls scripts/ops`
order (from `_backlog.py` through `manifest_training_staleness.py`), EXCLUDING
`*_action.sh` (CA-A10)."

**Scope-boundary note (process, not a code finding):** the row's numeric endpoint
("166") is off by one against its own named boundary. `ls scripts/ops | sort` puts
`manifest_training_staleness.py` at line 165 and `mark_netted_duplicate_pnl.py` at
line 166 — but CA-A12's row independently states its own start as "from
`mark_netted_duplicate_pnl.py` to the end." The two rows' **named files** partition
the directory with no gap and no overlap; only the printed line numbers disagree by
one. I used the named files as ground truth: **this lane's scope is lines 1–165**
(`_backlog.py` through `manifest_training_staleness.py`), excluding `*_action.sh`.
That yields **137 files** (105 `.py`, 32 `.sh`).

## Findings table (most severe first)

| id | axis | severity | one-line claim |
|---|---|---|---|
| AUD-...-backfill-monitor-sign-guard | trading-correctness | **high** | `backfill_monitor_closed_pnl.py` lacks the sign-consistency guard its sibling has, against a known fabricated-PnL failure mode, on a script dispatchable via system-actions |
| AUD-...-a8-already-ran | operating-model | **high** | CLAUDE.md's "archived rows NOT imported, A8 not run" claim is false — A8 has already run |
| AUD-...-pipeline-store-desc-stale | operating-model | **high** | CLAUDE.md/CLAUDE-RULES-CANONICAL/manager-SKILL describe the pipeline store as a flat `PIPELINE.jsonl` file that doesn't exist; the real store is a directory, and no CLI verb files a new record |
| AUD-...-retired-register-cluster | operating-model | **high** | ~18 scripts/ops files still target 2026-09-21-retired paths; 2 (`manager_preflight.py`, `blocked_lane_watch.py`) are provably broken today |
| AUD-...-commit-work-decisions-dead | operating-model | medium | `commit_work_decisions.py` targets an archived directory; the API route it feeds was already patched to acknowledge this, the script wasn't |
| AUD-...-demote-budget-orphaned | operating-model | medium | `demote_budget.py` is built, self-tested, and has zero call sites and a missing input register |
| AUD-...-backfill-pnl-nulls-docstring | provenance | medium | `backfill_pnl_nulls.py`'s docstring claims a write path the code doesn't use (currently harmless) |
| AUD-...-exit-path-coverage-not-ci-wired | ci-guards | low | `exit_path_coverage.py` has strong self-test coverage but isn't CI-wired, unlike its sibling |
| AUD-...-leg-flow-report-docstring-path | operating-model | low | Cosmetic wrong-path reference in a docstring |
| AUD-...-check-allow-degraded-escape | ci-guards | low | Cosmetic invalid-escape-sequence deprecation warning |
| AUD-...-notes-truncation-pattern | provenance | low | Blind JSON-string-slice truncation pattern recurs across several notes-writing scripts (design smell, not confirmed live corruption) |

Full records with evidence/expected/actual/population/detector/proposed_fix:
[`CA-A11.findings.jsonl`](./CA-A11.findings.jsonl) (11 records, schema per
`docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` §5).

**Pipeline filing:** all 4 HIGH findings filed immediately as pipeline items —
`PI-20260927-MJX1ZKYC-0001` (backfill guard gap), `PI-20260927-MJX1ZKYC-0002`
(pipeline-doc drift, covers both the A8 and store-description claims),
`PI-20260927-MJX1ZKYC-0003` (retired-register cluster, also covers the
`commit_work_decisions.py` MEDIUM as the same root cause). Verified clean with
`python3 scripts/ops/pipeline.py --check` after filing (only the 3 pre-existing
grandfathered `PI-20260921-0002` collisions remain, unrelated to this lane's writes).

## Method

Per the audit's standing method (read contract → check against tests/call-sites →
plant-a-defect-or-craft-an-input where cheapest → read live state only where nothing
else settles it; never grade a claim with the system that produced it). This lane had
no live-VM/broker access from its sandbox, so no script's write path was exercised
against real production data — only against local test fixtures, the (empty, 0-table)
local `trade_journal.db`, and read-only `--self-test`/`--check`/`--dry-run` runs
against the real committed `config/`, `docs/claude/work/`, and `.github/workflows/`
trees. Two scripts (`exit_mechanics_audit.py`, `dead_leg_audit.py`) were directly
observed refusing to report "clean" against the empty local DB rather than collapsing
that into a false-positive — treated as a positive data point about their own
collapsed-state discipline, not as a finding.

Work was split three ways: this lane read the money-at-risk-first cluster (money
gating: `mandate_resolver.py`) plus the pipeline-filing mechanism itself firsthand,
and dispatched two focused sub-agents in parallel — one over the 38 files with the
most direct real-money write/reconcile/flatten/grade surface, one a broad triage
of the remaining ~99 files prioritizing scheduled-workflow wiring and retired-register
references. Every finding below was independently spot-checked by this lane (not
only trusted from a sub-agent's report) before being filed: the CRITICAL-shaped claim
about A8 having already run was independently re-run (`--dry-run` reproduced the
exact counts; a sampled record's `origin.ref` was read directly); the
`backfill_monitor_closed_pnl.py` guard gap was independently re-read line-by-line
against its sibling; `manager_preflight.py --self-test`'s failure and
`git cat-file`'s failure against `SESSIONS.json` were reproduced directly by this
lane, not only quoted from the sub-agent.

Tests: 559+ tests run across the two sub-agents' scopes plus this lane's own targeted
runs (`tests/test_mandate_resolver.py` 56/56, plus the two combined runs the triage
agent reported: 300 passed/1 skipped, and 259 passed/1 skipped). All green except
the two directly-executed self-test failures reported above, which are findings, not
test-suite failures (they are standalone `--self-test` invocations, not pytest).

## Coverage — both ways, honestly

**Reading coverage** (of the 137 files in scope):
- **Read in full:** ~55 files — the 38-file money-path cluster (all read completely
  by the dedicated sub-agent) plus 17 files read completely by the triage sub-agent
  (`import_archived_registers.py`, the four `manager_*.py` files, `checklist_fill_missing_state.py`,
  `checklist_routing_age.py`, `claim_merge_slot.py`, `document_index.py`,
  `commit_work_decisions.py`, `_lib.sh`, and the three enable/disable `.sh` pairs).
  This lane itself also read `pipeline.py`'s relevant sections directly (not counted
  against the 137, since it is out of this lane's file scope, but its interface was
  exercised directly: `--help`, `--check`, `--stats`, `--mint-id`, and `append()`
  called live three times).
- **Read partially** (specific functions, not the whole file): ~9 files, including
  `manager_preflight.py` (1016 of 1705 lines plus the self-test section),
  `blocked_lane_watch.py` (world-builder + path-control functions), `handoff_check.py`,
  `in_flight_owner_liveness.py`, `lane_reconcile.py`.
- **Skimmed** (docstring/header + wiring comment only): ~27 files.
- **Not opened at all** (basename grepped against workflows/`run_guards.py` only): ~49
  files — mostly one-shot fetch/install/`m15_*` research scripts. Both of the scope-wide
  greps this lane relied on to clear this group (hardcoded-secret literals; references
  to retired-register paths) **did** run across every file in this group, including
  the unopened ones, and found zero hits in it for either pattern — but their internal
  logic, defect classes beyond those two greps, and test coverage were not otherwise
  checked.

Total: **≈55 full, ≈9 partial, ≈27 skimmed, ≈49 not opened**, out of 137 — stated as
counts, not asserted as "everything was read."

**Behavioral coverage** (capabilities actually exercised against real state, not just
read):
- `mandate_resolver.py` run live, twice, against the real `config/mandates.yaml` — both
  runs' outputs matched CLAUDE.md's claims about mandate state exactly (13 mandates,
  MD-DEMOTE-S2-S1 the sole autoland entry, MD-PROMOTE-S1-S2's `blocked_until` clause
  (a)/(b) split, zero mandates ever actually fired per `comms/mandate_firings/`
  being absent). **Verified-non-issue**, stated as a real check, not skipped.
- `import_archived_registers.py --dry-run` run live twice (once by a sub-agent, once
  independently by this lane) against the real archived registers and the real
  pipeline store — this is what surfaced the A8-already-ran finding.
- `pipeline.py --check`/`--stats`/`--mint-id` run live against the real store
  (1,686→1,689 records over the course of this lane's own 3 filing writes).
- `manager_preflight.py --self-test`, `manager_lease.py --self-test`,
  `manager_state_watch.py --self-test`, `manager_view.py --self-test` all run live.
- `column_provenance.py`, `exit_lever_map.py`, `exit_mechanism_coverage.py`,
  `lever_evidence_flag.py`, `lever_reachability_audit.py`, `dashboard_edge_watch.py`,
  `bybit_bracket_audit.py`, `journal_venue_audit.py`, `dead_leg_audit.py`,
  `grade_settlement_soak.py`, `broker_bracket_reconcile.py` all had their `--self-test`
  run live against real repo config, all passing (see the money-path sub-agent's
  per-file notes for exact pass counts).
- No script's real DB-write path (`--apply`) was exercised against production data —
  this lane had no live-VM/broker access, and running `--apply` against the empty
  local `trade_journal.db` would prove nothing about correctness (the guard-checked
  code paths were read instead, which is how the `backfill_monitor_closed_pnl.py`
  finding was found — by reading, not by running a mutating command).

## What remains unsettled, and what would settle it

1. **`check_allow_degraded.py`'s escape-sequence warning** (LOW) was seen once in a
   sub-agent's pytest output but not independently re-run by this lane before filing
   — recorded as PLAUSIBLE, not independently CONFIRMED. Settling it: re-run
   `python3 -W error::DeprecationWarning -c "import scripts.ops.check_allow_degraded"`
   and confirm the exact warning text and line.
2. **The exact commit that ran A8** could not be pinpointed — this container's clone
   is shallow (50 commits; the SHALLOW CLONE system notice applies). The finding does
   not depend on pinpointing it (the current on-disk state is conclusive), but a
   session with full history could name the exact PR.
3. **~49 files were not opened** (listed in the triage sub-agent's coverage section,
   reproduced above in aggregate). Two scope-wide greps (secrets, retired-path
   references) covered all of them and found nothing; no other defect class was
   checked in that subset. Settling it: a follow-up pass reading those ~49 files in
   full — mostly `fetch_*`, `install_*`, `m15_*` one-shot scripts, judged lower
   priority than the money-path and operating-model clusters within this lane's $40
   ceiling.
4. **Whether `manager_preflight.py`/`blocked_lane_watch.py` are meant to survive** as
   future manager tooling (re-point their fixtures) or should be archived with the
   registers they read is a judgment call for the manager/operator, not decided here
   — the pipeline item (`PI-20260927-MJX1ZKYC-0003`) states both options.

## Landing

Docs-only PR (this lane only finds; it does not fix `src/`, `config/`, `deploy/`,
`.github/workflows/`, `scripts/`, or `tests/`). Branch `claude/ca-a11-audit`, Tier-1
self-land per the recipe already in use by PR #13156 (`.github/pr-landing/`,
`.github/merge-slots/`, `.github/pr-automerge-requests/`).
