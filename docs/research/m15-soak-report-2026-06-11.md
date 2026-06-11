# M15 Phase 4 — Practice-Fleet Soak Report (2026-06-11)

> Workstream A of the M15 soak session (S-M15-PHASE4-SOAK part 2).
> Verifies the practice fleet that went live 2026-06-11 —
> `xauusd_trend_1h` on `oanda_practice` and
> `spy_trend_long_1d` / `qqq_trend_long_1d` / `gld_pullback_1d` on
> `alpaca_paper` — via the diag relays. All evidence pulls are linked by
> issue number. Paper money on both accounts; every real-money gate
> remains closed.

## Verdict

**The fleet is healthy and wired correctly; no practice fills have
occurred yet (expected), so fill-stamping (`is_demo`, broker-side
SL/TP) remains to be verified on the first real fills.** The gold leg
is provably evaluating live OANDA data every tick; the ETF legs are
correctly session-gated until 13:30 UTC. The Phase-0 backtest data is
faithful to the venue: OANDA XAU_USD 1h candles sit a median **0.73 bps**
above the Dukascopy bid-side series (p95 |Δclose| 1.65 bps) over 482
matched bars — immaterial against the strategy's 2.5×ATR stops.

## Evidence

| Check | Result | Source |
|---|---|---|
| Service health | heartbeat 38 s old, `running`; VM at `395c599` (latest main); mem 65 %, disk 37 % | #3342 (`/api/diag/status`) |
| Account gates | `live` map: `oanda_practice: true`, `alpaca_paper: true` (+ bybit_1/2, ib_paper); 14 strategies loaded incl. all four new legs (16-roster minus the two `enabled: false` entries vwap + trend_donchian_1h) | #3342 |
| Gold leg evaluation | `xauusd_trend_1h_eval` row every ~100 s tick on live OANDA 1h bars (`close=4084.715 within channel [4023.87, 4186.42]`, regime `trending`, ADX 38) — correctly non-actionable, multiplexer stays flat | #3353 (`audit_query`) |
| ETF legs session gate | `spy/qqq/gld`: `US market closed - side=none` each tick (journal); first live evaluation lands at 13:30 UTC | #3356 (`journalctl`) |
| Practice fills | **None yet** — newest journal trade row is 2026-06-08 (pre-deploy, Bybit); no XAUUSD/SPY/QQQ/GLD order packages | #3349, #3351 |
| Candle fidelity XAU_USD (OANDA vs Dukascopy 1h) | 482 matched bars 2026-05-12→06-10: Δclose median +0.73 bps signed (venue above bid — the spread), p95 abs 1.65 bps, max abs 59.8 bps (isolated session-edge bars); high/low deltas equivalent | #3355 (`m15_candle_fidelity.py` on the trainer) |
| Candle fidelity SPY/QQQ/GLD (Alpaca vs Dukascopy 1d) | **Blocked** — `/api/bot/candles` returns `no_data` for the three ETFs (root cause below); the trader-side fetch is exercised first at 13:30 UTC | #3355 |
| Balance probe | **Blocked at the snapshot endpoint** — `/api/bot/accounts/balances` is 17 days stale (as_of 2026-05-25) and carries only bybit_1/2 → logged BL-20260611-M15-2; OANDA token auth itself is proven by the working XAUUSD candle fetch + per-tick evaluation | #3352 |
| FX weekend gate | Not yet exercised — first window Fri 2026-06-12 21:00 UTC; watch for `fx_market_closed` rows | — |

## Findings & follow-ups

1. **BL-20260611-M15-2 (new, Tier 1)** — `balance_snapshots.json` stale
   since 2026-05-25; the hourly-report snapshot writer has been silent
   ~17 days and never recorded the new accounts. Until fixed the
   dashboard Accounts tab balance and this soak's balance probe are
   blind. (Backlogged this session.)
2. **BL-20260611-M15-1 resolved** — the runtime-status live-map now
   lists both new accounts (it was a post-flip settling artifact).
3. **FCM mobile-push spam** — every `signal_emitted` triggers FCM
   404 (`Requested entity was not found`) + 400 (invalid registration
   token) warnings, flooding the trader journal. Pre-existing, not
   M15-related; candidate for the health backlog if not already
   tracked.
4. **Alpaca default-window bug (root-caused + fixed on this branch,
   Tier-2 deploy pending operator OK)** — the `no_data` for SPY/QQQ/GLD
   was not an env gap: Alpaca's `/v2/stocks/{symbol}/bars` defaults
   `start` to *the beginning of the current day*
   (docs.alpaca.markets/reference/stockbars) and
   `AlpacaMarketData.get_ohlcv` never passed `start`, so it returns at
   most today's bars — an empty list pre-market and a single partial
   daily bar in-session. The trader uses the same connector, so all
   three `alpaca_paper` daily legs would have silently no-opped from
   their first session tick (Donchian-30 over 1 bar → "insufficient
   data" → looks like a quiet strategy forever — the
   stranded-capability pattern). Fix: explicit timeframe-aware
   lookback `start` + warning on empty bars + regression test
   (`fix(alpaca)` commit on PR #3360). The web-api journal pull (#3359)
   corroborates: the ETF candle requests returned 200 with no connector
   warning — i.e. Alpaca answered successfully with zero bars.
5. **First-fill verification still open** — on the first gold breakout
   / ETF entry, verify: journal row carries `is_demo=1` +
   `account_id` ∈ {oanda_practice, alpaca_paper}; OANDA order carried
   `stopLossOnFill`/`takeProfitOnFill`; Alpaca order was a bracket
   (entry + TP limit + SL stop). The soak monitor (follow-up checks)
   owns this.

## Reproduction

Diag-relay issues #3342–#3357 (titles carry the exact paths); fidelity
script `scripts/ops/m15_candle_fidelity.py` (run on the trainer against
`/home/ubuntu/m15-phase0/data/XAUUSD_15m.csv` + the live web-api candle
route).
