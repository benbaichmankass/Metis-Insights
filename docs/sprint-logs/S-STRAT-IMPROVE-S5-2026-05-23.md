# Sprint Log: S-STRAT-IMPROVE-S5

## Date Range
- Start: 2026-05-23
- End:   2026-05-23 (first read; regime-confirmation + MES + model-in-loop continue)

## Objective
- Primary goal: the cross-strategy inherent-edge audit — does
  `turtle_soup` and/or `ict_scalp` have a durable, fee-survivable edge
  (the basis for the operator's 3–5 complementary-strategy roster),
  unlike vwap (S4-B: no edge)?
- Secondary: build the turtle_soup harness (done); run both net-of-fee on
  rich historical data via the uncapped trainer VM.

## Tier
- Tier 1. Read-only backtests on the trainer (autonomous territory) +
  analysis docs. No strategy/risk/live change.

## Starting Context
- S4-B ruled out vwap. Operator North Star: 3–5 complementary edged
  strategies on BTCUSDT+MES + a decider, models in the backtest loop.
- Trainer VM up (1-core), with the S-TRAINER-BT-1 venv + the qashdev
  BTCUSDT 5m parquet (332,624 bars, Jan 2023→Feb 2026).

## Repo State Checked
- Trainer checked out branch `claude/strategy-improvement-program-EZi1X`
  @ e5ba873 (all 3 net-of-fee harnesses present); venv pandas 3.0.3;
  parquet schema [timestamp,open,high,low,close,volume,quote_volume].

## Files and Systems Inspected
- `scripts/backtest_turtle_soup.py` (built this sprint),
  `scripts/backtest_ict_scalp.py` (S4 net-of-fee), `docs/sprint-logs/S-TRAINER-BT-1.md`
  (trainer venv + data-cache paths).
- Trainer relays #1789–#1794.

## Work Completed
- Built `scripts/backtest_turtle_soup.py` (net-of-fee, long/short split,
  single-TP1 exit) + 6 unit tests — turtle_soup had no harness.
- Ran ict_scalp + turtle_soup net-of-fee on the trainer.
- **First inherent-edge read (12 months, 2025-03→2026-02):**
  - ict_scalp: 204 trades, 53.4% WR, **gross +45.6R / net +4.2R**.
  - turtle_soup: 60 trades, 61.7% WR, **gross +11.4R / net +1.3R**.
  - **Both have genuine gross edge and finish net-positive** — vwap had
    neither. Full table + caveats:
    `docs/audits/strategy-inherent-edge-2026-05-23.md`.

## Validation Performed
- Both backtests completed (`.done` markers); JSON parsed.
- Cross-check vs S4-B: vwap fee/trade ~0.45R (tight stop) vs
  ict/turtle ~0.17–0.20R (wider structure stops) — explains why the
  latter survive fees. Internally consistent.
- Gaps: only the recent 12-month window (regime confirmation pending on
  2023/2024); simplified single-TP harness (net is a conservative floor);
  the full-3yr run was abandoned (per-bar harness too slow on 1 core —
  optimization filed).

## Documentation Updated
- New audit: `docs/audits/strategy-inherent-edge-2026-05-23.md`.
- Roadmap: `S-STRAT-IMPROVE-S5` ledger row.
- Program plan: S5 first-read result + remaining S5 sub-tasks.

## Contradictions or Drift Found
- None new. (Reinforces S4-B's fee-efficiency thesis on edged strategies.)

## Risks and Follow-Ups
- Net edge is thin (~+0.02R/trade) — needs fee-efficiency tuning to be
  meaningful; the gross edge gives room to work.
- Harness too slow for full-history on the 1-core trainer → optimize
  (vectorize / cache window indicators) before larger sweeps.

## Deferred Items
- Regime confirmation (2023 bear + 2024 slices).
- Fee-efficiency sweeps for ict_scalp + turtle_soup (net-of-fee).
- MES backtesting (CME/IB fees + tick off instruments.yaml).
- Model-in-the-loop backtest.

## Next Recommended Sprint
- **S5 continued:** regime-confirm the two edged strategies, then
  fee-efficiency tune them; extend to MES + model-in-loop. Then **S6**:
  roster + decider recommendation (Tier-3 to ship).

## Wrap-Up Check
- [x] Code inspected directly; backtests run on real 12-month data.
- [x] Documentation updated (audit, plan, roadmap, this log).
- [x] No pipeline-stage change; TRADE-PIPELINE.md untouched.
- [x] Roadmap status updated.
- [x] Contradictions recorded (none new).
- [x] Unknowns stated (regime confirmation, harness speed, thin net,
      simplified exits).
