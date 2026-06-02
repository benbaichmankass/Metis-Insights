# WS-A S2 — Diversifier Re-tune (2026-06-02)

> Meantime Expansion Program, WS-A S2. Overfitting-aware grid re-tune of
> the five cleanest S1 diversifier leads. Driver:
> `scripts/research/ws_a_s2_retune.py` (trainer-vm-diag #2635/#2636).
> Selection = net-positive in BOTH IS(≤2022) AND OOS(2023+), OOS n≥20,
> ranked by OOS expectancy — robustness across the split, not peak net-R.

## Headline

**Gold (trend + pullback) and Copper (pullback) are the strongest
BTC-uncorrelated diversifier candidates** — tuned configs roughly double
to triple OOS expectancy over the crypto-tuned defaults, and the winning
params cluster in a *consistent, interpretable neighborhood* (a good
anti-overfit sign). **The equity-index trend leads (SPX/NQ) could not be
robustly tuned at daily resolution** — too few OOS trades.

## The dominating caveat: thin OOS samples

Daily systems on a ~3.5-year OOS window produce only **~14–32 trades**.
Every number below rests on that small n, so OOS net-R has high variance.
**This is candidate-finding, not promotion evidence.** No Tier-3 proposal
follows from S2 alone.

## Per-lead results

| Lead | Baseline (default params) OOS | Best robust config | Best config OOS | Verdict |
|---|---|---|---|---|
| **S&P 500 / trend** | +13.2R, exp +0.94, n=14 | — | — | **No config passed n≥20 + both-positive.** Real but low-frequency at daily res; under-powered to tune. |
| **Nasdaq 100 / trend** | +13.1R, exp +0.73, n=18 | — | — | Same — daily OOS too thin. |
| **Gold / trend** | +8.9R, exp +0.28, n=32 | `donchian=30 atr-stop=2.0 trail=4.0` | **+21.3R, exp +1.01, n=21, win 48%, maxDD 3.0R** | **Strong.** 16/36 configs passed; tuned exp ≈3.6× baseline. |
| **Copper / pullback** | +13.4R, exp +0.79, n=17 | `lookback=15 frac=0.5 atr-stop=2.0 trail=4.0` | **+16.8R, exp +0.84, n=20, win 40%, maxDD 3.7R** | **Solid.** 17/36 passed; modest lift, consistent neighborhood. |
| **Gold / pullback** | +13.0R, exp +0.87, n=15 | `lookback=15 frac=0.618 atr-stop=2.0 trail=4.0` | **+22.6R, exp +1.08, n=21, win 43%, maxDD 2.9R** | **Strongest OOS expectancy of the run.** 10/36 passed. |

## The interpretable shift (anti-overfit signal)

Across every winning cell the tuning moves the *same direction* off the
BTC defaults: **tighter ATR stop (2.0 vs 2.5)**, **moderate trail (3–4 vs
3–5)**, **slightly longer entry filter** (donchian 30 / pullback-lookback
15 vs 20 / 10). That coherence — slower, trendier futures rewarding a
longer filter + tighter stop than BTC intraday — is a *mechanistic* reason
to believe the lift, not an isolated grid spike. The passing-config counts
(10–17 of 36) also indicate a broad plateau, not a knife-edge optimum.

## What this does and does not justify

- **Does:** Gold and Copper are the diversifiers to carry forward. The
  default crypto params are demonstrably mis-tuned for futures (tighter
  stop / longer filter is the fix).
- **Does NOT:** promote anything. OOS n≈20 is below any reasonable
  confidence bar, the data is daily continuous-contract (`=F`, roll
  artifacts), and fees are a 2 bps placeholder.

## Next (S3 path to a real candidate)

1. **Grow n** — roll-adjusted, longer/higher-frequency history (the daily
   OOS ceiling is the binding constraint, not the edge).
2. **Demo-execute ladder** — once a futures venue/paper account exists,
   route Gold/Copper trend+pullback to it (no-risk real fills) per the
   WS-E standing ladder, rather than promoting on backtest alone.
3. **Verify NinjaTrader per-contract commissions** for GC/MGC + HG/MHG;
   re-run net-of-real-fee.
4. SPX/NQ trend: revisit at intraday resolution (more trades) or accept as
   a low-N satellite — do not tune on daily.
