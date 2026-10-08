# Breakout Prop — what its published text says about automation, multiple accounts and copy trading (read 2026-10-08)

> **Doc status:** `live` · category `lookup` · last verified `2026-10-08` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

> **Lane BREAKOUT-TERMS-READ** (manager session_01MM8o5js6TcDFeNAPBY4Ntv). Tier-1, read-only. Answers one question:
> what do Breakout's own published terms say about automated access, order placement, UI automation, login
> verification, multiple accounts per device/IP and copy trading. Quotes are verbatim; verdicts follow the quoted text only.

## 0. Read this first — what was and was NOT read

| Source | URL | Result | UTC |
|---|---|---|---|
| FAQ Center (Intercom), all 5 collections, **108 articles crawled** | `https://intercom.help/breakoutprop/en/` | **READ** (HTTP 200) | 2026-10-08T00:23:16Z → 00:25:09Z |
| Prohibited practices — Evaluation | `…/articles/11644090-what-trading-practices-are-prohibited-during-the-breakout-evaluation` | **READ** | 2026-10-08T00:23:08Z |
| Hedging and copy trading — Evaluation | `…/articles/11644103-what-are-the-rules-for-hedging-and-copy-trading` | **READ** | 2026-10-08T00:23:09Z |
| Prohibited practices — Funded | `…/articles/11647201-what-trading-practices-are-prohibited-in-my-funded-account` | **READ** | within the crawl window above |
| Hedging and copy trading — Funded | `…/articles/11647220-what-are-the-rules-for-hedging-and-copy-trading` | **READ** | within the crawl window above |
| Home page | `https://www.breakoutprop.com/` | **NOT READ — HTTP 403** | 2026-10-08T00:23:02Z |
| Program Rules | `https://www.breakoutprop.com/program-rules/` | **NOT READ — HTTP 403** | 00:25:38Z (curl), and WebFetch |
| Breakout Evaluation Agreement | `https://www.breakoutprop.com/breakout-evaluation-agreement/` | **NOT READ — HTTP 403** | WebFetch, ~00:26Z |
| Terms of Use | `https://www.breakoutprop.com/terms-of-use/` | **NOT READ — HTTP 403** | WebFetch, ~00:26Z |
| Terms and Conditions (footer) / Funded Trader Agreement | `https://breakoutprop.com/terms`, `…/terms-and-conditions`, `…/legal` | **NOT READ — HTTP 403** | 00:23:03Z–00:25:22Z |
| Portal sign-in | `https://portal.breakoutprop.com/sign-in` | **NOT READ — HTTP 403** | 00:25:21Z |

Every `breakoutprop.com` host answered with `cf-mitigated: challenge` (a Cloudflare "Just a moment…" interstitial). I did
not try to defeat it. **The binding documents — Terms of Use, Evaluation Agreement, Program Rules, Funded Trader
Agreement — are therefore unread.** The FAQ pages below are Breakout's own published text but are summaries, not the
contracts. Every "SILENT" in § 3 means *silent in the FAQ Center*, not silent in the agreements. Search-engine snippets
say the Evaluation Agreement points at `breakoutprop.com/terms-of-use/` and that the funded relationship is with Payward
Oceanic Ltd (POL); those are unverified second-hand and are not relied on for any verdict.

Searches of the 108 FAQ articles for `bot`, `automated`, `automation`, `algorithm`, `expert advisor`, `API`, `scrape`,
`two-factor`, `2FA`, `magic link`, `one-time`, `verification`, `device`, `IP` hit only in the articles quoted above plus
login-credential articles (11644061, 11644121, 11647187, 14215682) and a KYC article (11647237, SumSub). None of those
mentions automation, login verification or devices. (Probe check: the same grep finds
"device" and "IP" in 11644090, so it can find a positive.)

## 1. Verbatim clauses

**A. Evaluation — prohibited practices** (article 11644090, dated "September 3, 2026").
Retrieved 2026-10-08T00:23:08Z from `https://intercom.help/breakoutprop/en/articles/11644090-what-trading-practices-are-prohibited-during-the-breakout-evaluation`:

> The following are prohibited during a Breakout Evaluation:
> - Exploiting errors or latency in pricing or the platform
> - Using non-public or insider information
> - Front-running trades placed elsewhere
> - Trading in a way that jeopardizes Breakout's relationship with an exchange or market maker
> - Trading in a way that creates regulatory issues for Breakout, an exchange, or a market maker
> - Using a third-party or off-the-shelf approach marketed specifically to pass evaluations
> - Using one approach to pass the evaluation, then switching approaches once funded
> - Attempting to arbitrage your demo account against another Breakout or third-party account
> - Using strategies that are difficult to replicate in live markets or carry outsized risk when replicated (for example, trades that would trigger auto-deleveraging or produce exceedingly large swings in unrealized P&L)
> - Executing trade ideas copied from a third party, including signals, communities, social media, or research reports
> - **Sharing account access, or trading multiple accounts from the same household, device, or IP address**
> - Hedging across accounts, including opposing positions on the same or correlated assets across evaluation and funded accounts, or coordinating with another trader. See What are the rules for hedging and copy trading? for details

