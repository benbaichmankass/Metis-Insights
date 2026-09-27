# Apex Trader Funding vs Breakout (breakout_1): evaluation (2026-09-27)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane **W6-P3** (prop). Operator question, 2026-09-27: *is
https://apextraderfunding.com/ worth trading AS WELL AS, or INSTEAD OF,
breakout_1?* Tier-1 research. No sign-up, credentials, config or `src` change
was made. The companion automation spec is
[`prop-automation-options-2026-09-27.md`](prop-automation-options-2026-09-27.md).

Every Apex fact carries a source id (§ Sources) with a URL. **All Apex pages
were read live on 2026-09-27.** apextraderfunding.com sits behind Cloudflare.
Direct fetches returned 403, so the pages were read through a reader proxy
(`r.jina.ai`). Most help-center pages show a "Published Time" of 24–25 Sep
2026. The timestamps are near-identical, which suggests a site-wide re-publish
rather than per-page edits. "Could not look" is stated wherever a fetch failed.

---

## 5. Verdict first: **DO NOT PURSUE** (neither alongside nor instead)

The three deciding facts:

1. **Automation is prohibited on every Apex account type, evaluations
   included.** The Prohibited Activities page reads: *"No Automation or
   Algorithm Usage allowed: Rewards are intended to recognize human traders
   actively participating in the learning process, not to reward automated
   systems executing preprogrammed logic."* (A1). The page has no
   evaluation/PA carve-out. The legacy PA page spells out the penalty:
   *"immediate closure of your PA or Live account and the forfeiture of all
   funds"* (A2). The live program says the same: *"Can I use automation or
   trade algorithms? No."* (A3). Our whole operating model is a bot, so this
   ends it. The operator's decision to carry ToS risk on Breakout
   (automation spec, § D) **does not transfer**: Breakout's readable terms
   are silent on automation, while Apex's name it and name the penalty.
2. **No overnight holding.** *"Holding open trade positions through the market
   close… all open trade positions are closed prior to the market close."*
   (A1). **All three of our live futures legs are daily-bar and hold for
   days:** `mes_trend_long_1d`, `mgc_pullback_1d` and `mhg_pullback_1d`. A
   forced flat at every close is a different strategy with no backtest. So
   even a *manually* traded Apex account has no rostered futures leg to run.
3. **No compliant API route exists anyway.** The Tradovate API needs *"a LIVE
   account with more than $1000 in equity"* plus a paid *"subscription to API
   Access"* (A12). An Apex evaluation or PA is a simulated account. A
   Tradovate community post from a user (not staff, Oct 2024) says *"Prop
   Accounts do not allow direct API access"* (A13, **unverified against a
   Tradovate page**). Rithmic's API needs *"Conformance testing … before
   production access"* (A14). Neither route matters under fact 1.

**Versus Breakout** (INFERRED from the rows below): breakout_1 has no
overnight/weekend rule, a static (non-trailing) DD and no consistency rule.
Its readable terms do not prohibit automation. Apex is worse for us on all
four. The one thing Apex offers that Breakout does not is regulated CME
futures with a 100% payout split. We cannot run it by bot, and we have no
intraday futures leg to put on it.

**Filed for the operator:** `PI-20260927-R6FQK6DS-0001` (confirm or overrule),
due 2026-10-04.

**What would reverse this:** Apex removing the automation clause, *or* the
operator choosing to trade an Apex account by hand, with the bot emitting
tickets as the Breakout manual bridge did in June. The second option also
needs an **intraday, flat-by-close** futures leg that clears the prop EV bar.
None exists on the roster today. The closest candidate is
`ict_scalp_mgc_15m`, which is live on `ib_paper` but has no flat-by-close
variant and no prop-EV row.

---

## 1. Symbols: the overlap with our fleet

**Apex's tradable list** (A4, A5):
- **Micros:** MES, MNQ, MYM, M2K, MGC, SIL, MCL, M6A, M6E.
- **Crypto:** MBT and MET. The page notes *"do not trade mini BT … not
  supported"*.
