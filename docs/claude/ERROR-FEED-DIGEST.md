# Trader error feed — grouped for triage

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

_Generated 2026-09-13T21:07:06+00:00 · covers rows after `2026-09-13T04:13:49.648924+00:00` · verdict **all_feeds_read**_

> ⚠️ **Page cap hit** on `bot_logs` — older rows exist that this digest did not see.

## Population

- **operator_alerts** — state `read` · 347 of 1000 requested · span 2026-09-09T06:44:24.244346+00:00 → 2026-09-13T21:00:28.245155+00:00
- **bot_logs** — state `read` · 1000 of 1000 requested · span 2026-09-12T09:00:50.138418+00:00 → 2026-09-13T20:59:49.000568+00:00

## Groups (70, covering 1347 rows)

- **[error] x1** `operator_alerts` — 🎯 Stop-loss exit detected by reconciler Account: bybit_N Symbol: ETHUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: sl No
  - 2026-09-13T08:34:25.001724+00:00 → 2026-09-13T08:34:25.001724+00:00 · accounts: bybit_2 · symbols: ETHUSDT
- **[error] x1** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_portfolio Symbol: ETHUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification:
  - 2026-09-13T08:34:26.457938+00:00 → 2026-09-13T08:34:26.457938+00:00 · accounts: bybit_portfolio · symbols: ETHUSDT
