# All-models training run — 2026-05-08

## Goal

Comprehensive overnight tuning + A/B validation of every strategy in
`config/strategies.yaml`. The bot ships two parametric strategies and no
ML models, so "training" here means hypothesis-driven parameter / filter
search with walk-forward validation, not gradient descent.

## Models in scope

| Strategy | TF | Symbols | State |
|---|---|---|---|
| `vwap` | 5m | BTCUSDT | Phase-1 anchored VWAP shipped (PR #481). Phase-2 HTF gate queued, not shipped. |
| `turtle_soup` | 15m | BTC + ETH | Live. No prior tuning run on record. |

## Data

- **Source:** `qashdev/btc` GitHub mirror of Binance Vision spot
  klines. BTCUSDT 5m monthly archive Jan 2023 → Feb 2026 (38 months,
  332,624 bars, 1 missing-bar gap, price range $16.5k → $126k —
  spans the 2024 ETF approval, the 2024 halving, the late-2024 bull,
  Q1 2025 chop, the 2025 second-half rally, and the early-2026
  drawdown).
- **HTF builds:** 1h, 4h, 1d resampled from 5m for trend filters.
- **15m build:** resampled from 5m for turtle_soup.

Bybit / Coinbase / yfinance are firewalled from this sandbox so we
cannot pull a live feed; the qashdev archive is a verbatim mirror of
the Binance Vision public S3 bucket and matches what live Bybit
Linear BTCUSDT-PERP candles would show within the spot-perp basis.

## Hypothesis grid

### VWAP — `src/units/strategies/vwap.py`

| # | Name | Change | Rationale |
|---|---|---|---|
| **V0** | Baseline (production) | anchored VWAP, 1.0σ entry, 0.5 SL mult | sanity-check production code on the new dataset |
| **V1** | HTF 4h EMA-200 ±1% gate (= H6 Phase-2) | block BUY if 4h close < EMA200 × 0.99; block SELL if > × 1.01 | re-validate Phase-2 on cleaner 38-month dataset |
| **V2** | HTF gate band sweep | {0.005, 0.010, 0.015, 0.020, 0.030} | revisit prior optimum (1%) on this dataset |
| **V3** | HTF timeframe variant | 1h EMA-200 vs 4h EMA-200 vs 1d EMA-50 | which trend horizon best filters fades |
| **V4** | Volume-confidence size modulator | size = base × clamp(vol_t / vol20, 0.7, 1.3) | recast of rejected H5 — confidence weight, not hard gate |
| **V5** | SL multiplier sweep | {0.4, 0.5, 0.6, 0.75, 1.0} | rolling → session σ may have shifted SL optimum |
| **V6** | Entry threshold sweep | {0.8, 1.0, 1.2, 1.5, 2.0}σ | re-confirm operator's 1.0σ choice given new baseline |

### Turtle Soup — `src/units/strategies/turtle_soup.py`

No prior tuning run; this is the first systematic search.

| # | Name | Change | Rationale |
|---|---|---|---|
| **T0** | Baseline (production) | defaults | sanity-check |
| **T1** | sweep_lookback | {30, 45, 60, 90, 120} bars | the lookback sets which swings count as "the level" |
| **T2** | min_body_to_range | {0.50, 0.55, 0.60, 0.65, 0.70, 0.75} | stricter body filter = fewer fakeouts but smaller cadence |
| **T3** | min_sweep_buffer_bps | {4, 8, 12, 18, 25} | how deep does the sweep need to be |
| **T4** | atr_stop_mult | {0.25, 0.30, 0.35, 0.45, 0.60} | tighter stop = better R:R but more whipsaws |
| **T5** | tp1_at_r | {1.0, 1.25, 1.50, 2.0} | TP target controls realised win rate |
| **T6** | HTF 4h EMA-50 alignment | longs only above; shorts only below | trend-following strategies benefit from HTF alignment |
| **T7** | ATR regime filter | require ATR/close ≥ 0.5% | skip dead-vol regimes |

## Backtest harness

- 5m frame for VWAP, 15m frame for turtle_soup.
- Walk-forward 70/30 in/out-of-sample split.
- First-touch SL/TP exit (matches `scripts/training/backtest_helpers.py`).
- VWAP: `lookback_bars=120`, `max_hold_bars=96` (8 h) — same as prior runs.
- Turtle Soup: `lookback_bars=120`, `max_hold_bars=80` (20 h at 15m).
- No fees, no slippage, no funding (deltas are insulated from those).

## Adoption gates

| Hypothesis class | Promote if |
|---|---|
| Filter (V1, V3, T6, T7) | Δ Sharpe ≥ +0.30 AND win-rate or expectancy non-regressing AND OOS Sharpe ≥ in-sample Sharpe × 0.5 |
| Sweep (V2, V5, V6, T1-T5) | flat-or-improving curve in a monotonic band ≥ 3 wide; pick midpoint of best stable band |
| Re-cast (V4) | Δ Sharpe ≥ +0.20 AND OOS Sharpe ≥ in-sample × 0.5 |

## Out of scope (explicitly)

- No code changes to production strategy modules in this run — A/B
  evaluation only. Implementation lands in a follow-up PR after the
  operator approves the recommendations.
- No fee / funding / slippage modelling beyond the existing engine.
- No symbol expansion (BTCUSDT only — turtle_soup runs on ETHUSDT
  too in production but ETHUSDT 5m archive isn't cheaply available
  from the sandbox-allowed sources).