- **Minis/standard:** ES, NQ, YM, RTY, EMD, NKD, CL, QM, NG, QG, HO, RB, GC,
  SI, HG, PL, PA, the FX majors, and grains/livestock.
- **Eurex:** products listed, with the homepage noting *"Certain exchanges only
  available on Tradovate Platform"*.
- **Unlisted instruments:** *"attempting to place an order on a non-authorized
  instrument will result in a trade rejection without any penalties"*.
- **Micro Copper (MHG) is not on any of the three lists.** Only full-size HG
  appears. That is absence from a list, not an explicit ban.

**Our rosters** (MEASURED by parsing `config/accounts.yaml` and
`config/strategies.yaml` on this branch, 2026-09-27; population = every leg on
the named accounts):

| our leg | account(s) | symbol | TF | Apex contract | fit |
|---|---|---|---|---|---|
| `mes_trend_long_1d` | ib_paper | MES | 1d | **MES** | killed by the overnight ban |
| `mgc_pullback_1d` | ib_paper | MGC | 1d | **MGC** | killed by the overnight ban |
| `mhg_pullback_1d` | ib_paper | MHG | 1d | **none** (only HG, 10× the notional) | no equivalent, and overnight |
| `mgc_trend_1h` (`execution: shadow`) | ib_paper | MGC | 1h | **MGC** | only if flattened by the close; untested |
| `ict_scalp_mgc_15m` | ib_paper | MGC | 15m | **MGC** | the only structurally compatible leg; needs a flat-by-close variant and a prop-EV row |
| `spy_trend_long_1d`, `spy_pullback_1h`, `splg_trend_long_1d` | alpaca_paper | SPY/SPLG | 1d/1h | MES/ES (a basis change: ETF → future) | daily legs killed by overnight; 1h untested |
| `qqq_*`, `tqqq_trend_long_1d`, `qld_trend_long_1d` | alpaca_paper | QQQ/TQQQ/QLD | 1d/1h | MNQ/NQ | same |
| `iwm_trend_long_1d`, `scha_trend_long_1d` | alpaca_paper | IWM/SCHA | 1d | M2K/RTY | overnight |
| `gld_pullback_1d`, `gld_pullback_1h`, `iaum_pullback_1d` | alpaca_paper, alpaca_live (iaum) | GLD/IAUM | 1d/1h | MGC/GC | daily killed |
| `slv_pullback_1d`, `slv_trend_1h` | alpaca_live (slv_pullback), alpaca_paper | SLV | 1d/1h | SIL/SI | daily killed |
| `uso_trend_1h` | alpaca_paper | USO | 1h | MCL/CL (a poor basis match) | untested intraday |
| `tlt_pullback_1d`, `tlt_pullback_1h`, `ief_pullback_1d` | alpaca_paper, alpaca_live (ief) | TLT/IEF | 1d/1h | Treasury futures (ZN/ZB) **not verified as listed** | could not confirm; daily killed |
| `gdx_pullback_1d` | alpaca_paper | GDX | 1d | **none** | no equivalent |
| ETH legs (`trend_donchian_eth_4h`, `eth_pullback_2h`, `trend_donchian_eth_prop`, …) | bybit_*, breakout_1 | ETHUSDT perp | 1h–4h | **MET** (CME, closes daily, not 24/7) | overnight ban; the crypto perp 24/7 model doesn't apply |
| BTC legs (`trend_donchian`, `trend_donchian_1h`, `ict_scalp_5m`, …) | bybit_1 | BTCUSDT | 5m–4h | **MBT** | same |
| SOL / XRP / ADA / AVAX legs | bybit_*, breakout_1 (sol) | perps | 15m–4h | **none** | no equivalent |

**Legs with no Apex equivalent:** every SOL, XRP, ADA and AVAX leg,
`gdx_pullback_1d`, and `mhg_pullback_1d` (no micro copper). Also, **both
breakout_1 legs have no fit**: `trend_donchian_sol_prop` has no equivalent,
and `trend_donchian_eth_prop` would become MET under an overnight ban. So Apex
could not *replace* breakout_1's roster at all.

