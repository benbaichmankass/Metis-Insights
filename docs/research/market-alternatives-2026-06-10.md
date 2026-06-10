# Market & Platform Alternatives — Reducing Futures Dependence (2026-06-10)

> Research memo, Tier-1 (analysis only — no config, no live-path change).
> Operator request 2026-06-10: futures integration pain (IB Gateway) is high;
> evaluate simpler markets reachable through easier APIs and propose a
> migration path.
>
> **Supersedes the venue scoping in
> [`tradeable-universe-2026-06-02.md`](tradeable-universe-2026-06-02.md)** —
> that doc recorded the 2026-06-02 operator directive "futures
> diversification first, eventual venue NinjaTrader". This memo proposes
> de-prioritising the futures track. Adopting it is an **operator decision**
> (direction-setting); the WS-A futures sweep results remain valid evidence
> either way.

## 1. Why we want off futures (the actual cost we pay today)

The three futures strategies (`mes_trend_long_1d`, `mgc_pullback_1d`,
`mhg_pullback_1d`, all on IBKR **paper**) are the only consumers of the
heaviest, most failure-prone subsystem in the stack:

- **IB Gateway** is a Java GUI app running under Xvfb in Docker on a 2-core
  VM. It needed its own dead-man watchdog (`ict-ib-gateway-watchdog`), hard
  Docker CPU/memory caps (`IB_GATEWAY_CPUS`/`IB_GATEWAY_MEMORY`), three
  timeout/breaker env vars (`IB_FETCH_TIMEOUT_S`, `IB_PROBE_TIMEOUT_S`,
  `IB_BREAKER_COOLDOWN_S`), and was a root cause in the 2026-06-05
  restart-loop incident (PR #2814) and the 2026-06-10 CPU-wedge cascade.
- **Data is delayed** (type-3, no CME subscription) and COMEX metals depend
  on an entitlement we can't verify until orders fail (logged risk on
  MGC/MHG promotion).
- Nightly IBKR resets log the session out; recovery is watchdog-driven and
  has left MES dark for hours historically (`BL-20260527-003`).

By contrast, the Bybit path (8 strategies, the only real-money flow) is a
plain HTTPS REST API via CCXT and has needed none of that scaffolding. The
goal: keep the *exposure* the futures legs were buying (equity-index,
metals — BTC-uncorrelated diversification) while shedding the *transport*.

Two repo facts that shape everything below:

1. **No trading-calendar handling exists.** The pipeline ticks 24/7 and
   every strategy assumes continuous bars (`_build_killzone_exchange` is a
   naming shim, not session logic). Any market that closes (equities, FX
   weekends) needs a market-hours gate added — one new module, but it
   touches the tick path (Tier 2).
2. **The live roster is pure price action.** Only the killed `vwap.py`
   reads candle volume; every live strategy (Donchian trend, turtle_soup,
   ict_scalp FVG, fvg_range, htf_pullback, squeeze/fade) uses OHLC + ATR
   only. That makes them portable to FX (no true volume exists there) and
   to ETFs without code changes to the signal math — only re-tuning.

## 2. Platform / market comparison

