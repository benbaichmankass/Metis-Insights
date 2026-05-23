# Sprint Log: S-STRAT-IMPROVE-S4-B

## Date Range
- Start: 2026-05-23
- End:   2026-05-23

## Objective
- Primary goal: run the powered, regime-diverse **net-of-fee** selectivity
  sweeps for vwap (now that S4-A instrumentation is on main) and answer:
  can selectivity (entry threshold) and/or fee-efficiency (SL width) make
  vwap net-of-fee profitable?
- Secondary goals (operator-directed): think wide / question whether the
  base strategy has inherent edge; instrument `ict_scalp` backtest for
  net-of-fee to enable the cross-strategy edge audit.

## Tier
- Tier 1. Read-only relay backtests + an offline-tool instrumentation
  (`backtest_ict_scalp.py`) + analysis docs. No strategy/risk/live change.

## Starting Context
- S0–S4A merged (#1778). S4-A added net-of-fee to the vwap backtest.
- Operator directives 2026-05-23: keep going autonomously (stop only at
  the live-change gate); test combinations; "think deeper about base
  strategies that have inherent edge — current strategy isn't robust even
  in theory"; long/short gap is regime (down-market), no static
  short-bias.

## Repo State Checked
- Branch synced to `main` (0c2aa7f) post-merge; new work at 3604b86+.
- Live VM confirmed on `main` working tree (sweeps returned net-of-fee
  fields → new code active). Trainer VM probed (#1786): up (11d uptime),
  **1 core**, on `1bb7a5b` (behind main), default python lacks pandas.

## Files and Systems Inspected
- `src/backtest/run_backtest_vwap.py` (PARAM_SWEEP grid = 4 entry × 3 SL).
- `scripts/backtest_ict_scalp.py` (instrumented net-of-fee; bundled data
  is 2022 — local runs verify code only, not edge).
- Relays: `vwap-backtest-sweep` (#1784 threshold, #1785 param),
  `trainer-vm-diag` (#1786 probe).

## Work Completed
- Ran the net-of-fee threshold sweep (#1784, 8×14d/365d) and param sweep
  (#1785, 12 configs × 3×14d/365d).
- **Verdict: vwap has no inherent edge** — 0/8 (threshold) and 0/36
  (param) windows net-positive; best config −41.7R/14d. Full evidence +
  caveats: `docs/audits/vwap-viability-verdict-2026-05-23.md`.
- Instrumented `scripts/backtest_ict_scalp.py` for net-of-fee (committed
  3604b86) so the cross-strategy edge audit (S5) can run.
- Pivoted the program (operator-directed) from tuning-vwap to edge-first
  strategy assessment.

## Validation Performed
- Sweeps verified to carry net-of-fee fields (confirms merged code ran
  on the VM). Threshold + param sweeps mutually corroborate (both deeply
  net-negative, monotone selectivity effect). Live audit (−$36/7d)
  corroborates direction.
- ict_scalp instrumentation: ruff clean, 20 ict-cli tests pass; fee=0
  reproduces gross (by construction, mirrors vwap).
- Gaps: param sweep is 3 windows (noisy magnitudes); backtest is a
  simplified model (no live BE/partial/HTF-per-config); HTF *edge* filter
  for vwap not yet tested (S4-B-3); ict_scalp/turtle_soup edge on fresh
  data not yet run (S5).

## Documentation Updated
- New: `docs/audits/vwap-viability-verdict-2026-05-23.md`.
- Roadmap: `S-STRAT-IMPROVE-S4-B` ledger row.
- Program plan: S4-B marked done + pivot to edge-first (S4-B-3, S5, S6).

## Contradictions or Drift Found
- None. (S4-B revises the optimistic "thin gross edge" framing from S2 —
  that was a 7-day window; the powered 365-day read shows no gross edge.
  Recorded in the verdict doc, not a contradiction in any canonical doc.)

## Risks and Follow-Ups
- The real-money bybit_2 vwap account keeps losing until a decision is
  made (tune-further is ruled out; the live options are operator-gated:
  reduce frequency drastically, add an edge filter if S4-B-3 finds one,
  or retire/replace — all Tier-3).
- Trainer VM is 1-core + needs `git pull` + a pandas venv before it can
  run powered uncapped backtests (S5 prerequisite).

## Deferred Items
- **S4-B-3:** vwap HTF/regime edge filter (compare mode) — last vwap lever.
- **S5:** inherent-edge audit of turtle_soup + ict_scalp net-of-fee on
  fresh 365-day data (build a turtle_soup harness; run ict_scalp).
- **S2-B:** low-N live strategies.

## Next Recommended Sprint
- **S5 — cross-strategy inherent-edge audit** (Tier-1). Why next: vwap is
  ruled out by tuning; the program must establish which (if any) current
  strategy has a durable, fee-survivable edge before proposing any live
  direction.
- Required verification before S5: get the trainer VM (or a relay path)
  able to run ict_scalp + turtle_soup backtests on fresh 365-day data,
  net-of-fee, regime-split.

## Wrap-Up Check
- [x] Code inspected directly.
- [x] Documentation updated (verdict doc, plan, roadmap, this log).
- [x] No pipeline-stage change; TRADE-PIPELINE.md untouched.
- [x] Roadmap status updated.
- [x] Contradictions recorded (S2 optimism revised, with evidence).
- [x] Unknowns stated (HTF-edge untested; cross-strategy edge pending;
      trainer setup needed).
