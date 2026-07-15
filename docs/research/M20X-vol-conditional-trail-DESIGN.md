# M20-X — Vol-conditional trailing stop (regime-conditional exits, round 1)

**Status:** research design (Tier-1). 2026-07-15.
**Program:** M20 exit-refinement extension — first regime-conditional exit lever.
**Motivation:** the M21 round-4 vol-at-entry lever (shipped 2026-07-14, #6434)
proved the trailing-ATR-percentile signal carries real edge on ENTRY selection
across four legs (walk-forward 4/6, IS+OOS net_R + maxDD beats). This round
tests whether the SAME causal signal improves EXITS: condition the chandelier
trail multiplier on the current bar's vol percentile while a trade is open.

## Hypothesis

The chandelier trail distance is `mult × ATR` — in a vol spike the distance
blows out mechanically (ATR is large) exactly when reversal risk is highest,
giving back open profit; in dead vol, trends stall and the wide trail waits
through the bleed. A trail mult that TIGHTENS when the trailing ATR
percentile is extreme should cut both tails without touching the mid-regime
ride. This is the exit-side mirror of the shipped `vol_skip_{above,below}_pctl`
entry gates.

## Lever (harness first, per the fast-gate doctrine)

`scripts/research/backtest_trend.py` + `scripts/backtest_pullback.py` grow:

- `--trail-vol-above-pctl P` — when the CURRENT bar's trailing ATR percentile
  (same causal `rolling(window).rank(pct=True)` as the entry lever, window
  `--vol-pctl-window`, default 200) exceeds `P`, the effective trail mult
  becomes `--trail-vol-tight-mult`.
- `--trail-vol-below-pctl P` — same, for the dead tail (pctl < P).
- `--trail-vol-tight-mult M` — the tightened mult (0 = lever off,
  byte-identical baseline).

Semantics (identical discipline to the P4.1 trail-decay lever):

- Evaluated per managed bar; the mult is *conditional*, not a ratchet — the
  moment the percentile leaves the tail, the base mult applies again. The
  STOP itself remains a one-way ratchet (never loosens) exactly as today.
- Undefined percentile (window unfilled) ⇒ lever inert on that bar
  (fail-permissive, same as the entry lever).
- Interaction with the trail-decay lever: tightest fired mult wins
  (`min`) — but per the one-lever-per-leg doctrine, sweep cells run this
  lever alone on each leg's config-exact base (which MAY include a shipped
  decay declare — config-exact means whatever main declares).

## Cells (fleet sweep, `m20_fleet_exit_sweep.py` lever `vol_trail`)

Tight mult is config-relative: `max(base_trail/2, 1.5)` (the decay-cell
precedent). Cells per runnable leg:

| cell | args |
|---|---|
| `vt_hot90` | `--trail-vol-above-pctl 0.9 --trail-vol-tight-mult <half>` |
| `vt_hot80` | `--trail-vol-above-pctl 0.8 --trail-vol-tight-mult <half>` |
| `vt_cold10` | `--trail-vol-below-pctl 0.1 --trail-vol-tight-mult <half>` |

Gate: unchanged fast-gate — config-exact base (all shipped declares
threaded), IS **and** OOS beat on net_R **and** maxDD, yearly walk-forward
≥ 4/6. Results land in the M20 exit coverage matrix as a new `vol_trail`
column.

## Live-parity path (only if a cell passes)

The live twin lives in the unit `monitor()` (where the chandelier trail is
computed): same `_trailing_atr_pctl` helper the entry gate already ships in
`trend_donchian.py` / `htf_pullback_trend_2h.py`, applied to the monitor's
fetched candle window (same `limit=200` fetch ⇒ default window fills
exactly). Undeclared params ⇒ byte-identical monitor behaviour. YAML declare
(`trail_vol_above_pctl` / `trail_vol_below_pctl` / `trail_vol_tight_mult`)
is Tier-3, operator-gated, per leg.

## Non-goals

- No regime-ROUTER label reuse in this round (trend axis, ML vol verdict) —
  the ATR percentile is live-computable inside the unit with zero new
  dependencies; router-label conditioning is a later round if this one shows
  the conditioning principle works.
- No combo cells (vol-trail + decay armed simultaneously) unless a combo
  A/B is explicitly run later.
