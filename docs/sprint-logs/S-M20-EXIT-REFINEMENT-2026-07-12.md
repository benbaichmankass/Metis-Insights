# Sprint Log: S-M20-EXIT-REFINEMENT-2026-07-12

## Date Range
- **Start:** 2026-07-12
- **End:** 2026-07-12

## Objective
- **Primary:** Run the M20 Exit Refinement session (operator-directed #1
  next-strategy priority + the operator's chop-hold observation: "a lot of
  trades are held through long chop periods where it isn't clear the trend we
  entered on is still relevant"). Read the two exit shadow-soaks, resolve
  counterfactual exits against realized prices, quantify the chop-hold
  problem, test time-stop / trend-invalidation / cross-timeframe exit levers,
  and propose the Tier-3 change only if the evidence clears the gate.
- **Secondary:** leave the exit-research tooling reusable (harness levers,
  sweep driver, trainer soak mirroring) so the next exit session doesn't
  re-derive it.

## Tier
- **Tier 1.** All analysis is reads + research tooling (`scripts/research/`,
  `scripts/ops/sync_trainer_data.sh` trainer-side sync, additive default-off
  flags on two standalone research harnesses). No live-path file touched. Any
  live exit change is proposed only (Tier-3, operator-gated).

## Starting Context
- ROADMAP M20 row (planned 2026-07-11) + `docs/sprint-plans/M20-EXIT-REFINEMENT-SESSION-PROMPT.md`.
- Inputs soaking: `exit_ladder_soak` (ExitPlan P3, `PB-20260617-002`),
  `fc_geometry_soak` (M19 D1, `MB-20260705-FC-SLTP-GEOMETRY`).
- Known honest-negative: the offline fc backtest failed its own
  reality-calibration check (T0.4 evidence memo) — live exits close for
  non-barrier reasons, so re-simulation alone can't validate an exit change.

## Repo State Checked
- Branch `claude/exit-refinement-sprint-l74k6o` off `main` @ `c4068a4`.
- Canonical docs read: CLAUDE-RULES-CANONICAL, root CLAUDE.md, ROADMAP (M20 +
  Next-plan), session-coordination/diag-data/sprint-format/backtesting skills,
  live-trade-management contract, S-DTP-EXITPLAN log, T0.4 evidence memo.
- Session board: registered; no open PRs (no workstream collision).

## Files and Systems Inspected
- `src/runtime/exit_ladder_soak.py`, `src/runtime/exit_plan.py`
  (`build_exit_plan_from_legacy`), `src/runtime/exit_plan_materializer.py`,
  `src/runtime/fc_geometry_soak.py`, `scripts/ml/fc_geometry_resolve.py`,
  `scripts/ml/fc_sltp_geometry_backtest.py`.
- `src/units/strategies/trend_donchian.py::monitor` (chandelier-trail-only
  exit design), `src/units/strategies/turtle_soup.py` (the only `meta.tp2`
  producer), `config/strategies.yaml`, `config/instruments.yaml`.
- `src/web/api/_clean_trades.py` (reduce-leg/phantom exclusions),
  `src/utils/closed_at.py` (epoch-ms close times).
- `scripts/backtest_pullback.py`, `scripts/research/backtest_trend.py`,
  `scripts/ops/research_sweep.py`, `scripts/ops/sync_trainer_data.sh`.
- Live VM (diag relay #6157): soak tails, status, recent trades. Trainer VM
  (relays #6158/#6159/#6160/#6161): soak mirror, journal sync, analysis runs.

## Work Completed
- **Data-sufficiency gate (both soaks FAIL — with a structural finding):**
  - `exit_ladder_soak`: 135 rows (112 api / 23 prop), **0 differing** — only
    `turtle_soup` declares `meta.tp2` and it is `execution: shadow`, so no
    live strategy produces a ladder that differs from the flat SL/TP placed.
    P4 cannot be answered by more soaking; re-scoped (see memo § 5).
  - `fc_geometry_soak`: 23 rows / 7 fc-covered; censoring-aware resolver:
    85.7% censored, paired n=1. Re-check dated 2026-08-25.
- **Chop-hold deep research (90d, 275 path-resolved closed trades,
  BTC/ETH/SOL 15m candles, trainer-side):** real-money mean MFE +1.92R vs
  realized −0.16R (giveback 2.08R), 26% round-trippers; per-strategy tables +
  truncation counterfactuals (time-stops, stagnation-stops, 1h-EMA-flip
  cross-TF exits) in the memo. Signal concentrated in the 2h trend-following
  family; `ict_scalp_5m` counterfactuals negative (no blanket rule).
- **Full-history validation sweep** (5y BTC/ETH/SOL, IS/OOS split 2025-07-01)
  through the same standalone harnesses that validated the strategies, with
  new default-off exit levers: `--stale-exit-bars/--stale-exit-below-r`
  (conditional time-stop) on both harnesses, `--flip-exit-bars`
  (Donchian-midline trend-invalidation exit) on the pullback harness.
  **Result:** exactly one lever passes the gate (better net_R AND maxDD, IS
  and OOS): stale-stop (8 bars, <0R) on `trend_donchian_sol` +
  `trend_donchian_eth` — also the one cell where the live counterfactuals and
  the harness agree. BTC donchian + pullback levers fail (honest negatives).
  Full table + Tier-3 proposal: the memo
  (`docs/research/M20-exit-refinement-2026-07-12.md` § 4–5).
- **Tooling shipped (reusable):** `scripts/research/m20_exit_analysis.py`
  (soak + chop-hold + truncation-counterfactual analyzer, stdlib-only,
  trainer-runnable), `scripts/research/m20_exit_sweep.py` (compact IS/OOS
  lever sweep driver), `scripts/research/market_raw_to_csv.py` (candle
  side-stream → harness CSV).
- **Infra fixes:** trainer checkout was stale at `38ac1c04` (its git-sync had
  silently stopped rolling forward — reset to `origin/main`, relay #6159);
  `sync_trainer_data.sh` now mirrors both exit soak logs (the
  `fc_geometry_resolve.py` docstring contract was unimplemented until now).

## Validation Performed
- Analysis script validated on a synthetic fixture locally (candles + journal
  + soak rows; S1/S2/S3/S4 sections all execute), then run against the real
  synced journal + candles on the trainer (relay #6158).
- Harness lever edits smoke-tested locally on synthetic CSVs: base cells run
  with levers off (default None/0 keeps the original code path); lever cells
  produce the new `stale_stop`/`trend_flip` exit reasons. `py_compile` +
  `bash -n` clean on all touched files.
- Full-history sweep run on the trainer against real 5y candles (relay
  #6161; first two attempts failed on a path assumption and the system-python
  pandas gap — both fixed, recorded honestly).
- **Gaps not yet verified:** path metrics only cover BTC/ETH/SOL (no trainer
  candles for ADA/AVAX/XRP, equities, metals — coverage gap logged);
  15m-bar MFE overstates capturable profit for 5m strategies (stated caveat);
  counterfactual ΔR are hypothesis-grade at current live n (1–10 affected
  trades per cell) — that is exactly why the full-history sweep gates the
  proposal.

## Phase 2 (same session — operator directive after #6164 merged)

Operator: merge the research, implement the Tier-3 proposal, and keep going —
"deep research, extensive backtesting on variations, ML supplements, trailing
stops, exit-ladder optimization." Delivered (PR #6166):

- **Stale-stop implementation, annotate-first** (memo § 6.1):
  `trend_donchian.monitor()` conditional stale-stop, YAML-declared params via
  package meta (NO strategy declares yet — live behavior unchanged);
  undeclared packages annotate the reference cell observe-only to
  `runtime_logs/exit_lever_soak.jsonl` (diag `exit_lever_soak`). 8 new tests.
  **YAML declare for `trend_donchian_sol`/`_eth` follows after the annotate
  window sanity-checks (or on operator instruction).**
- **Trailing-geometry + exit-ladder sweep** (memo § 6.2, 55 cells, relay
  #6169): banking loses net_R in ALL 20 cells (tail-for-smoothness — parked
  for standard accounts, prop-EV follow-up `PB-20260712-PROP-BANKING-EV`);
  trail geometry is per-family (trail7 repairs donchian-BTC OOS but not to
  positive; **pullback BTC trail 5→4 is a near-pass** → walk-forward
  follow-up `PB-20260712-PULLBACK-TRAIL4-WALKFORWARD`); ETH/SOL stale-stop
  remains the champion vs every phase-2 cell.
- **ML-supplemented exits** (memo § 6.3, relay #6168): probe honest-negative
  on current data (shadow log reaches only 2026-07-07; vol heads read ≥0.6
  always — no exit discrimination as-is); dedicated exit-head experiment
  spec'd (`MB-20260712-ML-EXIT-HEAD`) + shadow-log-history gap
  (`MB-20260712-SHADOW-LOG-HISTORY`).
- Relay-preemption lesson: long trainer sweeps now run detached (`nohup` +
  collect pass) — the trainer-vm-diag concurrency group cancels in-progress
  runs when a newer request arrives (#6161 was preempted).

## Phase 3 (same session — operator go-live orders)

- **Stale-stop LIVE**: YAML declared on `trend_donchian_sol`/`_eth` (#6172),
  activated via `pull-and-deploy` #6173 — live HEAD `5b86e14`, trader
  restarted 08:54 UTC, verified active. SOL long-only re-validation was MIXED
  (OOS strongly better incl. maxDD, IS softer) — flagged to the operator
  pre-merge; the flip proceeded on explicit order. ETH config-exact clean pass.
- **Trail4 walk-forward**: 4/6 yearly folds incl. 2025+2026, 5y +35% —
  recommendation delivered (`trail_mult 5→4` on `htf_pullback_trend_2h`),
  **operator-approved same day ("let's move forward on the pullback btc") and
  SHIPPED LIVE** (#6176, `pull-and-deploy` #6177 — live HEAD `15becc0`, trader
  restarted 09:29 UTC). `eth_pullback_2h` stays 5.0 (folds failed). The stale
  BTC↔ETH trail_mult equality assertion in
  `tests/test_m15_eth_pullback_wiring.py` was updated to per-leg pins.
- **Giveback-stop lever** (memo § 7.3, 44 config-exact cells): FAILS for
  donchian (trail already covers it), **PASSES for pullback BTC**
  (gb1.0R@MFE1R: OOS −3.7→+7.4 at flat IS) — confirms trail4's read; combos
  worse than either lever alone.
- **Exit-head program**: full E0–E3 plan committed
  (`docs/research/M20-exit-head-PROGRAM.md`); **E0 FIRST BUILD DONE same day**
  (#6176 builder + #6181 journal-loader schema fix; trainer runs #6179/#6182).
  `scripts/ml/build_exit_head_dataset.py` + `--emit-trades` on both harnesses;
  5y config-exact harness trades (don BTC 428 / ETH 901 / SOL 402, pb BTC 375
  / ETH 244) + live journal → per-bar datasets on the trainer under
  `datasets-out/exit_head/{1h,2h}/`:
  - **donchian 1h**: 34,919 rows (1,662 harness + 15 live trades),
    `holding_pays` 20.7% — i.e. at ~4 of 5 in-trade bars, holding did NOT add
    ≥ +0.25R (the chop-hold thesis, now as a label distribution).
  - **pullback 2h**: 30,512 rows (614 harness + 26 live trades),
    `holding_pays` 28.9%.
  - **E1 coverage gate** (≥300 harness AND ≥20 live): **pullback-2h ENTERS
    E1**; donchian-1h is at 15/20 live trades (harness side far over) — waits
    for a handful more resolvable live closes. Other families are live-only
    (no harness generator yet) and wait per the program doc.
  - Caveat on record: `dist_to_stop_r` is measured vs the INITIAL stop; the
    live trailing-stop path is not replayed (documented in the builder).
- **E1 trained same day** (operator go "let's continue to e1"; trainer
  `scripts/ml/train_exit_head.py` #6184, run #6186): **pullback-2h FAIL**
  (fold AUCs 0.47–0.54, chance-level — honest negative; hard levers stand).
  **Donchian-1h promising but below-gate** (live n=15<20): AUC 0.56–0.62 in
  every fold; τ=0.1 policy beats actual on maxDD + net_R/pos-day in 5/5 folds
  and on aggregate net_R (86.3 vs 73.7), but gives up the big trend years and
  the tiny live set disagrees in sign. **No E1→E2 pass**; E1.5
  conditional-policy shapes + live-n≥20 re-run queued (memo § 8).

## Documentation Updated
- `docs/research/M20-exit-refinement-2026-07-12.md` (the evidence memo).
- ROADMAP.md M20 row → status update (this session's outcome + next gate).
- Backlogs: `PB-20260617-002` (ladder P4) re-scoped; `MB-20260705-FC-SLTP-GEOMETRY`
  annotated with the dated re-check; new follow-ups logged (see below).
- This sprint log.

## Contradictions or Drift Found
- `scripts/ml/fc_geometry_resolve.py` docstring claims the soak log is
  mirrored by `sync_trainer_data.sh` — it was not. Fixed in this PR (field
  beats comment: the *mirror* was the missing half, the docstring described
  the intended contract from the M19 D1 design).
- Trainer VM worktree was behind `origin/main` (at `38ac1c04`) — its
  self-update had not rolled forward; reset via relay. Logged as a follow-up
  to watch (below).

## Risks and Follow-Ups
- **Tier-3 proposal** (see memo § 6): per-strategy exit levers for the 2h
  trend-following family, shipped behind a `*_MODE` graduation flag —
  operator decision required; no live file changed this sprint.
- Trainer checkout staleness: if `ict-git-sync` (trainer side) is expected to
  keep the worktree current, it silently wasn't — logged to health backlog
  (`BL-20260712-TRAINER-CHECKOUT-STALE`).
- Candle-coverage gap for non-BTC/ETH/SOL symbols blocks the same analysis
  for the 4h alt-donchian + equities fleets — logged to ml backlog
  (`MB-20260712-EXIT-ANALYSIS-COVERAGE`).

## Deferred Items
- fc-geometry verdict (insufficient data until ~2026-08-25).
- Ladder P4 as originally scoped (no differing population exists to test);
  any real partial-TP ladder needs a strategy to declare one first.
- Equities/metals/alt-symbol chop-hold analysis (candle coverage).

## Next Recommended Sprint
- If the operator approves the Tier-3 direction: implement the approved exit
  lever(s) behind the graduation flag with a live shadow-annotate phase
  (soak logs the would-be lever exit vs the actual), then apply after the
  annotate soak confirms the backtest deltas. Otherwise: re-check the fc soak
  2026-08-25 and rerun this session's analyzers (all tooling now in-repo).

## Wrap-Up Check
- [x] Code inspected directly (paths above; exit paths mapped before analysis).
- [x] Docs reviewed + updated (memo, roadmap, backlogs, this log).
- [x] TRADE-PIPELINE — no pipeline stage changed (research-only sprint).
- [x] Roadmap updated (M20 row).
- [x] Contradictions recorded (resolver-docstring mirror gap; stale trainer checkout).
- [x] Unknowns stated plainly (coverage gaps, small-n caveats, 5m MFE inflation).
