# eth_pullback_2h on Breakout — swap-aware funded EV + a swap-robust variant

_2026-06-25. Tier-1 research (no live-path change). Driver: the
`validate_alt_prop` gate (`scripts/prop/validate_alt_prop.py`) + the system
engine (`scripts/backtest_system.py`) run on real ETHUSDT 5m candles
(2021-03-15 → 2026-06-18) against `config/prop_rulesets/breakout.yaml`. Trainer
relay runs #4514 / #4522 / #4528._

## TL;DR

- The **pre-swap** EV that looked like a green light for routing `eth_pullback_2h`
  to the Breakout prop account (`breakout_1`) was **dangerously optimistic**.
- Charging Breakout's flat **daily swap (0.09%/day)** flips the strategy's raw
  realised edge **negative**: pre-swap +$384 → **post-swap −$211.56** over 5y,
  with the swap eating **155% of gross**.
- Root cause is **structural**: `eth_pullback_2h` is a *let-winners-run*
  trend-follower (`trail_mult: 5.0`, no real TP — `tp_r: 50` sentinel). It holds
  for days-to-weeks, and 0.09%/day compounds brutally over long holds.
  **Trend/trail strategies are swap-poison on a daily-swap CFD venue.**
- A **swap-robust exit variant** — `tp_r: 6.0`, `trail_mult: 3.5` (everything
  else == live) — is the sweep winner: it *raises* pre-swap gross to +$618,
  halves swap drag to 87%, and flips realised **post-swap positive (+$80)**. It
  passes the same funded-EV gate baseline cleared (12-mo EV +$538 @72.7%
  P(net>0), ROI/fees 3.38, **walk-forward 4/4 folds EV-positive**).
- **But** realised post-swap is still thin and regime-dependent (negative in
  2 of 4 OOS folds; the EV passes on prop-account economics, not a fat raw
  edge). ⇒ **Decision: keep eth_pullback_2h OFF breakout_1. The swap-robust
  variant is a SHADOW/soak candidate at most — not a live-money promotion.**

## The numbers

Funded EV at the validated 1.5% risk cell, daily_swap 0.09%/day, ruleset
`config/prop_rulesets/breakout.yaml`, n_paths 2000–3000.

| config | trades | realised pre-swap | realised **post-swap** | swap drag | 12-mo EV | P(net>0) | ROI/fees |
|---|---|---|---|---|---|---|---|
| **baseline (live eth_pullback_2h)** — tp_r 50, trail 5.0 | 300 | +$383.96 | **−$211.56** | 155.1% | +$581 | 74.7% | 2.98 |
| tp_r 6.0, **trail 3.5** ← winner | 344 | +$618.39 | **+$80.42** | 87.0% | +$538 | 72.7% | 3.38 |
| tp_r 4.0, trail 3.5 | 344 | +$477 | −$52 | 110.9% | +$483 | 70.2% | 3.01 |
| tp_r 4.0, trail 2.5 | 432 | +$295 | −$108 | 136.4% | +$291 | 57.2% | 2.27 |
| tp_r 3.0, trail 2.5 | 433 | +$345 | −$56 | 116.4% | +$305 | 58.8% | 2.40 |
| tp_r 3.0, trail 2.0 | 516 | −$47 | −$362 | — | +$161 | 44.0% | 1.31 |

The baseline cell reproduces the authoritative `validate_alt_prop` run exactly
(300 trades / −$211.56 / EV +$581 @74.7%), confirming the sweep is sound.

**Reading the curve:** a *moderate* tightening is optimal. The live 5.0 trail
gives back too much (a tighter 3.5 trail *improves* gross), and a generous 6R
cap trims the rare runaway without choking winners. *Over*-tightening (tp_r 3,
trail 2.0) trades far more (516 trades) but cuts winners faster than it saves
swap — net deeply negative.

### Walk-forward of the winner (tp_r 6.0 / trail 3.5), 4 sequential OOS folds

| fold | window | trades | realised post-swap | 12-mo EV | P(net>0) |
|---|---|---|---|---|---|
| 1 | 2021-03 → 2022-07 | 89 | −$80 | +$246 | 57.8% |
| 2 | 2022-07 → 2023-10 | 78 | +$98 | +$646 | 75.4% |
| 3 | 2023-10 → 2025-02 | 75 | −$42 | +$452 | 68.3% |
| 4 | 2025-02 → 2026-06 | 88 | +$197 | +$798 | 82.8% |

EV-positive **4/4 folds** (passes the gate), but realised post-swap is negative
in folds 1 & 3 — the EV survives those folds on Breakout's asymmetric account
economics (capped downside = lose the eval fee; upside compounds across
re-funded accounts), not on a robust per-trade edge. The strength is
front-loaded into the recent regime (fold 4).

## Caveats

- **Realised-only EV optimism** (carried from the EV engine): a per-trade
  bootstrap has no intraday open-position swing, so daily-loss / drawdown
  breaches — and the fee churn they cause — are *under*-counted. Swap is now
  charged, but true EV remains, if anything, optimistic.
- **Swap rate is a public-review figure** (0.09%/day). If Breakout's real swap is
  higher, the margin evaporates; if lower, the case improves. Worth confirming
  against an actual statement before any live wiring.
- **ETH ≈ 0.7–0.9 correlated with BTC** — a prop pullback leg buys frequency,
  not diversification; the account caps assume concurrent drawdown.

## Decision & routing

- `breakout_1.strategies` stays `[trend_donchian_sol (live), trend_donchian_eth
  (shadow)]`. **`eth_pullback_2h` is NOT added to breakout_1** — its live
  let-winners-run exits are net-negative after the Breakout swap.
- `eth_pullback_2h` is unaffected on **bybit_1 (paper)** and **bybit_2 (real,
  ~$100)** — Bybit charges cheap 8h perp funding, not the daily CFD swap, so the
  swap-drag finding is venue-specific to Breakout.
- The **swap-robust variant** (`eth_pullback_prop_2h`, tp_r 6.0 / trail 3.5) is
  drafted as `execution: shadow` on `breakout_1` (observe-only, logs order
  packages, never sends a ticket) — a soak candidate mirroring where
  `trend_donchian_eth` sits. Promotion past shadow is the operator-gated Tier-3
  switch, contingent on (a) the soak confirming the edge live and (b) a real
  Breakout swap-rate check.

## Reproduce

Research-harness registration only (NOT a config/order-path change) — register
the strategy name into `scripts/backtest_system.ROSTER` reusing the shared
`htf_pullback_trend_2h` unit, pin eth_pullback_2h's live base params (the unit
defaults are the 50/0.33/3.0 *scaffold*, not the live 40/0.5), then
`apply_funding_to_ledger(swap_rate_daily=0.0009)` → `run_ev_montecarlo` vs
`breakout.yaml` @ 1.5%. Full drivers in the relay issues above.
