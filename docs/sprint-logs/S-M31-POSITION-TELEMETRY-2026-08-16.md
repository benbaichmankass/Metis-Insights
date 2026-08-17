# Sprint Log: S-M31-POSITION-TELEMETRY-2026-08-16

## Date Range
- **Start:** 2026-08-16 (overnight M20 session, operator-directed mid-session)
- **End:** 2026-08-16

## Objective
- **Primary:** answer the operator's question — *"look at this trade as a use
  case: what isn't currently being handled properly, and what would
  high-performance infrastructure look like?"* — then land the design, the
  roadmap milestone, and the first two phases.
- **Secondary:** do it without touching a live lever. Every `arm_r` correction
  the work surfaces stays Tier-3 and queued.

## Tier
**Mixed, and separated deliberately.**
- **Tier 1** — the design doc, the roadmap row, the CI guard, the registry file.
- **Tier 2** — the telemetry writer on the live exit loop + the new table.
  **Operator OK given in chat 2026-08-16** ("yes use the table, and go ahead
  with P2").
- **Tier 3** — nothing taken. Three legs measured inert/vol-conditional are
  recorded with `disposition: queued_tier3`, not corrected.

## Starting Context
- Active: the M20 exit-refinement program; coverage 373/376 = 99.2%, evidence
  vintage 22.9%.
- Immediately prior: PR #9588 (merged `9d41a0d`) established that a declared
  `trail_decay_arm_r` can sit above the R the 9.9% TP cap allows, making the
  lever inert — measured across six live legs.
- The operator then asked to generalise from the single live XRP trade to the
  infrastructure question.

## Repo State Checked
- Branch `claude/m31-position-telemetry` off `origin/main` @ `9d41a0d`.
- `M31–M35` confirmed unused in `ROADMAP.md` before claiming M31 (grepped, not
  assumed).
- Read in full before changing: `src/runtime/trail_decay.py`,
  `src/runtime/exit_lever_soak.py`, the `_trail_update` blocks of
  `trend_donchian.py` and `htf_pullback_trend_2h.py`,
  `scripts/ci/check_collapsed_states.py`, `scripts/check_new_table_wiring.py`.

## Files and Systems Inspected
- `src/runtime/trail_decay.py:88–99` — the peak computation (the finding).
- `src/units/strategies/trend_donchian.py:738–746` — the same math, second copy.
- `src/runtime/exit_lever_soak.py:14` — *"Nothing reads it back."*
- `src/web/api/routers/notifications.py` — the banner kinds (no lifecycle kind).
- `src/units/db/database.py` — the lazy-DDL pattern + the `# data-wiring:` marker.
- `src/units/strategies/pairs_engine.py:131` — the only `max_hold_bars` in the repo.

## Work Completed

### 1. The finding — the numbers exist and are discarded
`trail_decay.py` computes `peak_r` (MFE in R), `bars_since_peak`, entry and
sized `risk_per_unit` on **every exit-loop pass, for every open position on the
donchian/pullback family, declared or not.** Then:

* **declared** → collapsed to one boolean (`armed`); the rest discarded.
* **undeclared** → one soak row, only when `bars_since_peak >= 6`, deduped per
  process, into a log whose own docstring says nothing reads it back.

`trend_donchian._giveback_verdict` re-derives the same peak and discards it too.
So a live trade's trajectory is computed roughly every 30 s and thrown away —
which is why answering the operator's question required four diag pulls and a
page of hand arithmetic, and why MFE was **not reconstructible at all**.

**This is a missing-STATE problem, not a missing-capability one.** That framing
is the whole design.

### 2. Design + roadmap
- `docs/design/position-telemetry-DESIGN.md` — six gaps (G1–G6) each tied to a
  file:line, the record shape, four constraints drawn from this repo's own
  scar tissue, a five-phase plan with tiers, an explicit "what this does NOT
  do", and three open questions.
- `ROADMAP.md` → **M31**.

### 3. P1 — the declare-time reachability guard (Tier 1)
`scripts/ci/check_lever_reachability.py` + `config/lever_reachability.json`,
registered in `run_guards.py`.

CI cannot measure a leg, so the guard does not compute reachability. It
enforces that a measurement **exists**, is **current for the value declared**,
and has been **disposed of**. The load-bearing rule is that the registry
`arm_r` must equal `config/strategies.yaml` — **changing an arm without
re-measuring fails CI**, which is the recurrence path and what makes this
expensive to lie to rather than a presence-only marker.

Registry seeded with the honest state, not a clean sheet.

### 4. P2 — the telemetry record (Tier 2, approved)
`trade_journal.db::position_telemetry`, one row per order package, upserted on
each exit pass. `rr_from_here = r_to_target / r_to_stop` is the field that
answers the operator's question; it reads **0.713** on the motivating trade.

Four constraints, each honoured rather than asserted:
1. `since_entry_peak` **extracted** into `trail_decay.py`; `resolve_trail_mult`
   now calls it, so telemetry records the same peak the lever arms on. A test
   asserts `build_record` really calls it.
2. Wrapped in `tick_cost.hook("monitor.position_telemetry")` → `offloop_hooks`
   **from the first commit**.
3. `peak_state` four-state, registered with `collapsed-state-guard`.
4. `peak_r` stamped **ESTIMATED** (bar extremes cannot see an intrabar move).

Two choices not in the design doc:
- **`peak_r` is a running maximum in the upsert.** MFE is one-way; a pass on a
  briefly-shorter window must not walk a recorded peak backwards.
- **`rr_from_here` is `None` once a level is crossed** — a negative leg makes
  the ratio a sign artefact, not a decision input.

## Validation Performed
- **19 new telemetry tests**, incl. the motivating trade reproducing
  `open_R 2.880`, `pct_of_cap 73.4`, `rr_from_here 0.713` from its real numbers;
  all four peak states proven distinguishable; the peak-regression guard.
- **The 23 existing trail-decay / trail-vol tests pass unchanged** — the
  behaviour-preserved check on the `since_entry_peak` extraction. This is the
  load-bearing validation of the refactor and was run before anything else.
- `check_lever_reachability.py --self-test` **10/10**, plus **two plants against
  the REAL config**, both caught and reverted: a changed `arm_r` with a stale
  registry, and a new reach-gate on `ada_pullback_2h` with no entry.
- `collapsed-state-guard` clean at 9 contracts; `ruff` clean;
  `run_guards.py --base main` all relevant guards passed.

### Gaps not yet verified
- **Nothing has been observed on the live VM.** The table is created lazily on
  first access and the writer has never run in production — P2 is verified by
  tests only. The post-state check (does `position_telemetry` populate, and
  what does `offloop_hooks` say the write costs?) is owed after deploy and is
  the first thing the next session should pull.
- The record covers the **donchian/pullback** families only, because those are
  where the peak already exists. Other families will simply have no row — which
  is honest but means an empty query is not evidence of no open trades.
- `bars_held` is the window length, which is bars-since-entry only when the
  window is anchored; on an `unanchored` row it is the frame length. The
  `peak_state` field says which, but a consumer that reads `bars_held` alone
  can misread it. **Not yet fixed.**

## Documentation Updated
- New: `docs/design/position-telemetry-DESIGN.md`, this log.
- `ROADMAP.md` — M31 row.
- `config/lever_reachability.json` — new record file with its own legend.
- Backlog: `BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP` carries
  three measurement updates from this session.

## Contradictions or Drift Found
- **The `exit_lever_soak` docstring is accurate and that is the problem.** It
  says nothing reads it back, and nothing does. M31 P2 is the first consumer-
  shaped answer to it; the soak itself is unchanged.
- **`collapsed-state-guard` rejected my first contract registration**, correctly:
  the producer emitted constants, so the literal states never appeared at the
  emission sites. Fixed by emitting the literals and keeping the constants as
  the imported vocabulary, pinned equal by a test. Recorded because the
  tempting fix was to weaken the declared contract instead.
- **P2 was added to the open PR #9614 after P1's CI had gone green.** One
  milestone, two phases — coherent, but it invalidated the earlier run. Noted
  in the PR body rather than letting the stale green stand.

## Risks and Follow-Ups
- **Technical:** the writer runs on the live exit loop. It is best-effort,
  never raises, and is instrumented — but the June 2026 wedge class is exactly
  "individually cheap, sum unwatched", so the first post-deploy read of
  `offloop_hooks` matters more than the tests do.
- **Tier-3 queued:** `gld_pullback_1d` (inert, 0 of 8 over its complete
  history), `qqq_trend_long_1d` (inert), `xrp_pullback_2h` (vol-conditional) —
  each needs either a live-parity re-sweep of the arm or an explicit
  inert recording. Not taken here.

## Deferred Items
- **P3 readers** (Tier-1): `/api/bot/positions` R-fields, a diag surface, a
  position-lifecycle banner kind, the exit-ladder input. Unblocked once #9614
  merges.
- **P4 backtest↔live MFE parity** (Tier-1): needs the table to accrue first.
  This is the largest item — every bug in the tp-cap family is *"the harness
  measured a book production does not run"*, and there has never been a live
  measurement of the same quantity to check against.
- **P5** (Tier-3): any lever that reads telemetry to change an exit.

## Next Recommended Sprint
**Verify P2 in production, then P3.** Pull `/api/diag/tick_cost` and confirm
`monitor.position_telemetry` appears in `offloop_hooks` with a sane mean, and
query `position_telemetry` for the open book. Only then build readers — a
reader over a table nobody has confirmed populates is how a soak becomes
write-only, which is the exact failure M31 exists to correct.

## Wrap-Up Check
- [x] Code inspected directly (file:line, not recalled)
- [x] Canonical docs reviewed and updated
- [ ] TRADE-PIPELINE updated — **N/A**, no pipeline stage changed
- [x] Roadmap updated (M31 added; M31–M35 verified free first)
- [x] Contradictions recorded, including my own
- [x] Unknowns stated rather than smoothed over — see *Gaps not yet verified*