| | **Bybit alts** (spot + perps) | **Alpaca** (US stocks/ETFs) | **Alpaca** (options / crypto / fixed income) | **OANDA v20** (FX) | **NinjaTrader / Tradovate** (futures) | **CFD brokers** (Capital.com, IG, …) |
|---|---|---|---|---|---|---|
| **Integration effort** | **None** — same keys, same connector; add symbols to YAML (Tier-3 config) | Moderate — clean REST + `alpaca-py` SDK; ~150-line data connector + the `new-broker` package (~500–800 lines, has a skill checklist) | Same connector, but options adds chains/greeks/expiry handling (big); crypto duplicates Bybit at worse fees; fixed income not a strategy fit | Moderate — v20 REST is one of the cleanest broker APIs (token auth, candles + streaming + orders); same `new-broker` path | High — this is the dependence being reduced (Rithmic/Tradovate feeds, contract rolls, sessions) | Moderate, **but** regulatory availability varies by residency; pricing is dealer-quoted |
| **API quality** | Proven here for 2 months (V5 via CCXT) | Excellent docs, free real-time IEX feed (SIP-consolidated is a paid tier), websockets, **free paper env identical to live** | Good (same platform) | Very good; practice account mirrors live; history to 2005 | TBD; no first-party simple REST like Alpaca/OANDA | Varies widely |
| **Order types** | Market/limit + broker-side SL/TP (what we use today) | Market/limit/stop, **bracket orders** (entry+TP+SL in one) — maps directly onto our order package | Options multi-leg = new execution complexity | Market/limit/stop with attached TP/SL/trailing — also maps directly | Full futures stack | Broker-dependent |
| **Fees (high level)** | Perps 0.02%/0.055% maker/taker; spot 0.1% | **$0 commission** stocks/ETFs; cost ≈ spread (≈1bp on SPY/QQQ) | Options $0 commission (+OCC fees); crypto 0.15%/0.25% (worse than Bybit) | Spread-only ≈0.6–1.4 pips EUR/USD (≈0.6–1.2bp) or core+commission | ~$0.35–0.75/side micro + data fees | Spread + overnight financing (high for holds) |
| **Liquidity / slippage** | Majors (ETH, SOL…) fine; thin alts risky | SPY/QQQ/GLD among the most liquid instruments on earth | OK (majors) | Majors extremely deep; weekend gaps | Micros fine | Dealer book — opaque |
| **Hours complexity** | None (24/7 — matches pipeline) | **RTH 6.5h/day + gaps** → needs market-hours gate + gap handling | Same as underlying | 24/5 — near-match; weekend close only | ~23/5 + daily breaks + rolls | Mostly 24/5 |
| **Account gotchas** | Already live | **PDT rule**: <$25k margin accounts get ~3 day-trades/5 days → intraday strategies blocked at small size; **daily-timeframe holds unaffected**. $2k min for margin/short | Options approval levels; settlement | No PDT; leverage capped by jurisdiction; financing on holds | Margin per contract | Jurisdiction-dependent; CFDs banned for US residents |

