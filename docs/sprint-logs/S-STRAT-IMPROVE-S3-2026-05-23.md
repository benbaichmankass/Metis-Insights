# Sprint Log: S-STRAT-IMPROVE-S3

## Date Range
- Start: 2026-05-23
- End:   2026-05-23

## Objective
- Primary goal (operator directive 2026-05-23, technical-first):
  determine whether the `reconciler_filled` exit dominance (74% of
  bybit_2 vwap exits, S2 finding #2) is a **monitor bug** — designed
  exits not firing, everything reverting to exchange-side closes — or
  **working as designed** (native broker bracket fires between ticks).
  If a bug, fix it BEFORE any strategy tuning.
- Secondary goals: act on the operator's SL_STD_MULT ruling (0.3 is
  approved → fix the stale comments); record the regime caveat on the
  long/short gap; re-plan the program sequence.

## Tier
- Tier 1.
- Justification: read-only diagnostic (`monitor-miss-analysis`, a
  Tier-1 read-only operator action) + **comment-only** edits to
  `vwap.py` (field-vs-comment hygiene, value `0.3` unchanged, explicitly
  operator-authorized) + doc/plan updates + sprint log. No strategy
  logic, risk, sizing, threshold, or SL/TP value changed. No order/
  deploy action.

## Starting Context
- Active roadmap items: Strategy Improvement Program. S0/S1/S2 done.
- Prior sprint reference: `S-STRAT-IMPROVE-S2` (loss-driver report;
  flagged reconciler_filled exits + SL governance question).
- Operator directives (2026-05-23): (1) SL=0.3 is proven/approved — the
  in-code "must approve" comment is stale; (2) the long/short gap is
  likely **down-market regime**, not a permanent edge — do not bake in a
  static short-bias; (3) **technical-first** — fix bugs causing losses
  before tuning strategies, faster monitor cadence acceptable if needed.

## Repo State Checked
- Branch reviewed: `claude/strategy-improvement-program-EZi1X` at
  `7d043f2`.
- Deployment state: live VM `461bcb0` (== main), `TICK_INTERVAL_SECONDS=60`
  (`deploy/ict-trader-live.service`).
- Canonical docs reviewed: program plan, S2 loss-driver report.

## Files and Systems Inspected
- Code files inspected: `scripts/ops/monitor_miss_analysis.py` (full),
  `src/runtime/order_monitor.py` (exit_reason + reconciler paths, grep),
  `src/units/accounts/execute.py` (native stopLoss/takeProfit on entry
  — lines 340-341, 594-595, 960-962), `src/main.py` (tick interval, 589),
  `src/units/strategies/vwap.py` (SL constant + R:R comment block).
- Deployment files inspected: `deploy/ict-trader-live.service`
  (`TICK_INTERVAL_SECONDS=60`).
- GitHub Actions workflows inspected: `operator-actions.yml`
  (`monitor-miss-analysis` arg forwarding).
- Live systems pulled (read-only): `monitor-miss-analysis` for bybit_2.

## Work Completed
- Fired `monitor-miss-analysis` (#1782) and read the result.
- **Diagnosed: no bug.** Of 125 `reconciler_filled` closes — 36 TP_hit,
  84 SL_hit, 5 between_TP_SL → **96% native Bybit bracket fires**. Root
  cause is structural: `execute.py` submits `stopLoss`/`takeProfit` with
  each entry, so the exchange closes server-side between the bot's 60s
  ticks; the reconciler correctly records it. Losses are genuine
  strategy losses (stop hit 84× vs TP 36×, mean_R −0.788 vs +1.712).
- Fixed the now-confirmed-stale SL comments in `vwap.py` (R:R
  worked-example 0.5σ→1:2 updated to 0.3σ→3.33:1; "must approve before
  deploy" note replaced with the 2026-05-23 approved+live record). Value
  `0.3` unchanged.
- Updated the loss-driver report (driver #2 reframed = working-as-
  designed; driver #3 = regime caveat) and re-planned the program
  (inserted S3 technical diagnosis; selectivity→S4 with regime
  constraint, exit geometry→S5, validate→S6, package→S7).

## Validation Performed
- Tests run: none (read-only diagnostic + comment-only code edit; ruff
  + pytest run in CI on the PR).
- Live data verification: `monitor-miss-analysis` #1782 (run
  26327530047): 125 rows, 36 TP_hit / 84 SL_hit / 5 between_TP_SL; by
  direction long 71 SL / 20 TP, short 13 SL / 16 TP; exit-position
  histogram peaks at -0.5..0.0 (n=74, BE-protected partial losses) and
  ≥1.0 (n=19, TP).
- Manual code verification: `execute.py` confirmed to set native
  `stopLoss`/`takeProfit`; tick interval 60s confirmed.
- Gaps not yet verified: the S5 exit-geometry hypothesis (faster managed
  cadence / wider stop converting SL-runs into vwap_cross wins) — needs
  backtest, not asserted here.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Trade pipeline doc updates: none (diagnosis confirmed existing
  pipeline behavior; no stage change).
- Roadmap updates: added the `S-STRAT-IMPROVE-S3` ledger row.
- GitHub Actions doc updates: none.
- Subsystem doc updates: program plan (S3 inserted + renumber + SL flag
  resolved + handoff); loss-driver report (drivers #2/#3 + handoff);
  `vwap.py` SL comment block.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Resolved the S2 SL_STD_MULT governance contradiction:** operator
  confirmed 0.3 approved/live; fixed the stale `vwap.py` comments
  (field-vs-comment, field wins). No value changed.
- No new drift introduced.

## Risks and Follow-Ups
- Remaining technical risks: none from the exit mechanism (working as
  designed). The real-money vwap account keeps bleeding until an
  approved strategy fix lands (S4+).
- Remaining product decisions (Tier 3): all S4/S5 live changes.
- Blockers: none for S4.

## Deferred Items
- **S2-B:** journal-based per-strategy pull for low-N
  turtle_soup/ict_scalp/MES.
- **S5 exit-geometry hypothesis:** wider/time-boxed stop or faster
  managed-exit cadence to convert native SL-runs into `vwap_cross`
  thesis-completions — backtest in S5.

## Next Recommended Sprint
- Suggested next sprint: **S4 — selectivity / rule-tightening**
  (Tier-1 analysis; Tier-3 to ship).
- Why next: fee drag from overtrading is the dominant, evidence-backed
  driver and the exit mechanism is confirmed healthy, so cutting trade
  count is the highest-ROI next lever.
- Required verification before starting S4: re-confirm live SHA; use
  `vwap-backtest-sweep` (key `bt_mode:`) + trainer-VM sweeps; measure
  net-of-fee; **enforce the regime constraint** (no static short-bias;
  validate up AND down windows; n≥3 both legs).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline-stage changes, so `docs/TRADE-PIPELINE.md` did not need
      updating; Trade Process tab not affected.
- [x] Roadmap status was checked and an S3 ledger row added.
- [x] Contradictions were recorded + the SL governance one resolved
      (stale comments fixed, value unchanged).
- [x] Remaining unknowns were stated clearly (S5 exit-geometry
      hypothesis; S2-B low-N strategies).
