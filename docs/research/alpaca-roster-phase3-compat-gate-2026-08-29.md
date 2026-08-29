# Phase 3: the per-account compat gate — and it REFUSES both candidates

**2026-08-29 · Tier-1 research. Nothing wired; `alpaca_live` still carries
`strategies: []`.** This is the mandatory evidence step the `backtesting` and
`new-strategy` skills require before a strategy is called gate-cleared for an
account, and it **overturns the Phase 2 shortlist**.

## What it took to run it at all

The standard arm was **inert on a GitHub runner** — no journal DB, so
`load_balance_snapshots()` returned `None`, every standard account resolved
`unreadable`, and the whole arm graded `UNGRADED`. Part 2 (operator-approved)
gave the runner the balances the bot already publishes. See the commit for the
planted controls; the run itself carries its own proof:

> **10 of 11 accounts graded. `ib_live` alone stayed `UNGRADED`** — and that is
> *correct*, because the live payload carries `ib_live: balance=None,
> api_ok=false`. One table holds both the positive control (real sizes flowed)
> and the negative one (the api_ok gate still refuses). Before Part 2 all 11
> were `UNGRADED`.

## The verdicts

Gate for a standard account: positive mean end-return **AND** survival ≥ 0.9
**AND** P(breach) ≤ 0.1. Runs
[33249719919](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33249719919)
(TQQQ, 8 emitted trades) and
[33249723196](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33249723196)
(SLV), fee 0 bps, 730 d, ref `830154ba`.

**Population of every cell below:** one Monte-Carlo over the leg's own emitted
ledger, scored against that account's ruleset at its live balance. TQQQ =
**8 emitted trades**; the SLV emit count is on the run's own summary line and I
did not read it back, so it is not quoted here. `ret` is the mean end-return
over the simulated paths, `P(b)` the breach probability, `surv` the horizon
survival — **all three are the same population**, and the gate reads all three.

| account | class | `tqqq_trend_long_1d` | `slv_trend_1h` |
|---|---|---|---|
| **`alpaca_live`** | **real_money** | **skip** — ret 94.5%, P(breach) **1.0**, surv **0.4997** | **skip** — ret 40.7%, P(breach) **1.0**, surv **0.0** |
| `bybit_2` | real_money | **skip** — ret 48.2%, P(breach) 1.0, surv 0.4997 | **skip** — ret 13.7%, P(breach) 1.0, surv 0.0 |
| `ib_live` | real_money | UNGRADED (balance unreadable) | UNGRADED |
| `alpaca_portfolio` | paper | ROUTE — 6.9%, P(b) 0.0, surv 1.0 | ROUTE — 3.0%, P(b) 0.0, surv 1.0 |
| `alpaca_paper` | paper | ROUTE — ret 6.0%, P(b) 0.0, surv 1.0 | ROUTE — ret 2.6%, P(b) 0.0, surv 1.0 |
| `alpaca_options_paper` | paper | ROUTE — ret 4.9%, P(b) 0.0, surv 1.0 | ROUTE — ret 2.1%, P(b) 0.0, surv 1.0 |
| `bybit_1` | paper | ROUTE — ret 2.8%, P(b) 0.0, surv 1.0 | ROUTE — ret 1.2%, P(b) 0.0, surv 1.0 |
| `bybit_portfolio` | paper | ROUTE — ret 5.3%, P(b) 0.0, surv 1.0 | ROUTE — ret 2.3%, P(b) 0.0, surv 1.0 |
| `ib_paper` | paper | ROUTE — ret 0.4%, P(b) 0.0, surv 1.0 | ROUTE — ret 0.2%, P(b) 0.0, surv 1.0 |
| `oanda_practice` | paper | ROUTE — ret 4.9%, P(b) 0.0, surv 1.0 | ROUTE — ret 2.1%, P(b) 0.0, surv 1.0 |
| `breakout_1` | prop | skip — EV −$45, P(net>0) 0.0 | ROUTE — EV $1,040, P(net>0) 0.8983 |

## The finding: BOTH candidates are refused on `alpaca_live`, and the reason is size

**Every paper account ROUTEs. Both real-money accounts skip. The gate did not
split on strategy — it split on account size.** The paper books hold $82k–$1.34M;
`alpaca_live` holds **$200.10** and `bybit_2` **$305.04** (live balances, `source=db`,
`as_of 2026-08-29T11:01:03Z`).

⚠️ **Read the end-return beside the survival, never alone.** `alpaca_live` shows
the **highest** mean end-return of the 11 accounts scored (94.5% on TQQQ, over
that leg's 8-trade emitted ledger) *and* a survival of 0.4997 with
P(breach) 1.0 over the same paths. Those are not in tension — they are the
same fact: at one whole share of a $73.30 instrument on a $200 book, a single
position is ~37% of the account, so the paths that survive compound hard and the
rest breach. A mean end-return read on its own would have said this is the best
cell in the table. **That is exactly the reading the survival gate exists to stop.**

**So the Phase 2 shortlist does not survive Phase 3.** `tqqq_trend_long_1d` was
the one leg clearing every earlier filter — affordable, OOS 36, lever held
out-of-sample and cleared Path A. It does **not** clear its own account's
ruleset. `slv_trend_1h`, the most capital-efficient leg of the 19, is worse
here: survival **0.0**, i.e. no simulated path avoided a breach.

**Nothing here is a strategy verdict.** Both legs ROUTE on seven paper accounts.
The refusal is about routing *these* strategies to a *$200 whole-share cash
account*, and it composes with the three earlier constraints rather than
replacing them.

## What this does NOT say

- **It is not a reason to widen `risk_pct`.** The breach is driven by
  whole-share granularity against a small book, and `risk_pct` does not change
  the tradeable set (measured: identical at 0.02 and 0.05).
- **It is not a claim about `bybit_2`'s live routing.** `bybit_2` already trades
  and this run says its ruleset refuses these two ETF legs — which is expected,
  since neither is routed there. Nothing here proposes a change to it.
- **`breakout_1` ROUTEs `slv_trend_1h` at EV $1,040 / P(net>0) 0.8983.** That is
  a prop-ruleset verdict on a different account with a different economics
  block, and it is **not** transferable to `alpaca_live`. Recorded because the
  matrix computed it, not because it is being proposed.
- **`ib_live` is UNGRADED, which is neither pass nor fail.** Its balance reads
  `api_ok: false`, so nothing was measured against it.

## The decision this hands the operator

The `$200` account cannot currently carry either candidate under its own
ruleset. That is a real result, not a blocker to route around — and it makes
the go-live question *"what size does this account need, or what does a leg
need to look like to fit it?"* rather than *"which of these two do we wire?"*.
