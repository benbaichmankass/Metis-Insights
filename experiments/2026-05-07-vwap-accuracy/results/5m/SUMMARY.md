# Run 2026-05-07-vwap-accuracy — 5m re-run

Timeframe: **5m** (production cadence). Symbol: BTCUSDT.
`scripts/training/data_loader.load_candles` provided the candles via the yfinance → Coinbase → Bybit fallback chain on a GitHub Actions runner (sandbox blocks all three).

| Hypothesis | trades | win | E[R] | Sharpe |
|---|---:|---:|---:|---:|
| **baseline** | 251 | 28.69% | +0.1010 | +0.86 |
| H1 | 998 | 20.74% | +0.1503 | +1.93 |
| H2 | 217 | 28.11% | +0.0976 | +0.77 |
| H3 | 107 | 33.64% | +0.1896 | +1.09 |
| H4 | 232 | 27.59% | +0.0402 | +0.34 |
| H5 | 172 | 30.23% | +0.1507 | +1.03 |
| H6 | 541 | 20.52% | +0.1280 | +1.21 |
