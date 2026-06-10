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

## 0. Operator decisions (recorded 2026-06-10, post-review)

The operator reviewed this memo in chat and decided:

1. **Futures de-prioritisation confirmed.** The 2026-06-02 futures-first /
   NinjaTrader directive is superseded; this memo is the standing direction.
2. **Platforms: evaluate Alpaca AND OANDA — "we can also do both."**
   Tradeoffs incl. fees in §5a. The residency/division question stays open
   (§7) until the operator states country of residence.
3. **Timeframes: intraday-first, same as the current roster** (5m/15m/1h/
   2h/4h), *not* daily-first as §5 originally recommended. The live
   equities account will be **>$25k**, so the PDT rule is not a
   constraint. §5/§6 revised accordingly; the daily ETF replacement path
   remains documented as the lower-research-risk option.
4. **IBKR paper legs stay running.** The futures track still gets stood up
   properly, but system expansion comes first; gateway retirement
   (Phase 4) is deferred indefinitely, not scheduled.
5. **Both platforms, sequenced; US residency for both** (clarified in
   chat, same day). End state is Alpaca AND OANDA — they carry different
   halves of the roster (§5) — but integrations are built **one at a
   time**: Phase 0 backtests on both markets decide which is wired first
   (expected: OANDA, since more of the roster transfers unchanged).
   The operator lives in Israel but can claim US residency and will use
   the **US residency for both accounts**: Alpaca gets free ACH funding
   (vs $50 international wires), and OANDA US removes the risk that its
   divisions decline Israeli-resident retail FX clients (Israeli ISA
   rules have pushed many foreign brokers to do so). Consequence: the
   OANDA US division is **FX-only** (no index/metal CFDs), so the
   index/metals exposure replacement belongs to Alpaca ETFs. The tax
   caveat is **resolved**: the operator is a **US citizen** (files US
   taxes regardless of living in Israel), will use a US address/setup,
   and set "everything through the US" as the standing preference. No
   account needs to be opened until Phase 2.
6. **Plan adopted** (chat, same day): operator approved the memo and the
   phased plan; execution starts with **Phase 0 backtesting** — no
   integration code before the sweeps. Registered as **M15** in
   `ROADMAP.md`.

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

## 5. Recommendation (revised per §0 — intraday-first, >$25k account)

The intraday-first directive splits the roster across the two platforms by
timeframe, because an equities RTH session is 6.5 h/day:

| Roster timeframe | Bars per equities RTH day | Equities (Alpaca) | FX (OANDA) |
|---|---|---|---|
| 5m / 15m (`ict_scalp_5m`, `turtle_soup` 15m/1m, `fvg_range_15m`) | 78 / 26 | **Works** — with an RTH session gate (skip first/last minutes, no overnight holds without broker-side stops) | **Works as-is** (24/5) |
| 1h (`trend_donchian`) | 6.5 | Marginal — channels span days, gaps dominate | **Works as-is** |
| 2h / 4h (`htf_pullback_trend_2h`, fade/squeeze) | ~3 / ~1.6 | **Doesn't meaningfully exist** — feeds session-anchor these bars; the strategy becomes a different object | **Works as-is** |

1. **Do both, split by timeframe.** **OANDA FX carries the 1h–4h
   trend/pullback family unchanged** (all current timeframes survive; the
   pipeline's continuous-market assumption nearly holds — only a weekend
   gate is needed; turtle_soup/ict_scalp are conceptually FX-native).
   **Alpaca equities carries the 5m/15m family** (ict_scalp / fvg_range /
   the QQQ re-research) under an RTH session gate. Both paper/practice
   environments are free, so running both costs only the integration work
   (~one `new-broker` build each, the data connectors are ~150 lines each).
2. **Exposure note (the original reason the futures legs exist):** only
   Alpaca ETFs (SPY/QQQ, GLD, CPER) replace the equity-index + metals
   diversification on a US-division account — OANDA US offers FX (+ XAU/
   XAG) but not index CFDs; non-US OANDA divisions do offer index/metal
   CFDs but their overnight financing drags on multi-week trend holds.
   The daily-timeframe ETF replacement path from the original §5 remains
   the lowest-research-risk way to keep that exposure, available whenever
   wanted.
3. **Zero-cost parallel move: Bybit alt expansion** (ETH/SOL/etc. perps).
   No new integration at all — but it deepens crypto concentration rather
   than replacing futures exposure, so it's a complement, not the answer.
4. **Avoid for now:** options (order/execution complexity buys nothing for
   the current roster), fixed income (no strategy fit), Alpaca crypto
   (strictly worse fees than Bybit), standalone CFD brokers (regulatory
   ambiguity, opaque pricing), additional crypto exchanges (redundant).
   NinjaTrader futures stays parked as the "if we ever go back to futures"
   plan.

### 5a. Alpaca vs OANDA — account-opening + cost tradeoffs (operator Q2)

