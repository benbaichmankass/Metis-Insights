# Prop-platform automation survey: who allows a bot, and through what interface (2026-09-30)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status. It is a desk survey, not a decision record.**

Lane **PROP-PLATFORMS** (Tier 1, scope only). Operator request, popup ~05:10Z
2026-09-30, verbatim: *"... as well as researching other prop platforms to see if
any have an API or other better solution for automated trading"*. Context on
main: [`prop-automation-options-2026-09-27.md`](prop-automation-options-2026-09-27.md)
(the DXtrade browser executor, live on breakout_1 since 2026-09-29), the
Cloudflare ban of the Breakout proprietary terminal from our VMs
(PI-20260929-FRJ7NMPU-0001), and the operator's accepted breach-and-rebuy
economics (grade on **EV per account purchase**, rows P1b / P2).

**Nothing was bought, no account opened, no credential requested, and no firm
was contacted.**

## How to read the marks

Every external fact carries one of three tags and they are never mixed:

- **[1P]**: read on the firm's (or the interface owner's) own page on
  2026-09-30. `WebFetch` returns a small-model *summary* of the page, so
  quotation marks below are near-verbatim, not byte-exact. **Re-read the page
  before you rely on exact wording.**
- **[snippet]**: a search-result snippet that cites a first-party URL that I
  could not fetch. Treat as third-party.
- **[3P]**: third-party (review sites, bridge vendors, competitors). Bridge
  vendors (PickMyTrade, TradersPost, QuantVPS) and competing prop firms
  (Velotrade) have a commercial interest in how they describe automation.

**Coverage gaps, stated up front.** These first-party pages returned 403/404/429/DNS
failure and are therefore **unread**: all of `breakoutprop.com` and
`docs.breakoutprop.com`, `e8markets.com`, `fundingpips.com`,
`alphacapitalgroup.uk`, the Apex help center, the Take Profit Trader UTP, the
MyFundedFutures help center, `help.ftmo.com`, Tradovate's support article, and
Rithmic's site. A section built from those is 3P only and says so. Elite Trader
Funding, FundingTraders, Funding Pips (crypto) and Blueberry were **not researched**.

## 0. The answer

