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
