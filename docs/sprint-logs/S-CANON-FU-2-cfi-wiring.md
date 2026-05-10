# Sprint Log: S-CANON-FU-2-cfi-wiring

## Date Range
- Start: 2026-05-10
- End: 2026-05-10 (DRAFT — pending operator ack before merge)

## Objective
- Primary goal: wire `src/runtime/closed_flat_invariant.check()`
  into the runtime tick loop, gated by env var
  `CLOSED_FLAT_INVARIANT_ENABLED` (default false), in alert-only
  mode per the Phase-1 contract in
  `docs/claude/closed-flat-invariant.md`.
- Secondary goals: pin the call-site behavior with a unit test;
  update the design memo to record the wiring point.

## Tier
- Tier 2 (touches the live-order tick loop).
- Justification: edit lands inside `run_monitor_tick` in
  `src/runtime/order_monitor.py`, which is on the live-order path
  per `docs/CLAUDE-RULES-CANONICAL.md` § Tier 2 examples. Submit as
  DRAFT pending operator ack; the env gate keeps the change
  inert on production until the operator flips the var on the VM.

## Starting Context
- Active roadmap items: S-067 follow-up #3 (closed → exchange-flat
  invariant). PR #658 shipped the module + tests + memo. PR #672
  shipped the wiring helper `src/runtime/_closed_flat_wiring.py`
  + helper-level tests. The 3-line call-site edit was deferred to
  a follow-up because the autonomous session that filed PR #672
  did not have local clone access for a small change to a 100KB
  file.
- Prior sprint reference: PRs #658, #665 (S-CFI-FIX), #672.
- Known risks at start: any edit to `run_monitor_tick` is on the
  live-order path; the edit must be inert by default and never
  raise.

## Repo State Checked
- Branch or commit reviewed: `claude/post-canon-followups-3Ykp2`
  off `main` at `39e3c28`.
- Deployment state reviewed: production trader still has
  `CLOSED_FLAT_INVARIANT_ENABLED` unset; the helper is a no-op
  there once this PR merges, until the operator flips it.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/claude/closed-flat-invariant.md`,
  `docs/claude/closed-flat-invariant-phase2-wiring.md`.

## Files and Systems Inspected
- Code files inspected: `src/runtime/closed_flat_invariant.py`,
  `src/runtime/_closed_flat_wiring.py`,
  `src/runtime/order_monitor.py` (around `run_monitor_tick`,
  `_reconcile_orphan_positions`, end-of-tick), `src/main.py`
  (tick loop where `run_monitor_tick` is invoked).
- Config files inspected: `deploy/`, `config/`, `.env.example` —
  confirmed no `CLOSED_FLAT_INVARIANT_ENABLED` references (so
  default-false stays default-false on staging and prod until the
  operator flips it manually on the VM).
- Deployment files inspected: `deploy/*.service`, `deploy/*.timer`
  — none touched.
- Docs inspected: `docs/claude/closed-flat-invariant.md`,
  `docs/claude/closed-flat-invariant-phase2-wiring.md`.
- Services or timers inspected: `ict-trader-live.service` (call
  chain consumer of `run_monitor_tick`, not edited).
- GitHub Actions workflows inspected: none touched.

## Work Completed
- Applied the documented 3-line wiring patch to
  `src/runtime/order_monitor.py::run_monitor_tick`, immediately
  after the orphan-position reconciler block and before
  `return summaries`. The wiring is the existing
  `maybe_run_closed_flat_check(db, summaries)` call; it reads the
  env gate, builds the resolver, calls
  `closed_flat_invariant.check(...)`, and never raises.
- Added `tests/test_closed_flat_wiring_call_site.py` with two
  tests:
  - env-off (default) → `closed_flat_invariant.check` is NOT
    called from `run_monitor_tick`.
  - env-on → `check` is called exactly once with a callable
    resolver that returns the cfg dict for known account ids and
    `None` for unknown ones.
- Updated `docs/claude/closed-flat-invariant.md` with a "Wiring
  applied" subsection documenting the call site, test coverage,
  and rollback steps.

## Validation Performed
- Tests run (locally):
  - `tests/test_closed_flat_wiring.py` (helper-level, 6 tests) — passed.
  - `tests/test_closed_flat_wiring_call_site.py` (new, 2 tests) — passed.
  - `tests/test_closed_flat_invariant.py` (Phase-1 module, 21 tests) — passed.
  - `tests/test_s030_pr3_monitor_loop.py` (run_monitor_tick
    end-to-end, 15 tests) — passed.
- Dry-runs or staging checks: n/a — env gate keeps the call inert
  by default. The operator's 7-day soak with
  `CLOSED_FLAT_INVARIANT_ENABLED=true` on the VM is the post-merge
  validation per `docs/claude/closed-flat-invariant.md` § Soak plan.
- Manual code verification: `git diff origin/main..HEAD` shows
  exactly the 3 expected files changed for this task (helper
  module is unchanged; only `order_monitor.py`, the new test,
  and the design memo). `ruff check` clean on touched files.
- Gaps not yet verified: live-tick observation under
  `CLOSED_FLAT_INVARIANT_ENABLED=true` is the operator-driven 7-day
  soak; not pre-merge.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Roadmap updates: append a row to the Historical Sprint Ledger.
- GitHub Actions doc updates: none.
- Subsystem doc updates: `docs/claude/closed-flat-invariant.md`
  ("Wiring applied" subsection).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: none.
- Code/doc mismatch: pre-edit, the design memo claimed the wiring
  was deferred to a separate PR; that statement is now reconciled
  by the new "Wiring applied" subsection.

## Risks and Follow-Ups
- Remaining technical risks: a violation alert fired during the
  soak window means the close path has bugs to fix — the alert is
  the desired observability signal, but the underlying close path
  needs investigation if the count is non-zero.
- Remaining product decisions (Tier 3): the auto-flatten promotion
  PR (Phase-3 of S-067 #3) requires a per-account
  `closed_flat_auto_flatten` config flag and operator approval
  after the soak completes cleanly.
- Blockers: pre-merge — operator ack on this DRAFT PR. Post-merge —
  none; the env stays unset until the operator flips it.

## Deferred Items
- Phase-3 auto-flatten promotion PR — filed for after the 7-day
  alert-only soak per the doc.

## Next Recommended Sprint
- Suggested next sprint: monitor the soak for 7 days; then file
  the auto-flatten promotion PR if the violation log stays empty.
- Why next: completes the original S-067 follow-up #3 contract.
- Required verification before starting: 7 days of clean
  `runtime_logs/invariant_violations.jsonl`.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
