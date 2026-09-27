# Run 2026-05-07-vwap-accuracy — 5m re-run

Timeframe: **5m** (production cadence). Symbol: BTCUSDT.
`scripts/training/data_loader.load_candles` provided the candles via the yfinance → Coinbase → Bybit fallback chain on a GitHub Actions runner (sandbox blocks all three).

| Hypothesis | trades | win | E[R] | Sharpe |
|---|---:|---:|---:|---:|
| **baseline** | 244 | 28.69% | +0.1088 | +0.90 |
| H1 | 989 | 19.21% | +0.0604 | +0.79 |
| H2 | 209 | 30.14% | +0.1890 | +1.42 |
| H3 | 103 | 36.89% | +0.2938 | +1.62 |
| H4 | 227 | 29.96% | +0.1606 | +1.27 |
| H5 | 170 | 30.00% | +0.1774 | +1.19 |
| H6 | 95 | 37.89% | +0.3270 | +1.72 |
