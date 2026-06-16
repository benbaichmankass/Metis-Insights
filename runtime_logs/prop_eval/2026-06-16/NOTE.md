# Prop-firm evaluation matrix — REAL multi-year run

This `matrix.{md,json}` is a **real** evaluation over a multi-year BTCUSDT
feed (it replaces the earlier 3.5-day-fixture smoke run that produced an
all-zero "EVAL NOT REACHED" matrix).

## Feed

- **Source:** [`qashdev/btc`](https://github.com/qashdev/btc) — a GitHub mirror
  of Binance Vision's public BTCUSDT 5m monthly klines archive, fetched via
  `scripts/ops/fetch_qashdev_btc_archive.py` (GitHub raw is reachable from the
  sandbox; Bybit/Coinbase are firewalled).
- **Range:** 2023-01-01 → 2026-02-28 (38 monthly files; 2026-03+ not yet
  published upstream).
- **Bars:** 332,624 5m candles; price range $16,499 → $126,200; 1 missing bar.
- **Consolidated CSV:** `/home/user/ict-trader-data/btc_5m_multiyear.csv`
  (332,624 rows; `timestamp,open,high,low,close,volume` — the schema
  `scripts/backtest_system.py::_load_candles` expects). The raw monthly CSVs +
  the `btc_5m.parquet` cache live under `ICT_TRADER_DATA_ROOT`
  (`/home/user/ict-trader-data/`), **outside the repo** — the giant feed is
  deliberately not committed.

## Run

```
python scripts/prop/evaluate_prop.py --combos all \
  --data /home/user/ict-trader-data/btc_5m_multiyear.csv --clock-tf 1h
```

`--clock-tf 1h` (a supported `evaluate_prop` / `backtest_system` option) was
used so the full 15-combo sweep completes within the sandbox wall-clock budget;
the per-strategy signal streams are generated on each strategy's own setup
timeframe (unchanged) and only the shared netting/monitor clock coarsens from
the 15m default to 1h. Signal streams are cached under
`runtime_logs/system_backtest/signals/` (trend_donchian 476, fade_breakout_4h
180, squeeze_breakout_4h 73, fvg_range_15m 57 signals over the window).

Ruleset: `config/prop_rulesets/breakout.yaml` (profit target 10%, daily-loss
3%, max-DD 6% static, 30-day funded soak) — still flagged **UNCONFIRMED**
(placeholder fields not verified against the firm's terms; a pass here proves
nothing about the real evaluation until those numbers are verified).

## Headline result

- **2 of 15 combos PASS eval AND survive the funded soak:**
  - `squeeze_breakout_4h,fvg_range_15m` — eval pass in 673 days, worst-DD 3.3%,
    funded survive, net +$2,520 (best by drawdown margin).
  - `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` — eval pass in 519
    days, worst-DD 9.9%, funded survive, net +$922.
- **6 combos FAIL eval on `max_drawdown`** — every roster containing
  `trend_donchian` breaches the 6% static max-DD limit, first breach
  2023-01-26 (the early-2023 run-up), despite several being the most profitable
  on raw net P&L (e.g. `trend_donchian,fvg_range_15m` +$4,525).
- **7 combos NEVER REACH the 10% profit target** within the window (single
  4h/15m strategies + the `fade_*` pairs); `fade_breakout_4h` alone is net
  negative (-$1,162).
