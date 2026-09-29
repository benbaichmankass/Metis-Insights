# Run 2026-05-07-vwap-accuracy — 5m re-run

Timeframe: **5m** (production cadence). Symbol: BTCUSDT.
`scripts/training/data_loader.load_candles` provided the candles via the yfinance → Coinbase → Bybit fallback chain on a GitHub Actions runner (sandbox blocks all three).

| Hypothesis | trades | win | E[R] | Sharpe |
|---|---:|---:|---:|---:|
| **baseline** | 254 | 28.74% | +0.1096 | +0.93 |
| H1 | 979 | 19.92% | +0.1096 | +1.41 |
| H2 | 219 | 30.14% | +0.1864 | +1.43 |
| H3 | 110 | 36.36% | +0.2750 | +1.57 |
| H4 | 236 | 30.08% | +0.1687 | +1.35 |
| H5 | 177 | 29.94% | +0.1835 | +1.25 |
| H6 | 102 | 37.25% | +0.3049 | +1.67 |
