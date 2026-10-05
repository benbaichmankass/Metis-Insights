# Breakout: are there structural blockers to automated execution? — lane BREAKOUT-BLOCKERS, 2026-10-05

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-05` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> Read-only analysis. No orders, no terminal clicks, no config edits, no checklist edit. Evidence: `/api/bot/prop/fills` and `/tickets` (limit 60, read 2026-10-05), executor journals via the diag relay (breakout_1 unit 2026-10-04 03:30–10:15Z; tradeify_1 unit 2026-10-05 04:13–06:43Z), code and git history on main `c9436d58`, and one first-party page re-read today.

## Verdict

**Structural blocker: a replacement Breakout account will NOT be on the DXtrade terminal our executor drives.**

Breakout's own FAQ (re-read today, [article 14215629](https://intercom.help/breakoutprop/en/articles/14215629-does-breakout-offer-two-separate-trading-terminals)): *"Breakout no longer offers new purchases on the DXTrade terminal - only the Breakout terminal is available for new evaluations and funded accounts."* Existing DXtrade accounts keep their terminal. So the operator's re-buy lands on Breakout's proprietary terminal (dashboard → "Open Terminal"), not DXtrade.

On that terminal the open facts are all unfavourable (from `docs/research/breakout-term-access-2026-10-04.md`, not re-measured today):
- `app.breakoutprop.com` served an interactive Cloudflare Turnstile challenge on the first response to **4 egress networks under 6 browser configurations**, including our Oracle VM (ASN ban, Error 1005). The operator's home Wi-Fi and phone data are served. It is a network-class decision, not a fingerprint one. VM route closed (PROP-TERM blocked; do not retry).
- The `breakout_terminal` adapter was built against an **unmeasured** DOM. No account has ever run on it, so nothing is live-verified.
- The Funded Trader / Evaluation Agreements were never read; a hardened-browser route may meet an automation or "artificial means" clause.

**Not a blocker (DXtrade, venue-side):** nothing found. The DXtrade terminal itself does not block automation: the same executor code on the same VM and egress completed an automated round trip on tradeify_1 (below), and on breakout_1 itself placed one strategy trade (2026-10-02) plus several test trades. Every DXtrade failure class below is a code-side or account-state issue.

So: if the operator wants an automatable Breakout account, the question is not "does DXtrade block us" (no) but "can we automate the proprietary terminal from a consumer-class address" (unanswered, needs a live account plus a human-cleared challenge). Buying one before that is a bet on the phone-executor route (PHONE-EXEC-DESIGN, paused), not on the current executor.

## Per-failure-class table (breakout_1, DXtrade)

| # | Failure class | Instances (fills) | Class | Evidence and action |
|---|---|---|---|---|
| 1 | `form fields not found: ['price']` | #73, #74 (2026-09-30, SOL) | **FIXED** | Form opens in MARKET with no price input; code required price before clicking LIMIT. Fixed by #14737 (`e76e4902`). Observed: ETH LIMIT placed with price typed 2026-10-02 (#78/#79). |
| 2 | `submit: not visible at its centre` (bare) | #76, #77 (2026-10-02 04:44, 05:15, ETH) | **FIXED (diagnosis) / same cause as 3** | Geometry was never the problem: identical submit box passed the dry walk at 06:14Z. Polling plus naming added by #15468 (`5acb8bb7`). Cause identified under 3. |
| 3 | Submit "paints footer … DISABLED … pointer-events:none" | #81 (2026-10-04 00:55, ETH) | **FIXED on main (#16332, `50e83f90`), not observed on breakout_1** | MEASURED on tradeify_1 (same terminal engine): DXtrade disables the submit for a buy LIMIT at/above the ask, footer text "Entry Price you set must be lower than …" (#16311/#16313). #16332 retries in band (`awaiting_resting_price`). The breakout 00:55Z signature matches (disabled, pointer-events none, footer occluder); the footer text was not captured then (#16220 added capture afterwards). INFERRED same cause. A DXtrade rule, not a Breakout block. |
| 4 | `unconfirmed_submit` | #71, #72 (2026-09-29) | **FIXED** | Not real submit failures: stale test-close ledger rows read as unconfirmed submits and tripped the AUTO-REVERT latch (PI-20260929-AQRK6CL1-0009, done; close paths now record `close_unconfirmed`, tests `tests/test_prop_executor.py:3032,3047`). |
| 5 | `unconfirmed_submit` | #82 (2026-10-04 03:40–03:49, SOL) | **FIXABLE** (small) | Real. Journal: submit clicked at 03:40:28 for a LIMIT buy 65.78 @ 120.91 with ask 120.90, i.e. limit above the ask, then `not_found` on 3 re-reads, then latch. The submit was enabled at the check, so #16332 (which reacts to a DISABLED submit) does not cover it. Fix: a pre-submit guard that a long's limit is strictly below the ask (short: above the bid), reusing the `awaiting_resting_price` wait; ~30 lines in `prop_executor.py` plus tests. INFERRED mechanism (the terminal drops a marketable limit); the one measurement that settles it is a dry/live walk with a limit 0.5% above the ask on a breakout account. Filed: PI-20261005-BRKBLOCK-0001. |
| 6 | Tickets expire before an attempt | 17 `expired` rows, most from the 2026-09-29 cleanup batch and the manual-bridge era; recent: 10-04 08:01 ETH | **FIXED (alert) / by design** | Recent case: executor waited on the entry band for the whole window (ask outside band) and alerted NOT PLACED (#15595). Price leaving the band is a guard, not a venue block. The 10-04 22:18 ETH ticket expired because breakout_1 was `read_only`, timer off, account not active. |
| 7 | `closed_on_terminal`, no exit read | #80 (2026-10-03 00:39, ETH) | **FIXED, observed** | #15649 (`d883f64f`, merged 10-03 16:20Z) reads Trade History. Observed on the same code on tradeify_1: fill #87 carries exit 2701.62, pnl -57.05, `exit_source` "terminal Trade History". The reason (`sl`) is inferred from exit vs SL/TP, stated as such in the row. |
| 8 | Cap rounding refusal | #75 (10-02 SOL) | **FIXED** | #15465 (`196341a9`). Not yet observed on a resize-path ticket (checklist BREAKOUT-CAP-ROUND). |
| 9 | Executor halt reads as a crash (exit 3) | 10-04 03:49 onward | **FIXABLE, already filed** | PI-20261004-PUQ1APTH-0005. Observability only. |

Note the account was already below its drawdown floor when #82 ran (equity 4,698 vs floor 4,700, `breach_guards: report`), which is why breakout_1 is not active now. That is account state, not a terminal limit.

## breakout_1 vs tradeify_1 (same executor code, same VM, same egress)

| | breakout_1 | tradeify_1 |
|---|---|---|
| Terminal host | `wss.breakoutprop.com`, DNS-only AWS LB, **not** Cloudflare-proxied | `dx.tradeify247.co`, Cloudflare-fronted but landing served 200 to the VM |
| Login from the VM | Works (executor ran live 09-29 to 10-04) | Works |
| Symbol naming | `ETHUSD` | `ETH/USD` (canonicalised, #15101/#16223) |
| LIMIT price field | `page.fill` holds | needed key-by-key fill (#15987) |
| Buy/Sell label in price cells | visible | hidden (#15903) |
| Outcome | 1 strategy trade placed+closed-on-terminal, test round trips, then 4 submit-stage failures (classes 2, 3, 5) | placed 22:23Z 10-04, filled, closed at SL 04:38Z 10-05 (fill #87), 1 of 1 attempts past the guards placed |

Differences are layout details already handled in code. None is a venue-side block. Caveat on the comparison: tradeify has n=1 completed trip, and its own marketable-limit disabled-submit episodes (#16202, #16313) are the same class as breakout's 3 and 5, now handled. The tradeify success supports "DXtrade is automatable from this VM", not "breakout_1's remaining failures are gone".

## What would settle what is still open

- **Class 5 / 3 on breakout:** one dry walk with `lim-above` on a live breakout terminal (needs a live DXtrade breakout account, which no longer exists to buy). Tradeify already gives the platform-level answer.
- **Proprietary terminal:** (i) does a human click clear the Turnstile on a consumer-class address and for how long; (ii) the Funded Trader Agreement's automation clause (the operator's own copy); (iii) the terminal's DOM, measurable only with a login. All need a live account except (ii).

## Pipeline

- PI-20261005-BRKBLOCK-0001 (class 5, FIXABLE). Class 9 is already PI-20261004-PUQ1APTH-0005.