Sources: [Alpaca](https://alpaca.markets/), [Alpaca docs](https://docs.alpaca.markets/us/docs/getting-started), [Alpaca data](https://alpaca.markets/data), [Alpaca fees (BrokerChooser)](https://brokerchooser.com/broker-reviews/alpaca-trading-review/alpaca-trading-fees), [OANDA v20 API](https://developer.oanda.com/rest-live-v20/introduction/), [OANDA development guide](https://developer.oanda.com/rest-live-v20/development-guide/). Fee/plan details should be re-verified at integration time (they drift).

## 3. Strategy-fit map

Roster status as of 2026-06-10 (from `config/strategies.yaml` + changelog):
6 live BTC strategies, 3 live futures strategies on IBKR paper, `vwap`
**killed 2026-06-09** (net loser in every regime cell), fade/squeeze in
shadow after live failure.

| Strategy (status) | Bybit alts | Alpaca ETFs (daily) | Alpaca equities (intraday) | OANDA FX | Adaptation needed |
|---|---|---|---|---|---|
| `trend_donchian` 1h (live, real money) | **Good** — same code, re-tune per symbol | **Good as daily** — long-only ETF trend ≈ the validated SPX result | Poor — gaps break 1h channels | Fair — FX trends are choppier; needs sweep | Re-tune only (WS-A finding: crypto params don't transfer) |
| `htf_pullback_trend_2h` (live, real money) | **Good** | **Good as daily** — `mgc/mhg_pullback_1d` are literally this unit on daily bars | Poor | Fair | Re-tune only |
| `turtle_soup` 15m/1m sweep-reversal (live) | **Good** | Weak — 1m entry leg collides with PDT + RTH open/close noise | Weak | **Good** — sweep/raid logic is FX-native (ICT's home market) | Market-hours gate for FX weekend; re-tune |
| `ict_scalp_5m` sweep→displacement→FVG (live) | **Good** | n/a daily | **The QQQ candidate** — see §4 caveat | **Good** — FVG/displacement concepts originate in FX | Session gate (trade RTH / killzones only), gap handling, re-validate |
| `fvg_range_15m` (live) | **Good** | n/a daily | Fair — ranges exist intraday but PDT applies | Fair–good (Asian-session ranges) | Session gate + re-tune |
| `mes_trend_long_1d` (live, IBKR paper) | n/a | **Direct replacement: same logic on SPY/QQQ** — drops IB Gateway entirely | — | n/a | Symbol + venue swap; re-validate on ETF series |
| `mgc_pullback_1d` / `mhg_pullback_1d` (live, IBKR paper) | n/a | **Direct replacement: GLD / CPER** (copper ETF is thinner — check spread) | — | Partial (XAU/USD on OANDA covers gold) | Symbol + venue swap; re-validate |
| `vwap` (killed) | — | Conceptually *better* suited to RTH equities than 24/7 crypto (session VWAP is a real equities object), and it's our only volume-consuming strategy | — | Poor (no true volume in FX) | **Do not port without full re-validation** — it failed every regime cell; an equities retry is a new research project, not a migration |
| `fade/squeeze_breakout_4h` (shadow) | Shadow only | Shadow only | — | — | Stay shadow; not migration candidates |

**Bottom line:** the three IBKR futures strategies have one-for-one ETF
proxies (SPY/QQQ, GLD, CPER) at the **daily** timeframe — which sidesteps
both PDT and most market-hours complexity (a daily-bar strategy evaluates
once a day; the gate is simple). The BTC roster needs zero new platform.
The intraday ICT strategies fit FX naturally and QQQ speculatively.

## 4. The QQQ evidence, honestly

The first-pass result (sprint-plan 2026-04-28: **13 trades, 61.5% WR,
+0.85R** on QQQ 15m FVG scalp) did not hold up in the two follow-ups in the
repo: `ict-validation-report.md` — 128 trades, 46.9% WR, +0.27 expectancy
(⚠️ flag); `s006-synthetic-report.md` — 308 trades, 33.1% WR, **−0.12
expectancy** (loser). The M7 gate (≥50 validated trades) is technically met
by the 128-trade run, but the trend across the three runs is *decaying with
sample size* — the standard signature of an overfit first pass. QQQ
intraday is a **research candidate, not a migration target**. The
near-term equities case rests on the daily-timeframe ETF trend/pullback
replacements (§3), which inherit the already-validated SPX/metals logic.

## 5. Recommendation

1. **Best easy-integration start: Alpaca, US ETFs, daily timeframe, paper
   account first.** It directly retires the IB Gateway for the three
   futures legs (same strategy units, symbols MES→SPY or QQQ, MGC→GLD,
   MHG→CPER), the API is the simplest of any candidate, paper trading is
   free and identical to live, and daily bars avoid PDT and minimize the
   market-hours work. This replaces *transport*, not *strategy* — lowest
   research risk of any option.
2. **Zero-cost parallel move: Bybit alt expansion** (ETH/SOL/etc. perps).
   No new integration at all — but it deepens crypto concentration rather
   than replacing futures exposure, so it's a complement, not the answer.
3. **Second-best / fallback: OANDA FX.** Cleanest API after Alpaca, 24/5
   nearly matches the pipeline's continuous-market assumption, no PDT, and
   the ICT-style strategies (turtle_soup, ict_scalp) are conceptually
   FX-native. Pick this over Alpaca if (a) the operator's residency makes a
   US brokerage account awkward, or (b) we decide intraday ICT — not daily
   trend — is the priority. Requires the same `new-broker` build plus
   weekend-gap handling.
4. **Avoid for now:** options (order/execution complexity buys nothing for
   the current roster), fixed income (no strategy fit), Alpaca crypto
   (strictly worse fees than Bybit), CFD brokers (regulatory ambiguity,
   opaque pricing), additional crypto exchanges (redundant), and **intraday
   equities at small account size** (PDT). NinjaTrader futures stays parked
   as the "if we ever go back to futures" plan.

### Risk controls already in place vs missing (for any promotion talk)

In place and venue-agnostic: account `mode: live|dry_run` (operator-gated
`set-account-mode` wire), strategy `execution: live|shadow`, RiskManager
per-account caps (daily loss, max intraday DD, max position notional, min
balance), netting guard, news sizing, regime router — all enforced in
`Coordinator.multi_account_execute`, none Bybit-specific. **Missing for a
non-24/7 venue:** market-hours/calendar gate, overnight-gap handling in the
order monitor (SL/TP must live broker-side, which Alpaca bracket orders and
OANDA attached orders both support), and PDT-awareness in sizing if any
intraday equities strategy is ever proposed. Live promotion on a new venue
stays Tier-3 (operator-approved), exactly as it was for MGC/MHG.

## 6. Phased migration plan (proposed next sprints)

- **Phase 0 — evidence, no code** (trainer VM, existing harnesses, Tier 1):
  pull daily SPY/QQQ/GLD/CPER history (and XAU/USD as the OANDA
  cross-check), run the existing `backtest_{trend,pullback}` harnesses
  net-of-fee with walk-forward, per the WS-A method. Output: ETF
  generalization matrix. *Also:* Bybit alt sweep (already scoped in WS-A).
- **Phase 1 — Alpaca data-only connector** (Tier 1): `get_ohlcv` via
  `alpaca-py`, registered in `market_data.py::_build_exchange_client`
  (~150 lines, no order path). Lets the live roster's signal builders and
  the regime scorer see ETF bars; enables QQQ-FVG re-research with real
  data and a proper sample.
- **Phase 2 — Alpaca paper account, full wiring** (Tier 2; `new-broker`
  skill checklist): account package, `EXCHANGE_MAP` entry, executor branch
  using bracket orders, `accounts.yaml::alpaca_paper` (`mode: live`,
  paper money — mirroring `ib_paper`), market-hours gate for the daily
  strategies, secrets via `init-actions-secrets` + `sync-vm-secrets`.
  Operator hand-off: create the Alpaca account, paste two API keys.
- **Phase 3 — shadow → paper-live soak** (Tier 3 config PR per strategy):
  clone `mes_trend_long_1d` → `spy_trend_long_1d` etc., `execution: shadow`
  first, then paper-live on `alpaca_paper`. Run alongside the IBKR legs for
  ≥4–6 weeks; compare fills/slippage/uptime.
- **Phase 4 — retire the IB Gateway** (Tier 2/3, operator-approved): flip
  the three futures strategies off, decommission the gateway container +
  watchdog, reclaim ~0.75 CPU on the 2-core VM (a wedge-cascade win by
  itself). OANDA decision point: only start the FX build if Phase 0/1
  evidence says the intraday ICT strategies deserve a 24/5 venue.

## 7. Open questions for the operator

1. **Direction change vs the 2026-06-02 directive** (futures-first via
   NinjaTrader): confirm this memo's de-prioritisation of futures is the
   new standing directive.
2. **Residency / eligibility:** Alpaca individual accounts are
   US-jurisdiction brokerage accounts (international signups supported but
   country-dependent); OANDA's division (and leverage caps) also depend on
   residency. Which can you open?
3. **Account size** for the eventual live equities account — under $25k
   means daily-timeframe only (PDT); that's compatible with the
   recommended Phase 3 roster but rules out intraday QQQ work going live.
4. **Keep or kill the IBKR paper legs during the soak?** Recommendation:
   keep until Phase 3 comparison data exists, then Phase 4 retires them.
5. **Market data tier:** free IEX real-time is likely fine for daily bars;
   approve the paid consolidated feed only if an intraday equities
   strategy ever reaches Phase 3.

— Research memo only; nothing in this document changes runtime behaviour.
