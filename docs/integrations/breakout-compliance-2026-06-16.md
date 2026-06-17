# Breakout prop-firm rules — compliance deep-dive (2026-06-16)

> Tier-1 research. Source: the Breakout FAQ Center
> (`intercom.help/breakoutprop/en`) + the 1-Step Classic plan card, read
> 2026-06-16. Re-verify before funding — prop firms change terms. This doc
> backs `config/prop_rulesets/breakout.yaml` and the bridge design
> (`metacopier-bridge-DESIGN.md`).

## TL;DR

- **Account targeted:** 1-Step Classic — one-phase eval, **10% target / 3% max
  daily loss / 6% static max drawdown**, $45, 80/20 split.
- **Good news for our roster:** Breakout has **no consistency rule, no profit
  cap, no risk-per-trade rule, no minimum trading days, no time limit, no
  weekend/overnight flat rule.** Our 15m–4h ICT strategies fit comfortably.
- **The one hard compliance question is the BRIDGE, not the strategy.**
  Breakout **permits algorithmic trading**, but its prohibited-practices list
  bans "copy trading across users," "third-party / off-the-shelf approaches
  marketed to pass evaluation," and "account sharing incl. sharing
  credentials." A third-party copier (MetaCopier) that holds the Breakout
  login and mirrors an external account sits in a **gray-to-red zone**. This
  must be confirmed with Breakout support **before** we wire it. **Gate-zero.**

## 1. The numbers (1-Step Classic) — CONFIRMED

| Rule | Value | Notes |
|---|---|---|
| Profit target | **10%** | $10k on a $100k account; one phase clears the eval |
| Max daily loss | **3%** | 3% across all 1-Step variants (Classic/Pro/Turbo) |
| Max drawdown | **6%, STATIC** | balance-based, off the **starting** balance; not trailing, not locked/raised on payout |
| Phases | **1** | single step |
| Min trading days | **none** | can pass in a single trade, paid next day |
| Time limit | **none** | evaluation is not time-boxed |
| Consistency rule | **none** | also: no profit cap, no per-trade risk cap, no anti-gambling rule |
| Profit split | **80/20** | upgradeable to 90/10 (+$9) |

**Breach mechanics (CONFIRMED):**
- **Daily loss:** the limit is recomputed each day at **00:30 UTC** from the
  **balance** at that time (excluding open positions); a breach fires when
  **intraday equity (including open positions)** reaches/exceeds the limit.
- **Max drawdown:** static 6% off the starting balance, balance-based, always
  in force.
- **Both run simultaneously — hitting EITHER** → all positions auto-closed,
  **account permanently disabled.**

Variant comparison (for reference): max DD is **6% Classic / 5% Pro / 3%
Turbo**; daily loss is **3% on all three**; all one-phase.

## 2. Prohibited practices (CONFIRMED, quoted)

From "What trading practices are prohibited during the Breakout Evaluation?":

1. Exploiting errors or latency in pricing/platform
2. Non-public / insider information
3. Front-running trades placed elsewhere
4. Trading that jeopardizes Breakout's relationship with a centralized exchange
5. Trading that creates regulatory issues for Breakout
6. **"Utilizing any third-party approach, off-the-shelf approach or one
   marketed to pass evaluation"**
7. Using one approach to pass and a different one after
8. Arbitraging your demo account against another account
9. **"Using trading approaches that are difficult to replicate in the live
   market"**
10. **"Executing trade ideas belonging to or inspired by any third-party,
    including copying other traders'"**
11. **"Account sharing of any nature, including sharing credentials or trading
    multiple accounts"**
12. Hedging across accounts (opposing positions on same/correlated assets)

From "Rules for hedging and copy trading": **"Copying trades between different
users' accounts is also prohibited."** Hedging *within a single account* is
allowed (Hedge Mode on by default); **cross-account hedging is banned.**

## 3. How OUR system maps to each rule

