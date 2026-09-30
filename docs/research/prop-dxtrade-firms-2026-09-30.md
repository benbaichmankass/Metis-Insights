# Prop firms on DXtrade: who else runs it, and how much of our adapter carries over (2026-09-30)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status. It is a desk survey, not a decision record.**

Lane **PROP-DXTRADE-FIRMS** (Tier 1, scope only). Operator, verbatim, ~05:26Z
2026-09-30: *"other platforms that use DX trader and ... where we could reuse
some of that infrastructure that we've already built out here"*. This extends
[`prop-platform-automation-survey-2026-09-30.md`](prop-platform-automation-survey-2026-09-30.md)
(which shortlisted HyroTrader / CFT / TradeLocker and left DXtrade at "no prop
confirmed as enabling [the API] for retail"; that gap is what this closes) and
builds on [`prop-automation-options-2026-09-27.md`](prop-automation-options-2026-09-27.md).

**Nothing was bought, no account opened, no credential requested, no firm
contacted, and no terminal or API host was requested from the sandbox.**

## First-party URLs to check (read 2026-09-30)

Marks: **[1P]** = read on the firm's or Devexperts' own page. `WebFetch` returns a
small-model *summary*, so quotes are near-verbatim: **re-read the page before
relying on exact wording.** **[snippet]** = a search result citing a first-party
page I could not fetch. **[3P]** = review sites and bridge vendors.

| what | URL |
|---|---|
| Tradeify 247: DXtrade guide (login host, "API access for automated trading") | https://help.tradeify247.co/en/articles/13393256-dxtrade-platform-guide |
| Tradeify 247: FAQ (bots, VPS/VPN, instruments, sizes) | https://help.tradeify247.co/en/articles/13393249-tradeify-247-faq |
| Tradeify 247: trading rules overview | https://help.tradeifycrypto.co/en/articles/13393254-trading-rules-overview (same article on `help.tradeify247.co`) |
| Devexperts x Tradeify press release (2026-04-21) | https://dx.trade/news/press-releases/tradeify-to-bring-dxtrade-to-perpetuals-traders-worldwide/ |
| DXtrade traders' FAQ (client-side API; broker/prop controls IPs) | https://dx.trade/traders-faq/ |
| DXtrade APIs page / developer portal | https://dx.trade/apis/ , https://demo.dx.trade/developers/ (SPA, unreadable by fetch) |
| DXtrade public specs pattern `https://<platform host>/specs` | https://dx.trade/news/release-notes/dxtrade-xt-updates-automatic-liquidation-api-improvements/ , example https://demo-xt.dx.trade/specs |
| BrightFunded: platforms (host `trade.brightfunded.com`) | https://brightfunded.com/trading-platforms |
| BrightFunded: EA policy ("API and automated trading are not supported on DXtrade") | https://help.brightfunded.com/en/articles/9241699-can-i-use-ea |
| FXIFY: "DXtrade does not support trading bots" | https://fxify.com/faqs/all-faqs/can-i-add-trading-bots-eas-into-dxtrade/ |
| Alpha Capital: platforms; EA rule; VPN/VPS rule | https://help.alphacapitalgroup.uk/en/articles/6933883-what-trading-platforms-are-available-for-use , `.../6934236-can-i-use-an-expert-advisor-ea` , `.../8420522-is-the-use-of-vpns-and-vps-allowed` |
| FTMO: DXtrade discontinued 2026-03-24 | https://ftmo.com/en/blog/trading-updates/trading-update-24-mar-2026/ |
| Blueberry Funded: DXtrade "not offered" | https://help.blueberryfunded.com/en/articles/10741972-platform-downloads |
| Funded Trading Plus: EA rule; platforms | https://help.fundedtradingplus.com/does-your-simulated-live-environment-support-the-use-of-eas-algos-or-bots/ , https://help.fundedtradingplus.com/which-platforms-do-you-offer/ |
| Breakout: two terminals; prohibited practices | https://intercom.help/breakoutprop/en/articles/14215629-does-breakout-offer-two-separate-trading-terminals , `.../11644090-what-trading-practices-are-prohibited-during-the-breakout-evaluation` |

## 0. The answer

1. **One firm stands out: Tradeify 247 (crypto perpetual-style CFDs, DXtrade
   only for US traders).** It is the only firm where the firm's *own* page says
   the DXtrade API may be used for your own bot: *"DXTrade supports API access
   for automated trading. You can connect your own trading bots (bots must be
   owned by you)."* [1P DXtrade guide]. Bots ("as long as you own the bot"), and
   *"VPNs and VPS are allowed"* [1P FAQ], and named BTC/ETH/SOL/XRP pairs [1P FAQ]
   make it a fit on every axis but price certainty. No other DXtrade firm read
   says anything comparable.
2. **The rest of the DXtrade field is a poor fit.** FTMO discontinued DXtrade
   2026-03-24 [1P]. Blueberry Funded lists DXtrade as *"not offered"* [1P].
   Seacrest (ex-MyFundedFX) closed its prop arm 2026-02-06 [3P]. FXIFY and
   BrightFunded say in writing that DXtrade does not support bots / API [1P].
   Alpha Capital prohibits autonomous execution on every platform, has no crypto
   and wants a static-IP notice for a VPS [1P/3P]. Funded Trading Plus's DXtrade
   availability is contradictory [1P vs 3P] and its bot rule points at MT5/cTrader.
3. **The API question is now answerable for one firm, not for the family.**
   Devexperts publishes the REST/FIX/Push specs at `https://<platform>/specs`
   [1P] but says the *broker or prop firm decides* whether traders get the API
   and which IPs may reach it [1P traders-faq; no list is published]. So API
   availability is **per firm and must be probed per firm** (§ 4).
4. **Our adapter reuse is real but bounded.** The order-ticket logic, the
   six-field read-back, bracket verification, table parsers and close/cancel
   flow are written against Devexperts' UI vocabulary and carry over. The login
   selectors, symbols, lot table, per-firm rulesets, and a Breakout-hardwired
   executor do not (§ 3). Tradeify would still be a **new account entry plus
   a small executor generalisation**, not a rewrite, and if its API is really
   enabled the browser adapter is not needed at all.

## 1. Firm-by-firm

Columns follow the operator's list. "Eval/funded" means what DXtrade covers.

### 1.1 Tradeify 247 (Tradeify Crypto): the lead

| | |
|---|---|
| DXtrade coverage | [1P press release 2026-04-21] DXtrade is the platform for Tradeify's crypto prop (simulated funded accounts $5k-$100k: 2-Step, 1-Step, Instant). [1P FAQ] *"DXTrade - available as web platform and desktop application"*; MT5 for non-US only, **US traders use DXtrade only**. Whether DXtrade is used for both evaluation and funded stages is not stated in a page I read; the paths are described as accounts on the same platform. The Tradeify *futures* brand is a different product; its platform was not checked. |
| Terminal host | [1P DXtrade guide] *"Login URL: dx.tradeify247.co"*. The old `help.tradeifycrypto.co` help host redirects to `help.tradeify247.co`. Login markup and DOM at this host are **unmeasured** (§ 3). |
| Bots | [1P FAQ] *"Yes, as long as you own the bot. Copy trading is also allowed. Shared bots and signal services that auto-execute trades (that you do not own) are not allowed."* [1P rules overview] bots *"permitted if you own them exclusively"*. [1P DXtrade guide] API access for own bots; *"Tradeify cannot provide technical support for the DXTrade API"*. [3P, not found 1P] ownership verified by code scans / a video; HFT prohibited. |
| Instruments | [1P FAQ] 100+ crypto pairs *"including BTC, ETH, SOL, DOGE, XRP, and altcoins"*; ~30 tokenized stocks (2:1). [1P rules] leverage fixed: 5:1 BTC/ETH, 2:1 alts/indices/stocks. **ADA, US500/US100 and metals were not verified by name** (rules mention "indices", so some exist). Net for our crypto legs: BTC/ETH/SOL/XRP yes, ADA unconfirmed, indices/metals unconfirmed. |
| Rules | [1P rules overview] daily loss **3%** of account size; max loss **6%**, static (fixed 6% below start) or trailing on Instant Funding; **20-second minimum hold**; **hedging not allowed**; inactivity closes at 30 days (warning at 28). Not stated in what I read: minimum profitable days, weekend rule, stop-loss requirement, news policy. [1P DXtrade guide] SL/TP "recommended", not required. [3P] no weekend/overnight restriction. |
| Price | [1P FAQ] sizes $10k/$25k/$50k/$100k; [1P PR] $5k-$100k, 80% split. Cheapest price is [3P] ~$100 (2-Step 10k): **unverified**. |
| Official API to the trader? | **Yes per the firm's own guide** ([1P] above). Devexperts side: [1P traders-faq] *"client-side API available"*, broker/prop controls permitted IPs. Whether `https://dx.tradeify247.co/specs` is served, and whether it accepts a VPS IP, is **unverified** (§ 4). |
| Datacenter/VPS IP | [1P FAQ] *"VPNs and VPS are allowed"*; the account must stay personal and *"Tradeify may request verification if multiple IP addresses access your account"*. **No Cloudflare ASN-ban evidence** but no probe was run: the Breakout terminal's Error 1005 is exactly what a probe must rule out. |

### 1.2 BrightFunded

| | |
|---|---|
| DXtrade | [1P] *"DXtrade is a multi-asset platform for Forex, CFDs, and digital assets"*; web terminal `trade.brightfunded.com`; simulated environment only. Eval vs funded: not stated. |
| Bots | [1P EA article] EAs allowed (in general) **but** *"API and automated trading are not supported on DXtrade."* [3P] 60 s minimum trade duration, HFT/arbitrage/tick scalping/grid banned. Browser-driving the terminal is not addressed. |
| Instruments | [1P] "Forex, CFDs, and digital assets"; no symbol list read. |
| Rules | [3P only] 1-Step 3% daily / 6% trailing; 2-Step Bright 4%/8% static; 2-Step Classic 5%/10% static; min 5 days (waivable, +15% add-on). |
| Price | [3P] 2-Step Classic from **€5 (5k)** to €97.50 (200k): unverified, and unusually cheap. |
| API | **[1P says no.]** |
| Datacenter IP | No statement found. |

### 1.3 Alpha Capital Group

[1P help center] DXtrade is offered (*"MetaTrader 5, cTrader, DX Trade, and TradeLocker"*; US: cTrader, DX Trade, TradeLocker); [snippet] terminal `dxtrade.alphacapitalgroup.uk`. **EA rule** [1P]: EAs allowed on MT5 *"strictly limited to risk management and trade assistance tools"*; *"Automated EAs that execute trades independently, without human oversight, are strictly prohibited"*; pre-approval required. **VPN/VPS** [1P]: masking IP prohibited; VPS only with a static dedicated IP and prior notice, else flagged for closure. **Instruments** [3P]: ~40, forex/metals/energy/indices, **no crypto**. Rules [3P]: 3-5% daily, static 6-10% (Alpha One trailing). Price [3P] $70 for 5k Swing. **Verdict: no.**

### 1.4 The others

| firm | DXtrade status | bots on DXtrade | verdict |
|---|---|---|---|
| **FTMO** | **Discontinued 2026-03-24** [1P blog]; `ftmo.com/en/dxtrade/` now lists MT5/MT4/TradingView/cTrader. Existing accounts may still run (3P). `dxtrade.ftmo.com` returned HTTP 530 to fetch. [3P, unverified] support ended because prop firms removed third-party API access. | not read | out |
| **Funded Trading Plus** | [1P] `which-platforms` lists **MT5 and Match Trader** only; [snippet] a DXtrade help section exists; [3P] lists DXtrade. Contradictory. | [1P] EAs/algos/bots allowed, no arbitrage/grid/tick-scalping, no support; [3P] not workable on DXtrade | unclear; re-check platform page |
| **Lark Funding** | [3P] cTrader/DXtrade/Match-Trader, "DXtrade for all account types"; no host, no 1P platform statement | [3P] EAs allowed except HFT/arbitrage; no 1P text | unverified; weak lead |
| **Blueberry Funded** | **[1P] "DXTRADE ... currently not offered"**; MT5 and TradeLocker only | n/a (its own EA clause: proprietary only, pre-approval by support) | out for DXtrade |
| **Seacrest (ex-MyFundedFX)** | Prop arm **closed 2026-02-06** [3P] | n/a | defunct |
| **FXIFY** (forex) | [1P] DXtrade offered "as an additional platform" | **[1P] "DXtrade does not support trading bots"** | out |
| **Audacity Capital** | [1P] home page: DXtrade and MT5; ~FX/metals/indices/energy/crypto [3P] | [1P] home "EAs Allowed"; [3P] EAs MT5-only, "not supported on DXtrade" | conflict, weak lead |
| **Ment Funding**, **Tradexprop** | [3P] DXtrade available; no host, no 1P page read (Tradexprop's FAQ sits behind a bot interstitial) | [3P] custom EAs allowed; copy trading conflicts | unverified |
| **Breakout (ours)** | [1P] **no new DXtrade purchases**; legacy DXtrade accounts stay at app.breakoutprop.com and can fund and withdraw [1P intercom 14215629]. Rules are the same on both terminals [1P 14211064]. | no 1P bot article; prohibited-practices [1P] bans latency exploitation and same-IP multi-account | standing position |

Only *listed in third-party rankings* for: Eightcap Challenges, Axi Select,
Rebels Funding, Hola Prime, FX2 Funding, DNA Funded (sources conflict; one says
TradeLocker). Not researched.

## 2. API: does DXtrade's REST/FIX beat the browser?

- **Exists, per firm switch.** [1P] Devexperts publishes the REST, Push and FIX
  specs at `https://<platform address>/specs`; the platform "officially supports
  integrations through REST API". [1P traders-faq] *"We have client-side API
  available"*; brokers/prop firms control which IPs reach it. [1P dx.trade/apis]
  frames the APIs as for "integrating DXtrade with your systems", i.e. broker
  first. No published list of firms that enable it for traders.
- **Wire shape (third party only).** [3P] Sway Technologies' docs and community
  SDKs (e.g. GitHub `scotthooker/dxtrade-python-sdk`, `zLeki/DxTrade-Api-Go`)
  show `POST <base>/dxsca-web/login`, then `Authorization: DXAPI <sessionToken>`,
  `POST /ping` to extend the session. **Not confirmed first-party** and the SL/TP
  order fields were not read. [1P traders-faq] DXtrade supports only regular
  SL/TP protective orders (no trailing/breakeven/multiple TPs).
- **Firm evidence.** Tradeify: yes ([1P] above). BrightFunded and FXIFY: no
  ([1P]). Breakout: our own `GET https://wss.breakoutprop.com/dxsca-web/` returned
  `403 RBAC: access denied` (MEASURED 2026-09-27, S26 of the 09-27 doc), and the
  operator dropped the API route there. FTMO: [3P] third-party API access removed.
  Everything else: no evidence either way.
- **Consequence.** If Tradeify's API works from our VM, it beats the browser adapter
  on every axis (no DOM drift, no Cloudflare page, deterministic fills read-back,
  native bracket fields), and the reusable part is our **shared layer** (ticket
  intake, guards, write-back), not the adapter. Only the verification in § 4 can
  say whether it works.

## 3. What in our adapter is DXtrade-generic and what is Breakout-specific

Basis: reading `src/prop/platform/dxtrade.py`, `base.py`, `__init__.py`,
`src/prop/prop_executor.py`, `config/prop_platforms.yaml`,
`config/prop_rulesets/breakout_routing.yaml` on `main` 2026-09-30. "Generic"
below means *written against Devexperts' stock UI vocabulary and expected to
carry over*; nothing here has been run against any non-Breakout host. **That
expectation is the assumption a probe must check** (a white label may re-skin
labels, headers, tab markup and login).

| piece | verdict | why / evidence |
|---|---|---|
| Pure parsers: `parse_number`, `parse_account_metrics` (label list Balance/Equity/Free Margin/Day RPL…), `positions_from_tables`, `orders_from_tables`, header-based table classification | **Generic** | Labels and headers come from the served Devexperts i18n dictionary (5,571 keys, e.g. `metric.name.short.cashBalance`), matched by header text, not class names. A white label can rename labels; `unparsed` (never 0) already reports that. Breakout-*measured* orders headers (Sts/Status/Symbol/Side/Size/Price/Type/Stop loss/Take profit/…/Fill Price) are the reference layout. |
| Login-state classifier, CAPTCHA/Cloudflare marker list, `FeasibilityError` reasons (`asn_blocked`, `challenge`, `2fa`…), positive-marker rule (`TERMINAL_MARKERS`) | **Generic** | Platform-independent by design. |
| Order ticket: `open_order_ticket`, `_find_form`, field fills, six-field read-back (symbol/side/type/qty/SL/TP), `verify_*`, unique-submit rule, `check_bracket_spec`, `_after_submit`, `place_bracket`, `cancel_order`, `flatten`, `modify_bracket`, row-action matching | **Generic in shape; selectors unmeasured elsewhere** | Built on the Devexperts sidebar ticket and Positions/Orders tables. Tab markup (`[data-active]` span, not `role=tab`) and opener button names (`TICKET_OPENER_NAMES`) were **MEASURED on Breakout only**. Same Devexperts codebase implies likely, not certain. |
| Response capture + passive instrument-spec extraction | **Generic** | Same-origin JSON on any DXtrade host; gave us Breakout's `lotSize`/`quantityPrecision`. Best tool for measuring a new host without clicking. |
| Login selectors (`form.loginForm-main`, `#username`, `#password`, `#submitLogin`, `form#totpSecure`) | **Breakout-measured, probably stock** | MEASURED 2026-09-27 on `wss.breakoutprop.com`. A different white label may add fields (broker "domain"/vendor) or a different form. Tradeify's login is unmeasured. |
| Login URL / host | **Breakout-specific** | `DEFAULT_LOGIN_URLS["dxtrade"] = https://wss.breakoutprop.com/`, and `load_platform_config` **rejects** a `login_url` equal to the other platform's default; a new firm needs an explicit `login_url` (allowed). |
| Symbols | **Breakout-specific** | `ETHUSD`/`SOLUSD` drop the perp `T` (operator-confirmed). Another white label may use `BTC/USD`, `BTCUSD.P`, etc.: unmeasured. Mapped in `breakout_routing.yaml`. |
| Lot sizes / steps / min lots / price steps | **Breakout-specific** | `config/prop_platforms.yaml executor.lots` per venue symbol; ETH/SOL measured, BTC not, min lots null. |
| Executor wiring | **Breakout-hardwired** | `prop_executor.py` fixes `RULESET_PATH`, `ROUTING_PATH`, `account_id="breakout_1"`, risk cap `$75`; `enabled_venue_symbols: [SOLUSD]` (**symbol switching is not built**: the sidebar sits on the one symbol). A second firm needs `RULESET_PATH`/`ROUTING_PATH` per account, a per-account `prop_rulesets/<firm>.yaml` (the ruleset resolver in `account_rulesets.py` is already multi-account: "an `accounts.yaml` entry (+ a ruleset file); zero code change"), and symbol switching for more than one instrument. |
| Shared layer above the adapter (ticket intake, reconcile, guards, `ingest_report`, kill switch `PROP_EXECUTOR_MODE`) | **Generic** | Explicitly platform-neutral (options doc § 2). |
| Egress/anti-bot posture | **Generic** | No stealth, feasibility stops reported not evaded (operator boundary). Carries over unchanged. |

Rough proportion, **my judgement, not a measurement**: of the ~3,500-line
`dxtrade.py`, the pure parsers, guards and form-verification (~70%) are
plausibly reusable as-is; the rest is Breakout-host-specific DOM measurements
(tabs, openers, login) that must be re-measured per host. The executor and
config need targeted generalisation (per-account ruleset/routing/symbol
enablement), which is more work than the adapter.

## 4. Ranked top 3 (reuse and fit)

Ranking order: (a) is there a route better than the browser, (b) can a
24/7 cloud bot legally and practically run on it, (c) instrument overlap with
our crypto legs, (d) reuse of what we built, (e) price. **Confidence is low on
#2 and #3.**

### #1 Tradeify 247 (crypto), via its DXtrade API, browser adapter as fallback

- **Why.** Only firm with a first-party "own bot via DXtrade API" statement;
  VPS allowed; bots allowed if owned; BTC/ETH/SOL/XRP named; static 6% max loss
  option; 3% daily; 20 s hold clears every leg of ours (1h+ bars); the same
  Devexperts platform family as breakout_1, so the shared executor layer applies.
- **Open, and each one can kill it.** (i) Is `dx.tradeify247.co/specs` served and
  does the login API accept our egress (the Breakout ASN ban is the precedent:
  Cloudflare Error 1005 at `app.breakoutprop.com`, PI-20260929-FRJ7NMPU-0001)?
  (ii) Does the "bots must be owned by you" clause require the code scan / video
  ([3P] only)? (iii) Bracket semantics through the REST order: SL/TP fields
  unread. (iv) Hedging is banned, and the daily-loss basis and DD type per path
  (static vs trailing) must be read at purchase. (v) Prices 3P only.
  (vi) **A simulated ("funded" accounts are simulated) book gives sim fills**, so
  it cannot measure realised cost versus the backtest (the Stage-1 concern) any
  better than Breakout does.
- **Integration effort.**
  - *If the API works:* **Medium**. New `dxtrade_api` client (login/ping/orders/
    positions/close behind the existing `PropPlatformAdapter` interface, so the
    executor does not change); credential env vars via the existing
    `sync-vm-secrets` path; `tradeify.yaml` ruleset (3%/6%, 20 s hold, no
    hedging); routing/symbol/lot map read from the API's instrument endpoint;
    the executor generalised off `breakout.yaml`. Est. **3-5 lane-days** after a
    green probe.
  - *If only the browser works:* **Small-Medium**. Re-measure login + tabs +
    ticket on the new host via the existing read-only probe pattern
    (`probe-ticket`, response capture), a `login_url` entry, per-firm lot table,
    per-account ruleset/routing, and symbol switching if more than one
    instrument. Est. **4-7 lane-days**, most of it the measurement loop that took
    breakout_1 several rounds.
- **The blocking unknown is the ASN/Cloudflare posture**, which nobody can
  answer from documents.

### #2 BrightFunded

- **Why.** DXtrade on a known host (`trade.brightfunded.com` [1P]), cheapest
  entry seen ([3P] €5-€97.50: unverified), digital-asset CFDs, EAs generally
  allowed.
- **Why not higher.** [1P] *"API and automated trading are not supported on
  DXtrade"*: driving its terminal by browser is the same position we have taken on
  Breakout (clicks placed by a bot under the operator's mandate, breach = account
  closed, accepted 2026-09-27) but here the firm has **said in writing that
  automation on DXtrade is unsupported**. 60 s minimum duration [3P]; no symbol
  list, so crypto overlap is unverified.
- **Effort: Small-Medium** (browser path only; same list as #1's fallback).
  Cloudflare/ASN posture unknown.

### #3 Lark Funding (weak)

- **Why.** [3P] DXtrade on all accounts, EAs allowed bar HFT/arbitrage, 5% daily
  and 7% max drawdown on the [1P] home page, no consistency rule, no news
  restriction, "no minimum trading days".
- **Why last.** No first-party platform, host, bot, instrument, DD-type or price
  data beyond the home page; I could not find a terminal host. This is a lead,
  not a candidate, until someone reads its rules.
- **Effort: Small-Medium** (browser path), **plus a discovery task**.

**Ruled out for our purposes:** FTMO (DXtrade discontinued), Blueberry (not
offered), Seacrest (closed), FXIFY (no bots), Alpha (no crypto, no autonomous),
Funded Trading Plus (DXtrade unclear, bots via MT5/cTrader), Breakout DXtrade
(already ours; no new purchases).

## 5. Decision for the operator (2 to 4 options)

| option | what it is | cost / risk | what it buys |
|---|---|---|---|
| **A. $0 read-only probe of Tradeify, then decide (recommended)** | Open the two Tradeify help pages in a browser and, from the VM via a labelled read-only probe (same shape as `breakout-terminal-probe`): (1) `GET https://dx.tradeify247.co/` for the landing page and any Cloudflare/ASN page; (2) `GET https://dx.tradeify247.co/specs` for the OpenAPI. No login, no account, no click. Also read the firm's "bots must be owned by you" clause and the crypto rules page. | Zero spend; one lane. Whether requesting a firm's public landing page counts as "contacting" it is the operator's call; this lane did not do it. | Answers the two kill questions (ASN block, API enabled) before any purchase. |
| **B. Buy one Tradeify 247 10k (~$100 [3P]) and run the login-only probe (recommended second step)** | Operator opens the account and sets `TRADEIFY_DX_*` secrets; we log in read-only through the existing adapter pattern, then test the REST login with the same credentials. | ~$100 per the 3P price; breach-and-rebuy already accepted. Risk: simulated fills; ASN block. | Real measurement of the browser DOM and of the API on the firm that actually says yes. |
| **C. Skip DXtrade as a family; go straight to the Bybit-API props (HyroTrader / CFT) from the 09-30 survey** | Spend nothing on DXtrade; pursue survey #1/#2. | None. Loses the reuse the operator asked for. | The lowest-integration crypto route we know of. |
| **D. Stay on breakout_1 only** | Nothing new. | Keeps ASN/Cloudflare fragility and a re-buy landing on the terminal we cannot reach. | Nothing new. |

**Recommendation: A, then B if A passes.** A costs nothing and either kills
Tradeify quickly (ASN block or no `/specs`) or turns "the firm says API" into
a measured fact. Do not promote any of this to the ladder from this document.

## Not done / coverage gaps (stated so nobody reads absence as absence of evidence)

- No terminal or API host was requested from this sandbox (the "no contact" scope).
  Terminal login markup, `/specs`, Cloudflare and datacenter-IP behaviour are
  **unmeasured for every firm except Breakout**.
- `dx.tradeify247.co`'s crypto **rules pages** were read only through summaries.
  Minimum days, weekend, SL requirement and news policy for Tradeify Crypto were
  **not found**.
- Every price except Lark's home page and BrightFunded/Alpha snippets is 3P.
- Lark, Ment, Tradexprop, Audacity, FTF DXtrade coverage: **third-party or
  contradictory**. Blueberry's 2-Step platform page did not load.
- `breakoutprop.com` and `help.breakoutprop.com` were 403; Breakout facts come from
  its Intercom help center. Its own bot policy was not found.
- The Devexperts developer portal is a single-page app the fetch tool cannot read;
  the `dxsca-web/login` path and `DXAPI` header are third-party (Sway, community
  SDKs).
- The 3P claim "prop firms removed third-party API access from DXtrade" (FTMO
  context) could not be traced to a source and is not used above.
- **Verification level.** I re-read myself (via `WebFetch` summaries) only the Tradeify
  DXtrade guide, FAQ, rules overview and Devexperts press release, plus the `dx.trade`
  traders' FAQ and `dx.trade/apis`. **Everything about BrightFunded, FXIFY, Alpha, FTMO,
  Blueberry, Funded Trading Plus, Lark, Breakout and the other firms comes from three
  research sub-agents' reports** (also `WebFetch` summaries) and was not re-fetched by
  me. My own fetch of the `dx.trade` traders' FAQ returned a garbled answer to the
  IP question; the wording used in § 2 is the sub-agents' and is a summary, not a quote.
