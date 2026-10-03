# LIVE-SILENCE replay, 2026-10-03

The question: why have `xrp_pullback_2h`, `ada_pullback_2h`, `trend_donchian_eth_4h` and
`trend_donchian_xrp_4h` sent no order package on any account since 2026-09-28T03:35Z?

**Verdict: market silence.** No code or config change stopped them.

- `replay.py` calls the LIVE units' `order_package()` on 200-bar windows. It uses
  the config from `config/strategies.yaml` (`load_strategy_config()`) and Bybit candles
  from the bot's own feed (`/api/bot/candles`, fetched 2026-10-03 ~05:50Z).
  - It replays every closed bar.
  - For the `forming` legs, it also rebuilds the forming bar at every 15-minute checkpoint.
- `baseline.py` runs the same functions over Binance-spot candles from 2026-04-01 to
  2026-09-27 to get a 6-month signal rate. Binance spot is a stand-in for Bybit, because
  Bybit is geo-blocked from the session sandbox. Its agreement with Bybit is printed per leg.
- `output.txt` is the raw output of both scripts.

The scripts read their candle files from a scratch directory, which is not committed.
To re-run them, fetch the candles again (endpoints are in each script) and pass that
directory as argv[1].