| Breakout rule | Our system | Verdict |
|---|---|---|
| 10% target / 3% daily / 6% static DD | Enforced pre-trade by `PropRiskManager` seeded from `breakout.yaml`; pre-screened offline by the evaluation tool | ✅ compliant by construction |
| No consistency / profit cap / min days / time limit | Nothing to do — our roster has no behaviour these would catch | ✅ no constraint |
| Latency/arbitrage/front-running/HFT (items 1, 3) | We trade 15m–4h ICT structure on candle closes — not latency/HFT, no arbitrage | ✅ clearly compliant |
| "Difficult to replicate" (item 9) | Bar-close ICT entries with ATR stops — trivially replicable | ✅ compliant (a copier is actually *easy* to replicate) |
| "Copying other traders' / third-party trade ideas" (item 10) | Our **own** proprietary strategy, not a third party's signals | ✅ not third-party ideas |
| **"Third-party / off-the-shelf approach marketed to pass evaluation" (item 6)** | **MetaCopier is a third-party tool explicitly marketed for passing prop evals (so is Copygram).** The *approach* is ours, but the *tool* may be read as prohibited | ⚠️ **gray — confirm** |
| **"Copy trading across users' accounts"** | Our copy is **self → self** (same owner: bybit_1 → our Breakout account), not cross-user | ✅ *if* "user" = account holder; ⚠️ confirm a copier is OK at all |
| **"Account sharing incl. sharing credentials" (item 11)** | A copier requires giving **MetaCopier our Breakout login/API** so it can place orders — arguably "sharing credentials" with a third party | ⚠️ **gray — confirm** |
| Cross-account hedging (item 12) | We run ONE Breakout account; the netting layer prevents opposing positions; no second prop account | ✅ compliant |
| Automation-detection red flags (identical sizes, microsecond/exact-timestamp entries, zero SL variation) | ATR-based sizing + per-setup stops on 15m–4h bars → natural variation, human-scale cadence | ✅ low flag risk |

## 4. The gate-zero finding (read this before funding)

**Breakout allows algorithmic/automated trading in principle, but provides no
documented public API (as of early 2026).** That leaves three automation paths,
each with a catch:

1. **Direct bot → Breakout Terminal** — no official API; only fragile,
   unofficial browser automation. Unsupported.
2. **Third-party copier (MetaCopier / Copygram)** — what we planned. Works
   technically and is widely marketed for Breakout, **but** collides with
   prohibited-practices items 6 (third-party tool marketed to pass evals) and
   11 (handing credentials to a third party). The copy itself is self→self
   (not the banned cross-*user* copy), so it's not obviously prohibited — but
   it's not obviously permitted either.
3. **No automation** — trade the Breakout account manually. Defeats the purpose.

**Therefore the single most important question for Breakout support is:**

> "Is it permitted to run my **own** algorithmic strategy on my 1-Step account
> by **self-copying my own trades from my own exchange account via a
> trade-copier service (e.g. MetaCopier)**? Does connecting such a service to
> my account count as prohibited 'account sharing' or a prohibited
> 'third-party/off-the-shelf approach'?"

Get the answer **in writing** before we connect anything. If they say no to
copiers, the fallback is to re-evaluate direct Terminal automation or shelve
the live integration — while the **evaluation tool still delivers full value**
(it tells us which strategies *could* survive any prop ruleset, no integration
needed).

## 5. Tuning implications for our risk model

- **Static 6% DD is the binding constraint.** Because it never ratchets up with
  profit, an early loss is disproportionately dangerous. The `PropRiskManager`
  must treat the 6% floor as an absolute equity stop off the starting balance,
  and position sizing should be conservative early (most account deaths happen
  before any profit cushion exists).
- **Daily loss is equity-based intraday** off the 00:30-UTC balance — our gate
  must consider open-position mark-to-market, not just realized PnL, when
  deciding whether a new entry could breach the day.
- **No consistency rule** means we do **not** need to throttle a hot day — a
  simplification vs many firms. Reflected in `breakout.yaml` (`consistency.enabled:
  false`).
- **Per-symbol position caps** exist in the Terminal (breakoutprop.com/symbols);
  pull the BTC cap at wire time and set `limits.max_position_pct` accordingly.

## Sources
- [Breakout FAQ — Account Rules collection](https://intercom.help/breakoutprop/en/collections/13572090-account-rules)
- [Maximum daily loss](https://intercom.help/breakoutprop/en/articles/10340635-what-is-the-maximum-daily-loss-in-the-breakout-evaluation)
- [Evaluation types & differences](https://intercom.help/breakoutprop/en/articles/11643732-what-types-of-breakout-evaluations-may-i-purchase-and-what-are-the-difference-s-between-the-types-of-breakout-evaluations)
- [Prohibited trading practices](https://intercom.help/breakoutprop/en/articles/11644090-what-trading-practices-are-prohibited-during-the-breakout-evaluation)
- [Hedging & copy trading rules](https://intercom.help/breakoutprop/en/articles/11644103-what-are-the-rules-for-hedging-and-copy-trading)
- [Position size limits](https://intercom.help/breakoutprop/en/articles/11644099-what-are-the-position-size-limits)
- [What constitutes a breach](https://intercom.help/breakoutprop/en/articles/11644085-what-constitutes-a-breach-in-the-breakout-evaluation)
