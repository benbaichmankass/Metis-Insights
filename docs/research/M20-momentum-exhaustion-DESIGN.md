# M20 Phase 4 — Momentum-Exhaustion Exits (DESIGN)

**Status:** DESIGN APPROVED-TO-START (operator, 2026-07-12: "make sure it's all
planned and noted … then we can start according to the priorities").
**Owner surface:** this doc (design of record) + the ROADMAP M20 row (status)
+ `docs/research/exit-refinement-coverage.json` (per-leg verdicts as work
items complete) + the `exit-refinement` skill (the binding pipeline every
item below runs through).

## 1. Problem statement (the evidence that motivates this phase)

The M20 exit machinery so far reads **position state** — R-geometry (open_r /
mfe_r / mae_r / giveback_r), age, stagnation, distance to stop — and almost
nothing about whether the **market move itself is exhausting**. Consequences,
all on record:

- The live 1h donchian exit head (`exit-head-donchian-1h-v1`, E3 live
  2026-07-12) gates because its *conditional* shape (only act below +0.5R)
  side-steps the question; the unconditional head bleeds trend years.
- Exit-head rounds 2-3 (4h donchians, 2h alt pullbacks) FAILED the E1 gate
  precisely on trend-year giveback: 4h 2023 actual +67.8R vs +3.0R for the
  best conditional arm — the model cannot tell "pause inside a live trend"
  from "trend is over" (sprint log S-M20-EXIT-REFINEMENT-2026-07-12 § rounds
  2-3; `runtime_logs/m20_exit_head/{4h,2h}` on the trainer).
- The chop-hold quantification stands: 90d real-money mean MFE +1.92R vs
  realized −0.16R (giveback 2.08R) — the money is in exiting closer to the
  peak, which is a momentum-exhaustion question.

**Goal:** detect when the momentum that justified a trend/momentum entry is
over, and start exiting there — as hard levers where a rule suffices and as
ML heads where the signal is conditional.

## 2. Work items, in priority order (operator-acked 2026-07-12)

Priorities follow "fastest to a shippable, per-leg walk-forward verdict
first". Every item ships through the fast-gate doctrine
(`M20-exit-head-PROGRAM.md` § doctrine): offline purged walk-forward IS the
confidence gate; live shadow is a mechanical parity check in hours-days;
Tier-3 to change any live exit.

### P4.1 — Trail-decay-on-exhaustion hard lever (FIRST)

*"Ride loose while momentum is alive, choke it once it stalls."*

- **Mechanic:** the chandelier `trail_mult` is no longer constant; it decays
  when an arming condition is met. Two decay families to sweep:
  - **R-armed:** once `peak_r >= arm_r` (e.g. 2.0), trail_mult steps from
    the leg's base (e.g. 4.0) to `tight_mult` (e.g. 2.0). One-way ratchet.
  - **Stall-armed:** once `bars_since_new_peak >= stall_bars` (e.g. 6 native
    bars without a new favourable extreme), same step. Re-arms if a new peak
    prints before the stop is hit (momentum resumed).
- **Harness:** `scripts/research/backtest_trend.py` + `backtest_pullback.py`
  grow `--trail-decay-arm-r`, `--trail-decay-stall-bars`,
  `--trail-decay-tight-mult` (default 0/0/off = byte-identical). Exit reason
  stays `trail_stop` (it is the same stop, tighter) but the harness tags
  `trail_decayed: true` per trade for attribution.
- **Cells:** per leg, config-exact base vs {arm2R→tight2, arm2R→tight2.5,
  stall6→tight2, stall8→tight2.5, arm1.5R+stall6→tight2}. Same
  IS/OOS + yearly-walk-forward gate as the fleet sweep (`beats()` on net_R
  AND maxDD, ≥⅔ folds).
- **Sweep driver:** extend `m20_fleet_exit_sweep.py::cells_for` with the
  decay cells (only for legs whose family harness has the lever).
- **Live monitor:** on a PASS, `_trail_decay(meta, cfg…)` in the family
  monitor adjusts the effective trail_mult before the ratchet computation —
  same YAML-declared / annotate-undeclared contract as the stale/giveback
  levers. Tier-3 per leg.
- **Interaction rule:** one lever per leg stands (the M20 finding: combos
  underperform singles) — a leg that just shipped a stale/giveback/trail
  cell is NOT stacked with a decay cell without a fresh combo A/B.

### P4.2 — "Peak-is-in" exit head (retargeted label)

*Predict whether the peak is already in, not whether holding pays.*

- **Label (E0):** `peak_is_in = (future_mfe_delta <= eps)` where
  `future_mfe_delta = final_mfe_r − mfe_r(t)` and `eps = 0.25R` (mirror of
  the `holding_pays` +0.25 threshold). Pure truncation observable — no
  barrier re-simulation (the T0.4 lesson). Add the column to
  `build_exit_head_dataset.py` rows ADDITIVELY (existing labels unchanged).
- **Policy shape:** act on P(peak_is_in) > τ_hi — but the ACTION is not a
  full close by default: sweep three action arms in the E1 replay:
  (a) full close, (b) trail-tighten (halve trail_mult — connects to P4.1),
  (c) close only if ALSO open_r ≥ +0.5R (protect-the-runner inverse of the
  live head's shape: the live head cuts losers, this one banks winners).
- **Train/eval:** `train_exit_head.py` grows the label switch
  (`--target holding_pays|peak_is_in`) + the new policy arms. Same purged
  yearly walk-forward + live-source sign agreement.
- **First family:** donchian/1h (the pipeline that already gates) — datasets
  exist; a re-run is one `m20_exit_head_round.py` invocation once the
  builder lands. Then 4h donchians (the family the holding_pays head failed).
- **E2/E3:** identical machinery to the live head (`exit_head_shadow.py`
  scores any artifact the mirror carries; a second model_id rides the same
  channel). Operator promotion gate unchanged.

### P4.3 — Exhaustion features for the exit heads

*Give the heads market-state eyes.*

- **Feature block (E0, computed strictly from bars ≤ t, same leakage guard):**
  - `mom_z_decay` — rolling k-bar return z-score now vs at the trade's peak
    bar (momentum fading while price holds).
  - `streak_break` — length of the favourable consecutive-close streak,
    and bars since it broke.
  - `vol_at_extreme_ratio` — volume on the most recent new-extreme bar vs
    median volume (climax vs fade). Null-safe where volume is absent.
  - `atr_impulse_phase` — ATR expansion-then-contraction: atr_now /
    atr_at_peak (an ending impulse contracts).
  - `band_extension_pct` — close's distance above/below the donchian mid in
    ATRs, as a percentile of the trade's own history (blow-off extension).
  - `failure_swing` — new favourable extreme printed but close failed to
    confirm (closed back inside the prior bar's range) within the last 3 bars.
- **Where:** `build_exit_head_dataset.py` (offline) + the live parity twin in
  `exit_head_shadow._compute_features` — BOTH sides in one PR so live==train
  parity holds by construction (the spot-vs-linear lesson: parity checks run
  against the live feed's market).
- **Gate:** re-run donchian/1h E1 with and without the block — adopt only if
  OOS AUC and the τ-replay improve; then re-run the failed 4h/2h rounds.

### P4.4 — MFE-percentile adaptive exits

- Per-leg winner-MFE distributions are already computable from the E0
  datasets. Lever: when `open_r >= P80(winner MFE)` for that leg, tighten
  trail to `tight_mult` (reuses the P4.1 decay mechanic with a
  distribution-derived arm instead of a fixed R). Harness lever
  `--trail-decay-arm-pctl 80`; the percentile table is baked per leg at
  sweep time from the leg's own harness trades (train window only — no
  test-fold leakage).

### P4.5 — Regime-flip exits

- Exit trigger: the ML regime label that gated the ENTRY flips (trend→chop
  per the advisory 15m heads / `regime_policy` cells) while the position is
  open. Offline first: replay regime labels over harness trades (labels from
  the regime heads' historical scores where they exist; else the frozen
  detector) and measure exit-at-flip vs actual. Only BTC (and any symbol
  with an advisory head) can go live — per-symbol resolution, same as the
  vol gate. Tier-3, and it touches the regime plumbing — scope carefully.

### P4.6 — Exhaustion-conditioned partial banking

- Fixed +1R banking rungs FAILED the M20 sweep (parked). The untested
  variant: bank `bank_frac` (0.5) on the FIRST exhaustion signal
  (P4.1's stall-arm or P4.2's peak-is-in) after `peak_r >= 1R`, run the
  remainder on the trail. Harness-only until it beats run-the-winner —
  and it must also beat the P4.1 pure-tighten arm to justify the added
  execution complexity (partial closes are a new order path on some venues).

## 3. Sequencing & gates

| step | item | where | gate to proceed |
|---|---|---|---|
| 1 | P4.1 lever + fleet decay sweep | harness + trainer | per-leg walk-forward PASS → Tier-3 cell proposal |
| 2 | P4.2 label + P4.3 features (one E0/E1 round, donchian/1h) | trainer | E1 gate (AUC materially >0.55 + τ-replay beats actual AND hard levers AND the P4.1 winner, live sign-agreement) |
| 3 | re-run failed families (4h donchian, 2h pullback) with P4.2+P4.3 | trainer | same E1 gate |
| 4 | P4.4 percentile arm folded into the decay sweep | harness | same as P4.1 |
| 5 | P4.5 regime-flip offline replay | trainer | beats actual on net_R AND maxDD in walk-forward before any live design |
| 6 | P4.6 partial banking | harness | must beat BOTH run-the-winner AND the P4.1 tighten arm |

Anything that passes ships exactly like today's package: draft Tier-3 PR with
the per-leg evidence table, operator approval, activate, first-fire mechanics
check, matrix fold. Anything that fails is recorded as an honest negative in
the coverage matrix (new columns `trail_decay`, `peak_head`, `regime_flip_exit`
as items reach verdicts).

## 4. Non-goals / guardrails

- **No entry-side changes** — this phase is exits only.
- **No proxy-data head training** (levers OK on proxies; heads need native
  history — MES/MGC/MHG heads stay blocked on `MB-20260712` native pulls).
- **One lever per leg** unless a combo A/B explicitly passes.
- **No new default-on order-path behaviour**: every new close path is
  YAML-declared per leg; undeclared = annotate-only soak rows.
- Live-parity twin features land in the SAME PR as offline features.

## 5. Tooling touchpoints (all existing)

`scripts/research/backtest_{trend,pullback}.py` (levers) ·
`scripts/research/m20_fleet_exit_sweep.py` (cells/gate) ·
`scripts/ml/build_exit_head_dataset.py` (labels/features) ·
`scripts/ml/train_exit_head.py` (targets/policy arms) ·
`scripts/research/m20_exit_head_round.py` (one-command rounds) ·
`scripts/ml/export_exit_head.py` + trainer mirror (artifact channel) ·
`src/runtime/exit_head_shadow.py` + family monitors (live side) ·
`.claude/skills/exit-refinement/SKILL.md` (the binding pipeline).
