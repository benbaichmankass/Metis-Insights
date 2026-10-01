# ict_scalp HTF trend filter — decision rule, REGISTERED BEFORE THE RE-RUN

> Registered 2026-10-01 (lane ICT-SCALP-HTF, manager instruction 22:53Z), committed before any re-run. Applies to PR #15340 (wire the filter) vs the alternative (explicit `htf_trend_filter_enabled: false` on the 7 legs).

**Population:** the 6 crypto ict_scalp variant legs (sol_5m, xrp_5m, avax_5m, xrp_15m, eth_15m, sol_15m). `mgc_15m` is excluded: no data source from this sandbox, so it is neither for nor against.

**Harness:** `scripts/backtest_ict_scalp.py` with the CAUSAL HTF alignment (PR #15341, closed bars only), invoked exactly as `scripts/research/regime_debt_matrix.py::build_harness_cmd` invokes it for the committed evidence records (leg's own `--strategy-name`, its lever flags, fee 7.5 bps round trip, venue-aware slippage/funding defaults), 365-day window, one candle feed per leg shared by both arms.

**Arms:** OFF = `htf_trend_filter_enabled` forced False. ON = filter on (harness default). Same feed, same everything else.

**RULE — filter ON iff** ON net R >= OFF net R (net of the full cost stack) on **at least 5 of the 6 legs**.

- Otherwise the proposal is the explicit-off PR, not #15340.
- Reported, NOT gating: pooled net R and trades both arms; per-fold sign agreement (the evidence builder's 4 time-folds); ON net R per trade.
- Known limits stated up front: no parameter is fitted, so the 4 time-folds are consistency slices of one window, not an out-of-sample test; the ON arm uses completed HTF bars (one bar staler than live's forming bar); candles are Binance spot, not Bybit perps (Bybit is geo-blocked here).
