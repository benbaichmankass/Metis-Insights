# Sprint Log: S-P1X-REALR-NETTEDPNL-20260806

## Date Range
2026-08-06 (single session, `roadmap-workplan-update` lw6qf0)

## Objective
Continue the WORKPLAN-2026-08-05 queue from P0.2b. Deliver operator decisions #2/#3
(netting partial-close attribution), then platform **P1.x** (real stop-distance
live-R). What P1.x measured then drove the rest of the session.

## Tier
Mixed. Tier-1 for the calibrator + tooling + docs; **Tier-2** for
`src/runtime/order_monitor.py` and the money-DB marker script — both
operator-approved in chat this session.

## Starting Context
P0.2b wallet-truth reconciliation certified; W0.2 soak read done. The workplan
named P1.x the critical path: the fidelity calibrator's `drifts` verdicts were
suspected to be an artifact of a ±1 sign-proxy R axis rather than a cost finding.

## Repo State Checked
`main` @ `f5f2154` at session start → `386b100` after two merges. Branch
`claude/roadmap-workplan-update-lw6qf0`. Live journal read via trainer-vm-diag
relays #8528–#8537.

## Files and Systems Inspected
- `scripts/research/backtest_fidelity_calibrate.py`, `backtest_fidelity_cost_ab.py`
- `src/runtime/order_monitor.py` (three broker-close persist sites), `provenance.py`
- `src/units/accounts/clients.py::_bybit_closed_pnl_lookup`, `src/units/db/database.py`
- `config/regime_policy.yaml`, `.github/workflows/*` (129 files)
- Live `trade_journal.db` via relays; `docs/claude/health-review-backlog.json`

## Work Completed

**PR #8527 (merged, `ba9c6e5`)** — netting partial-close attribution reconciler
(decisions #2/#3) + P1.x real stop-distance live-R.
- Attribution: leg-gone evidence first, FIFO residual, still-resting leg picked
  LAST. Provenance ladder honours `bar_close_at`'s three-way contract; the anchor
  is the divergence's FIRST OBSERVATION, not "now". Ships at `annotate`.
- P1.x: real R = `pnl / (|entry−stop|·|qty|·contract_value)` via the canonical
  `_clean_trades.r_multiple`, now the DEFAULT axis. Unmeasurable rows are EXCLUDED
  and reported as `r_coverage`, never proxied.
- Fixed a real defect CI caught: the reconciler SELECTed `strategy`; the column is
  `strategy_name`. It would have raised every live tick. Root cause was a
  hand-written test fixture; it now builds the real schema via `Database`.

**PR #8532 (merged, `386b100`)** — third calibrator gate axis.
The gate had frequency (win-rate) and shape (KS) but nothing for magnitude, so
`htf_pullback_trend_2h`/BTC read `calibrated` on a live mean-R of −3.41 vs a
backtest −0.037. `mean_r_gap` is now a gate condition; `live_mean_r_outlier_share`
is reported and deliberately NOT gated.

**PR #8534 (open, approved, CI-blocked)** — netted-PnL proration, both halves.
Forward: `_prorate_netted_broker_pnl` shared by all three persist sites, closing
the one (`_recover_close_from_broker_pnl`) that had no guard and stamped the
broker source, so a fabricated figure classified MEASURED. Retroactive:
`scripts/ops/mark_netted_duplicate_pnl.py`, dry-run by default, NOT yet run.

## Validation Performed
- 84 targeted tests pass (22 new: 11 proration, 11 marker). Full suite green on
  #8527/#8532 via CI (31/31 and 33/33).
- ruff + `provenance-consumer`, `canonical-db-resolver`, `diagnostic-provenance`,
  `claim-basis` guards green locally.
- Trust map re-run on the real-R axis (#8528): KS roughly halved on every leg with
  the sample unchanged, confirming the sign-proxy artifact.
- Blast radius measured and discriminated (#8535).

## Documentation Updated
`WORKPLAN-2026-08-05.md` (P1.x status), `FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md`
(§5b KS table marked as the RETIRED axis), `CLAUDE.md` env table, health-review
backlog (5 new items).

## Contradictions or Drift Found
- The design doc's §5b KS figures are on an axis that no longer exists. Marked
  explicitly so no one compares across the change.
- The `htf_pullback_trend_2h`/BTC trust-map verdict is unusable in either
  direction until its rows are clean — recorded, not quietly carried forward.

## Risks and Follow-Ups
- `BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS` (HIGH, open) — $24.3k across 31
  paper rows still reads MEASURED; feeds the calibration set and ML labels. Real
  money bounded at $45.52 (an upper bound, not an estimate).
- `BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES` (medium) — 29 concurrent jobs
  per push; P(green)=(1−p)^29. Needs a branch-protection change with the fix.
- `BL-20260806-TRAINER-RELAY-MANGLES-ANGLE-BRACKETS` (medium) — relay corrupts
  `<`/`>` AND truncates heredocs; cost three round trips.
- `BL-20260806-CALIBRATOR-GATE-HAD-NO-MAGNITUDE-AXIS` (resolved).

## Deferred Items
- ML2 2-D cell re-audit (discovery dispatched, #8537).
- C4 `c_reg` calibrator; P3 hybrids.
- **ML1 is BLOCKED** — specified as "evaluated through the calibrated platform",
  and the calibration set is the thing currently poisoned.

## Next Recommended Sprint
Merge #8534, run the marker against the live journal (needs a dispatch path),
re-run the trust map on clean rows, then ML2.

## Wrap-Up Check
Two self-corrections recorded rather than buried: the cited rows came from the
reconciler path, not the watchdog path this session fixed (posted on the PR); and
the outlier-share flag was initially set at 0.5, the metric's mathematical
ceiling, making it unreachable. The raw "236/408 real-money rows" census figure
was NOT published — it was ~2/3 rounding collisions.