**B. Funded — prohibited practices** (article 11647201, "September 3, 2026").
Retrieved within 2026-10-08T00:23:16Z–00:25:09Z from `https://intercom.help/breakoutprop/en/articles/11647201-what-trading-practices-are-prohibited-in-my-funded-account`:

> The following are prohibited in your funded account:
> Using any trading or order-entry method expressly prohibited by a liquidity provider.
> Exploiting pricing or latency errors on the Breakout Terminal or a liquidity provider's platform.
> Trading on material non-public information.
> Front-running.
> Trading in a way that, in Payward Oceanic, Ltd (POL)'s sole discretion, jeopardizes its relationship with an exchange or market maker, or creates regulatory issues for POL, an exchange, or a market maker.
> Using a third-party or off-the-shelf strategy marketed to achieve capital appreciation in a funded account.
> Using one trading approach to pass your evaluation and a different one in your funded account.
> Using strategies that are difficult to execute or carry heightened risk (including trades likely to be auto-deleveraged, or those causing outsized swings in unrealized P&L).
> Copying trade ideas from third parties, including other traders, analysts, social media, research reports, or crowdsourced signals.
> Hedging across accounts, including between the evaluation and funded stage, or between two or more traders. See: What are the rules for hedging and copy trading?
> Violations are assessed at Breakout's discretion and can result in account termination.

⚠️ **The funded list does NOT contain the household/device/IP or "sharing account access" line** that the evaluation list (A) contains. Whether the agreements carry it for funded accounts is unread.

**C. Evaluation — hedging and copy trading** (article 11644103, "September 3, 2026").
Retrieved 2026-10-08T00:23:09Z from `https://intercom.help/breakoutprop/en/articles/11644103-what-are-the-rules-for-hedging-and-copy-trading`:

> Hedging within a single account is allowed. Hedge Mode is on by default for both evaluation and funded accounts, so you can hold a long and a short position on the same asset in the same account at once.
> What's not allowed:
> Cross-account hedging: opposite positions on the same or closely correlated assets across two or more accounts (any combination of evaluation and funded), regardless of whether the accounts share a name, email, or identity
> Cross-trader hedging: coordinating with another trader to take opposite sides of the same or correlated assets across your respective accounts, including via shared signals, strategies, or timing
> **Copy trading across users: copying trades between different traders' accounts**
> Breakout monitors evaluation and funded trading activity and reviews timing, entry prices, notional size, and exposure patterns to identify prohibited hedging. Determinations are made at Breakout's sole discretion.
> A confirmed violation results in immediate termination of all associated accounts, forfeiture of accrued profits, and permanent suspension. A first-time inadvertent violation may receive a warning at Breakout's discretion; deliberate or repeated violations will not.

**D. Funded — hedging and copy trading** (article 11647220, "September 3, 2026").
Retrieved within 2026-10-08T00:23:16Z–00:25:09Z from `https://intercom.help/breakoutprop/en/articles/11647220-what-are-the-rules-for-hedging-and-copy-trading`. Same three prohibitions; the wording that differs:

> Cross-account hedging - opposite positions on the same or closely correlated assets across two or more accounts (any combination of evaluation and funded), regardless of whether the accounts share the same name, email, or identity.
> Cross-trader hedging - coordinating with another trader to take opposite sides of the same or correlated assets across your respective accounts, including sharing signals, strategies, or timing to engineer a guaranteed profit.
> **Copy trading across users - copying trades between different users' accounts.**

**E. Multiple evaluations** (article 11644097, "September 3, 2026"; retrieved in the same crawl):

> Yes, you can purchase multiple evaluations.
> You may not hedge across evaluations or funded accounts, though.

**F. Terminal access** (article 14215682, "March 25, 2026"; same crawl):

> To access the Breakout terminal, log in to your Breakout Dashboard and click the "Open Terminal" button. This will launch the Breakout terminal for your account.

**G. Logins** (articles 11644121 and 11644061; same crawl) — "Your login for a new Breakout Evaluation matches the login from your previous purchase, as long as you use the same email." / "Log in with the email you used to purchase your evaluation; your password is in the confirmation email from Breakout." Nothing in the FAQ Center describes the emailed number-match step, session length, or any rule about reading that email by software.

## 2. What clause A forbids — device / IP / household (for the operator's judgement)