- **[error] x61** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: ict_scalp_mgc_Nm | Symbol: MGC Reason: candles_unavailable (for N c
  - 2026-09-10T09:07:19.410872+00:00 → 2026-09-13T21:00:28.245155+00:00 · symbols: MGC · strategies: ict_scalp_mgc_15m
- **[error] x51** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mes_trend_long_Nd | Symbol: MES Reason: candles_unavailable (for N 
  - 2026-09-09T16:16:17.159897+00:00 → 2026-09-12T22:59:19.453313+00:00 · symbols: MES
- **[error] x51** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mhg_pullback_Nd | Symbol: MHG Reason: candles_unavailable (for N co
  - 2026-09-09T16:16:17.435098+00:00 → 2026-09-12T22:59:19.747385+00:00 · symbols: MHG · strategies: mhg_pullback_1d
- **[error] x42** `operator_alerts` — 🧱 Position CLOSE wedged BROKER-SIDE — carried in the digest Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failures: N share_hold: ca
  - 2026-09-09T10:03:15.361800+00:00 → 2026-09-11T23:15:47.459088+00:00 · accounts: alpaca_paper · symbols: GLD
- **[error] x11** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mgc_trend_Nh | Symbol: MGC Reason: candles_unavailable (for N conse
  - 2026-09-09T06:44:24.244346+00:00 → 2026-09-10T05:45:10.406988+00:00 · symbols: MGC · strategies: mgc_trend_1h
- **[error] x6** `operator_alerts` — 🧹 Stuck linked-package sweep fired Force-closed N order package(s) whose linked trade was already terminal but the package stayed open (the strategy-monocle gat
  - 2026-09-09T15:42:30.931533+00:00 → 2026-09-13T08:38:35.444030+00:00
- **[error] x4** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: SOLUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker_
  - 2026-09-09T12:04:19.986616+00:00 → 2026-09-12T02:07:14.949099+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[error] x4** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: XRPUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker_
  - 2026-09-09T09:38:26.902352+00:00 → 2026-09-11T09:38:06.860434+00:00 · accounts: bybit_1, bybit_2 · symbols: XRPUSDT
- **[error] x3** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: gld_pullback_Nd | Symbol: GLD Reason: candles_unavailable (for N co
  - 2026-09-11T16:18:02.082744+00:00 → 2026-09-13T07:02:00.845289+00:00 · symbols: GLD · strategies: gld_pullback_1d
- **[error] x3** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: gld_pullback_Nh | Symbol: GLD Reason: candles_unavailable (for N co
  - 2026-09-11T16:18:08.975421+00:00 → 2026-09-13T07:02:11.134527+00:00 · symbols: GLD · strategies: gld_pullback_1h
- **[error] x3** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: AVAXUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker
  - 2026-09-10T07:23:53.005707+00:00 → 2026-09-13T14:15:14.146372+00:00 · accounts: bybit_1 · symbols: AVAXUSDT
- **[error] x3** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: AVAXUSDT | Side: short DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broke
  - 2026-09-10T10:36:36.725248+00:00 → 2026-09-13T06:58:07.913309+00:00 · accounts: bybit_1 · symbols: AVAXUSDT
- **[error] x3** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: BTCUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker_
  - 2026-09-09T09:48:24.051387+00:00 → 2026-09-11T15:57:45.925576+00:00 · accounts: bybit_1, bybit_2 · symbols: BTCUSDT
- **[error] x3** `operator_alerts` — 🚩🚩 ORPHAN TRADE CREATED — needs reconciliation Account: bybit_N Symbol: SOLUSDT | Side: long Trade id: N Origin: reverse_reconciler_adopt Reason: exchange posit
  - 2026-09-12T05:05:31.695296+00:00 → 2026-09-12T11:07:25.943264+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[error] x3** `operator_alerts` — 🛑 Position CLOSE failing — won't flatten Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failures: N share_hold: broker_cancel_wedged 
  - 2026-09-09T08:01:22.114859+00:00 → 2026-09-11T08:01:24.048228+00:00 · accounts: alpaca_paper · symbols: GLD
- **[error] x3** `operator_alerts` — 🛑 Position CLOSE failing — won't flatten Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failures: N share_hold: cancel_accepted_ineff
  - 2026-09-09T09:01:54.887471+00:00 → 2026-09-11T09:01:52.898109+00:00 · accounts: alpaca_paper · symbols: GLD
- **[error] x3** `operator_alerts` — 🪝 Exchange-side orphan position — policy=adopt Account: bybit_N Symbol: SOLUSDT | Side: long | Size: N Entry (Bybit avgPrice): N DB trade id (adopted): N
  - 2026-09-12T05:05:31.697737+00:00 → 2026-09-12T11:07:25.944833+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[error] x2** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: gdx_pullback_Nd | Symbol: GDX Reason: candles_unavailable (for N co
  - 2026-09-11T16:18:15.713409+00:00 → 2026-09-11T16:32:10.160877+00:00 · symbols: GDX · strategies: gdx_pullback_1d
- **[error] x2** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: iaum_pullback_Nd | Symbol: IAUM Reason: candles_unavailable (for N 
  - 2026-09-11T16:18:19.103379+00:00 → 2026-09-11T16:32:13.521433+00:00 · symbols: IAUM · strategies: iaum_pullback_1d
- **[error] x2** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: ief_pullback_Nd | Symbol: IEF Reason: candles_unavailable (for N co
  - 2026-09-11T16:18:05.690313+00:00 → 2026-09-11T16:31:53.323690+00:00 · symbols: IEF · strategies: ief_pullback_1d
- **[error] x2** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: slv_pullback_Nd | Symbol: SLV Reason: candles_unavailable (for N co
  - 2026-09-11T16:18:12.384487+00:00 → 2026-09-11T16:32:06.714414+00:00 · symbols: SLV · strategies: slv_pullback_1d
- **[error] x2** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: spy_pullback_Nh | Symbol: SPY Reason: candles_unavailable (for N co
  - 2026-09-11T16:21:41.731909+00:00 → 2026-09-11T16:31:59.924116+00:00 · symbols: SPY · strategies: spy_pullback_1h
- **[error] x2** `operator_alerts` — ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: tlt_pullback_Nh | Symbol: TLT Reason: candles_unavailable (for N co
  - 2026-09-11T16:19:41.351826+00:00 → 2026-09-11T16:32:03.240294+00:00 · symbols: TLT · strategies: tlt_pullback_1h
- **[error] x2** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: ETHUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker_
  - 2026-09-11T01:31:34.077624+00:00 → 2026-09-11T15:57:43.658103+00:00 · accounts: bybit_1 · symbols: ETHUSDT
- **[error] x2** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: SOLUSDT | Side: short DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker
  - 2026-09-11T12:39:21.718481+00:00 → 2026-09-12T08:02:25.066804+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[error] x2** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_portfolio Symbol: BTCUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification:
  - 2026-09-09T09:48:28.122679+00:00 → 2026-09-11T15:57:47.231690+00:00 · accounts: bybit_portfolio · symbols: BTCUSDT
- **[error] x2** `operator_alerts` — 🚩🚩 ORPHAN TRADE CREATED — needs reconciliation Account: bybit_N Symbol: BTCUSDT | Side: short Trade id: N Origin: reverse_reconciler_adopt Reason: exchange posi
  - 2026-09-12T03:03:13.179034+00:00 → 2026-09-12T06:04:09.389342+00:00 · accounts: bybit_1 · symbols: BTCUSDT
- **[error] x2** `operator_alerts` — 🪝 Exchange-side orphan position — policy=adopt Account: bybit_N Symbol: BTCUSDT | Side: short | Size: N Entry (Bybit avgPrice): N DB trade id (adopted): N
  - 2026-09-12T03:03:13.180849+00:00 → 2026-09-12T06:04:09.391096+00:00 · accounts: bybit_1 · symbols: BTCUSDT
- **[error] x1** `operator_alerts` — *DEMO TRADER* ⚠️ Order execution failed Account: bybit_N Strategy: ict_scalp_avax_Nm Symbol: AVAXUSDT | Side: buy | Qty: N Reason: RuntimeError: Order submissio
  - 2026-09-11T11:44:06.560104+00:00 → 2026-09-11T11:44:06.560104+00:00 · accounts: bybit_1 · symbols: AVAXUSDT · strategies: ict_scalp_avax_5m
- **[error] x1** `operator_alerts` — *DEMO TRADER* ⚠️ Order execution failed Account: bybit_N Strategy: ict_scalp_avax_Nm Symbol: AVAXUSDT | Side: sell | Qty: N Reason: RuntimeError: Order submissi
  - 2026-09-10T17:40:42.149653+00:00 → 2026-09-10T17:40:42.149653+00:00 · accounts: bybit_1 · symbols: AVAXUSDT · strategies: ict_scalp_avax_5m
- **[error] x1** `operator_alerts` — 🎯 Stop-loss exit detected by reconciler Account: bybit_N Symbol: BTCUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: sl No
  - 2026-09-09T09:48:26.416223+00:00 → 2026-09-09T09:48:26.416223+00:00 · accounts: bybit_2 · symbols: BTCUSDT
- **[error] x1** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: ADAUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker_
  - 2026-09-10T16:21:21.501489+00:00 → 2026-09-10T16:21:21.501489+00:00 · accounts: bybit_1 · symbols: ADAUSDT
- **[error] x1** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: ADAUSDT | Side: short DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker
  - 2026-09-11T14:02:20.331813+00:00 → 2026-09-11T14:02:20.331813+00:00 · accounts: bybit_1 · symbols: ADAUSDT
- **[error] x1** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_N Symbol: BTCUSDT | Side: short DB trade id: N Package: pkg-<hex> Reason: reconciler Classification: broker
  - 2026-09-12T01:08:04.794828+00:00 → 2026-09-12T01:08:04.794828+00:00 · accounts: bybit_1 · symbols: BTCUSDT
- **[error] x1** `operator_alerts` — 🔔 Broker close detected by reconciler Account: bybit_portfolio Symbol: XRPUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Classification:
  - 2026-09-10T12:50:56.898462+00:00 → 2026-09-10T12:50:56.898462+00:00 · accounts: bybit_portfolio · symbols: XRPUSDT
- **[error] x1** `operator_alerts` — 🚨 ALL accounts failed to dispatch Strategy: ict_scalp_avax_Nm | Symbol: AVAXUSDT | Side: buy Accounts attempted: N | Trades placed: N Failures: • bybit_N: Runti
  - 2026-09-11T11:44:06.568358+00:00 → 2026-09-11T11:44:06.568358+00:00 · accounts: bybit_1 · symbols: AVAXUSDT · strategies: ict_scalp_avax_5m
- **[error] x1** `operator_alerts` — 🚨 ALL accounts failed to dispatch Strategy: ict_scalp_avax_Nm | Symbol: AVAXUSDT | Side: sell Accounts attempted: N | Trades placed: N Failures: • bybit_N: Runt
  - 2026-09-10T17:40:42.158263+00:00 → 2026-09-10T17:40:42.158263+00:00 · accounts: bybit_1 · symbols: AVAXUSDT · strategies: ict_scalp_avax_5m
- **[error] x1** `operator_alerts` — 🚨 ALL accounts failed to dispatch Strategy: ict_scalp_mgc_Nm | Symbol: MGC | Side: buy Accounts attempted: N | Trades placed: N Failures: • ib_paper: sizing_fai
  - 2026-09-10T02:40:01.140482+00:00 → 2026-09-10T02:40:01.140482+00:00 · accounts: ib_paper · symbols: MGC · strategies: ict_scalp_mgc_15m
- **[error] x1** `operator_alerts` — 🚩🚩 ORPHAN TRADE CREATED — needs reconciliation Account: bybit_N Symbol: SOLUSDT | Side: short Trade id: N Origin: reverse_reconciler_adopt Reason: exchange posi
  - 2026-09-12T11:05:09.355462+00:00 → 2026-09-12T11:05:09.355462+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[error] x1** `operator_alerts` — 🛑 Position CLOSE failing — won't flatten Account: bybit_N Symbol: AVAXUSDT | Side: short | Qty: N Consecutive close failures: N share_hold: not_classified Nobod
  - 2026-09-10T19:01:50.437535+00:00 → 2026-09-10T19:01:50.437535+00:00 · accounts: bybit_1 · symbols: AVAXUSDT
- **[error] x1** `operator_alerts` — 🛑 Position CLOSE failing — won't flatten Account: bybit_N Symbol: ETHUSDT | Side: long | Qty: N Consecutive close failures: N share_hold: not_classified Nobody 
  - 2026-09-11T15:57:27.633784+00:00 → 2026-09-11T15:57:27.633784+00:00 · accounts: bybit_1 · symbols: ETHUSDT
- **[error] x1** `operator_alerts` — 🛑 Position CLOSE failing — won't flatten Account: bybit_N Symbol: SOLUSDT | Side: long | Qty: N Consecutive close failures: N share_hold: not_classified Nobody 
  - 2026-09-11T15:56:56.900746+00:00 → 2026-09-11T15:56:56.900746+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[error] x1** `operator_alerts` — 🪝 Exchange-side orphan position — policy=adopt Account: bybit_N Symbol: SOLUSDT | Side: short | Size: N Entry (Bybit avgPrice): N DB trade id (adopted): N
  - 2026-09-12T11:05:09.380483+00:00 → 2026-09-12T11:05:09.380483+00:00 · accounts: bybit_1 · symbols: SOLUSDT
- **[warn] x203** `bot_logs` — position_read_state hedge_book_dropped
  - 2026-09-12T09:00:50.138418+00:00 → 2026-09-12T11:05:29.593930+00:00
- **[warn] x170** `bot_logs` — strategy_builder exception: transient_market_data_unavailable: ict_scalp_mgc_Nm: no candle data for symbol=MGC timeframe=Nm.
  - 2026-09-12T09:02:29.110348+00:00 → 2026-09-13T20:59:48.609664+00:00 · symbols: MGC · strategies: ict_scalp_mgc_15m
- **[warn] x163** `bot_logs` — strategy_builder exception: transient_market_data_unavailable: mgc_trend_Nh: no candle data returned for symbol=MGC timeframe=Nh. Check that the IBKR connection
  - 2026-09-12T09:02:30.666768+00:00 → 2026-09-13T16:45:32.638881+00:00 · symbols: MGC · strategies: mgc_trend_1h
- **[warn] x161** `bot_logs` — strategy_builder exception: transient_market_data_unavailable: mgc_pullback_Nd: no candle data returned for symbol=MGC timeframe=Nd. Check that the IBKR connect
  - 2026-09-12T09:02:29.992265+00:00 → 2026-09-13T20:59:49.000568+00:00 · symbols: MGC · strategies: mgc_pullback_1d
- **[warn] x152** `bot_logs` — strategy_builder exception: transient_market_data_unavailable: mes_trend_long_Nd: no candle data returned for symbol=MES timeframe=Nd. Check that the IBKR conne
  - 2026-09-12T09:02:26.733306+00:00 → 2026-09-12T22:58:39.869788+00:00 · symbols: MES
- **[warn] x150** `bot_logs` — strategy_builder exception: transient_market_data_unavailable: mhg_pullback_Nd: no candle data returned for symbol=MHG timeframe=Nd. Check that the IBKR connect
  - 2026-09-12T09:02:33.404103+00:00 → 2026-09-12T22:58:43.735180+00:00 · symbols: MHG · strategies: mhg_pullback_1d
- **[warn] x7** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ict_scalp_avax_Nm | Symbol: AVAXUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its
  - 2026-09-10T10:09:23.612270+00:00 → 2026-09-13T19:57:31.543047+00:00 · symbols: AVAXUSDT · strategies: ict_scalp_avax_5m
- **[warn] x6** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: pairs_bnb_btc_a | Symbol: BNBUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-09T17:01:27.636417+00:00 → 2026-09-13T09:03:54.271953+00:00 · symbols: BNBUSDT · strategies: pairs_bnb_btc_a
- **[warn] x6** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: pairs_bnb_btc_b | Symbol: BTCUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-09T17:01:27.628546+00:00 → 2026-09-13T09:03:54.262651+00:00 · symbols: BTCUSDT · strategies: pairs_bnb_btc_b
- **[warn] x6** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: pairs_sol_eth_a | Symbol: SOLUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-09T08:04:42.949292+00:00 → 2026-09-12T23:01:33.147690+00:00 · symbols: SOLUSDT · strategies: pairs_sol_eth_a
- **[warn] x6** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: pairs_sol_eth_b | Symbol: ETHUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-09T08:04:43.034515+00:00 → 2026-09-12T23:01:33.140520+00:00 · symbols: ETHUSDT · strategies: pairs_sol_eth_b
- **[warn] x3** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ict_scalp_mgc_Nm | Symbol: MGC Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its timef
  - 2026-09-10T08:11:01.071018+00:00 → 2026-09-11T07:02:00.828513+00:00 · symbols: MGC · strategies: ict_scalp_mgc_15m
- **[warn] x2** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ada_pullback_Nh | Symbol: ADAUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-11T12:01:15.717382+00:00 → 2026-09-11T20:10:41.499282+00:00 · symbols: ADAUSDT · strategies: ada_pullback_2h
- **[warn] x2** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ict_scalp_eth_Nm | Symbol: ETHUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its t
  - 2026-09-09T10:27:48.974392+00:00 → 2026-09-11T13:37:46.734208+00:00 · symbols: ETHUSDT · strategies: ict_scalp_eth_15m
- **[warn] x2** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ict_scalp_sol_Nm | Symbol: SOLUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its t
  - 2026-09-11T10:30:59.936370+00:00 → 2026-09-11T20:57:37.524740+00:00 · symbols: SOLUSDT · strategies: ict_scalp_sol_15m
- **[warn] x2** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ict_scalp_xrp_Nm | Symbol: XRPUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its t
  - 2026-09-11T01:10:31.853415+00:00 → 2026-09-11T14:47:01.690225+00:00 · symbols: XRPUSDT · strategies: ict_scalp_xrp_15m
- **[warn] x2** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: trend_donchian_eth_Nh | Symbol: ETHUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× 
  - 2026-09-11T00:57:01.021107+00:00 → 2026-09-12T01:55:17.028276+00:00 · symbols: ETHUSDT · strategies: trend_donchian_eth_4h
- **[warn] x1** `bot_logs` — pairs_half_open cleaned: pairs SOLUSDT/ETHUSDT: one leg was stranded open on bybit_N (SOLUSDT) after a partial close; flattened this tick
  - 2026-09-12T11:05:29.939625+00:00 → 2026-09-12T11:05:29.939625+00:00 · accounts: bybit_1 · strategies: pairs, pairs_half_open
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: eth_pullback_Nh | Symbol: ETHUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-12T02:03:15.754756+00:00 → 2026-09-12T02:03:15.754756+00:00 · symbols: ETHUSDT · strategies: eth_pullback_2h
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: ict_scalp_Nm | Symbol: BTCUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its timef
  - 2026-09-11T21:25:50.319834+00:00 → 2026-09-11T21:25:50.319834+00:00 · symbols: BTCUSDT · strategies: ict_scalp_5m
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: spy_pullback_Nh | Symbol: SPY Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its timefr
  - 2026-09-11T17:08:30.818635+00:00 → 2026-09-11T17:08:30.818635+00:00 · symbols: SPY · strategies: spy_pullback_1h
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: tlt_pullback_Nh | Symbol: TLT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its timefr
  - 2026-09-11T16:56:57.006126+00:00 → 2026-09-11T16:56:57.006126+00:00 · symbols: TLT · strategies: tlt_pullback_1h
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: trend_donchian_sol_Nh | Symbol: SOLUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× 
  - 2026-09-11T00:57:00.242862+00:00 → 2026-09-11T00:57:00.242862+00:00 · symbols: SOLUSDT · strategies: trend_donchian_sol_4h
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: uso_trend_Nh | Symbol: USO Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its timeframe
  - 2026-09-10T19:40:59.239006+00:00 → 2026-09-10T19:40:59.239006+00:00 · symbols: USO · strategies: uso_trend_1h
- **[warn] x1** `operator_alerts` — 🔎 Stuck-strategy watchdog (informational — no action) Strategy: xrp_pullback_Nh | Symbol: XRPUSDT Package: pkg-<hex> DB trade id: N Held for: N min (≥ N× its ti
  - 2026-09-11T21:58:59.633358+00:00 → 2026-09-11T21:58:59.633358+00:00 · symbols: XRPUSDT · strategies: xrp_pullback_2h

---

_Watermark: `2026-09-13T21:00:28.245155+00:00` — advanced to the newest row read (2026-09-13T21:00:28.245155+00:00)_
