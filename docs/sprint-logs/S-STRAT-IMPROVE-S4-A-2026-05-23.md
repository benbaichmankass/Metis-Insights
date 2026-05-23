# Sprint Log: S-STRAT-IMPROVE-S4-A

## Date Range
- Start: 2026-05-23
- End:   2026-05-23

## Objective
- Primary goal: instrument the VWAP backtest so selectivity work measures
  the **right** thing. S2 proved the strategy is gross-positive but
  net-negative (fees 418% of gross), so the existing gross-R backtest
  output is misleading. Add **net-of-fee R** + **long/short split** +
  **trade-count** to `run_backtest_vwap.py` so S4-B can rank selectivity
  variants on net-of-fee expectancy.
- Secondary goals: keep the change additive/backward-compatible; verify
  locally on bundled data.

## Tier
- Tier 1.
- Justification: change is confined to `src/backtest/run_backtest_vwap.py`,
  an **offline analysis tool** with no live-trading path. Additive only
  (new fields + a new `--fee-bps-roundtrip` arg, default 7.5). Gross
  fields unchanged; `--fee-bps-roundtrip 0` reproduces prior gross
  results exactly. No strategy/risk/config/live code touched.

## Starting Context
- Active roadmap items: Strategy Improvement Program; S0–S3 done. S4 =
  selectivity. Operator approved S4 ("yes let's go").
- Prior sprint reference: `S-STRAT-IMPROVE-S2` (fee drag = dominant
  driver); `S-STRAT-IMPROVE-S3` (exit mechanism healthy; regime caveat).
- Known risks at start: the backtest models gross R only — sweeping on it
  would mislead. Operator directive: measure **net-of-fee**, regime-robust.

## Repo State Checked
- Branch reviewed: `claude/strategy-improvement-program-EZi1X` at
  `45d0e01`.
- Canonical docs reviewed: program plan (S4 spec), S2 report.

## Files and Systems Inspected
- Code files inspected/edited: `src/backtest/run_backtest_vwap.py`
  (`_simulate_trade`, `run_single`, `run_windows`, `main` argparse).
- Code files inspected: `scripts/ops/vwap_backtest_sweep_action.sh`
  (the relay wrapper + its bt_mode options).
- Confirmed (grep): the backtest had **no fee modeling** before this
  sprint (only volatility "bps" buckets); `run_single` already had a
  per-window long/short split (added post-S-VWAP-POLICY-INVESTIGATION).

## Work Completed
- Added module constant `FEE_BPS_ROUNDTRIP = 7.5` (Bybit linear taker
  round-trip) with a `--fee-bps-roundtrip` override.
- `_simulate_trade` now computes per-trade `fee_r =
  (FEE_BPS_ROUNDTRIP/1e4) × (entry+exit)/2 / risk` and `net_pnl_r =
  pnl_r − fee_r`.
- `run_single` aggregates net-of-fee metrics: `net_total_r`,
  `net_total_r_long/short`, `net_avg_r_per_trade`, `net_win_rate_pct`,
  `net_wins`, `total_fee_r` (gross fields preserved).
- `run_windows` adds `mean_net_total_r` (+ long/short),
  `mean_trades_per_window`, `net_positive_windows`, plus per-regime
  `mean_net_total_r` / `net_positive_windows`. Adaptive-skip windows zero
  the net fields too.
- All net fields flow into the JSON output automatically (no printer
  changes), so the `vwap-backtest-sweep` relay will surface them once the
  code reaches a VM.

## Validation Performed
- Tests run: `tests/test_vwap_strategy.py`, `test_backtester.py`,
  `test_vwap_order_package_sl.py` → **102 passed**.
- Backward-compat check: `--fee-bps-roundtrip 0` → `net_total_r ==
  total_r` and `total_fee_r == 0` for every row (verified).
- Local smoke run (`--threshold-sweep` on bundled
  `data/backtest_candles.csv`, **single ~17-day window — LOW-CONFIDENCE
  dev data, NOT regime-diverse, NOT powered**):

  | Entry σ | Trades | Gross R | Net R | Fee R | Net WR | Net long | Net short |
  |---|---|---|---|---|---|---|---|
  | 0.8 | 59 | +8.59 | −16.01 | 24.6 | 30.5% | −17.73 | +1.73 |
  | 1.0 (live) | 52 | +2.56 | −17.86 | 20.4 | 26.9% | −17.76 | −0.10 |
  | 1.2 | 49 | +1.76 | −17.36 | 19.1 | 26.5% | −17.66 | +0.29 |
  | 1.5 | 43 | −0.90 | −17.81 | 16.9 | 23.3% | −14.71 | −3.09 |
  | 2.0 | 24 | +9.39 | +0.35 | 9.0 | 33.3% | +0.35 | 0 |

  Directionally consistent with S2 (fees dwarf gross; fewer trades = less
  drag; long leg bleeds in this down-window) — but a single window proves
  nothing. S4-B runs the powered, regime-diverse sweep.

- Gaps not yet verified: the regime-diverse, multi-window net-of-fee
  sweep (needs 90+ days + random sub-windows) — that is S4-B.

## Documentation Updated
- Roadmap updates: `S-STRAT-IMPROVE-S4-A` ledger row.
- Subsystem doc updates: program plan (S4 split into S4-A done / S4-B
  next, with the deploy-to-VM prerequisite noted).
- Architecture/rules/pipeline docs: none (offline tool only).

## Contradictions or Drift Found
- None.

## Risks and Follow-Ups
- **S4-B is blocked on the new code reaching a VM.** The
  `vwap-backtest-sweep` relay runs `run_backtest_vwap.py` from the live
  VM's `main` checkout — so the net-of-fee output appears there only
  after this branch merges to `main`, OR S4-B runs via the
  `trainer-vm-diag` relay with an explicit branch checkout. Operator
  decision point.
- Remaining product decisions (Tier 3): none yet (S4-A is tooling).

## Deferred Items
- **S4-B:** regime-diverse net-of-fee sweep (entry-threshold + session
  gating + regime-robust counter-trend gate), n≥3 both legs, validated
  across up AND down windows. Plus **S2-B** (low-N strategies).

## Next Recommended Sprint
- Suggested next sprint: **S4-B** — run the powered net-of-fee selectivity
  sweep once the instrumentation is on a VM.
- Why next: the measurement tool is ready; S4-B turns it into ranked,
  evidence-backed selectivity proposals.
- Required verification before starting S4-B: confirm the new
  `run_backtest_vwap.py` is on the VM running the sweep (merge to main or
  trainer-checkout); enforce the regime constraint (no static short-bias).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline-stage changes; `docs/TRADE-PIPELINE.md` untouched.
- [x] Roadmap status checked + S4-A row added.
- [x] Contradictions recorded (none).
- [x] Remaining unknowns stated (S4-B needs the code on a VM; single-
      window preview is not powered).
