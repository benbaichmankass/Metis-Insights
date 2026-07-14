# Sprint Log: S-M21-ENTRY-REFINEMENT-2026-07-13

## Date Range
- Start: 2026-07-13
- End: (in progress)

## Objective
- Primary goal: execute **M21 Entry Refinement** per the design of record
  ([`docs/research/M21-entry-refinement-DESIGN.md`](../research/M21-entry-refinement-DESIGN.md),
  merged #6279; operator-acked 2026-07-13) — starting with **E-1**, the
  entry-quality evidence pass, then E-2 hard entry-filter cells, then the
  E-3 P_win head (the M18 allocator unlock).
- Secondary goals: collect the M20-tail peak-is-in retarget round verdicts
  (4h donchians + 2h pullbacks) running concurrently on the trainer.

## Tier
- Tier 1 for everything in this log so far (research tooling + docs).
  E-2 declares and any E-3 gating will be Tier-3 batches per the design.
- Justification: `m21_entry_baseline.py` reads emit/candle files and writes
  reports under `runtime_logs/`; never touches `config/` or `src/`.

## Starting Context
- Active roadmap items: M21 (PLANNED → EXECUTING with this sprint); M20
  essentially complete (15 cells + exit head live; soaks/first-fire checks
  remain event-gated).
- Prior sprint reference: `S-M20-EXIT-REFINEMENT-2026-07-12.md`.
- Known risks at start: trainer is 1-OCPU and already running the
  peak-retarget rounds — all heavy jobs must serialize
  (BL-20260712-TRAINER-JOB-SERIALIZATION); operator is running a
  `/system-review` in parallel — merge-slot discipline applies.

## Repo State Checked
- Branch or commit reviewed: `main` @ 4b41185 (post doc-freshness sweep);
  work on `claude/exit-refinement-sprint-l74k6o`.
- Deployment state reviewed: live at `c4b103b` (batch-2 declares active).
- Canonical docs reviewed: CLAUDE-RULES-CANONICAL, ROADMAP (M21 row),
  M21 design doc.

## Files and Systems Inspected
- Code files inspected: `m20_fleet_exit_sweep.py` (resolvers reused),
  `m20_regime_flip_replay.py` (load_candles reused),
  `src/runtime/regime/detector.py` (regime_label/wilder_adx reused).
- Config files inspected: `config/strategies.yaml` (leg roster, read-only).

## Work Completed

### E-1: entry-quality baseline tool (PR #6285)
- **`scripts/research/m21_entry_baseline.py`** — per-leg entry diagnostics
  from the harness `--emit-trades` jsonl (the flip-replay sweep left emits
  for every runnable donchian/pullback leg under
  `runtime_logs/m20_flip_replay/2026-07-13/` on the trainer) + candles:
  winner MAE-before-peak (p50/p80), early-fail rate + its net_R cost
  (first `--early-bars`=3 bars never above +0R), bars-to-peak, hour/dow
  buckets (min-n 10), entry-bar ADX label + ATR percentile vs trailing
  year (strictly bars ≤ entry — no lookahead). Output: per-leg JSON +
  deficit-ranked `SUMMARY.md` (deficit = −early_fail_net_R) with
  worst-axis hints → selects the E-2 filter axes per leg.
- **`docs/research/entry-refinement-coverage.json`** — the M21
  done-condition matrix skeleton: the same 15-leg roster as the M20 exit
  matrix × 6 entry axes (entry_baseline, confirmation_bars,
  depth_threshold, vol_at_entry, time_of_day, p_win_head), all `pending`.
- Trainer dispatch: relay issue #6286 installs a **queued** E-1 run
  (`/tmp/m21_e1_queued.sh`) that waits for the peak-retarget rounds to
  drain (`pgrep` wait-loop), then re-pulls `main` until the tool lands,
  then runs the fleet baseline into `runtime_logs/m21_entry_baseline/`.

## Validation
- Smoke test on the synthetic pullback emit + candle pair (29 trades):
  all fields populate (early-fail 20.7% / −2.31R; regime bucket; ATR
  percentiles; ranked SUMMARY.md renders). `ruff check` clean.

## Docs Updated
- This sprint log (new).
- `docs/research/entry-refinement-coverage.json` (new skeleton).

## Follow-ups
- Collect the fleet E-1 deficit ranking → pick E-2 axes per leg.
- Peak-retarget round verdicts (M20 tail) → possible 4h/2h shadow heads.
- E-2 harness entry-filter flags + fleet sweep (next after E-1 evidence).

## E-1 fleet baseline — RESULTS (2026-07-13)

Fleet run complete on the trainer (43 legs; relay #6305 launch, #6306
readout; `runtime_logs/m21_entry_baseline/2026-07-13/`). Headline findings:

- **The donchian family is the entry-quality problem.** ~20% of donchian
  entries never trade above +0R in the first 3 bars: trend_donchian_eth
  −144.5R of early-fail cost (n=957; the leg is net −25.7R overall),
  trend_donchian (BTC) −79.3R, sol −58.7R, the 4h legs −10…−37R each,
  and the shadow trend_donchian_1h −187.4R at n=1263 confirms the shape
  at scale. This is the false-breakout share; **E-2 axis: confirmation
  bars + tighter depth (min_confidence) cells, donchians first.**
- **2h pullbacks moderate** (12.8–19.0% early-fail, −2.9…−30.6R) with
  hour-of-day pockets (eth hour:10, htf hour:16, xrp hour:14) — worth
  time-of-day cells only where a pocket survives the yearly walk-forward.
- **1d equity/futures trend legs are clean** (0–13.8% early-fail, tiny
  cost) — no E-2 effort there. Outlier: slv_pullback_1d (19.9%, −29.3R).
- **1h equities**: low early-fail but real absolute cost on busy legs
  (tlt −25.6R, qqq −17.8R); late-session (19–20 UTC) negative pockets.
- **xauusd/mgc 1h**: near-zero early-fail but trending-regime entries
  −20.9R — a vol-at-entry / regime-shaped deficit, not confirmation.
- 4h crypto legs share a negative **hour:0 (UTC midnight bar)** pocket
  (ada/avax/eth/xrp) — a candidate cell, walk-forward-gated.

`entry_baseline` column marked shipped across the matrix (squeeze/fvg
blocked — no emit in the flip-replay set; they need their own
`--emit-trades` runs). **Next: E-2 confirmation-bar + depth flags in the
donchian/pullback harnesses, swept config-exact on the donchian legs
first.**

## E-2 donchian round-1 sweep — RESULTS + Tier-3 batch 1 (2026-07-13)

Fleet sweep complete (23 donchian-family legs × confirm_1/confirm_2/
depth+0.10/+0.20; relay #6309 launch, #6319/#6321 readouts;
`runtime_logs/m21_entry_sweep/2026-07-13/`). 9 legs produced gate passes.

**Tier-3 batch 1 (depth-only — `min_confidence` already has a live
effect, YAML-only):** trend_donchian 0.6→0.7 (wf 5/6), trend_donchian_sol
0.6→0.8 (6/6, dominates 0.7), sol_prop 0.6→0.8 (5/6), xrp_4h 0.6→0.8
(5/6), avax_4h 0.6→0.7 (4/6), xauusd_1h 0.0→0.1 (5/6), mgc_1h shadow twin
0.0→0.1. Draft PR opened for operator approval.

**Parked (need the live signal-builder confirmation twin before declare):**
trend_donchian_1h confirm_2 (shadow; wf 6/6 — the round's biggest effect,
IS −38.9→−9.5 net_R), xauusd confirm_1 (stronger than its depth cell),
xrp_4h confirm_1 (depth declared instead), scha confirm_1 (tiny). E-2
batch 2 = the live confirm twin + these declares.

**Honest negatives:** ETH 1h/prop/4h (the fleet's worst E-1 deficit is
NOT fixed by depth/confirmation — points at E-3 P_win or structural),
sol/ada 4h, mes, all 1d equity trend legs except scha, slv/uso 1h.

## E-2 batch 1 — APPROVED, MERGED, ACTIVE (2026-07-13)

Operator approved in chat; #6322 merged (squash `9155f87`) after two
param-pinning test updates surfaced by full CI (prop exit-variants +
breakout-prop wiring — sol pins moved to 0.8 with the sibling-parity
contract kept). Deployed via `restart-bot-service` (#6328, restart
14:29:59Z, service active); `/api/diag/version` confirms the worktree at
`9155f87` so the restart loaded the new YAML. First-fire checks logged as
BL-20260713-E2-DEPTH-FIRST-FIRE.

## E-2 batch 2 — live confirmation twin + confirm_1 declares (2026-07-13)

Operator granted proceed-and-merge authority ("keep moving forward…
anything we need to merge, you can merge it"). Batch 2:

- **Live twin**: `trend_donchian._confirmed_breakout` — stateless N-bar
  lookback replicating the harness pending semantics exactly (signal-bar
  anchored channel edge, held closes, opposite-raw-breakout cancel with
  long_only honoured, depth gate at the signal bar, entry at the latest
  close). `confirm_bars` in `_DEFAULTS` (0 = byte-identical) + threaded
  into `base_args` so future sweeps stay config-exact. Harness-parity
  test proves the twin fires on exactly the harness's entry bar.
- **Declares**: xauusd_trend_1h + mgc_trend_1h `confirm_bars: 1`,
  REPLACING the batch-1 depth cell (confirm_1 dominates IS net/dd + OOS
  dd at the 0.0 base; one lever per leg — no untested stacking).
- **Scope cuts**: trend_donchian_1h confirm_2 NOT declared — the leg is
  RETIRED (enabled:false; config adopted into the flagship) so a declare
  is inert, and the cell leaves it net-negative anyway. scha confirm_1
  skipped (4/6, +0.3R — not worth it). xrp_4h keeps depth_0.8.

## E-2 round 2 — pullback confirm sweep: HONEST NEGATIVE (2026-07-13)

Fleet sweep complete (19 pullback legs × confirm_1/confirm_2; relay #6345
launch, #6346 readout). **18/19 legs fail** — the pullback trigger already
embeds a resume-close confirmation, so an extra confirm bar mostly costs
entry price without cutting enough early-fails. Sole pass:
ada_pullback_2h confirm_2 at wf 4/6 — the weakest possible pass, alone
across the fleet; **PARKED** (a pullback live twin for one marginal cell
fails the cost-benefit; the scha precedent). Matrix updated fleet-wide.

**E-2 is now complete for both harness families.** Shipped live:
7 depth declares (batch 1) + the donchian confirmation twin with
xauusd/mgc confirm_1 (batch 2). Everything else is an honest negative
or a recorded park. **Next: E-3 — the P_win entry head** (first_touch
+1R-before-−1R label; ETH's only remaining lever; the M18 allocator
unlock), and the walk-forward-gated time-of-day cells remain optional
follow-ups.

## E-3 tooling: P_win entry head (2026-07-14)

Tier-1 tooling for the E-3 round (design § E-3; the M18 allocator's
P_win unlock and ETH's only remaining lever):

- **Labels in the E0 builder** (`build_exit_head_dataset.py`):
  per-trade `first_touch_1r` (+1R bar-high basis touched BEFORE −1R
  bar-low basis; a both-in-one-bar crossing counts conservatively as
  loss-first — the intrabar-stop-first convention) + `reaches_2r`,
  stamped on every row; plus `entry_confidence` (the emit's live-parity
  depth confidence; None on pre-E-3 emits / live rows).
- **Harness emit** (`backtest_trend.py`): the breakout-depth confidence
  is now computed unconditionally at the signal bar (gating behaviour
  unchanged — still only enforced when `min_confidence > 0`), carried
  through the pending-confirmation path, and written to the emit
  (`Trade.confidence`). The pullback harness already emitted it.
- **Trainer** (`scripts/ml/train_entry_head.py`): LightGBM on the
  `age_bars==0` slice, ENTRY-TIME features only (`mom_8`,
  `donchian_mid_dist_atr`, `hour_of_day`, `dayofweek`, `is_long`,
  `entry_confidence`); per-year purged folds (7-day embargo on the
  trade's LAST bar); per-fold OOS AUC + reliability + the **τ-skip
  replay** (survivors with P(win) ≥ τ, entry-time order, actual
  final_r — net_R + running-peak maxDD vs taking every trade) + a
  per-τ walk-forward beats-actual roll-up + live validation.
- **Round driver** (`scripts/research/m21_entry_head_round.py`):
  per-leg config-exact re-emit (pre-E-3 emits lack confidence) →
  per-`(family, tf)` pooled dataset build → entry-head train/replay,
  emit rows re-stamped with the leg name so same-symbol legs can't
  collide on `trade_key`. Donchian family first, ETH priority.
- Tests: `tests/test_entry_head_labels.py` (win/loss/both-in-one-bar
  label contract + entry_confidence stamping); confirm-bars lever/twin
  + exit-head parity suites re-run green.

Gate (unchanged from the design): OOS AUC materially > 0.55 AND a
τ-skip arm beats actual on net_R AND maxDD across the walk-forward AND
the live set agrees in sign. Consumer: M18 allocator ranking first;
any per-leg live entry gate is a separate Tier-3 ask.

## E-3 round 1 (donchian) — RESULTS (2026-07-14)

Round complete on the trainer (relay #6359 launch, #6368/#6371 readouts;
`runtime_logs/m21_entry_head/2026-07-14/`):

- **donchian_1h pooled crypto head (4,039 entries): GATE PASS — the
  round's headline.** OOS AUC 0.61–0.672 in ALL 5 yearly folds;
  τ-skip beats actual on net_R AND maxDD **5/5 folds at τ ≤ 0.35**
  (2022: actual −57.0 net_R / 77.1 dd → +46.0 / 15.6 keeping 50%;
  2025: −14.9 / 50.7 → +131.9 / 21.8 keeping 59%). Live validation:
  n=13, AUC 0.875, τ0.35 +16.6/4.0 vs actual +9.3/9.4 — sign agrees.
  This is the proven P_win input M18 P2/P3 was parked on
  (`docs/research/M18-allocator-backtest-findings-2026-06-29.md`: the
  EV scorer's 2026-06 ranker was AUC ≈ 0.51). **Consumer wiring is the
  next Tier-3 ask** — nothing live reads the head yet.
- **donchian_4h (913 entries): honest negative on the replay gate.**
  AUC real (0.61–0.727) but τ-skip beats actual only 2/4 folds at
  every τ — it rescues bad years and costs good ones (2024 actual
  +119.3 → ~+44).
- **Driver bug found + fixed (PR #6369):** the per-leg restamp used raw
  leg names, which for legs not containing "donchian"/`trend_`-prefixed
  (mgc/slv/uso/xauusd 1h, all `*_trend_long_1d`) fell out of the
  builder's family pooling — the 1d group's train step 404'd and the 1h
  pool was crypto-only. Restamp now family-prefixes when needed;
  `--tf` added for group reruns. 1d + stranded-1h rerun queued.

Matrix updated: `p_win_head` passed_unshipped (1h crypto donchians) /
honest_negative (4h) / pending-rerun (1d + stranded 1h); stale
`depth_threshold` statuses from the batch-1 shipping corrected to
`shipped`.

## E-3 round 2 (corrected pools + pullback family) — RESULTS (2026-07-14)

Round complete (relay #6376 launch, #6378/#6379 readouts;
`runtime_logs/m21_entry_head_r2` + `_pb`):

- **donchian_1h FULL pool (5,244 entries, now incl. mgc/slv/uso/xauusd +
  shadow legs): GATE PASS strengthened** — τ=0.35 beats actual on net_R
  AND maxDD **6/6 yearly folds**; AUC 0.576–0.627; live n=13 AUC 0.792,
  τ0.35 +17.6/4.0 vs actual +9.3/9.4. The round-1 crypto-pool pass is
  confirmed on the corrected full pool.
- **pullback_2h (1,948 entries): PASS at τ=0.4 — 4/4 usable folds**,
  AUC 0.553–0.626. Caveat recorded: live n=23 is a losing window
  (−19.4R actual); the skip improves it (−8.4R, dd 25.2→14.2) but stays
  negative — directionally consistent, small n.
- **pullback_1h: honest negative** (best τ 2/8 folds; AUCs straddle 0.5).
- **pullback_1d: honest negative** (fold AUCs erratic 0.40–0.59).
- **donchian_1d: blocked:insufficient_n** (509 entries across 8 legs →
  zero usable yearly folds at min-fold-trades 50).

**E-3 verdict: the P_win entry head is REAL on the intraday trend/
pullback families** (donchian-1h 6/6, pullback-2h 4/4) and not on the
sparse 1d fleets. Matrix `p_win_head` column verdicted fleet-wide.
**Next: the M18 allocator P_win wiring Tier-3 proposal** (the consumer
this head was built for; M18 P2 was parked on exactly this input).
