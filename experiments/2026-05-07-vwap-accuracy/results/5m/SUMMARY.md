# Run 2026-05-07-vwap-accuracy — 5m re-run

Timeframe: **5m** (production cadence). Symbol: BTCUSDT.
`scripts/training/data_loader.load_candles` provided the candles via the yfinance → Coinbase → Bybit fallback chain on a GitHub Actions runner (sandbox blocks all three).

| Hypothesis | trades | win | E[R] | Sharpe |
|---|---:|---:|---:|---:|
| **baseline** | 261 | 29.12% | +0.1263 | +1.08 |
| H1 | 991 | 20.99% | +0.1702 | +2.16 |
| H2 | 227 | 28.63% | +0.1269 | +1.01 |
| H3 | 113 | 33.63% | +0.2160 | +1.25 |
| H4 | 241 | 28.22% | +0.0812 | +0.68 |
| H5 | 179 | 30.17% | +0.1564 | +1.09 |
| H6 | 525 | 21.33% | +0.1726 | +1.59 |
