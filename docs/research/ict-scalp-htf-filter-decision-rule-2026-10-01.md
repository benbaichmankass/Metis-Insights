# ict_scalp HTF trend filter — decision rule, REGISTERED BEFORE THE RE-RUN

> Registered 2026-10-01 (lane ICT-SCALP-HTF, manager instruction 22:53Z), committed before any re-run. Applies to PR #15340 (wire the filter) vs the alternative (explicit `htf_trend_filter_enabled: false` on the 7 legs).

**Population:** the 6 crypto ict_scalp variant legs (sol_5m, xrp_5m, avax_5m, xrp_15m, eth_15m, sol_15m). `mgc_15m` is excluded: no data source from this sandbox, so it is neither for nor against.

**Harness:** `scripts/backtest_ict_scalp.py` with the CAUSAL HTF alignment (PR #15341, closed bars only), invoked exactly as `scripts/research/regime_debt_matrix.py::build_harness_cmd` invokes it for the committed evidence records (leg's own `--strategy-name`, its lever flags, fee 7.5 bps round trip, venue-aware slippage/funding defaults), 365-day window, one candle feed per leg shared by both arms.

**Arms:** OFF = `htf_trend_filter_enabled` forced False. ON = filter on (harness default). Same feed, same everything else.

**RULE — filter ON iff** ON net R >= OFF net R (net of the full cost stack) on **at least 5 of the 6 legs**.

- Otherwise the proposal is the explicit-off PR, not #15340.
- Reported, NOT gating: pooled net R and trades both arms; per-fold sign agreement (the evidence builder's 4 time-folds); ON net R per trade.
- Known limits stated up front: no parameter is fitted, so the 4 time-folds are consistency slices of one window, not an out-of-sample test; the ON arm uses completed HTF bars (one bar staler than live's forming bar); candles are Binance spot, not Bybit perps (Bybit is geo-blocked here).

---

## RESULT (appended after the run; the rule above was committed first, f19ad8d3)

Run: fixed harness (#15341) invoked as the evidence builder does (`--strategy-name <leg>`, fee 7.5, `--sim-breakeven`, 1h EMA-20), 365 d Binance spot ending 2026-10-01, 12 runs (6 legs x OFF/ON). Population: 6 legs, 2168 trades OFF, 813 trades ON.

| leg | OFF n / netR | ON n / netR | ON - OFF |
|---|---|---|---|
| sol_5m | 543 / -102.0 | 203 / -22.5 | +79.5 |
| xrp_5m | 506 / -85.2 | 176 / -34.1 | +51.1 |
| avax_5m | 484 / -67.4 | 170 / -30.2 | +37.2 |
| xrp_15m | 211 / -76.7 | 87 / -6.4 | +70.3 (+14.7 excluding the one degenerate-stop trade) |
| eth_15m | 215 / -10.4 | 93 / +7.7 | +18.1 |
| sol_15m | 209 / +5.2 | 84 / +2.8 | -2.3 |
| pooled | 2168 / -336.5 | 813 / -82.7 | +253.8 |

**Rule verdict: CLEARED — ON >= OFF on 5 of 6 legs (needs 5).** Reported, not gating: ON beats OFF in 20 of 24 four-way time-slices; ON is still net-negative on 4 of 6 legs (sol_5m, xrp_5m, avax_5m, xrp_15m) — the filter cuts losses by removing ~63% of trades, it does not by itself produce an edge. The harness ON arm uses completed HTF bars, one bar staler than live's forming bar, and removes more trades (-63%) than my earlier forming-bar arm (-44%): live behaviour will sit between the two.

**Why the earlier full-window runs disagreed with the committed records (reconciled):** cost stack is identical (7.5 / 3.0 / 1.0 bps; read from the record and the harness constants); for xrp_15m 104 of the record's 129 entries reproduce at the same entry_time with matching per-trade net R (4 trades differ by >0.5R). One trade — XRPUSDT 2026-07-24 08:15, stop distance ~0.002% — costs 54.6R on my Binance feed vs 5.2R in the record's Bybit feed and alone explains ~49R of the gap (filed PI-20261001-JWZSVYVS-0002). The record's "OOS" is 4 time-slices of one full-window run with no fitting, not an out-of-sample test. The committed records were built on the lookahead harness and must be rebuilt (PI-20261001-JWZSVYVS-0003).
