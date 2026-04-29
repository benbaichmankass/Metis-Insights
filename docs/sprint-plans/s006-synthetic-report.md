# S-006 Synthetic Multi-Symbol Validation Report

**Generated:** 2026-04-29 16:30 UTC
**Symbols:** 5 × 10,000 candles each
**PF threshold:** > 1.2

## ✅ GO — PF > 1.2, recommend ICT_RISK_PCT bump to 0.4

---

## Aggregate

| Metric | Value |
|--------|-------|
| Symbols run | 5 |
| Symbols with trades | 5 |
| Total trades | 1048 |
| Blended WR% | 48.4 |
| Avg R-multiple | 0.47 |
| Avg profit factor | 2.04 |
| Avg return% | -3.09 |
| Avg max DD% | 38.22 |

---

## Per-symbol results

| Symbol | TF | Regime | Trades | WR% | Avg R | PF | Return% | Max DD% | Status |
|--------|----|--------|-------:|----:|------:|---:|--------:|--------:|--------|
| BTCUSDT | 5m | bullish | 98 | 65.3 | 0.86 | 3.15 | 46.88 | 2.72 | ✅ |
| ETHUSDT | 5m | bearish | 100 | 63.0 | 0.76 | 2.79 | 31.39 | 2.67 | ✅ |
| SPY | 15m | mixed | 264 | 43.2 | 0.25 | 1.11 | -51.2 | 79.75 | ⚠️ |
| QQQ | 15m | bullish | 308 | 33.1 | -0.12 | 0.81 | -88.81 | 88.89 | ⚠️ |
| GOLD | 15m | ranging | 278 | 59.0 | 0.61 | 2.32 | 46.3 | 17.08 | ✅ |
---

## Config used

```json
{"disable_session_filter": true, "ob_confluence_only": false}
```

## Next step (M3)

- If PF > 1.2: open PR to bump `ICT_RISK_PCT` from current to **0.4** in
  `config/master-secrets.template.yaml` and validate with live kill-switch ready.
- If NO-GO: lower `min_fvg_size_pct` or widen session window and re-run.