1. **No surveyed firm publishes an API for its *own* prop account.** What exists
   is one of: (a) the prop routes the trader to an **exchange sub-account** and the
   trader's own exchange API key drives it (HyroTrader, CryptoFundTrader, both on
   Bybit); (b) the prop runs a **retail-trading-platform** that has a public API
   (cTrader Open API, TradeLocker REST); (c) an **EA inside MetaTrader**; or
   (d) nothing scriptable (Breakout's terminal).
2. **Only (a) is the same shape as our existing stack.** We already trade Bybit
   through a REST client with native brackets. Routes (a) need a new account
   config and a rule guard, not a new broker package.
3. **Futures props are the worst fit and are excluded from the shortlist**:
   Topstep bans VPS/remote-server order transmission, its API refuses live
   funded accounts, and Tradovate reportedly refuses API access to prop accounts
   (§ 4).
4. **None of the crypto props' bot clauses could be read verbatim from a
   first-party page** (HyroTrader's is in the firm's FAQ index but its article body
   did not fetch, twice). Any pick is conditional on **written confirmation from
   the firm** that a custom API bot is permitted. That is a decision, not a fact
   this survey can settle.

## 1. Crypto props

### 1.1 HyroTrader (Bybit demo sub-account, your own API key)

| | |
|---|---|
| Bots allowed? | **Custom bots: yes, per the firm's own FAQ index, wording unconfirmed.** [1P `https://www.hyrotrader.com/faq/hyrotrader-account/`] lists the article *"Can I use trading bots or signal bots during the challenge or on a funded account?"*; its body did not fetch (my second attempt this session returned only the FAQ menu). [snippet, same article URL] paraphrases it as: custom bots via your own Bybit demo API key are allowed in the challenge; third-party signal apps are "not supported" in the challenge and allowed once funded. [1P `https://www.hyrotrader.com/blog/crypto-api-trading/`]: traders can "scalp, swing, or run crypto trading bots freely" and trades "execute on ByBit's real order books". [3P `velotrade.com/blog/algo-bot-trading-crypto-prop-firm`, competitor] calls the position "Unclear": terms bar bots "except where expressly permitted in the Trading Rules". |
| Interface | Bybit demo sub-account API key (REST/WS) linked to the HyroTrader dashboard: works from a headless Linux VM. Also Tealstreet and CLEO terminals [1P home]. [3P cryptoslate] API keys expire after 3 months (unconfirmed 1P). |
| Prohibited | [1P `.../faq/trading-restrictions/what-are-prohibited-trading-actions/`] "Modifying or removing API keys" (Bybit challenges); spot, EUR/USD, options and USDC pairs. "Violation ... may result in account failure or termination." [3P cryptoslate] the API key *can* place prohibited instruments and the firm detects them automatically, so **the bot must hard-restrict itself to USDT perps**. |
| Instruments | Bybit USDT perps [1P blog]. BTC/ETH/SOL/XRP/ADA are **inferred** from the Bybit route (no firm symbol list seen). |
| Brackets | Bybit's own order engine, so native SL/TP. **Inferred**, not a firm statement. SL is not mandatory [1P `.../faq/rules/d/`]. |
| Rules that bind a bot | [1P home] daily drawdown 4%, max loss 6%; [3P cryptoslate] describes an older 5%/10%, so verify at purchase. Standard = trailing daily drawdown, Swing (+$89) = fixed [1P FAQ titles]. Max loss per trade 3% of initial balance [1P]. Evaluation: no day > 40% of net result [1P]. Martingale "strictly prohibited" [1P]. Copy trading "not allowed" [1P]. "Trading solely based on news events is not allowed"; holding through news is fine [1P]. Funded: total margin ≤ 25% of initial, open notional ≤ 2x initial, no profit-distribution rule [1P]. Low-cap (<$100M mcap) capped at 5% of initial [1P]. 5 minimum trading days (1-step) [1P]. **No HFT ban or minimum hold time found in what I read: absence in the fetched pages, not proof.** |
| Price / split | [1P home] $5k **$59**, $10k $119, $25k $249, $50k $379, $100k $579, $200k $969; fee refundable with first payout; split 80% scaling to 90%; USDT/USDC payouts. [3P cryptoslate] a 5% withdrawal cap: unverified. |
| Reputation | [3P cryptoslate] Trustpilot ~4.3/5 on ~250 reviews; conflicting support answers on tradable instruments; "$5 million paid" unverified. |

### 1.2 CryptoFundTrader (CFT; Bybit API, MT5 or Match-Trader)

| | |
|---|---|
| Bots allowed? | **Yes, except HFT-shaped.** [1P `cryptofundtrader.com/evaluation-rules/`] "Using programs or algorithms to perform large quantities of purchases and sales in small fractions of time is not allowed"; "The use of bots or EA's that take advantage of the demo environment" is banned. [1P `/faq/`] HFT, tick scalping and arbitrage EAs prohibited "across all platforms". [3P snippet] EAs and copy trading "fully permitted": unconfirmed 1P. |
| Interface | [1P FAQ] platforms are Match-Trader, MT5 and Bybit. For Bybit "an API Key must be generated and connected to the Crypto Fund Trader dashboard" and must not be "edited, deleted, or replaced" or the evaluation is auto-invalidated. Headless-friendly. |
| Instruments | "depend on the trading platform you choose" [1P]. Bybit up to 1:100 depending on instrument [1P]. Specific coins not verified. |
| Rules | [1P FAQ] 1-phase: daily 4% (12:05 UTC), **trailing 6%** that locks at the initial balance. 2-phase: daily 5%, overall 10%. Reverse trades ≥ 60 s simultaneous duration banned; daily/per-trade simulated profit cap **$10,000** [1P evaluation-rules]. |
| Price | [1P FAQ] a table headed **"Reset Accounts"** lists 2-phase $5k $180 … $100k $1,880; 1-phase $5k $198 … $100k $2,079. **I cannot tell from the fetch whether these are initial prices or reset fees.** If they are initial prices CFT is ~3x HyroTrader per account, which matters for EV per purchase. Split and refundability not retrieved. |

### 1.3 Breakout (breakoutprop.com): third-party only

Every first-party page and the docs host returned 403. What is known:
[3P quantvps, fetched] the platform "lacks bot and EA support" and MT4/MT5/TradingView are unavailable; [3P snippets, quantvps/velotrade] instead say bots are permitted, HFT/latency exploitation banned, and an "API layer" exists. **These conflict and neither is first-party.** [3P Trustpilot summary] DXtrade terminal, lag and auto-logout complaints. Rules [3P]: 1-step static, 4% daily; 2-step trailing, 5% daily, reset 00:30 UTC; leverage BTC/ETH 5:1, alts 2:1. From our own record: an unauthenticated `GET https://wss.breakoutprop.com/dxsca-web/` returned `403 RBAC: access denied` (MEASURED 2026-09-27, S26 of the 09-27 doc). **This survey found nothing that changes that route.**

### 1.4 Bitfunded, FundedNext, The5ers (crypto CFDs)

- **Bitfunded.** [1P `bitfunded.com/crypto-prop-trading-firm/`] "allows algorithmic trading provided strategies respect platform rules, risk limits, execution stability, and overall funded account performance requirements." [3P thetrustedprop] proprietary platform, no evidence of a public API; 5x leverage; 5% daily, 8-10% total; Trustpilot ~4.6/5 (187) with complaints of hidden "anti-gambling" rules and 70% payout penalties (one review site's claims). Interface for a bot: **unknown**.
- **FundedNext.** [1P `help.fundednext.com/en/articles/8020763-is-ea-allowed-in-fundednext`] under $50k, "traders are welcome to use third-party Expert Advisors (EAs) and trading bots on the MetaTrader 4 and MetaTrader 5 platforms" (additional fee); $50k and above "must trade manually and may not use Expert Advisors, trading bots, or any automated tools"; not on cTrader or Match-Trader; $300,000 allocation cap per EA strategy. MT4/MT5 only, so no headless-friendly API.
- **The5ers.** [1P `the5ers.com/faqs/prohibited-trading-practices/`] bans EAs "where the trader does not own the source code", third-party EAs "where other traders have the same trades open", rollover scalping EAs, HFT "the majority of trade durations ... a few seconds or less", copy trading. Own-code EAs are therefore fine. Platform and crypto symbol list not read first-party.

## 2. FX/CFD props and the platform APIs behind them

The prop firms below all sit on retail platforms. The useful question is which
platform API is scriptable from a headless Linux VM and what it does at order
entry, so those come first.

### 2.1 Interface table

| Interface | Official API | SL/TP at order entry | Headless Linux | Offered by (as read) |
|---|---|---|---|---|
| **TradeLocker Public API** | REST + JWT (`accessToken`/`refreshToken`) [1P `public-api.tradelocker.com/llms.txt`] | **Yes.** [1P `.../reference/placeorder.md`] `stopLoss` + `stopLossType` (absolute, offset, trailingOffset), `takeProfit` + `takeProfitType` (absolute, offset), `strategyId` "to identify orders and positions placed through algorithmic trading"; order types limit/market/stop; validity GTC/IOC | **Yes** (plain REST) | E8, FundingPips, Alpha Capital [3P]; Blue Guardian [1P home] |
| **cTrader Open API** | Spotware, OAuth2, Protobuf/JSON over TCP/WS [1P `help.ctrader.com/open-api/`] | Absolute and relative SL/TP, trailing, GSL fields exist, but **"Not supported for MARKET orders"** [1P `ProtoOANewOrderReq`]: a market entry needs a follow-up amend, i.e. a naked window | **Yes after one browser consent**: access token ~30 days, refresh token no expiry [1P `.../account-authentication/`]. Rate limit 50 req/s (5 req/s historical) [1P] | FTMO [1P home]; E8, FundingPips (+$20), Alpha Capital [3P] |
| **Match-Trader Platform API** | REST (login, one-time token, refresh) [1P `match-trader.com/technology/platform-api/`] | Not verified | REST is headless, **but** "for those utilizing a server license, this API's functionality can be selectively disabled" [1P], so it depends on each prop | E8, FundingPips, Blue Guardian [3P/1P mix; see firms] |
| **DXtrade** | REST, Push (WS), FIX, specs at `<host>/specs` [3P] | Not verified | REST yes | Alpha Capital [3P]; no prop confirmed as enabling it for retail |
| **MetaTrader 5** | EA (MQL5) inside the terminal only | Native | Official Python package is Windows-only [3P]. Community Wine bridges (`mt5debian`, `mt5linux`, `mt5bridge`, `meta-docker`) exist, unvetted; Wine on our ARM Ampere VM is an unresearched risk | FTMO, The5ers, E8, FundingPips, Alpha Capital, Blue Guardian |

### 2.2 Firms

- **FTMO.** [1P `ftmo.com/en/faq/which-instruments-can-i-trade-and-what-strategies-am-i-allowed-to-use/`] "whether it's discretionary trading, algorithmic trading, EAs, etc." [1P `ftmo.com/en/forbidden-trading-practices/`] bans EAs that cause "an excessive number of more than **2,000 server requests per day**", "any software ... ultra-high-speed tools, or mass data entry that might manipulate, abuse, or give you an unfair advantage", exploiting price errors, simultaneous opposite positions, and "opening simulated trades when major global news ... are scheduled" (the FAQ says news trading is allowed; scope of that line not established, read both pages); "You must not allow any third party to access or otherwise use your FTMO Account." Platforms MT4/MT5/cTrader [1P home]; DXtrade not mentioned. [1P `.../trading-objectives/`] 1-step: 3% daily, **10% trailing**, target 10%; 2-step: 5% daily, static 10%, min 4 trading days; daily reset 00:00 CE(S)T. Price and split not obtained. Crypto and indices listed generically [1P FAQ]; specific symbols unverified.
- **E8 Markets.** Official site 403. [1P `tradelocker.com/hub/prop-firms/e8-markets`] (TradeLocker's page about E8, so first-party for TradeLocker, third-party for E8) "Trading bots" explicitly allowed; E8 One prices **$40 (5k) … $488 (100k)**, split 80%, 3% daily (5k row); leverage crypto **1:1**, indices 1:15, forex 1:30. [3P] platforms cTrader/MatchTrader/MT5/TradeLocker, US traders cannot use MT5/cTrader; HFT prohibited; 2-minute news restriction; one strategy per user; mass-distributed EAs banned.
- **Blue Guardian.** [1P `help.blueguardian.com/en/articles/14062186-1-step-standard-rules`] "EAs are allowed. You may use EAs (Expert Advisors) that you setup to suit your own strategy."; "Copying trades from another trader's account" prohibited; news trading allowed on challenge, on funded "refrain from Opening/Closing trades 5 minutes before and 5 minutes after red folder"; **"The minimum holding time is 2 minutes"** (under that "may be flagged for tick scalping"); daily loss 4% (reset 5pm EST); **"6% trailing of your Highest Watermark Closing Trade"**; no overnight/weekend restriction. [1P `blueguardian.com`] platforms MT5, TradeLocker, Match-Trader, NinjaTrader, Tradovate, DeepCharts, TradingView (no cTrader); $5k-$400k; "up to 90%" split; Instant plan $3,000 daily / $6,000 trailing on the $100k; instant 100k $467 with a code vs $623 standard; leverage 1:30. Only the 1-step Standard rules were read.
- **FundingPips.** Site 429. [3P snippets] MT5/cTrader (+$20)/Match-Trader/TradeLocker; EAs allowed for own trade and risk management, third-party strategy EAs may be prohibited, "get confirmation before using an automation tool". **Most restrictive of the set; no first-party text.**
- **Alpha Capital.** Site 403. [3P snippets] MT5/DXTrade/TradeLocker/cTrader; EAs only on MT5 after submitting `.MQ5` source; **2-minute minimum hold**; HFT/arbitrage/copy trading banned. Unsuitable for an API bot per those snippets; unverified.
- **FundedNext (FX).** See § 1.4: MT4/MT5 EAs only under $50k. Up to 95% share; some plans no daily loss limit [1P home].
- **Instrument overlap.** Only E8 has first-party crypto evidence (via TradeLocker's page). **BTC/ETH/SOL/XRP/ADA and US500/US100 availability was not verified at any FX/CFD firm**; symbol lists are per firm per platform and must be read from a demo or the firm's instrument page. No SPY/QQQ-like evidence found.

## 3. Rules that decide whether a 24/7 bot survives (across everything read)

| rule | seen at | why it matters to us |
|---|---|---|
| **Minimum hold 2 minutes** | Blue Guardian [1P], Alpha Capital [3P] | Our 1h/2h/1d legs clear it. A scalp leg would not. |
| **Request/HFT caps** | FTMO 2,000 requests/day [1P]; Tradeify >10 s hold share [1P]; MFFU >200 trades/day [3P] | Reconcile/poll loops must be budgeted per account, not per process. |
| **Static vs trailing drawdown** | Static: HyroTrader max loss [3P], Breakout 1-step [3P]. Trailing: FTMO 1-step, Blue Guardian, CFT 1-phase, Hyro daily | Trailing DD eats a breakeven-then-runner strategy's open-profit give-back. |
| **API key immutability** | HyroTrader, CFT [1P] | Key rotation kills the evaluation: the bot must never call anything that edits the key. |
| **Own-code-only** | The5ers [1P], FTMO third-party-EA note [1P], E8 [3P] | Fine: our code, one owner. |
| **No third party may use the account** | FTMO [1P] | A cloud VM under the operator's mandate is the same "clicks placed by a bot" position the operator accepted for Breakout; **it is a stated risk, not a finding that it is allowed.** |

## 4. Futures props: why none is on the shortlist

Only Topstep's help center fetched first-party; the rest is 3P.

- **Topstep.** [1P `help.topstep.com/en/articles/11187768-topstepx-api-access`] custom bots permitted "subject to standard platform rules and our prohibition on high-frequency trading"; **"All trading activity must originate from your personal device. The use of VPS, VPNs, and remote servers is prohibited."** and the server "can watch and record, but it cannot trade". **"Live funded accounts are not allowed to trade through the ProjectX API. The API Gateway is built for the simulated environment."** ProjectX API costs $29/month ($14.50 with code). Instruments ES/MES/NQ/MNQ/**MBT/MET**/GC [1P `.../8284206-when-and-what-products-can-i-trade`]; all positions flat by 3:10 PM CT; MLL trailing, intraday-monitored, $2,000/$3,000/$4,500 at 50k/100k/150k; XFA split 90/10 [1P]. Combine and reset prices are 3P only ($49/$99/$199 per month, $149 activation).
- **Apex.** Own pages 403. [snippet of Apex's Prohibited Activities article] "the use of automation is strictly prohibited on all account types, including any form of AI, Autobots, algorithms, fully automated trading systems, and HFT". [3P PickMyTrade] automation allowed in evaluations, "fully automated hands-off" banned on funded PAs, enforcement automated from 2026-03-01. **Conflicting; treat as banned until Apex's page is read.**
- **Tradeify.** [1P `tradeify.co/funded-trader-agreement`] bots "permitted only under the following conditions": provable sole ownership, exclusive to Tradeify, "not high-frequency trading (HFT) bots"; ≥50% of profit must come from trades held over 10 s; no copy/mirror. Interface Tradovate, bridged by TradersPost [3P].
- **Take Profit Trader.** UTP unread. [snippet] "prohibit[s] automated trading systems, bots, or algorithmic execution tools"; [3P] personal automation allowed on PRO/PRO+. **Unresolved.**
- **MyFundedFutures.** [3P] algo trading allowed since 2025-07-23 but "fully autonomous bots are restricted; active supervision is required"; page unverified.
- **Interfaces.** Tradovate API [3P]: $25/month, needs a live funded account ≥ $1,000; **"Prop firm and evaluation accounts are not eligible for API access"** (unverified, forum-sourced, no date). Rithmic R|Protocol [3P]: protobuf/WebSocket, Linux-friendly, prop eligibility unknown. Also: **every prop account here is simulated**, so fills are sim fills.

**Verdict:** a cloud Linux trader cannot legally place orders at Topstep, and the rest either ban full automation, bar API access, or could not be read. The overlap that was attractive (MBT/MET, ES/NQ) does not rescue them.

## 5. Ranked shortlist (3 best fits for our stack)

Ranking criteria, in the operator's order: an API with native brackets over
browser automation; crypto-perp coverage; rules a 24/7 bot can obey. Integration
effort is in `new-broker` skill terms (package, factory, integrator, PnL source
declaration, executor wiring, `accounts.yaml`, verification). The skill's
operator surface still applies: **the operator originates the account and the
API-key secret; everything else is ours.**

### #1: HyroTrader via its Bybit sub-account API

- **Why first.** Same exchange, same order API and same native bracket
  semantics we already run on bybit_1/bybit_2. Crypto-perp coverage is the
  route's whole point. Rules are bot-survivable: no minimum hold and no HFT ban
  found, 4%/6% limits, 3% per-trade cap, lowest-priced crypto-native entry seen ($59 for 5k,
  refundable with first payout [1P]; E8's $40 is an FX/CFD firm at 1:1 crypto leverage). Price is only
  one side of EV per purchase (§ 6): P(pass) and payout are not measured here.
- **Caveats that are still open.** (i) The bot FAQ body is unread first-party.
  (ii) A demo sub-account may not reproduce real fills, so **realised cost versus
  backtest cannot be read off it**, the Stage-1 concern. (iii) The key must never be
  edited. (iv) Prohibited instruments are reachable through the key, so a symbol
  allowlist must be enforced client-side.
- **Integration effort: Small.** No new broker package: reuse
  `src/units/accounts/clients.py::bybit_client_for` and `src/exchange/bybit_connector.py` (there is no `bybit/` package; checked 2026-09-30). Needed: (1) an `accounts.yaml` entry with its own env-var
  key names and `mode: dry_run` first; (2) a `prop_rulesets/hyrotrader.yaml` and
  the existing `src/prop/` ruleset/risk-gate wired to it (4% daily, 6% max, 3%
  per-trade, ≤2x notional); (3) a symbol allowlist of USDT perps; (4) a `clients.py`
  factory reusing `bybit_client_for`'s adapter with the key pair; (5) the § 2b PnL-source
  declaration: Bybit closed-PnL reader as broker truth; (6) verification: a
  read-only balance/position pull through a labelled diag issue, then one minimum-size
  round trip under the same pre-registered pass criteria used for breakout_1.
  No browser automation, no Cloudflare exposure.

### #2: CryptoFundTrader via its Bybit API route

- **Why second.** Identical integration to #1 (the route is also a Bybit API key
  connected to the firm's dashboard), and the bot clause is first-party read: bots
  allowed, HFT/tick-scalp/arbitrage banned. Two costs: the price table is headed
  "Reset Accounts" and reads $198 (5k) to $2,079 (100k) 1-phase, and I cannot tell if
  that is the initial price; and the 1-phase drawdown is **trailing 6%**.
- **Integration effort: Small, and ~free if #1 lands.** Second ruleset file plus a second
  account entry; no new adapter.

### #3: A TradeLocker prop (Blue Guardian first, E8 second), via the TradeLocker REST API

- **Why third.** It is the only *non-Bybit* route with an official REST API,
  native SL/TP **at order entry** (verified against the place-order reference),
  a `strategyId` field made for algorithmic orders, and headless operation. Blue
  Guardian's EA clause is first-party and explicit. It ranks below the Bybit
  routes because: crypto coverage on an FX/CFD platform is unverified (E8 shows
  crypto at **1:1** leverage), TradeLocker access for a *user's own script* is not
  stated in the docs and the prop can restrict it, Blue Guardian's **2-minute
  minimum hold** and **6% trailing** drawdown apply, and "EA" in the firm's rule
  may not cover an external REST client.
- **Integration effort: Medium.** A real `new-broker` job: a `tradelocker` package
  (JWT fetch/refresh, accounts, instruments incl. `routeId` lookup, order, position,
  modify), a `tradelocker_client_for` factory, an `EXCHANGE_MAP` entry, a
  symbol map for whichever crypto/index CFDs the demo exposes, a PnL source
  declaration (broker-side positions history), an `accounts.yaml` entry, and a
  demo-account verification. **Do not start it before a $0 verification
  step:** confirm from a demo that `placeOrder` works with the account's own token
  and that BTC/ETH/US index symbols exist.
- **Runner-up: FTMO via cTrader Open API.** Stronger and more documented API and a
  clear "algorithmic trading, EAs" FAQ line, but: market entry cannot carry SL/TP, so
  a follow-up amend leaves a naked interval (unacceptable next to our
  naked-position autoprotect rules unless entries are limit/stop orders); a
  2,000 requests/day cap; a **10% trailing** 1-step drawdown; an initial OAuth browser
  step. Effort Medium-to-large.

### Not shortlisted, and why

Breakout Terminal / DXtrade (no first-party rule readable, our own probe 403);
Bitfunded (proprietary platform, no API evidence); FundedNext (MT4/MT5 EA only,
manual above $50k); The5ers, FundingPips, Alpha Capital (MT-only or unread, minimum
holds); all futures props (§ 4).

## 6. Fit against the breach-and-rebuy economics

EV per account purchase = P(pass) x E[payout net of split] − price. This survey
supplies only the **price** side, and only partly:

| candidate | cheapest price seen | source |
|---|---|---|
| HyroTrader 5k | $59, refundable with first payout | [1P] |
| E8 One 5k | $40 (100k $488) | [1P via TradeLocker's page] |
| Blue Guardian instant 100k | $467 promo / $623 list | [1P] |
| CFT 1-phase 5k | $198 (**maybe a reset fee**) | [1P, ambiguous] |
| Breakout (ours) | $50-$999, non-refundable | [3P] |

P(pass) and payout depend on rule fit with our legs, not on this survey.
The relevant follow-up is to run the existing P1/P2 evaluator against the
HyroTrader and Blue Guardian rule sets (4/6% Bybit perps, 3/6% trailing CFDs).

## 7. Decision for the operator (2 to 4 options)

| option | what it is | cost / risk | what it buys |
|---|---|---|---|
| **A. Ask before buying (recommended, $0)** | Send HyroTrader (and, if wanted, Blue Guardian) support one written question: "may a custom bot use our own Bybit demo API key / your TradeLocker account through the public API, from a cloud server?" and read the firms' own bot pages in a browser (`breakoutprop.com/program-rules`, HyroTrader's bot FAQ article, Apex/TPT UTPs). | Zero spend; a day or two. Firm replies are not our records: file the reply text. | Converts the four biggest unknowns (bot clause, real fills vs demo, key rules, symbol list) into first-party text before any purchase. |
| **B. Buy one HyroTrader 5k (~$59) and trial via the Bybit API** | Operator opens the account and adds the key pair to Actions secrets; we build the Small integration (§ 5 #1) in `dry_run`, then one minimum-size round trip. | ~$59, refundable with first payout [1P]. The operator has accepted breach-and-rebuy. Risk: bot clause unread, demo-fill fidelity. | The cheapest real trial of "API + native brackets + crypto perps", with none of the Cloudflare and browser fragility of breakout_1. |
| **C. Same as B but a Blue Guardian instant account via TradeLocker** | $467-$623 (100k) or a smaller size; the Medium build in § 5 #3 after the $0 demo verification. | Higher price; 2-minute hold and trailing DD; crypto coverage unverified. | Tests a *non-crypto-exchange* API route and the index CFDs. |
| **D. Stay on the breakout_1 DXtrade executor only** | No new platform. | Keeps the Cloudflare and terminal-layout fragility; a re-buy lands on the terminal we cannot reach (PI-20260929-FRJ7NMPU-0001). | Nothing new; the standing position. |

**Recommendation: A now (free), then B if HyroTrader's answer is yes.** Do not
promote any of this to the ladder from this document: it establishes what
platforms *exist*, not that any leg has edge net of their cost stack.

## Sources (all read 2026-09-30 unless stated)

First-party fetched: `hyrotrader.com` (home, `/faq/hyrotrader-account/`, `/blog/crypto-api-trading/`, `/faq/trading-restrictions/what-are-prohibited-trading-actions/`, `/faq/rules/d/`, funded-account and risk-management FAQs), `cryptofundtrader.com` (`/faq/`, `/evaluation-rules/`), `bitfunded.com/crypto-prop-trading-firm/`, `help.fundednext.com/en/articles/8020763-...`, `help.blueguardian.com/en/articles/14062186-...`, `blueguardian.com`, `ftmo.com` (home, FAQ, forbidden-trading-practices, trading-objectives), `the5ers.com/faqs/prohibited-trading-practices/`, `fundednext.com`, `tradelocker.com/hub/prop-firms/e8-markets`, `public-api.tradelocker.com` (`/llms.txt`, `/reference/placeorder.md`), `help.ctrader.com/open-api/` (overview, account-authentication, messages), `match-trader.com/technology/platform-api/`, `help.topstep.com` (articles 11187768, 10305426, 10296582, 8284206, 8284204, 8284215), `topstep.com/express-funded-account-rules`, `tradeify.co/funded-trader-agreement`.
Third-party: `cryptoslate.com/prop-firms/hyrotrader-review/`, `quantvps.com/blog/breakout-crypto-prop-firm-rules`, `velotrade.com/blog/algo-bot-trading-crypto-prop-firm`, `thetrustedprop.com/prop-firms/Bitfunded`, `blog.pickmytrade.trade` and `blog.pickmytrade.io`, `blog.traderspost.io`, `proptradingvibes.com`, `quantvps.com` MFFU post, GitHub/PyPI MT5-on-Linux projects, financemagnates/dx.trade snippets, `eafunded`, `newyorkcityservers`, `tradingfinder`, `propfirmtrader` snippets.
