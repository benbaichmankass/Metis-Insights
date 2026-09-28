# Run 2026-05-07-vwap-accuracy — 5m re-run

Timeframe: **5m** (production cadence). Symbol: BTCUSDT.
`scripts/training/data_loader.load_candles` provided the candles via the yfinance → Coinbase → Bybit fallback chain on a GitHub Actions runner (sandbox blocks all three).

| Hypothesis | trades | win | E[R] | Sharpe |
|---|---:|---:|---:|---:|
| **baseline** | 248 | 28.23% | +0.0909 | +0.76 |
| H1 | 986 | 19.37% | +0.0719 | +0.94 |
| H2 | 213 | 29.58% | +0.1667 | +1.27 |
| H3 | 107 | 35.51% | +0.2454 | +1.39 |
| H4 | 231 | 29.44% | +0.1405 | +1.13 |
| H5 | 173 | 29.48% | +0.1570 | +1.07 |
| H6 | 99 | 36.36% | +0.2734 | +1.49 |