| | **Alpaca** | **OANDA** |
|---|---|---|
| **Who can open** | US brokerage account; international onboarding supported in ~195 countries (W-8BEN, identity verification via international providers), $1 minimum | Regional divisions (US / Canada / UK-EU / Asia-Pacific / Global-BVI) — the division your residency lands in fixes the product set, leverage cap, and protections |
| **Leverage** | 4× intraday / 2× overnight (Reg-T margin, $2k min for margin) | US ~50:1 majors, EU 30:1, Global division higher — far more than the strategies need either way |
| **Trading cost** | $0 commission; cost ≈ SPY/QQQ spread (~1bp) + tiny SEC/TAF sell fees | Spread-only ~0.8–1.6 pips, or core pricing ~0.2–0.4 pips + ~$5/100k commission (≈0.5–1.5bp round-trip on majors) |
| **Holding cost** | None on cash equity positions (margin interest 6.5% only if levered) — **multi-day/week holds are free** | Daily tom-next financing on every overnight position — **a real drag on the multi-week trend/pullback holds**, fine for intraday |
| **Funding friction** | Free ACH (US); international wires $50/transaction; Rapyd local funding in many countries | Local funding rails per division; $14/mo inactivity fee only after 12 idle months (irrelevant for a bot) |
| **Asset coverage for our exposure goals** | Full ETF palette (equity index, gold, copper, anything) | FX majors everywhere; metals/index CFDs only on non-US divisions |
| **Paper/practice** | Free, identical API to live | Free practice account, identical v20 API |

**Net:** costs are comparable and small for intraday use; the real
differentiators are (a) **holding cost** — Alpaca free for long holds, OANDA
financing drags on them, which maps exactly onto the timeframe split in §5;
and (b) **residency division** — determines whether OANDA can also cover the
index/metals exposure or only FX. Doing both is realistic and is the
recommended end-state; sequencing is in §6.

§5a sources: [Alpaca non-US accounts](https://alpaca.markets/learn/live-trading-account-non-us),
[supported countries](https://alpaca.markets/support/countries-alpaca-is-available),
[transfer fees](https://alpaca.markets/support/fees-transfers-outside-us),
[OANDA US pricing](https://www.oanda.com/us-en/trading/our-pricing/),
[OANDA financing](https://www.oanda.com/us-en/trading/financing-fees/),
[OANDA fees breakdown (BrokerChooser)](https://brokerchooser.com/broker-reviews/oanda-review/oanda-fees).
Re-verify at account-opening time.

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

- **Phase 0 — evidence, no code** (trainer VM, existing harnesses, Tier 1),
  at the **current roster timeframes** per §0: (a) FX sweep — EUR/USD,
  GBP/USD, XAU/USD on 15m/1h/2h/4h through `backtest_{trend,pullback,
  ict_scalp,fvg_range}` (OANDA v20 candle history to 2005 is free via the
  practice API); (b) equities sweep — QQQ/SPY 5m/15m **restricted to RTH**
  (Alpaca free historical bars), incl. the QQQ-FVG re-research with a
  ≥50-trade sample per the M7 gate; (c) the daily ETF replacement sweep
  (SPY/QQQ/GLD/CPER) as the low-risk baseline; (d) Bybit alt sweep
  (already scoped in WS-A). Output: one generalization matrix tagged by
  venue, deciding which platform gets wired first.
- **Phase 1 — data-only connectors** (Tier 1): `get_ohlcv` for Alpaca
  (`alpaca-py`) and OANDA (v20 candles), registered in
  `market_data.py::_build_exchange_client` (~150 lines each, no order
  path), plus the **market-hours/session module** (RTH calendar for
  equities, weekend gate for FX) — built once, venue-parametrised.
- **Phase 2 — broker wiring, evidence-first order** (Tier 2; `new-broker`
  skill checklist per platform): whichever venue Phase 0 favours gets
  wired first — account package, `EXCHANGE_MAP` entry, executor branch
  (Alpaca bracket orders / OANDA attached TP-SL), `accounts.yaml` entry
  (`alpaca_paper` / `oanda_practice`, `mode: live`, paper money —
  mirroring `ib_paper`), secrets via `init-actions-secrets` +
  `sync-vm-secrets`. Operator hand-off per platform: open the account,
  paste the API keys. The second platform follows once the first soaks
  clean.
- **Phase 3 — shadow → paper-live soak** (Tier 3 config PR per strategy):
  new strategy entries with `execution: shadow` first, then paper-live.
  Run alongside the IBKR legs (which **stay running**, per §0) for ≥4–6
  weeks; compare fills/slippage/uptime.
- **Phase 4 — IB Gateway retirement: deferred** (operator decision
  2026-06-10 — keep expanding the system first). When eventually taken,
  it reclaims ~0.75 CPU on the 2-core VM and removes the gateway watchdog
  + breaker stack.

## 7. Open questions for the operator

All original questions are now **answered** — recorded in §0 (directive
confirmed; >$25k + intraday-first; IBKR legs stay; both platforms under
the operator's US citizenship/setup, §0.5; tax caveat resolved — US
citizen files US taxes regardless). Still open:

1. **Market data tier for live intraday equities:** Alpaca's free
   real-time feed is IEX-only (a thin slice of consolidated volume).
   Likely fine for 5m/15m signal bars, but worth a paid consolidated-feed
   trial (~$99/mo tier) before any equities strategy goes paper-live at
   5m — decide at Phase 2.

— Research memo only; nothing in this document changes runtime behaviour.
