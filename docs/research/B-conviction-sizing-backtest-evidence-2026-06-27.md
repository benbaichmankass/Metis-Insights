# B — conviction-sizing backtest evidence (2026-06-27)

First evidence run for Design B's apply path, via the new `--conviction-sizing`
A/B in `scripts/backtest_system.py`. Run on the trainer over BTCUSDT
2021-01-01 → 2026-06-01, roster `trend_donchian + squeeze_breakout_4h`
(`htf_pullback_trend_2h` emitted no fills on this window; sub-1h strategies can't
build on the 1h base — a roster/tf constraint, not a sizing effect).

## Result — conviction sizing FAILS the gate on this evidence

| Metric | Flat (baseline) | Conviction-sized | Δ |
|---|---|---|---|
| Net PnL | $379 (3.79%) | $463 (4.63%) | +$84 |
| **Max drawdown** | $1,269 (11.63%) | $8,606 (**52.73%**) | **+41pp** |
| **Return/DD** | **0.30** | **0.05** | **−0.25 (6× worse)** |
| Trades / WR | 458 / 30.79% | 456 / 30.92% | ~flat |
| Attribution | trend +$349, squeeze +$27 | trend **−$415**, squeeze **+$869** | risk re-concentrated |

Conviction sizing bought ~flat return (+$84) for a **4.5× larger max drawdown**
(11.6% → 52.7%); the risk-adjusted return (ret/DD) collapsed 0.30 → 0.05. The
attribution flip shows why: the (symmetric/enlarging) sizing scaled **up** the
high-`c_strat` trades, concentrating risk and turning the dominant trend sleeve
net-negative.

**Design B gate criterion** ("advance G2→G3→G4 only if non-inferior return with
reduced maxDD, or improved Sharpe") → **NOT MET.** Do NOT graduate the conviction
apply path to `apply`/symmetric on this evidence.

## Honest caveats (what this does and doesn't say)

1. **Offline conviction ≈ `c_strat` only.** The heads (`c_setup`/`c_wr`/`c_reg`)
   are not replayed offline, so this tests "size by the **calibrated strategy
   confidence alone**" — which Design §4b already flagged as a weak discriminator.
   The full multi-input conviction (with the live head inputs + `c_reg` once the
   calibrator lands) could behave differently; this run cannot measure it.
2. **This is the symmetric/enlarging case.** The harness sizes by
   `conviction × 2% budget` (can enlarge). The apply path's default
   `DIRECTION=reductive` (`min(conviction, flat)`) can only ever *shrink* and so
   cannot blow out drawdown via enlargement — it wasn't tested here but is
   safe-by-construction (lower return floor, never higher DD).
3. Small roster (2 strategies, trend-dominated). A broader/multi-symbol run would
   sharpen the picture but is unlikely to reverse a 4.5× DD blow-up.

## Implication for the rollout

- **Stay at `annotate` (observe-only).** Ship B's apply code (done, default-off),
  run the live `annotate` soak to accrue the would-be **full** conviction size on
  real packages, and re-evaluate. Do **not** flip `CONVICTION_SIZING_MODE=apply`
  (and certainly not `DIRECTION=symmetric`) on `c_strat`-alone evidence.
- If apply is ever used before the multi-input conviction proves out, **reductive
  only** — it can't worsen drawdown.
- The real graduation evidence needs the **live head inputs** (which is exactly
  why A's per-bar advisory scoring + the c_reg calibrator matter) + a per-strategy
  `rank_auc>0.5` readiness pass, then a re-run of this A/B with the full conviction.

This is the backtest gate doing its job: it caught that the cheapest version of
conviction sizing (symmetric, `c_strat`-only) is risk-destructive before it
reached live money.
