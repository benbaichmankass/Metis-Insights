# VWAP accuracy improvement — plan (run 2026-05-07-vwap-accuracy)

**Goal:** raise the **accuracy** of the VWAP mean-reversion strategy
(`src/units/strategies/vwap.py`) without falling back on the lazy lever
of "raise the threshold". The previous run (#350,
`experiments/2026-05-03-vwap-improvement/`) already established that
bumping `ENTRY_STD_THRESHOLD` from 1.0σ → 2.0σ trades cadence for
Sharpe; the operator deliberately reverted to 1.0σ to keep cadence and
asked for **accuracy at the current cadence**.

**Production baseline (today, on `main`):**

```
ENTRY_STD_THRESHOLD = 1.0
SL_STD_MULT_DEFAULT = 0.5
TP                  = VWAP (anchor reverts to mean)
risk:reward         = 1:2 at the entry boundary
timeframe           = 5m
symbol              = BTCUSDT
```

The VWAP itself is computed on a **120-bar rolling window** (≈10h at
5m) inside `build_vwap_signal` via `compute_vwap`. σ is the standard
deviation of typical price over the same window.

## Background research

- VWAP slope filter — mean reversion only works when VWAP is flat.
  Steep slope = trending regime, fades get run over.
  ([VWAP & Its Slope — Definedge](https://forum.definedgesecurities.com/topic/3880/vwap-its-slope-the-silent-voice-of-market-truth),
  [Mean reversion — Forextester](https://forextester.com/blog/mean-reversion-trading/))
- Anchored VWAP from session open is the institutional standard reading;
  rolling-window VWAP drifts and loses reference to "today's auction".
  ([Anchored VWAP — TrendSpider](https://trendspider.com/learning-center/anchored-vwap-trading-strategies/),
  [Databento — VWAP in pandas](https://databento.com/blog/vwap-python))
- ADX < 25 / Hurst < 0.5 / flat slope all proxy "ranging regime where
  reversion works"; ADX > 25 / strong trend = skip the fade.
  ([QuantPedia — Trend & Mean-Reversion in Bitcoin](https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/),
  [QuantMonitor — Regime classification](https://quantmonitor.net/how-to-identify-market-regimes-and-filter-strategies-by-trend-and-volatility/))
- Volume-spike + reversal candle at the σ band is the classical
  high-probability mean-reversion entry trigger.
  ([VWAP Mean Reversion — VolatilityBox](https://volatilitybox.com/docs/vwap-mean-reversion-strategies/),
  [VWAP Strategy — ChartsWatcher](https://chartswatcher.com/pages/blog/vwap-strategy-trading))
- HTF EMA-200 alignment was tested in the previous run as a hard filter
  (Sharpe lift +0.35, but trade-count drop did not justify standalone
  adoption). Re-test as a **soft** filter that only rejects strong
  counter-trend trades (within 1% of EMA-200 still allowed).

## Hypothesis table

Every variant uses **`ENTRY_STD_THRESHOLD = 1.0σ`** (the live setting);
no variant raises selectivity by lifting the threshold. Adoption gates:

- **Promote** if Sharpe + expectancy + win-rate all improve and
  trade-count drop ≤ 40%.
- **Reject** if any of those three regress OR trade-count drops > 60%
  (that's the "selectivity-not-accuracy" failure mode).

| # | Name | Filter / change | Rationale | Adoption gate |
|---|---|---|---|---|
| **H1** | Anchored VWAP (UTC daily) | Replace rolling-120 VWAP with session-anchored VWAP. σ from session typical price. Same 1.0σ threshold. | Institutional reading; σ scoped to today's auction is more meaningful than a window that mixes 10h-old context. | Δ Sharpe ≥ +0.30 with trade-count drop ≤ 40% |
| **H2** | VWAP slope filter | Reject when \|VWAP_t − VWAP_{t−12}\| / VWAP_t > 0.15% (i.e. > 0.15%/h drift). | Flat slope = ranging regime; steep slope = trend that punishes fades. | Δ Sharpe ≥ +0.30 with trade-count drop ≤ 40% |
| **H3** | HTF soft trend filter | Block BUY only when 1h close < EMA200 × 0.99 (≥ 1% below). Block SELL only when 1h close > EMA200 × 1.01. Neutral / mild counter-trend trades pass. | Last run's hard EMA-200 filter cost too many trades. Soft band keeps the "with-trend pullback" reversions, kills only the "knife-catching against a strong trend" trades. | Δ Sharpe ≥ +0.30 with trade-count drop ≤ 40% |
| **H4** | RSI(14) confirmation | BUY needs RSI < 45; SELL needs RSI > 55 on the 5m bar. | Mild momentum-direction confirmation; not extreme OB/OS (would gut cadence). Filters out fades into accelerating moves. | Δ Sharpe ≥ +0.30 with trade-count drop ≤ 40% |
| **H5** | Volume-spike confirmation | Entry bar volume > 1.3 × rolling-20 mean. | Reversion entries on average volume = drift-trades; entries on volume spikes catch climaxes. | Δ Sharpe ≥ +0.30 with trade-count drop ≤ 40% |
| **H6** | Stacked best | Apply the top two of H1-H5 by Sharpe lift together. | Tests whether independent filters compound or interfere. | Δ Sharpe ≥ +0.50 vs baseline; trade-count drop ≤ 60% |

## Backtest setup

- **Source:** `scripts.training.data_loader.load_candles` (yfinance →
  Coinbase → Bybit fallback chain), 365 days BTCUSDT 5m + 1h.
- **Backtest engine:** `scripts.training.backtest_helpers.simple_backtest`
  with `sl_tp_exit` (first-touch SL/TP). `lookback_bars=120`,
  `max_hold_bars=96` — same parameters as the previous VWAP run for
  apples-to-apples comparison.
- **Baseline signal:** `vwap.build_vwap_signal()` directly (matches what
  the live pipeline calls — see `src/runtime/pipeline.py:489-529`). The
  previous run accidentally exercised `vwap.order_package()` instead,
  which falls through to a hardcoded 2 % SL when the σ-based path
  underflows; that's not the live behaviour. Re-establishing a
  representative baseline is part of this run.
- **Metrics:** trade count, win rate, expectancy_R, Sharpe, max DD R.
  All five reported per variant + delta vs baseline.

## Out of scope

- No qty / risk-pct changes.
- No SL/TP-multiple sweeps (that's a separate "R:R tuning" run).
- No threshold sweeps — the operator already settled the σ debate.
- No execution-side changes (slippage, fees, order types).

## Cross-references

- `experiments/2026-05-03-vwap-improvement/RECOMMENDATIONS.md` — prior run.
- `src/units/strategies/vwap.py` — production strategy.
- `scripts/training/backtest_helpers.py` — backtest engine.