## 2. Costs

| item | Apex (new accounts, after 2026-03-01) | source | breakout_1 (1-Step Classic $5k) |
|---|---|---|---|
| model | *"one-time fee… not a subscription and does not renew… 30 calendar days"* | A6 | one-time eval fee |
| evaluation price | **could not look** (JS/Cloudflare). A third-party claim of "$197 for 50K EOD" is **unverified** | — | **$45** |
| activation (PA) fee | *"One-Time Activation Fee $59"*, seen only on the **25K Intraday** card. A no-activation-fee variant exists (*"At checkout, the PA fee is $0"*) | A7, A8 | none known |
| monthly/recurring | none on new products (*"One Time Fees – No recurring billing"*). Legacy: 25K $177/mo, 50K $197/mo, 100K $497/mo, PA $90 | A7, A9 | none |
| reset | *"There are no reset fees."* A new one-time purchase replaces a failed evaluation | A6 | re-buy at $45 (`rebuy_fee_usd`, marked DEFAULT/CONFIRM) |
| data | *"Level 1 data (L1) is included"*. DOM costs extra. Tradovate warns of *">$115"* monthly if the non-pro agreement is not signed | A6, A10 | none known |
| commissions per side (Rithmic / Tradovate) | MES/MNQ $0.51/$0.52 · MGC $0.76/$0.67 · MCL $0.51/$0.67 · MBT $2.76/$2.67 · MET $0.46/$0.37 · ES/NQ $1.99/$1.55 · HG $2.31/$1.77 · CL $1.98/$1.67 | A4, A5 | 0.04% notional per side (P2's instruments doc, operator screenshot, unverified) |
| split | *"100% payout split"* | A11 | **80/20** (90/10 upgrade) |
| payout cadence / minimum | *"Up to weekly"*; minimum *$500*; *"Maximum 6 payouts per Performance Account… After 6 payouts, the PA is closed"* | A11 | first after 14 d, then weekly; min $50 (breakout.yaml marks these **[WEB; CONFIRM]**) |
| 50K payout caps (Intraday) | $1,500 / $2,000 / $2,500 / $2,500 / $3,000 / $3,000, so **$14,500 lifetime maximum** (sum of the six) | A11 | no cap known |
| safety net | *"drawdown limit plus $100"*. 50K: net $52,100, request minimum $52,600 | A11 | none |
| inactivity | *"at least 2 trading days with $50 or more in net profit within every rolling 30-day period"* | A15 | none known |

**Breakout figures: inherited, not re-verified this session.** They are the
$45 fee, the 80/20 split, 3% daily loss, 6% static DD and the $4,700 floor. All
come from `config/prop_rulesets/breakout.yaml`, marked `[CONFIRMED — FAQ + plan
card]` on 2026-06-16. The payout cadence and minimum in that file are marked
`[WEB; CONFIRM]` and were never FAQ-confirmed. This lane re-read Breakout's FAQ
for *automation* terms only (see the automation spec). It did not re-check the
money figures.

## 3. Drawdown and rules, and the fit for OUR strategies

| rule | Apex | source | breakout_1 |
|---|---|---|---|
| DD type | two products: **Intraday Trail**, *"enforced in real time, including unrealized PnL"*; and **EOD**, *"recalculated once per trading day at 4:59:59 PM ET… enforced in real time"* | A16, A17 | **static 6%** off the starting balance, not trailing |
| trail stops | PA: *"trailing stops once Starting Balance + $100 is reached."* Rithmic/WealthCharts evals lock at the target balance. **Tradovate evals: *"trails indefinitely"*** | A16, A17 | n/a |
| max DD (25K/50K/100K/150K) | $1,000 / $2,000 / $3,000 / $4,000 | A18 | $300 on $5k |
| profit target | $1,500 / $3,000 / $6,000 / $9,000 | A18 | +10% ($500) |
| daily loss | EOD evals $500 / $1,000 / $1,500 / $2,000. Intraday evals: *"No Daily Loss Limit"*. PA DLL on 50K: $1,000 → $3,000 as profit scales. Hitting it: *"positions close automatically → trading pauses for the day… Your account remains active"* | A18, A19, A20 | **3%** ($150), and a breach is **permanent** |
| max contracts | eval 4 / 6 / 8 / 12. 50K PA scales 2 → 4 with profit. *"ten (10) micro contracts equal one (1) standard contract"* | A18, A19 | per-instrument caps on the terminal |
| consistency | PA only: *"no single trading day accounts for more than 50%…"*. Evals: *"Consistency Not Applied"* | A21, A18 | **none** |
| min days / time limit | evals: *"No minimum trading days"*, *"30-day assessment"*. PA payouts: 5 qualifying days at ≥ $200 (Intraday) / $250 (EOD) on 50K | A18, A11 | none / none |
| overnight / weekend | **banned**. Flat before the close; evals reset at 6:00 PM ET | A1 | **no overnight or weekend rule** |
| news | allowed for a normal strategy; "chasing" or both-sides news straddles banned | A1 | none known |
| stops / R:R | *"All trades must have either pending or mental stop losses"*. Example of unacceptable: *"five-tick profit target with a 150-tick stop loss"*. The 5:1 R:R and 30% MAE rules apply **only to legacy PAs** | A1, A7 | none |
| hedging | *"No Hedging of Any Kind – Directional Trading only"* | A1 | cross-account hedging banned |
| cloud servers / VPN | banned when used *"for the purpose of misrepresenting, concealing, or disguising your identity, device, or location"* | A1 | eval: no multiple accounts from the same IP |

**Fit for our book** (INFERRED from the table and the roster rows in § 1):
- **Holding periods.** Daily legs hold for days, so the overnight ban removes
  them outright. That is all three live futures legs and every daily ETF leg
  that maps to a future.
- **Stop width vs a trailing DD.** An intraday trail that counts
  **unrealized** P&L consumes the DD on favourable excursions that later
  retrace. Our trend legs use `atr_stop_mult` 1.5–2.5 (MEASURED,
  `config/strategies.yaml`). They are built to give back open profit before
  the stop, which is exactly the behaviour a real-time trail punishes. The EOD
  product is gentler but still trails until start + $100.
- **Frequency vs consistency.** A low-frequency book concentrates P&L in a few
  days. The 50% PA consistency rule plus the "2 days ≥ $50 per 30 days"
  inactivity rule both work against it.

## 4. Automation (the operator's main question)

**Answer: NO.** Apex's current terms prohibit automated and algorithmic
trading on every account type, evaluations included. The penalty is account
closure and forfeiture. No compliant fully automated route exists, with or
without a third-party bridge.

- **(a) Terms:**
  - A1, current: *"No Automation or Algorithm Usage allowed"*, with no eval/PA
    split, pointing to the user agreement for details. **The user agreement
    itself: could not look.**
  - A2, legacy PA: *"the use of automation is strictly prohibited on all
    account types. This includes any form of AI (Artificial Intelligence),
    Autobots, algorithms, fully automated trading systems, and high-frequency
    trading (HFTs)… Any type of hands-off, set-and-forget… including systems
    that run continuously 24 hours a day, is strictly prohibited"*, and *"PA
    and Live Prop Accounts must be traded exclusively by the individual
    listed… not… managed or influenced by any… system, automated trading bot,
    copy trading service, or trade mirroring software."*
  - Copying between your *own* Apex accounts looks tolerated: the Tradovate
    FAQ endorses the *"Apex Trade Copier"*, and WealthCharts has a built-in
    copier (A10, A22). *"trade copying with other traders"* is banned.
  - **Third-party blogs saying "bots allowed on evals" contradict the current
    first-party page.**
- **(b) Platforms and APIs:**
  - **Platforms provided:** Apex provisions **Rithmic, Tradovate and
    WealthCharts** (A22). Tradovate is reachable through NinjaTrader,
    Tradovate Web/Desktop/Mobile and TradingView. TradingView via Tradovate
    gets data *"that updates every 5 seconds"*.
  - **Tradovate API:** needs a LIVE account with more than $1,000 in equity
    plus an API subscription (A12). **The price could not be read**
    (support.tradovate.com returned 503/524). A search snippet claims
    $25/month and that prop/evaluation accounts are ineligible; **unverified.**
    A user post says prop accounts do not allow direct API access (A13).
  - **Rithmic R|API+ / R|Protocol:** *"Conformance testing required before
    production access"* (A14). **Fees: could not look.** Third-party claims of
    $100 plus $25/month are unverified. Whether Apex permits its Rithmic
    credentials in a conformed API app: **could not look.**
  - **Session limit:** *"Rithmic allows only one Market Data session at a
    time… you'll need the 'Rithmic 2nd Login Session' add-on"* (A23).
  - **What a direct integration would need, if it were allowed:**
    - Rithmic credentials;
    - a conformance-tested R|Protocol client and its fees;
    - the 2nd-login add-on, so the bot does not kick the operator's own
      session;
    - or, for Tradovate, a *live funded* Tradovate account, which an Apex
      account is not.
- **Compared with Breakout:** Breakout is manual today because it offers no
  broker API to account holders, not because of a ban
  (`breakout-compliance-2026-06-16.md` §4). That is still true as far as we
  could look. DXtrade publishes an API, but whether Breakout enables it is
  unknown, and the automation spec's probe step 0 answers it. Breakout's
  readable FAQ contains no automation ban. Apex's does.

## EV sketch for the W6-P1 prop simulator (a SKETCH, not a result)

**No backtest was run in this lane.** The block below is the parameter shape
`scripts/prop/montecarlo_prop.py --cost-aware` and P1's simulator could score,
in the `breakout.yaml` `economics` idiom. Values marked `?` could not be
verified.

```yaml
ruleset: apex
plan: 50k-intraday-trail          # alternative: 50k-eod
account_size_usd: 50000
profit_split: 1.00                                  # A11
phases:
  evaluation: {profit_target_usd: 3000, min_trading_days: 0, max_eval_days: 30}   # A18, A6
limits:
  drawdown_type: trailing_intraday_unrealized       # A16 (EOD variant: A17)
  max_drawdown_usd: 2000                            # A18
  trail_locks_at: start_plus_100_on_PA              # A16 (Tradovate eval: never locks)
  daily_loss_usd: null                              # intraday eval (A18); PA DLL 1000→3000 (A19/A20), pause-not-breach
  max_contracts: {eval: 6, pa_scaling: [2,3,4,4]}   # A18, A19
consistency: {pa_max_single_day_share: 0.50}        # A21
restrictions: {overnight_flat: true, weekend_flat: true, automation_allowed: false}  # A1
economics:
  account_fee_usd: "?"            # could not look; 3rd-party claim $197 (50K EOD) UNVERIFIED
  activation_fee_usd: "?"         # $59 seen for 25K Intraday only (A7); $0 variant exists (A8)
  rebuy_fee_usd: "= account_fee"  # "no reset fees" -> re-buy (A6)
  commission_per_side_usd: {MES: 0.51, MGC: 0.76, MET: 0.46}   # Rithmic, A4
  payout: {min_usd: 500, max_count: 6, caps_usd: [1500,2000,2500,2500,3000,3000], safety_net_usd: 52100}  # A11
```

The sketch as an expression:
`EV = −F_eval − P(pass)·F_act + P(pass)·Σ_{i=1..6} P(reach payout i | trailing DD, DLL, 50% consistency)·min(cap_i, withdrawable_i) − commissions`.
Two properties hold *before* any simulation. INFERRED from A1 and the § 1
roster:
- **For an automated operator, compliance P(retain payout) = 0** (A1, A2), so
  EV = −fees.
- **For the current futures roster, the set of runnable legs under
  `overnight_flat: true` is empty.** The only candidate is a flat-by-close
  variant of `ict_scalp_mgc_15m`, which does not exist yet.

The lifetime upside is bounded at **$14,500** per 50K PA (the six caps summed)
against ~$50k of notional DD capacity. For comparison, breakout_1's
`economics` block is already scored by the existing engine.

---

## Sources

All Apex pages were retrieved 2026-09-27 via a reader proxy (Cloudflare blocks
direct fetches). Page dates are "Published Time" values of 24–25 Sep 2026,
likely a site-wide re-publish.

| id | what | URL |
|---|---|---|
| A1 | Prohibited Activities (automation, overnight, news, hedging, stops, cloud/VPN, windfall) | https://apextraderfunding.com/help-center/getting-started/prohibited-activities/ |
| A2 | Legacy PA compliance (automation penalty) | https://apextraderfunding.com/help-center/performance-accounts-pa/legacy-performance-account-pa-compliance/ |
| A3 | Live prop program FAQ | https://apextraderfunding.com/help-center/getting-started/apex-live-prop-trading-program-faq/ |
| A4 | Rithmic commissions and instruments | https://apextraderfunding.com/help-center/rithmic/rithmic-commissions-instruments/ |
| A5 | Tradovate commissions and instruments | https://apextraderfunding.com/help-center/tradovate/tradovate-commission-instruments/ |
| A6 | Evaluation fees and access | https://apextraderfunding.com/help-center/billing/evaluation-plan-fees-and-access-explained/ |
| A7 | Homepage (new vs legacy; 25K Intraday $59 activation; "NO 5/1 … NO MAE") | https://apextraderfunding.com/ |
| A8 | No-activation-fee evaluations | https://apextraderfunding.com/help-center/uncategorized/no-activation-fee-evaluations/ |
| A9 | Legacy products | https://apextraderfunding.com/legacy-products |
| A10 | Tradovate FAQ (data fees, Apex Trade Copier) | the Tradovate section of https://apextraderfunding.com/help-center/ |
| A11 | Payouts (Intraday; EOD) | https://apextraderfunding.com/help-center/intraday-trailing-drawdown-accounts/intraday-trailing-drawdown-payouts/ · https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-payouts/ |
| A12 | Tradovate API requirements | https://api.tradovate.com/ |
| A13 | Tradovate community, "Prop account API access" (user post, Oct 2024) | https://community.tradovate.com/t/prop-account-api-access/10430 |
| A14 | Rithmic API suite (conformance) | https://www.rithmic.com/products/api-suite |
| A15 | PA inactivity policy | https://apextraderfunding.com/help-center/billing/inactivity-policy-on-performance-accounts-pa/ |
| A16 | Intraday trailing drawdown explained | https://apextraderfunding.com/help-center/intraday-trailing-drawdown-accounts/intraday-trailing-drawdown-explained/ |
| A17 | EOD drawdown explained | https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-drawdown-explained/ |
| A18 | EOD / Intraday evaluation parameters | https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/ (and the intraday-trailing-drawdown evaluations page) |
| A19 | PA scaling levels | https://apextraderfunding.com/help-center/additional-helpful-items/scaling-levels-pa-explained/ |
| A20 | Daily loss limit explained | https://apextraderfunding.com/help-center/additional-helpful-items/daily-loss-limit-explained/ |
| A21 | 50% consistency requirement | https://apextraderfunding.com/help-center/additional-helpful-items/50-consistency-requirement/ |
| A22 | Choosing the right platform | https://apextraderfunding.com/help-center/getting-started/choosing-the-right-platform/ |
| A23 | Rithmic account setup (one market-data session) | https://apextraderfunding.com/help-center/rithmic/rithmic-account-setup/ |

**Could not look:**
- new-product evaluation prices, and activation fees other than 25K Intraday
  (https://apextraderfunding.com/#products, JS/Cloudflare);
- all of `support.apextraderfunding.com/hc/en-us` (403; the old help center);
- the Tradovate API price and prop eligibility
  (`support.tradovate.com/s/article/Tradovate-API-Access`, 503/524);
- Rithmic API fees, and whether Apex permits Rithmic API use;
- **the Apex user agreement**, which A1 cites for automation details;
- web.archive.org (blocked by egress policy).