Verbatim: *"Sharing account access, or trading multiple accounts from the same household, device, or IP address"*. Read
literally, this is a single bullet with two limbs joined by "or":

1. **Sharing account access** — letting someone else in. Not the same thing as one person holding two accounts.
2. **Trading multiple accounts from the same household, device, or IP address** — each of *household*, *device* and *IP
   address* is listed separately, so **any one** is enough on its face. It is **not** limited to different people: it does
   not say "different traders" the way the copy-trading clause does ("different traders' accounts"), and it says nothing
   about whether the accounts are yours. So one person with two evaluations logged in from one phone, one home network
   or one VM IP is, on the text, inside it. Nothing in the text carves out sequential use (one account at a time) or
   distinguishes "logged in" from "trading". It also cannot be said to apply only to accounts run concurrently.
3. **Where it appears:** the *evaluation* list only (A). The *funded* list (B) omits it. Whether the agreements repeat it
   for funded accounts is **unread**, so a funded-stage reading cannot be given from the quoted text.
4. **Enforcement text:** the hedging article says violations can mean *"immediate termination of all associated
   accounts, forfeiture of accrued profits, and permanent suspension"*. That sentence sits in the hedging/copy-trading
   article (C), not under the device/IP bullet; the FAQ gives no stated penalty for the device/IP bullet specifically.

Applied to the operator's setup (facts as given in the dispatch, not verified here): `breakout_2` on one phone alone is
one account on one device. `breakout_1` previously ran through a VM browser executor — a *different* device and IP — so
the two were never on one device or IP **if** the VM egress IP differs from the phone's network, which this memo did not
check. The household limb is the one that is hard to rule out for any two accounts of one person. Whether `breakout_1`
being dead/retired matters is a question for Breakout, not for the text.

## 3. Grades — per the quoted text only

| # | Behaviour | Verdict | Basis |
|---|---|---|---|
| a | UI / screen automation of the web terminal | **SILENT** (FAQ Center) — agreements unread | No quoted clause mentions the terminal UI, scripting, WebView or automation. Closest: "Using any trading or order-entry method expressly prohibited by a liquidity provider" (B), which depends on a liquidity-provider list that is not in the text. |
| b | Automated order submission | **SILENT** (FAQ Center) — agreements unread | No clause mentions bots, algorithms or EAs. Clauses A/B ban *third-party/off-the-shelf* approaches "marketed specifically to pass evaluations", and copying *third-party* trade ideas; neither speaks to the account holder's own algorithm. |
| c | Automatic re-login via an emailed number-match read from a dedicated inbox | **SILENT** (FAQ Center) — agreements and sign-in page unread | No FAQ article describes the number-match step or says who may perform it. The nearest text is "Sharing account access" (A) — the inbox is the operator's own, so the clause does not obviously apply, but the clause is not about this. |
| d | Multiple accounts / logins per device or IP | **PROHIBITED — evaluation accounts** (A). **Not stated for funded** (B omits it). | "trading multiple accounts from the same household, device, or IP address". See § 2. |
| e | Copy trading / mirrored trades across accounts | **PROHIBITED across users** (C, D, A, B). **Mirroring between one person's own accounts: SILENT on the copy limb**, but see below. | "copying trades between different traders' accounts" / "different users' accounts". Own-to-own copying is not mentioned. Two things bite anyway: clause A (same device/IP/household), and **cross-account hedging**, which is prohibited regardless of "name, email, or identity" — mirrored accounts that take *the same* side do not trip it; any opposing/correlated-offset positions across them do. |

## 4. Question for the operator (for the PROHIBITED rows)

**Single question:** *"Breakout's FAQ says, for evaluations, that trading 'multiple accounts from the same household,
device, or IP address' is prohibited, and that copying trades between different traders' accounts is prohibited. The
agreements themselves could not be read (Cloudflare 403). Do you want to (i) accept the risk as you did for the
automation terms on 2026-10-05, (ii) paste the Evaluation Agreement / Program Rules / Terms of Use text so a session can
quote it, or (iii) ask Breakout support in writing whether one person may hold two accounts on one household network and
whether software may complete the email number-match?"* Any answer to (iii) is a first-party statement and is worth more
than any inference here. (i) is already recorded for the automation terms (pipeline `PI-20261005-9HEP9LYP-0003`, killed
2026-10-05); this memo does not cover the device/IP row.

## 5. Not established

- The content of the Terms of Use, Evaluation Agreement, Program Rules, Funded Trader Agreement and portal sign-in page.
- Whether FAQ and agreements agree. Where they differ, the agreements very likely control; that is a general
  expectation, not something read here.
- Any mention of session length, remember-me, authenticator-app 2FA or device trust (the FAQ is silent on all of them).
- Whether `breakout_1` and `breakout_2` share a household or IP.
