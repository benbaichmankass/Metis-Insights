# LIVE-SILENCE-2: real-money silence re-check, 2026-10-04

> **Doc status:** `unknown` · category `evidence` · measured `2026-10-04T06:20Z` · lane LIVE-SILENCE-2 (session_01273KDGS7axZSs4qxaTGX5c) for manager session_01MM8o5js6TcDFeNAPBY4Ntv · follows [`../live-silence-2026-10-03/`](../live-silence-2026-10-03/README.md)

**The question:** is the real-money silence still fully explained by market conditions, as of now, for every real-money leg (bybit_2 and alpaca_live)?

**Verdict: yes. It is market silence.** No leg had a valid signal that the system failed to act on.

- Every leg was evaluated on every tick or bar.
- Every reason string matches what the replay produces.
- Nothing was dropped downstream.

## Scope

The rosters were read from `config/accounts.yaml` on 2026-10-04:

| account | legs |
|---|---|
| bybit_2 (= bybit_portfolio) | xrp_pullback_2h, ada_pullback_2h, trend_donchian_eth_4h, trend_donchian_xrp_4h |
| alpaca_live, `side_filter: long` | ief_pullback_1d, slv_pullback_1d, iaum_pullback_1d |

- alpaca_portfolio is the same list minus the iaum proxy.
- All seven legs are `execution: live`.
- All four accounts read `live: true` on `/api/diag/status` at 06:13Z.
- The trader is running `f3c302ca5`, which contains PREVBAR (#15894), RESTART-SAFE (#15830) and LIVE-PARITY (#15882).

## Per leg (window 2026-09-28T03:35Z .. 2026-10-04T06:20Z)

| leg | live eval rows (actionable) | replay closed bars / forming checkpoints → valid signals | last valid signal (closed-bar replay) | the binding filter now | distance to the next signal |
|---|---|---|---|---|---|
| xrp_pullback_2h | 4,299 (0) | 74 / 513 → 0 | 09-25 22:00 bar (long) | ADX(14) 11.5 vs adx_min 25; price −1.26 ATR below the trend midline | LONG impossible next bar: the window is empty by 1.74 ATR (0.78%). A SHORT setup is geometrically open, but the ADX filter blocks it. |
| ada_pullback_2h | 4,299 (0) | 74 / 513 → 0 | 09-26 22:00 bar (long; it traded live 09-26 22:07) | ADX 9.5 vs adx_min 28; ATR percentile 0.005 vs the vol_skip_below 0.10 dead-tail gate | LONG window empty by 1.66 ATR (1.41%). SHORT window empty by 0.31 ATR (0.27%). |
| trend_donchian_eth_4h | 4,308 (3 = the 09-28 03:35 fire) | 37 / 550 → 0 | 09-18 16:00 closed bar; the live forming fire was 09-28 03:35 | no breakout: close is inside the channel [2650.4, 2778.2] | LONG needs a close above 2793.3 (+3.92 ATR, +3.66%). SHORT needs a close below 2635.3 (−2.36 ATR, −2.20%). |
| trend_donchian_xrp_4h (closed bar, short only) | 1,662 (0); one eval per closed 4h bar since 09-30, no bar missed | 37 / n/a → 0 | 09-15 16:00 (short) | no breakout | SHORT needs a close below 1.4287 (−2.95 ATR, −4.11%). Longs are filtered out by the leg's side_filter. |
| ief_pullback_1d (alpaca_live: long only) | 949 (0) | 5 / 136 → 0 | 07-06 (long) | price −3.75 ATR below the midline (bond downtrend) | LONG window empty by 2.13 ATR (1.29%). |
| slv_pullback_1d | 948 (3, see note 1) | 5 / 145 → 0 | 09-25 (long; blocked by its own open package) | price −2.66 ATR below the midline | LONG window empty by 0.49 ATR (1.45%). |
| iaum_pullback_1d | 944 (0) | 5 / 129 → 0 | 09-22 (long; traded 09-23) | price −3.71 ATR below the midline | LONG window empty by 1.62 ATR (2.64%). |

**How to read "window empty":** given the latest closed bar, there is no possible close on the next bar that satisfies trend, pullback and confirmation at the same time. The market has to move for several bars before a setup can exist.

Note 1: on 09-28 at 13:31–13:35Z, `slv_pullback_1d` emitted a long at 58.14. That price is Friday 09-25's close: the PREVBAR stale-bar bug, fixed by #15894, which is in the running build. The 09-17 SLV package was still open until 13:36:30Z, so the open-package gate held the signal. Neither the order_packages journal nor the broker shows an SLV entry.

## Gate audit since 2026-09-27 (live signals audit)

These events have a non-zero count on other legs, so the probe can find positives:

- `regime_hard_gate`: 69 rows
- `open_package_blocked`: 98 rows
- `open_package_scoped`, `open_package_pair_coupled`, `open_package_read_failed`: 0 rows

On the 7 real-money legs the count is **0 drops of any kind**.

- **Regime router:** `would_gate` returns false for all 7 legs, both sides, in every regime. The positive control, `htf_pullback_trend_2h`, is gated.
- **Account mode and execution:** all four accounts are live, and all seven legs are `execution: live`.
- **Account side_filter (alpaca_live long):** it dropped nothing. No Alpaca leg produced a short since 09-28.
- **Risk manager:** no `rejected` package on these legs since 09-28.

**Known latent blocker, already filed as PI-20260930-QZSE4AMA-0001:** the alpaca_portfolio IEF short (pkg-5e636e3adddf4a83, trade 4195; 152 shares at the broker on 10-04) pair-couples alpaca_live's `ief_pullback_1d` out. Re-proven with `_open_package_scope`, which returns `block`.

- **Cost today:** none. An IEF long needs a close above the 91.085 midline, and the short's broker stop at 90.84 would fire first. The stop price is from the 09-30 measurement in that row; it was not re-read today.

## Rarity (rarity.py)

**Baseline:** closed-bar signals from 2025-01-01 to 2026-09-27, on a 2h grid. The data range is stated in `output.txt`.

| set | current silence (closed-bar proxy) | rolling windows of that length | fully silent | longest earlier run | earlier episodes at least as long |
|---|---|---|---|---|---|
| all 7 real-money legs | 174h (since 09-27 02:00) | 7,534 | 0.27% | 192h | 3 (192h from 2025-08-22, 186h from 2025-01-19, 178h from 2025-08-31) |
| bybit_2's 4 legs | 174h | 7,534 | 0.64% | 202h | 5 |
| alpaca_live's 3 legs | 202h | 7,520 | 52% | 1,654h | 15 |

- **Measured on live timestamps,** the silence is about 147h (since 09-28T03:35Z). It is rare, about 1 window in 370, but not unprecedented: three earlier all-leg silences of this length occurred in 21 months.
- **It passes the longest earlier all-7 silence (192h) at 2026-10-05T02:00Z.** Counted from the live last fire (09-28T03:35Z), 192h ends at 2026-10-06T03:35Z.
- **The market state is consistent with the silence.** ADX(14) is 9.5–11.5 on the 2h crypto legs, and ADA's ATR is at the 0.5th percentile of its trailing 200 bars. That is an extreme low-volatility chop, which a trend-pullback filter is designed to sit out.

## Files

| file | what it is |
|---|---|
| `replay.py` | Calls the live units' `order_package()` on candles from the bot's own feed (`/api/bot/candles`, fetched 2026-10-04 06:13Z). It replays every closed bar and every 15-minute checkpoint of the forming bar since 2026-09-28T03:35Z, then computes the next-bar distance to a signal. |
| `rarity.py` | Joint-silence rarity across all 7 legs. Crypto uses Binance-spot candles, 2025-01-01..2026-09-27. Alpaca uses the bot's Alpaca daily feed. It also checks Binance against Bybit per leg. |
| `output.txt` | Raw output of both scripts. |
| `live_audit_summary.txt` | Live `signals` audit since 09-28T03:35Z: eval rows per leg, actionable rows, and reason histograms. |

The candle files are not committed. To re-run, fetch them again from the endpoints named in each script and pass that directory as argv[1].

## Known caveats (stated, not hidden)

- **The rarity figure uses closed bars.** The live crypto legs decide on the forming bar, so the live silence started at 09-28T03:35Z, about 147h ago. On the closed-bar proxy it is 174h.
- **Binance spot stands in for Bybit history.** Agreement on the overlap:
  - ETH 4h: 26/26
  - XRP 4h: 6/6
  - ADA 2h: 39 shared signals (Bybit 39, Binance 42)
  - XRP 2h: 27 shared signals (Bybit 30, Binance 34)
- **The Alpaca forming-bar replay is approximate.** It builds the forming daily bar from 15m RTH bars.
