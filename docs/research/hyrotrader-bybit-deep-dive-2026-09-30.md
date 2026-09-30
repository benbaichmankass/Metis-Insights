# HyroTrader on Bybit: rules, platform, and the framework to trade it (2026-09-30)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **desk research, not a decision record. Nothing was bought, no account opened, no credential requested, and HyroTrader was NOT contacted** (operator, ~05:26Z 2026-09-30, relayed by the manager: *"we're not going to ask them specifically ... I want you to do the research to understand what the rules are and what kind of framework we would need"*).

Follow-on to [`prop-platform-automation-survey-2026-09-30.md`](prop-platform-automation-survey-2026-09-30.md) (#14529), which ranked HyroTrader first.

## Links to check it yourself (first-party, all `hyrotrader.com`)

1. **Homepage, plans, prices, rule summary:** https://www.hyrotrader.com/
2. **Trading bots policy (the article the survey could not read):** https://www.hyrotrader.com/faq/hyrotrader-account/can-i-use-trading-bots-or-signal-bots-during-the-challenge-or-on-a-funded-account/
3. **Bybit API setup FAQ (key scope, demo vs live):** https://www.hyrotrader.com/faq/bybit-platform/how-to-correctly-set-up-bybit-api-and-why-am-i-getting-an-error-when-connecting-the-api/
4. **Bybit demo compliance rules (the rule that matters most to a bot):** https://www.hyrotrader.com/faq/bybit-platform/bybit-demo-compliance-rules-/
5. Trading rules: https://www.hyrotrader.com/trading-rules/ · Crypto API trading page: https://www.hyrotrader.com/blog/crypto-api-trading/ · Terms: https://www.hyrotrader.com/terms-and-conditions/ · Scaling plan: https://www.hyrotrader.com/scaling-plan/

## How to read the marks

**[1P]** = read on hyrotrader.com on 2026-09-30. `WebFetch` returns a small-model summary, so quotation marks are near-verbatim, not byte-exact; **[1P-paraphrase]** = the tool returned a paraphrase, not a quote. **[snippet]** = a search-result snippet only. **[INFERRED]** = my reasoning, not a firm statement. **No third-party source is used for any rule in this document.** Two facts (the bots FAQ and the demo-compliance article) were fetched twice, by the research agent and by me, and agree.

## 0. The answer

1. **The evaluation is NOT on real Bybit. It is a simulated Bybit DEMO account.** [1P] The API-setup FAQ: *"Ensure you are connecting to the Bybit Demo API, not the live environment, when trading a demo challenge."* Terms § 3.2 [1P via tool digest, not section text]: the service "operates entirely on simulated accounts" and "no order you place through the Platform is executed against a live market venue". So the evaluation and verification are the same kind of venue as our `bybit_1` (`demo: true`, `api-demo.bybit.com`). **This corrects the survey**, which quoted a HyroTrader blog line that trades "execute on ByBit's real order books" (§ 8).
2. **The funded phase is unresolved.** FAQs still call the post-verification account "a simulated demo trading account", and say "real capital" comes only after a further **15% profit** [1P]. No page says the funded phase is a mainnet sub-account. **Do not assume real fills.**
3. **A custom API bot is permitted by the firm's own FAQ**, verbatim below. One contradicting clause (Terms § 10.2(e)) exists in the tool's digest of the Terms; I could not read its section text. The operator has declined to ask HyroTrader, so this stays a **stated, accepted risk**, not a finding that it is allowed.
4. **The rule that will actually bind a bot is the demo-realism rule**, not drawdown: market orders that fill "at exact levels during high volatility with no slippage" can be flagged, and *"Only 40% of profits from flagged trades count toward profit targets"* [1P]. A bot must not look like it exploits demo fills.
5. **No unbreakable obstacle to our Oracle VM was found.** The key must have **no IP restriction** [1P], so there is no allowlist to satisfy; no datacenter/VPS ban was found (absence in what was read, not proof).
6. **Build effort (INFERRED): about 4-6 lane-days**, mostly rule guards and a status poller, because the Bybit adapter, demo endpoint and prop risk machinery already exist (§ 5).

## 1. The full rule set

### 1.1 Evaluation structure

| item | rule | source |
|---|---|---|
| Steps | **One-step** and **two-step** both sold | [1P] home |
| Profit target | One-step: **10%**. Two-step: Phase 1 **10%**, Phase 2 (verification) **5%** | [1P] home, `/start-trading/` |
| Minimum trading days | One-step **5 days**; two-step **5 trading days** in Phase 1 (10 in total per the paraphrase). *"even if you achieve your profit goal in fewer than 5 trading days, you must still complete the minimum of 5 trading days"* | [1P] home; `/faq/evaluation-process/minimum-trading-days/` |
| What counts as a trading day | A trade of **at least 5% of the initial balance**, P&L of at least **±1% of trade value**, **opened and closed the same calendar day** | [1P-paraphrase] same page |
| Time limit | *"There is no minimum or maximum number of calendar days required to pass, and your evaluation will not expire if it takes longer to complete."* | [1P] `/faq/evaluation-process/how-many-days-do-i-have-to-complete-the-challenge/` |
| Inactivity | *"if your most recent trading day is older than 30 days, your account will be disabled."* | [1P] `/faq/rules/do-you-have-an-inactivity-rule/` |
| After passing | Phase 3 "(Funded)": KYC, agreements, Funded Trader Form, connect the funded account; *"Bybit and Tealstreet accounts require you to generate an API key and manually connect it"* | [1P-paraphrase] `/faq/evaluation-process/what-happens-after-...` |
| Demo to real | After the evaluation the trader gets *"a simulated demo trading account ... request a payout after achieving a minimum of $100 in profit. With consistent performance and a minimum profit of 15%, traders become eligible for a funded trading account with real capital."* | [1P] `/faq/hyrotrader-account/when-will-i-transition-from-the-simulated-demo-...` |
| Failure | *"that specific account will be automatically invalidated"*; funded: the agreement is terminated; *"there are no discounts for repeat attempts"* | [1P] `/faq/rules/if-i-break-the-rules-do-i-get-another-chance/` |
| Capital cap | *"The maximum capital allocation per user is $200,000 across all active challenges."* | [1P] `/faq/trading-restrictions/what-are-prohibited-trading-actions/` |

### 1.2 Daily loss and maximum loss: two conflicting figures, and a basis that is stated two ways

- **4% daily / 6% maximum loss** for Challenge and Funded [1P-paraphrase `/trading-rules/`; [1P] home also says daily "4%", max loss "6%"]. The same home page also prints an **"Overall drawdown cap: 5% of balance"**.
- A separate FAQ says *"During verification phases, the 5/4% drawdown is 'calculated based on your initial funded account capital'"* with a $50k example "$2,500 or $2,000 respectively", constant day to day [1P] `/faq/rules/how-is-the-5-daily-drawdown-calculated/`. **Which of 4% / 5% / 6% applies to which phase or variant is not established by anything I read.**
- **Two drawdown types**, chosen at challenge setup [1P] `/start-trading/`:
  - **Standard = trailing daily drawdown:** *"Your maximum daily loss is calculated from the highest balance reached during the day, updating continuously in real time."* [1P] `/faq/rules/what-is-the-difference-between-standard-trailing-and-swing-fixed-daily-drawdown/`. Worked example [1P-paraphrase]: $5,000 account, day peak $5,200, dip to $4,950 = $250 drawdown.
  - **Swing = fixed daily drawdown:** starts from *"your balance at the start of the day and does not change during the day"*; paid upgrade; the reference "only resets once every 24 hours".
- **Basis is stated both ways.** The Standard/Swing page says *balance*; the calculation page says peak/day-start *equity* including unrealized P&L, recalculated "multiple times daily, with the server reset occurring at UTC. All floating losses, profits, and fees are factored" [1P-paraphrase]. **Treat unrealized P&L as counting.** This is the safe reading.
- **Max loss is static** relative to initial capital per the calculation page ("remains constant"); a third-party review had described a different scheme, and is not used here.
- **How the firm reads it from Bybit is not documented.** The only tracking statement found: *"To ensure your challenge is tracked and evaluated correctly, you must use either Cross Margin or Isolated Margin."* (no Portfolio Margin) [1P] `/faq/bybit-platform/can-i-use-portfolio-margin-mode-on-my-bybit-account/`.

### 1.3 Leverage, size, per-trade loss, stop-loss

- **Leverage:** *"Most altcoins on Bybit allow leverage between 50x and 100x."* [1P] `/faq/platform/what-leverage-can-i-use/`; "more than 700 trading pairs" with leverage up to 100x [1P]. **No first-party per-instrument table was found.** Bybit's own limits apply.
- **Funded limits:** *"A trader may allocate up to 25% of the initial account balance as total margin across open positions."* and *"The total notional value of all open positions must not exceed 2× the initial account balance."* Funded accounts have *"no profit distribution requirement"* [1P] `/faq/rules/are-there-any-other-rules-for-a-funded-account/`. **Not verified whether these bind in the challenge phases.**
- **Per-trade loss:** *"the maximum realized loss on any single position must not exceed 3% of the initial account balance."* and *"this rule is currently not monitored by our automated system and is reviewed manually."* [1P] `/faq/rules/what-is-the-maximum-loss-per-trade-rule/`
- **Stop-loss:** *"No, setting a stop-loss is not mandatory. Traders may open positions without a stop-loss order."* but still bound by the 3% rule [1P] `/faq/rules/d/`.
- **Number of positions:** *"entirely up to you, as long as you follow good risk management."* [1P] `/faq/rules/how-many-positions-can-i-open/`

### 1.4 News, weekend, overnight

- *"Trading solely based on news events is not allowed ... However, during news events, you do not need to close your positions"* [1P] `/faq/trading-restrictions/is-news-trading-allowed/`.
- *"No, it is not required to close your positions overnight when trading with a HyroTrader funded account. You are allowed to keep your positions open throughout the night and even over the weekend."* [1P] `/faq/rules/do-i-have-to-close-my-positions-overnight/`. **Worded for funded accounts; no separate challenge statement was found.**

### 1.5 Prohibited practices and consistency

- **Bots (verbatim, fetched twice), [1P]** `.../can-i-use-trading-bots-or-signal-bots-during-the-challenge-or-on-a-funded-account/`: *"Trading bots connected via API are permitted."* covering "Self-developed, custom, and third-party trading bots". Not allowed: *"Copy-trading services, account mirroring, and external signal providers"*; *"High-Frequency Trading (HFT) strategies"*; *"Any strategy designed to exploit, manipulate, or gain an unfair advantage from our systems, infrastructure, execution environment, pricing, latency, or risk management mechanisms"*. *"Traders may generate their own API key and connect it to their trading setup."* Non-compliance "may result in account review, account restrictions, or disqualification". **HFT is not defined and no numeric threshold or minimum hold time was found.**
- **Terms conflict [1P via tool digest, section text NOT read]:** § 10.2(e) reportedly prohibits "automated trading systems, bots, expert advisors (EAs), copy trading, or third-party trade-signal services" *except where expressly permitted in Trading Rules*. The bots FAQ is the express permission a reader would cite; whether the firm agrees is unknowable without asking. Other digest items: § 10.2(c) "exploitation of platform errors, latency arbitrage, data-feed inconsistencies"; § 10.2(b) "mirror trading, group trading, or any strategy that relies on simulated positions taken across multiple Accounts".
- **Prohibited actions on Bybit challenges** [1P] `/faq/trading-restrictions/what-are-prohibited-trading-actions/`: "Requesting additional demo funds"; "Modifying the USDT balance"; "Deleting or altering the API key"; "Trading spot in a futures-based challenge"; "Trading the EUR/USD pair"; "Trading options or USDC pairs"; "Combining spot and margin trading on the same account". *"Violation of any of the above may result in account failure or termination."*
- **Martingale** "strictly" forbidden; **hedging across multiple accounts** prohibited (hedging inside one Bybit account permitted); manual review for "gambling behavior or excessive risk-taking" (warnings, profit removal, payout adjustment, termination) [1P-paraphrase] `/faq/rules/what-are-the-risk-management-conditions-at-hyrotrader/`.
- **Low-cap assets** (market cap under $100M, typical 24h volume $500K-$5M, or Bybit "Innovation Zone"): limited to **5% of the initial balance including leverage**, and only **40% of that profit counts** toward the target [1P] same page and demo-compliance article. BTC/ETH/SOL/XRP/ADA are not low-cap by that definition **[INFERRED: no symbol list was read]**.
- **Copy trading** *"Copy trading other traders is not allowed."* [1P] `/faq/trading-restrictions/is-copy-trading-allowed/`.
- **Spot/USDC/tokenized:** spot "No, currently, HyroTrader challenges are focused on futures trading."; USDC "not possible"; all Bybit **USDT** pairs are allowed, including tokenized stocks, oil, metals [1P].
- **Consistency (40% day rule), Phases 1 and 2 only:** *"no single trading day may contribute more than 40% of the trader's total net result, including both profits and losses."* Example: $500 target, Day 1 +$320 is capped at $200 [1P] `/faq/trading-restrictions/i-have-one-trading-day-that-exceeds-...`.
- **Demo realism** [1P, fetched twice] `/faq/bybit-platform/bybit-demo-compliance-rules-/`: flagged practices include *"Market orders filled at exact levels during high volatility with no slippage"* and *"Large positions filled instantly without price impact"* (and, per the agent's read, "Tight entries and exits during volatile moves with no spread or delay"). Consequences: manual review; *"PnL may be adjusted to reflect realistic execution conditions"*; *"Only 40% of profits from flagged trades count toward profit targets"*; reassignment to a real-liquidity platform (Cleo). *"A challenge will not be failed solely due to demo slippage discrepancies."* Bybit's demo "does not simulate real slippage, liquidity depth, or market impact".

### 1.6 Payouts, split, fees, refunds

| item | rule | source |
|---|---|---|
| Split | *"All traders start with a profit split of 80%. With the scaling-up plan, they can increase their profit split by 5% every 4 months of trading a funded account, reaching up to 90% after 16 months."* | [1P] `/faq/hyrotrader-account/how-much-is-the-profit-split/` |
| First payout | *"You have the option to request a payout on the same day as your first trade executed on the account. Payouts are typically processed within 12-24 hours. ... USDT or USDC ... no withdrawal commissions"* | [1P] `/faq/hyrotrader-account/how-can-i-withdraw-my-profits/` |
| Minimum | *"traders must earn at least $100 in profit after the split."* | [1P] `/faq/orders-billing-taxes/is-there-a-minimum-withdrawal-amount/` |
| Refund | *"Once you become eligible for your first profit split, your Refundable Challenge Deposit is processed during the same payout cycle."* (separate crypto transaction) | [1P] `/faq/hyrotrader-account/is-the-refundable-challenge-deposit-returned-...` |
| Fail while in profit | On a demo live account *"any profits you've made will be lost, and the account will be closed"*; on a sub-account remaining profit is withdrawable | [1P] `/faq/hyrotrader-account/what-happens-if-i-fail-the-account-but-its-still-in-profit/` |
| Scaling | "Achieve a total profit of at least 20% to qualify"; up to $400k challenges, $200k funded [1P-paraphrase] `/scaling-plan/`; cadence details [snippet only] | |
| Prices (deposit, refundable) | $5k **$59**, $10k $119, $25k $249, $50k $379, $100k $579, $200k $969; "No monthly or hidden fees" | [1P] home (one- vs two-step not specified on the page) |
| Swing upgrade | Two-step: 5k $29, 10k $49, 25k $89, 50k $119, 100k $179, 200k $299. One-step: 5k $39, 10k $59, 25k $119, 50k $169, 100k $229, 200k $419. **The home page's "+$89" matches only the two-step 25k price.** | [1P] `/faq/swing-daily-drawdown-upgrade/what-is-the-cost-to-upgrade-a-challenge-to-swing/` |
| Bybit fees | maker 0.0200%, taker 0.0550% | [1P] `/faq/bybit-platform/-what-are-bybits-fees/` |

**Economics that matter for breach-and-rebuy:** the deposit is refunded only with the first payout, so a breach before any payout **loses the deposit**, the same shape as Breakout's non-refundable fee for that outcome. And *"any profits ... will be lost"* if the demo-live account fails while in profit.

## 2. The evaluation platform

| question | answer | source |
|---|---|---|
| Is the challenge a Bybit sub-account with API access? | **A Bybit DEMO account with API access.** Bybit Demo API, not live. Each challenge has *"its own subaccount and a separately created and connected API key"* | [1P] API-setup FAQ; `/faq/bybit-platform/how-can-i-correctly-set-up-multiple-bybit-challenges-under-one-hyro-profile/` |
| Is the whole thing simulated? | Terms § 3.2: "operates entirely on simulated accounts ... no order ... executed against a live market venue", real-time market data | [1P via tool digest] |
| Funded phase? | **Unresolved.** "a Bybit sub-account" is named; the same FAQs call it "simulated demo" until 15% profit; the marketing blog says "trades execute on ByBit's real order books" | [1P] |
| Other platforms | **Tealstreet** (uses Bybit demo data) and **CLEO** (Binance-based, 400-500+ pairs, **no API key**) | [1P] `/faq/getting-started/which-platforms-can-i-use-for-trading/` |
| Who creates the key, and its scope | **We do.** Required: *"Read/Write access; Unified account; Assets enabled; No IP restrictions selected. If IP restrictions are enabled, our system will not be able to establish the connection."* | [1P] API-setup FAQ |
| Key lifecycle | Copy with the "Copy" button; *"Once connected, the API key must not be altered or removed"*; close all positions and cancel orders before connecting; *"Do not reuse an API key that was previously linked to another challenge"* | [1P] API-setup FAQ |
| Key expiry | *"Five days before your API expires, you will receive an email reminder, and a 'Reconnect' button will appear on your challenge."* If not reconnected *"your challenge will fail automatically"*. **The "3 months" figure in the survey is NOT stated by any page read.** | [1P] `/faq/bybit-platform/what-happens-if-my-api-expires-during-a-bybit-challenge/` |
| Margin mode | Cross or Isolated only, no Portfolio Margin | [1P] |
| Third-party apps | *"using third-party applications is not supported during HyroTrader challenges. You can utilize third-party applications once you are on a Bybit sub-account."* Our own custom bot is a different clause (§ 1.5) | [1P] `/faq/bybit-platform/can-i-create-an-api-key-on-bybit-to-trade-on-a-different-platform/` |
| VPS / datacenter / VPN | **No datacenter or VPS IP rule found.** Terms § 10.4: VPN "for privacy purposes, provided that you do not use a VPN to conceal or misrepresent the jurisdiction". Our Oracle VM has a fixed IP; the key must have *no* IP restriction, so nothing needs allowlisting | [1P via digest] |
| Bybit KYC | Not needed for the demo | [1P] `/faq/bybit-platform/i-would-like-to-participate-in-the-challenge-do-i-need-a-bybit-account/` |
| How the firm's dashboard tracks the account | **Not documented.** It evidently reads the connected key (balance/equity/positions); how it computes daily loss is not stated | [INFERRED] |

**Consequence for us.** The challenge and probably the first funded stage are the *same class of venue as `bybit_1`*: `bybit_client_for` with `demo: true` (`src/units/accounts/clients.py`, pybit `HTTP(demo=True, ...)`). If funded turns out to be a mainnet sub-account, the same client with `demo: false` works, and then the venue is real and our Stage-1 cost-fidelity question applies for real. **Which of the two it is only becomes known when a funded account exists**, so any plan must survive both.

## 3. The framework we would need, mapped to our code

Verified in the working tree on 2026-09-30 (`origin/main` `6398bea5`).

| need | existing piece | work |
|---|---|---|
| **Account entry** | `config/accounts.yaml` `bybit_1` is the template: `exchange: bybit`, `demo: true`, `api_key_env`, `market_type: linear`, `strategies:`, `symbols:`, `risk:` block. `breakout_1` shows the prop shape: `type: prop`, `account_class: prop`, `account_state`, `phase_requirements` | New `hyro_1` entry: `type: prop`, `account_class: prop`, `exchange: bybit`, **`demo: true`**, own `api_key_env`/secret pair, `mode: dry_run` first. `account_class: prop` keeps it out of the real-money and paper KPIs. |
| **Broker wiring** | `bybit_client_for` already handles demo and per-account env names; `EXCHANGE_MAP` has `bybit`. There is **no `src/units/accounts/bybit/` package** (the survey's wording, corrected there) | **No new broker package and no `EXCHANGE_MAP` entry.** The `new-broker` skill's steps 1-2 are already satisfied; steps 2b (PnL source), `accounts.yaml`, verification apply. Bybit closed-PnL is already the broker-truth reader (`_bybit_closed_pnl_lookup`). |
| **Rule guards** | `PropRiskManager` (`src/units/accounts/prop_risk.py`, evaluation/funded state, overnight/weekend windows); `RiskManager` has `max_dd_pct` measured as *"max intra-day equity drawdown from today's high"* and `daily_loss_pct`; both reset at **UTC midnight** and rebuild from the journal | HyroTrader **Standard** (trailing-from-day-peak, server reset UTC) is close to what `RiskManager` already computes (intraday drawdown from today's high, UTC day). **Swing** would need "from day-start balance". Needs a `config/prop_rulesets/hyrotrader.yaml` (schema per `src/prop/ruleset.py`) carrying the numbers **once the 4/5/6% ambiguity is resolved at purchase time**. |
| **Balance and day-start** | Breakout's rule distance reads an *operator-reported* snapshot (`prop_reconcile.compute_rule_distance`, `prop_journal.latest_account_status`), which is why it has a staleness gate | For a Bybit-backed account the snapshot can be **read from the API**, not reported by a human: a poller writes balance/equity/unrealized to the same status store, so `compute_rule_distance` and `prop_sizing.binding_cushion` work unchanged and the "stale cushion" failure cannot recur. |
| **Sizing to the cushion** | `src/prop/prop_sizing.py`, `prop_risk_gate.py`: `within_cushion` / `exceeds_cushion` / `cushion_unknown` | Reused. Add a **3%-of-initial per-position cap** as a hard guard (the firm says it reviews it manually, so the guard is ours alone). |
| **Symbol allowlist** | `accounts.yaml::strategies:` is the roster; `symbols:` is data-pull only | Roster of USDT perps only; **hard refusal of spot, USDC, options, EURUSD, Innovation Zone / sub-$100M symbols** in the order path, because the key can place them. |
| **Key hygiene** | none | A guard that **never** calls a key-edit or balance-modifying endpoint, a "dedicated key per challenge" rule in the runbook, and an alert on the 5-day-before-expiry reconnect window. |
| **Demo-realism** | `src/exchange/bybit_connector.py`; `execution_costs.slippage_bps_roundtrip_for` | See § 4: prefer **limit/post-only entries**, avoid outsized market orders, and log every fill's slippage so a "flagged trades" review can be answered with data. |
| **Reporting** | Firm dashboard reads the API; our journal is `trade_journal.db` | The journal needs the account tagged `prop`; nothing else, since the firm needs no manual reporting. There is no Telegram-ticket bridge needed (that exists only because Breakout had no API). |
| **Strategies and instruments that fit** | The crypto-perp legs already on `bybit_1` (trend_donchian family, pullbacks, `eth_pullback_prop_2h`) and the P2 candidate set | A `bybit_1`-soaked leg is the natural roster. **Fit against the 40% day rule** (Phases 1-2): a trend leg that makes most of its profit on one day is capped, so the P1 evaluator must model it. |

**Build effort (INFERRED, not measured), lane-days:** ruleset file + tests 0.5-1; `hyro_1` account entry + roster + symbol allowlist guard 0.5-1; API-backed status poller feeding the existing rule-distance path 1-2; 3%-per-position and demo-realism guards + tests 1; dry-run + one minimum-size round trip on a purchased demo account 1. **Total about 4-6 lane-days.** The survey said "Small" and that holds; this is the sizing behind it.

## 4. "Making a trade" end to end

1. **Signal.** A rostered leg fires on `hyro_1`, exactly as on `bybit_1`.
2. **Gates, in order:** account `mode`/leg `execution` gates (the two execution gates, unchanged) → `PropRiskManager` (mission state, windows) → `RiskManager` (daily loss / drawdown) → `prop_risk_gate` (does `risk_usd` fit the API-read cushion?) → HyroTrader hard guards (allowlisted symbol, ≤3% of initial per position, margin/notional caps if they apply, cross/isolated margin).
3. **Order.** `bybit_client_for(account)` places the order on `api-demo.bybit.com`. **Native SL/TP go on the order itself**, the way our Bybit executor already sends them. Prefer limit or post-only entries: a market order that fills "at exact levels" is the flagged pattern.
4. **What the firm's rule checks see.** The firm reads the connected account: equity including unrealized P&L, recalculated several times a day, day boundary at UTC [1P-paraphrase]. It sees a Bybit demo position with an SL, opened and closed like a manual trade. Our own guards must therefore hold *tighter* than the firm's (e.g. daily loss limit set inside 4%) because the firm's clock and equity basis are only partly documented.
5. **Exit and journal.** Exit via TP/SL/strategy close as today; the Bybit closed-PnL reader is the broker truth; the trade is journaled under the `prop` account class.
6. **Minimum-trading-day accounting.** A qualifying day needs a same-day open and close of ≥5% of initial balance with ≥1% P&L [1P-paraphrase]. **Our 1h/2h/4h/1d legs rarely open and close within one day**, so passing the 5-day minimum may need a deliberate same-day leg. This is a real constraint on which legs can pass the evaluation and belongs in the P1 evaluator.

## 5. Open questions this research could not close (and why they matter)

| # | question | why it matters | how it can be closed without contacting the firm |
|---|---|---|---|
| Q1 | Terms § 10.2(e) vs the bots FAQ | The whole plan rests on a custom API bot being allowed | Read the Terms section text in a browser; then it is an accepted risk (same class as breakout_1's ToS line) |
| Q2 | 4% vs 5% vs 6% and which applies to which phase | Sets every guard number | The checkout page for each plan and a purchased account's own dashboard show the exact limits |
| Q3 | Funded phase: demo or mainnet | Decides whether Stage-1 cost fidelity is even measurable | Only visible on a funded account; the plan must work either way |
| Q4 | Trailing basis: balance or equity | Off-by-unrealized-P&L breaches | Treat equity as binding (safe side); a purchased account's live drawdown readout would show it |
| Q5 | Do 25% margin / 2x notional bind in challenge phases | Sizing headroom | Same: read on the account |
| Q6 | Whether "HFT" has a threshold | Our 5m scalp legs are excluded regardless; 1h+ legs are far from HFT | Keep to 1h+ legs; no need to resolve |

## 6. Correction to the merged survey (#14529)

Filed in the same PR as a note at the top of the survey's § 1.1:
- it quoted the HyroTrader blog *"execute on ByBit's real order books"*; the firm's FAQ and Terms say the challenge runs on the **Bybit Demo API** and the service is "entirely simulated";
- it said keys "expire after 3 months"; **no first-party page states that**; a 5-day reminder before an unstated expiry is what the FAQ says;
- it said the bot-FAQ body was unread; it has now been read (verbatim above).

## 7. What this does and does not establish

- **Established (first-party, this session):** the rules above as quoted, the demo venue, the no-IP-restriction key, the permitted-bots FAQ text, and the demo-realism rule.
- **Not established:** the funded-phase venue, the drawdown percentages by phase, the Terms § 10.2(e) section text, any first-party per-instrument leverage table, and whether any of our legs would pass the evaluation. **This document says nothing about P(pass) or EV per purchase**; that needs the P1 evaluator run on the HyroTrader rule set (4-5% daily trailing or fixed, 6% max, 40% day rule, 5 qualifying days).
- Next actions that need no operator ask: (a) read Terms § 10.2 in a browser; (b) run the P1 evaluator against a `hyrotrader.yaml` ruleset with both the 4% daily / 6% max and the 5% daily variants; (c) only then decide on buying one $59 5k.
