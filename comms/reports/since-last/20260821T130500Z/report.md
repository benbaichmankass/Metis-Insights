# System report — since-last

- Generated: 2026-08-21T13:05:00+00:00
- Window: 2026-08-16T09:25:00+00:00 → 2026-08-21T13:05:00+00:00
- Roll-up grade: investigate

Execution is the story: only 8.1% of closes used a declared bracket, exit evaluation misses its 60s requirement on a third of cycles, and 12 of 33 capabilities shipped this window already have nothing running them. Real money is flat; the paper book's loss sits at 0.06 provenance coverage.

## P&L by class
- **real**: window — (prior —, flat)
- **paper**: window — (prior —, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Exit evaluation breaches its own 60s requirement on 32.4% of cycles — n=398 intervals, max 89.1s. The pass is fetch-bound, so the remedy (candle TTL / fetch budget) reaches live entry geometry as well as exit decisions. Third review in a row to record this class.
2. Give the pairs sleeve its own Bybit sub-account — 8 of 8 opens strand a naked leg because the sleeve shares bybit_1 with a directional strategy under one-way netting. The reorder shipped this session shortens the exposure window from ~60 minutes to one tick; it does not remove the cause. Needs a sub-account + key pair in Actions secrets — the one thing only you can originate.
3. Kill or fix slv_trend_1h — 0 wins in 13, -$5,375 at pnlCoverage 0.77 — the only large loser whose coverage makes the number trustworthy.
4. Trainer disk at 93.5% with a GC that cannot help — 3.15 GB free; the GC reclaims 0.09 GB. Training cannot be assessed until the box has room, and the last cycle trained 0 manifests.
5. Promote the unwired-artifacts guard from advisory to blocking — 12 of 33 new scripts this window are already unwired. The detector exists and is right; it just does not block. This is the structural fix for the largest backlog class.

## Review coverage
- Strategy promotion: No strategy cleared a promotion gate this window. One honest kill candidate (slv_trend_1h). uso_trend_1h is the credible winner (+$8,778 at pnlCoverage 1.00) but is not up for a stage change.
- ML training health: Last cycle trained 0 manifests. 7 stale, 5 permanent refusers. Trainer disk 93.5% (3.15 GB free) and the GC reclaims 0.09 GB — the fill is not something retention can clear.
- Soak `exit_interval_soak`: gate_met — n=398 intervals across 3 processes; 32.4% breach the 60s requirement. The soak has produced its answer and the answer is bad.
- Soak `eth-15m regime head (shadow)`: stalled — at shadow since 2026-06-28 — 54 days awaiting its promotion gate
- Soak `netting_attribution_soak (annotate)`: accruing — still at NETTING_ATTRIBUTION_MODE=annotate; rows accruing for review before any flip to apply
- Soak `exposure_soak`: accruing — per-account gross-exposure rows accruing; no ceiling value proposed yet
- Soak `pairs_soak`: gate_met — 8 of 8 opens stranded a leg — enough to act, and half the fix shipped
- Execution capture: 30 of 369 closes (8.1%) exited via a declared bracket; 148 (40%) closed reconciler_filled; the M20 lever set = 14 (3.8%). DOLLARS DID NOT RECONCILE WITH R this window — they disagree in SIGN at rCoverage 1.00 (PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN), so R is reported as diagnostic only and the dollar figures stand alone. (dollars reconciled: False)
  - `ict_scalp_mgc_15m` [paper]: round-trip —, giveback —R, hold 270.50/4.00h → anomaly
  - `ict_scalp_sol_5m` [paper]: round-trip —, giveback —R, hold 41.70/1.00h → anomaly
  - `qqq_pullback_1h` [paper]: round-trip —, giveback —R, hold 312.00/8.00h → anomaly
  - 🔴 `ict_scalp_mgc_15m`: held 10-100x design horizon (open 1 review(s), BL-20260821-SCALPS-HELD-10-TO-100X-THEIR-DESIGN-HORIZON)
  - 🔴 `ict_scalp_sol_5m`: held 10-100x design horizon (open 1 review(s), BL-20260821-SCALPS-HELD-10-TO-100X-THEIR-DESIGN-HORIZON)
- 🚩 EXIT EVALUATION MISSES ITS OWN 60s REQUIREMENT ON A THIRD OF CYCLES — requirement_state='breached'; 129 of 398 intervals over 60s (32.4%), max 89.1s, mean 48.3s, median 41.7s, across 3 processes. Every previously recorded mean sits BELOW every reading taken today. Third consecutive review to record this class, worse each time. BL-20260821-EXIT-EVAL-BREACHES-60S-ON-A-THIRD-OF-CYCLES.
- 🚩 12 OF 33 SCRIPTS SHIPPED IN THE LAST FIVE DAYS ALREADY HAVE NOTHING THAT RUNS THEM — scripts/ops/exit_mechanics_audit.py, scripts/ops/exit_path_coverage.py, scripts/ops/portfolio_conflict_audit.py, scripts/ops/position_ranking.py, scripts/ops/regime_policy_coverage.py, scripts/ops/system_invariants.py, scripts/research/e2_null_calibration.py, scripts/research/e3_barrier_decomposition.py, scripts/research/e3_joint_lever_sweep.py, scripts/research/exit_census.py, scripts/research/m20_split_dispersion.py, scripts/research/peak_banking_basis.py. This is the operator's 2026-08-20 'built half way and left to rust' directive, measured at 36% of one window's output. Repo-wide the unwired set is 150.
- 🚩 EXECUTION CAPTURE IS THE HEADLINE, NOT A DETAIL — only 30 of 369 closes (8.1%) exited via a declared bracket; 148 (40%) closed 'reconciler_filled'. The whole M20 lever set accounts for 14 closes (3.8%). The strategies are not exiting the way they are written.
- 🚩 SCALPS HELD 10-100x THEIR DESIGN HORIZON — ict_scalp_mgc_15m p90 = 1082 bars (11.3 days on a 15m leg); ict_scalp_sol_5m p90 = 501 bars (41.7h on a 5m leg); qqq_pullback_1h median 312 bars. trend_donchian 4h is the well-behaved control, which is what rules out a measurement artifact.
- 🚩 ML: THE REGISTRY REPORTS status='candidate' FOR ALL 95 MODELS while deployment_bucket correctly reports LIVE 3 / SHADOW 28 / OFFLINE 64 — the nightly re-train writes to_status='candidate' over the promoted stage, and stage_history exists on only 26 of 95. The registry's own status column cannot be read as the deployment stage. Last cycle trained 0 manifests with 7 stale and 5 permanent refusers.
- 🚩 TRAINER DISK 93.5% (3.15 GB free) and the GC reclaims only 0.09 GB — the retention tool cannot fix this class of fill. MB-20260821-TRAINER-DISK-93PCT-AND-THE-GC-CANNOT-HELP.
- 🚩 slv_trend_1h: ZERO wins in 13 closed trades, -$5,375 at pnlCoverage 0.77 — the only large loser at credible coverage, and therefore the one honest kill candidate.
- 🚩 PROVENANCE IS COLLAPSING: pnlCoverage 0.37 lifetime -> 0.33 -> 0.11 (7d) -> 0.06 (24h). Only 55 of 369 closes (15%) are broker-measured. The scoreboard is increasingly made of reconstructions.
- 🚩 pairs_sol_eth STRANDS A NAKED LEG ON 8 OF 8 OPENS since 2026-08-18, confirmed against exchange truth (bybit_1 SOLUSDT Buy 373.00 = donchian 367.80 + pairs 5.20 exactly; no ETH short exists). Half of the fix shipped this session; the cause is the sleeve sharing bybit_1 with a directional strategy under one-way netting and is operator-blocked on a sub-account.

## Monitoring (soaking / awaiting decision)
- `BL-20260821-EXIT-EVAL-BREACHES-60S-ON-A-THIRD-OF-CYCLES` [health · execution] re-measure max_interval_ms beside intervals_measured; a short-lived process reads reassuringly because the tail needs a large n (next: 2026-08-22)
- `pnlCoverage trend` [performance · provenance] 0.37 -> 0.33 -> 0.11 -> 0.06. The hourly Bybit fills timer shipped in this report's own PR should arrest it; verify the direction reverses. (next: 2026-08-22)
- `pairs_sol_eth strand rate` [health · order path] the reorder should cut exposure from ~60 min to one tick; strand COUNT should be unchanged until the sub-account lands (next: 2026-08-22)

_report_id RPT-20260821-130500-since-last_