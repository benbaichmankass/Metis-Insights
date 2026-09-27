# Run 2026-05-07-vwap-accuracy — 5m re-run

Timeframe: **5m** (production cadence). Symbol: BTCUSDT.
`scripts/training/data_loader.load_candles` provided the candles via the yfinance → Coinbase → Bybit fallback chain on a GitHub Actions runner (sandbox blocks all three).

| Hypothesis | trades | win | E[R] | Sharpe |
|---|---:|---:|---:|---:|
| **baseline** | 950 | 31.05% | +0.1631 | +2.74 |
| H1 | 962 | 33.26% | +0.2060 | +3.47 |
| H2 | 847 | 31.76% | +0.1793 | +2.88 |
| H3 | 526 | 34.22% | +0.2791 | +3.36 |
| H4 | 854 | 30.21% | +0.1700 | +2.65 |
| H5 | 636 | 31.29% | +0.1962 | +2.61 |
| H6 | 523 | 36.33% | +0.3147 | +3.90 |
