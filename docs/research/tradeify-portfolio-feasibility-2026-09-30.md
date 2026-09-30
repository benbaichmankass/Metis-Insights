# Tradeify 247: can our crypto legs run a portfolio inside its limits? (2026-09-30)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **desk research plus offline Monte Carlo, not a decision record. Nothing was bought, no account opened, no credential requested, no firm contacted, no order path touched.**

Lane **TRADEIFY-PORTFOLIO** (Tier 1). Operator, verbatim, ~05:47Z 2026-09-30: *"the Tradeify option sounds like it uh, might be good, even if they don't serve the API docs, if we can at least reuse the info where we already build ... let's see there also if like uh, we think we can get a portfolio that would uh, be reasonably profitable within their limits."* Earlier requirement from the same operator, applied in § 4: *"if we only figure out ... one or two strategies and they only trade once or twice a month, then it's not necessarily going to be a worthwhile path as opposed to building some sort of local option for breakout."*

Builds on [`prop-dxtrade-firms-2026-09-30.md`](https://github.com/benbaichmankass/Metis-Insights/blob/claude/prop-dxtrade-firms/docs/research/prop-dxtrade-firms-2026-09-30.md) (PR #14540, not yet on `main` when this was written). HyroTrader is lane HYRO-STRATS's; it is not re-worked here.

## 0. Bottom line

**Marginal.** The portfolio clears the operator's frequency test easily and has positive expected value at a small per-trade risk, but the edge is thin, cost-dominated, and its evidence interval straddles zero.

1. **Frequency is not the problem.** The 3-leg book trades about 30 trades a month, not one or two (§ 4.1).
2. **Expected value is positive but small per account and slow.** At 0.5% risk per trade on a $10k 1-Step account the simulator gives a mean net of about +$830 to +$850 per account life after the $120 fee (path model, full-size runs, 2 seeds), median 242-245 days to pass, and 77% of simulated lives net-positive. The registered rule (V2) returns **INDETERMINATE**, not PASS, because the 5th percentile of the evidence bootstrap is about −$100 (§ 3.3).
3. **The result is fragile to risk and to cost.** At 0.75% risk the pessimistic bound turns to about −$6; at 1% risk the mean is +$205 and the bound −$117. With 10 bps round-trip slippage instead of 3, the 0.75% arm falls from +$521 to +$71 (§ 3.4).
4. **Three of the five legs lose their sign without their best fold** (§ 3.1). The whole book rests on one year of history.
5. **Tradeify is worth roughly ten times Breakout per account, on the same tool and same trades** (§ 4.3), but that is a comparison of two small numbers, one of which comes from a sizing regime that shrinks risk.
6. **The API is still unprobed.** Everything about reuse depends on a probe that can be run without buying anything (§ 5, option B).

## 1. The Tradeify 247 rule set (first-party, read 2026-09-30)

**Marks.** **[1P]** = read on `help.tradeify247.co` on 2026-09-30 by `WebFetch`, which returns a small-model *summary*: quotes are near-verbatim, so re-read the page before relying on exact wording. **[3P]** = third party, used nowhere below except where marked. **[INFERRED]** = my reasoning. No first-party page states the daily-loss basis for the 2-Step phases separately from the 1-Step page.

First-party pages: [rules overview](https://help.tradeify247.co/en/articles/13393254-trading-rules-overview) · [FAQ](https://help.tradeify247.co/en/articles/13393249-tradeify-247-faq) · [choosing an account type](https://help.tradeify247.co/en/articles/13393252-choosing-your-account-type) · [account sizes and pricing](https://help.tradeify247.co/en/articles/13393253-account-sizes-and-pricing) · [payouts and billing](https://help.tradeify247.co/en/articles/13393260-payouts-and-billing) · [1-Step guide](https://help.tradeify247.co/en/articles/13744939-1-step-evaluations-guide) · [DXTrade guide](https://help.tradeify247.co/en/articles/13393256-dxtrade-platform-guide) · [3-Step promo](https://help.tradeify247.co/en/articles/16547729-3-step-promotional-plan).

### 1.1 Products, targets, prices [1P]

| product | target | drawdown | min days | $10k | $25k | $50k | $100k |
|---|---|---|---|---|---|---|---|
| **2-Step** | 10% then 5% | static 3% daily / 6% max | none stated for the phases; **3 profitable days before the first payout** (funded) | $100 | $200 | $350 | $580 |
| **1-Step** | 12% | static 3% / 6% | **none**; no minimum profitable days | $120 | $231 | $431 | $838 |
| **Instant Funding** | none | 3% daily; 6% **trailing on closed-trade balance**, locks at the start balance on the first payout request | none fixed; **20% consistency score (5 profitable days in practice)** | $200 | $340 | $580 | $1,000 |
| APE-X (legacy, not sold) | n/a | single 4% trailing | n/a | n/a | n/a | n/a | n/a |
| 3-Step promo | 3 × 10% | 3% / 10% static | none | $47 for a $247,000 account, one per customer, "may be withdrawn"; sold for 10 days at the end of August 2026 | | | |

All are one-time purchases, "no recurring fees". Evaluation time limit: "Unlimited" (1-Step page). Inactivity: **30 consecutive days without a trade closes the account as breached** (warning at day 28).

### 1.2 Loss limits, reset, basis [1P]

- **Daily loss 3% of account size.** Resets at **22:00 UTC**; the limit is the *previous day's closing balance minus 3%*; *"Breaches trigger immediately based on live equity, including unrealized losses."* An open losing trade can breach before it is closed (rules overview).
- **Max loss 6%.** **Static** on 2-Step and 1-Step: the floor is start minus 6% and never moves, including after payouts. **Trailing on Instant** (floor rises with the highest closed-trade balance, capped at the start balance).
- Balance versus equity: the daily limit is *reference balance, breach on equity*; the max-loss basis is described only as a fixed floor. **Strictest reading used: both checked on live equity.**

### 1.3 Trading restrictions [1P]

- **20-second minimum hold** on every trade ("microscalping rule"); violations may end in termination.
- **Hedging banned**: no simultaneous long and short *in the same instrument*; cross-account and group hedging also banned.
- **Leverage fixed**: BTC/ETH 5:1 (2-Step, 1-Step); altcoins, indices, tokenized stocks 2:1; Instant Funding 2:1 on everything. Whether the cap binds per position or on the account total is **not stated [INFERRED: per position]**.
- **Costs**: commission **0.04% per trade**; swap/funding **0.033% of position value per day**. Whether "per trade" is per side or per round trip is not stated; **strictest reading: per side (8 bps round trip)**, the same reading our Breakout ruleset uses.
- **News trading allowed** with no restriction. **No weekend or overnight flat rule**: "You can hold positions as long as you want" (the *original position size must remain fully intact* overnight; partial closes do not count as extra trading days).
- **Bots**: allowed *if you own the bot*; shared bots and signal services are not. **VPN/VPS allowed**; the account must stay personal and Tradeify *may request verification if multiple IPs access it*.
- **Instruments**: "100+ cryptocurrency pairs including BTC, ETH, SOL, DOGE, XRP", about 30 tokenized stocks; PAXG named on the FAQ (5:1).
- Limits on accounts: no limit on evaluation accounts; funded accounts capped at **$300,000 combined**.

### 1.4 Payouts [1P, payouts page]

| | 2-Step funded | 1-Step funded | Instant |
|---|---|---|---|
| frequency | on demand | on demand | on demand |
| minimum | $100 | $100 | $100 |
| split | 80% | 80% | 80% |
| minimum profitable days | **3** (a profitable day is a trading day gaining 0.5% or more, resetting 22:00 UTC) | none fixed | 20% consistency, 5 in practice |
| payout lock | none, floor stays at start minus 6% | none | first payout permanently locks the floor |
| fee refund | not stated (N/A) | N/A | N/A |

No payout cap ("withdraw all available profits"); one open payout request at a time; bank transfer "1-2 business days" via Rise or Confirmo USDC.

### 1.5 Do the trading-day rules apply in the funded phase?

**Yes for the 2-Step, no for the 1-Step.** The 2-Step funded account still needs 3 profitable days (each at least +0.5% of account size) before its first payout; the 1-Step funded account has no minimum trading or profitable days ([payouts page]). The rules overview says only *"All restrictions continue post-evaluation on funded accounts"*, which covers the 20-second hold, hedging ban and loss limits. Whether the funded account starts at the starting balance or carries evaluation profit is **not stated on any page read**; the ruleset takes the strictest reading (fresh start).

### 1.6 Unreadable or unresolved

- No first-party page states a funded-phase daily-loss basis different from the evaluation, or whether the max-loss check uses equity.
- A dedicated 2-Step guide page was not found (the search index returned only the 1-Step guide); 2-Step figures come from the account-type, pricing and payouts pages.
- DXtrade API availability for retail: the DXTrade guide says *"DXTrade supports API access for automated trading. You can connect your own trading bots"*, points to `demo.dx.trade/developers`, and says Tradeify cannot support the API. Whether `dx.tradeify247.co/specs` is served and accepts our VM's IP is unverified.

## 2. The ruleset file and the simulator change

**`config/prop_rulesets/tradeify_247_1step.yaml`** (loads through `src.prop.ruleset.load_ruleset`; checked): $10k 1-Step, 12% target, 3%/6% static, reset 22:00 UTC, no minimum days, fee $120, 80% split, payouts daily with a $100 minimum, bank-as-soon-as-allowed, funded restart at the start balance, no fee refund, `unconfirmed: true`. Rules the schema cannot hold (20 s hold, no hedging, leverage caps, ownership, inactivity, costs) are in comments beside the fields.

**Strictest readings chosen where pages are silent or conflict:** equity for both limits; commission per side; funded account restarts fresh; fee never refunded; swap charged prorated by hold time (the dxtrade midnight-crossing model was also run, § 3.4); leverage cap per position.

**`scripts/research/prop_ev_sim.py`** (the existing B6 simulator; self-test passes) gained three things, all off by default so Breakout results are unchanged: a ruleset-declared daily reset (`limits.daily_loss_reset_utc`; default 00:30 UTC), a per-leg notional cap (`--lev-cap LEG=X`, scaling risk down so notional does not exceed X times balance), and a same-symbol opposite-direction entry guard (`--no-hedge`). **Not modelled:** the trailing Instant floor (the tool refuses non-static rulesets), the 2-Step's 3-profitable-day payout gate, and two-phase chaining (§ 3.5 approximates it).

## 3. The portfolio

### 3.1 Leg selection and the evidence each rests on

**Population.** Legs on BTC/ETH/SOL/XRP with a committed `comms/strategy_evidence/<leg>.json` record, `coverage_state: measured`, `fidelity: faithful`, positive `net_r_oos`. Window: 365 days, four time folds, about 2025-09-25 to 2026-09-24. All come from the `trend` and `ict_scalp` harnesses, whose records carry `cost_stack` fee 7.5 bps + slippage 3 bps + funding (the D1 note lists the harnesses lacking a full cost stack: `backtest_orb.py`, `src/backtest/backtester.py`, `backtest_xsec_momentum.py`, `backtest_vol_target.py`; none is used here). The simulator then **recomputes cost from `gross_r`, `entry` and `sl` with Tradeify's stack**, so the record's own net figure is not what is simulated.

| leg | symbol | n | net R (record) | net R after Tradeify costs | folds (net R) | net R ex best fold |
|---|---|---:|---:|---:|---|---:|
| `trend_donchian_eth_prop` (1h) | ETH | 169 | +13.61 | +13.03 | +4.8 / +13.3 / −3.2 / −1.3 | **+0.32** |
| `trend_donchian_sol_prop` (1h) | SOL | 65 | +5.19 | +4.97 | +1.4 / −0.6 / −1.9 / +6.2 | **−1.03** |
| `ict_scalp_xrp_15m` | XRP | 129 | +4.08 | +3.09 | +3.8 / +5.2 / +0.3 / −5.2 | **−1.09** |
| `ict_scalp_eth_15m` | ETH | 117 | +9.12 | +8.50 | +7.2 / +8.8 / −1.8 / −5.0 | +0.34 |
| `ict_scalp_sol_15m` | SOL | 132 | +4.86 | +4.24 | +5.3 / +2.8 / −0.1 / −3.1 | **−0.44** |

Every record carries `param_selection_provenance: not_established`. Mean cost per trade in R after the Tradeify stack is 0.065 to 0.150 against expectancy of 0.024 to 0.077 R: **costs eat 50-85% of the gross edge**, worst on `ict_scalp_xrp_15m`.

**BTC has no eligible leg.** Every BTC leg with a `measured, faithful` record is net-negative: `htf_pullback_trend_2h` −4.93 (n=86), `fade_breakout_4h` −16.09 (n=27), `fvg_range_15m` −9.70 (n=17), `ict_scalp_5m` −9.48 (n=193), `squeeze_breakout_4h` −0.40 (n=10). `trend_donchian` (BTC 1h) is +4.60 (n=73) but `fidelity: approximate`, so excluded. **Also excluded:** `trend_donchian_eth_4h` (+10.17, n=44; ex-best-fold **−4.25**, one fold carries all of it), `trend_donchian_xrp_4h` (n=16, underpowered), `sol_pullback_2h` (n=18), `xrp_pullback_2h` and `trend_donchian_eth` (`approximate`), `ict_scalp_*_5m` (negative or approximate).

**Books scored** (hedging ban means two legs on one symbol are only allowed when they do not hold opposite sides, enforced in the sim by `--no-hedge`):

- **B3** = `trend_donchian_eth_prop` + `trend_donchian_sol_prop` + `ict_scalp_xrp_15m` (one leg per symbol, so no hedging risk by construction).
- **B5** = B3 + `ict_scalp_eth_15m` + `ict_scalp_sol_15m` (two legs on ETH and on SOL; the guard blocked entries constantly).
- **BS** = the three 15m scalps only.

The requested 2-5 legs is met (3 and 5); BTC is absent for the reason above.

### 3.2 Sizing under the limits

Risk per trade is a fraction of the *current* balance. At 0.5% the median position notional is about 0.3 times balance, so the 5:1 and 2:1 caps rarely bind: 0 of 169, 0 of 65 for the two donchian legs and 3 of 129, 1 of 117, 1 of 132 for the scalp legs (stops as tight as 0.02% to 0.2%; the simulator scales those trades down). Peak simultaneous notional after the per-position caps, from the trade rows, is 2.4 times balance for B3 and 5.4 for B5; this exceeds 2:1 only if the cap is read as an *account-total* limit, which no page says **[INFERRED: per position]**. All 378 scalp and donchian minimum holds are at least 15 minutes, so the 20-second rule cannot bind.

Sweep of risk per trade (reduced-size Monte Carlo, 1000 lives + 30×100 bootstrap, seed 7, $10k, prorated swap):

| book | risk | path EV/life | path P(net>0) | `stop` bound EV/life | median days to pass |
|---|---:|---:|---:|---:|---:|
| B3 | 0.50% | +$851 | 0.77 | +$640 | 238 |
| B3 | 0.75% | +$521 | 0.58 | −$6 | 131 |
| B3 | 1.00% | +$205 | 0.38 | −$117 | 79 |
| B5 | 0.50% | +$976 | 0.70 | +$125 | 148 |
| B5 | 0.75% | +$299 | 0.45 | −$120 | 70 |
| BS | 0.50% | +$674 | 0.67 | +$481 | 231 |
| BS | 0.75% | +$472 | 0.54 | +$26 | 115 |
| BS | 1.00% | +$85 | 0.26 | −$87 | 54 |

The `stop` bound marks every open position at its stop for its whole life (the harness cannot see the intra-trade path), so it is the pessimistic end. **0.5% is the only risk at which the pessimistic bound is positive for B3.** B5 is not robust: its `stop` bound collapses in the full-size run (below).

### 3.3 Results at 0.5% risk, full-size Monte Carlo

Full defaults (4000 lives + 100×200 bootstrap), $10k 1-Step, prorated swap, 3 bps slippage, `--no-hedge`, per-position caps. Raw JSON beside this file in `tradeify-portfolio-2026-09-30/`.

| run | model | EV/life | evidence p5 / p95 | P(net>0) | P(pass eval) | median days to pass | payouts/life | EV per 365 d with re-buy |
|---|---|---:|---|---:|---:|---:|---:|---:|
| B3 seed 1 | path | +$828 | −$100 / +$1,901 | 0.77 | 0.88 | 242 | 6.8 | +$551 |
| B3 seed 1 | stop bound | +$637 | −$92 / +$1,571 | 0.69 | 0.83 | 238 | 5.4 | +$502 |
| B3 seed 2 | path | +$852 | −$98 / +$1,907 | 0.77 | 0.88 | 245 | 6.9 | +$563 |
| B3 seed 2 | stop bound | +$653 | −$107 / +$1,653 | 0.70 | 0.84 | 237 | 5.5 | +$507 |
| B5 seed 1 | path | +$957 | −$69 / +$2,241 | 0.72 | 0.83 | 144 | 7.6 | +$1,050 |
| B5 seed 1 | stop bound | +$110 | −$105 / +$581 | 0.31 | 0.44 | 132 | 1.6 | +$260 |

**Registered rule.** `RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2` (PASS if the 5th percentile of the evidence bootstrap under `path` is above zero) returns **INDETERMINATE** for B3 (both seeds) and B5: p5 is −$98 to −$100 (B3) and −$69 (B5). The point EV is positive on every run and on both bounds for B3; only B5's pessimistic bound is weak.

**Deaths.** In the full-size B3 runs every death is a static-drawdown breach: 369 evaluation and 1,951 funded (seed 1) against **zero** daily-loss deaths, over 4,000 + 10,000 simulated lives; at 0.5% the 3% daily rule never binds first. Population caveat: 42% of B3 path lives were still alive at the 730-day horizon and are scored at what they banked, which understates EV.

**Time to pass is long**: median 242-245 days on B3 (8 months), because the leg edge is 0.024 to 0.077 R per trade. That is the price of the 0.5% risk that keeps the book robust.

### 3.4 Stress: swap model, slippage, account size

| variant (B3, reduced MC, seed 7) | path EV/life | `stop` EV/life |
|---|---:|---:|
| 0.75%, prorated swap | +$521 | −$6 |
| 0.75%, dxtrade midnight-crossing swap | +$459 | +$0 |
| 0.75%, prorated swap, **10 bps** round-trip slippage | +$71 | −$103 |

Slippage on the Tradeify simulated book is unknown; **realized cost cannot be measured until an account exists**, and a simulated funded book gives simulated fills (same limitation the DXtrade memo names). The 10 bps case was run only at 0.75%, not at 0.5%; I have not measured how much 0.5% survives it.

**Account size** (B3, 0.5%, reduced MC, path model): EV scales with account size while the fee scales less, so the larger tiers pay more per purchase, subject to the $300,000 funded cap.

| tier | fee | path EV/life | evidence p5 / p95 | `stop` EV/life | EV per 365 d with re-buy |
|---|---:|---:|---|---:|---:|
| $10k | $120 | +$851 | −$90 / +$2,216 | +$640 | +$561 |
| $50k | $431 | +$4,096 | −$413 / +$8,518 | +$3,119 | +$2,825 |
| $100k | $838 | +$8,602 | −$618 / +$24,198 | +$6,181 | +$5,767 |

**Gross monthly payout arithmetic** (not a simulation): the three legs sum to about 21.1 R a year after Tradeify costs; at 0.5% of $50k that is about $5,270 a year, or about **$350 a month** at the 80% split, before any breach. Per $10k it is about $70 a month.

### 3.5 2-Step approximation

Not chainable in the tool; approximated by two independent single-phase runs on a fresh $10k (B3, 0.5%, path): P(pass) = 0.883 for the 10% phase and 0.914 for the 5% phase, so about **0.81** overall against 0.88 for the 1-Step, and median days to pass about 198 + 78 = 276 against 245. Add the 3-profitable-day payout gate. **The 1-Step dominates** for this book; the 2-Step is $20 cheaper at $10k and $81 cheaper at $50k.

## 4. Trade frequency and the worthwhileness test

### 4.1 Frequency, measured

**Population.** Committed trade rows, calendar months 2025-10 to 2026-08 (11 full months) of the window above. B3: monthly counts 26-37, **about 30 trades a month** (range 26 to 37; about 5.5 for `trend_donchian_sol_prop`, about 14 for `trend_donchian_eth_prop`, about 11 for `ict_scalp_xrp_15m`). B5: about 51 a month. **The portfolio does not trade once or twice a month**, and the 30-day inactivity rule is not a risk.

### 4.2 Test, stated before the verdict

Fixed in this section, using the repo's own bar shape (`RULE-TPL-PROP-FIT-B6V2`). **Honest provenance:** I set these thresholds after the reduced-size exploration and before reading the full-size runs; the registered V2 rule is the strict test and it is INDETERMINATE, so this is a weaker, declared test on top of it.

- **W1, frequency:** at least 8 portfolio trades a month. B3: **pass** (about 30).
- **W2, expected value:** mean net EV per account life above zero on both the `path` model and the `stop` bound, on every seed run, and `path` P(net>0) at least 0.70. B3: **pass on point EV** (all four values positive); `path` P(net>0) 0.77 (**pass**); `stop`-bound P(net>0) is 0.69 and 0.70, under 0.70 on one seed. B5: fails (`stop` bound EV +$110 but P(net>0) 0.31).
- **W3, economics:** mean EV per life at least 3 times the account fee. B3 at $10k: +$840 against $120 (7 times), at $50k about 9.5 times: **pass**.

**Result: B3 at 0.5% risk passes W1 and W3, and passes W2 on point EV but sits on the 0.70 line for the pessimistic bound. The registered V2 rule is INDETERMINATE.** Hence marginal, not yes.

### 4.3 Against the other routes

**Breakout local-agent route.** Same tool, same trade files, Breakout's ruleset ($5k, $45, first payout after 14 days then weekly), room sizing k=0.33 as the next Breakout instance would use, reduced MC:

| route (path model) | EV/life | fee | EV per 365 d with re-buy | `stop` EV/life | P(net>0) |
|---|---:|---:|---:|---:|---:|
| Breakout, its two live legs (`trend_donchian_sol_prop` + `_eth_prop`) | +$103 | $45 | +$52 | +$23 | 0.30 |
| Breakout, B3 | +$78 | $45 | +$52 | +$45 | 0.29 |
| Tradeify $10k, B3, 0.5% | +$851 | $120 | +$561 | +$640 | 0.77 |
| Tradeify $50k, B3, 0.5% | +$4,096 | $431 | +$2,825 | +$3,119 | 0.77 |

Breakout's actual live sizing (flat 1.5%, $75 a ticket) is −$10 per life on `path` for its own book ([`b6-prop-ev/breakout_1-2026-09-27.json`](b6-prop-ev/breakout_1-2026-09-27.json)). **Per account, Tradeify is about ten times Breakout in EV.** Reasons visible in the rules: 80% split versus 80% but a 14-day wait then weekly, no consistency or minimum-day gate, payouts on demand, and 12% target on a 6% static floor. Reasons for caution: the $50k comparison rests on a simulated book whose fills we cannot check, and the Breakout number is depressed by room sizing (k=0.33) that trades risk for survival.

**HyroTrader route.** Lane HYRO-STRATS owns it; only the rule facts from [`hyrotrader-bybit-deep-dive-2026-09-30.md`](hyrotrader-bybit-deep-dive-2026-09-30.md) are used: its evaluation is a Bybit **demo**, 4%/5-6% limits, 10% target, and a consistency rule of 40% per day in Phases 1-2. No EV is simulated here for it.

**Build cost.** Breakout local agent: about 2.5-3 lane-days (the manager's figure from `breakout-local-agent-requirements-2026-09-30.md`). Tradeify: § 5.

## 5. Reuse and build effort

From the PROP-DXTRADE-FIRMS memo § 3 (read from the memo; this lane did not re-read the adapter code beyond noting that `prop_executor.py` is hardwired):

| piece | carries over? |
|---|---|
| Table parsers, login classifier, feasibility errors, order-ticket form logic, six-field read-back, bracket verify, close/cancel, response capture | **Yes**, generic Devexperts vocabulary; selectors were measured only on Breakout's host |
| Login selectors, login URL, symbols, lot table | **No**, per host (`dx.tradeify247.co`); `load_platform_config` rejects a login URL equal to the other platform's default, so an explicit URL is required |
| Executor, ruleset and routing paths, `account_id`, $75 risk cap, `enabled_venue_symbols: [SOLUSD]` | **No**, Breakout-hardwired; **symbol switching is not built** and this book needs three symbols |
| Shared layer (ticket intake, reconcile, guards, write-back, kill switch) | **Yes** |

New Tradeify-specific work that this lane's numbers add: a same-symbol **no-hedge guard** across legs, **per-position leverage sizing** (5:1 / 2:1), the **22:00 UTC daily reset** in the risk gate, and 0.5% risk sizing. All are small next to the adapter.

**Lane-day estimate** (the DXtrade memo's figures, kept; **[INFERRED]**, never measured):

- **With a working DXtrade API:** 3-5 lane-days after a green probe (new `dxtrade_api` client behind `PropPlatformAdapter`; multi-symbol orders come free from the API).
- **Without it (browser only):** 4-7 lane-days, most of it the measurement loop; symbol switching for three instruments adds to that.

## 6. Operator decision

The bottom line is **marginal**. Options, cheapest first:

| option | what | cost | what it buys |
|---|---|---|---|
| **A. Do nothing now** | Keep the Breakout local-agent route (2.5-3 lane-days) | $0 now | The known small EV (+$52 a year per account) |
| **B. Probe first, buy nothing** (recommended first step) | One read-only lane: fetch `https://dx.tradeify247.co/specs` and the login endpoint from our VM egress; report reachable / blocked | about 0.5 lane-day, $0 | Settles the one fact that decides build cost (API or browser) and whether Cloudflare blocks our IP, before any spend |
| **C. Buy one $50k 1-Step ($431) and build** | After a green B: 3-5 lane-days (API) or 4-7 (browser), run B3 at 0.5% risk, bank at every payout | $431 at risk plus build | Simulated +$4.1k per life, `stop` bound +$3.1k, about $350 a month gross once funded, after roughly 8 months to pass. **P(net>0) 0.77; the evidence interval includes a loss of the fee.** |
| **D. Buy the $10k ($120) as a pilot** | Same, smaller | $120 at risk | Same shape at one-fifth the payout; tests fills and the API for a fee under the cost of one lane-day |

Recommendation (mine, not a decision): **B, then D** if the probe is green. D limits the downside to $120 and produces the one thing this document cannot: realized fills and cost on Tradeify's simulated book. The scale-up to C is data-backed only after that.

## 7. What is not established

- **One year of history** (2025-09 to 2026-09); three of five legs lose their sign without their best fold; `param_selection_provenance` is `not_established` for all.
- **Simulated fills on the firm's side** are unknown; 10 bps slippage cut a 0.75% run from +$521 to +$71.
- **First-party summaries only**, not page text; several rule details (per-side commission, per-position leverage, funded start balance, equity basis for max loss) are stated as the strictest reading, not as confirmed.
- **No probe of the API, the login, or the platform's real symbol names**; `ETHUSD`-style naming may differ.
- The 3-Step promotional plan ($47 for $247,000) is documented on a first-party page as sold only in August 2026 and "may be withdrawn"; not evaluated.
- **The 2-Step is approximated**, the Instant Funding trailing floor is not modelled.
- No consistency or profitable-day gate is modelled for the 1-Step because none exists; the 2-Step gate is not modelled.

## Reproduce

```
python3 scripts/research/prop_ev_sim.py --self-test
L=comms/strategy_evidence/runs/2026-09-25   # per-leg dir differs; the latest __trades.jsonl per leg was used
python3 scripts/research/prop_ev_sim.py \
  --trades trend_donchian_eth_prop=<run>/trend_donchian_eth_prop__trades.jsonl --lev-cap trend_donchian_eth_prop=5 \
  --trades trend_donchian_sol_prop=<run>/trend_donchian_sol_prop__trades.jsonl --lev-cap trend_donchian_sol_prop=2 \
  --trades ict_scalp_xrp_15m=<run>/ict_scalp_xrp_15m__trades.jsonl --lev-cap ict_scalp_xrp_15m=2 \
  --ruleset config/prop_rulesets/tradeify_247_1step.yaml --risk-pct 0.005 --swap-model prorated \
  --no-hedge --modes path,stop --lives 4000 --outer 100 --lives-per-outer 200 --seed 1
```

Result JSONs: `tradeify-portfolio-2026-09-30/` (`FULL_*` full-size; `B3/B5/BS_r*` risk sweep; `SZ_*` sizes and 2-Step phases; `BRK_*` Breakout comparators; each holds its config and per-leg inputs). Trade files: the newest `comms/strategy_evidence/runs/*/<leg>__trades.jsonl` per leg.
