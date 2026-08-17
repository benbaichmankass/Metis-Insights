# Capital & activation decision packet — 2026-08-14

> **This packet presents a decision. It does not take one.** Account-mode flips are
> Tier-3 and ride the `set-account-mode` operator action; funding is the operator's
> alone. Lane 2 of [`WORKPLAN-2026-08-14.md`](./WORKPLAN-2026-08-14.md).
>
> Operator direction on 2026-08-14 was **"fix what's broken before widening
> anything"**, so the default recommendation here is *not yet*, with the blocking
> conditions named and measured rather than asserted.

---

## 0. The question

§0 of the workplan established that the entire real-money surface is **one Bybit
account holding $280.92**, while an enormous research apparatus measures itself
against a book that cannot act. The question no research lane can answer is:
**what is the intended real-money configuration?**

## 1. The declared surface — 11 accounts

Read from `config/accounts.yaml` + `config/strategies.yaml` on 2026-08-14.
"enabled+live" counts strategies routed to the account that are both
`enabled: true` and `execution: live`.

| account | exchange | mode | class | routed | enabled+live | shadow/off |
|---|---|---|---|--:|--:|--:|
| bybit_1 | bybit | live | paper | 26 | 22 | 4 |
| **bybit_2** | bybit | **live** | **real_money** | 7 | **6** | 1 |
| bybit_portfolio | bybit | live | paper *(portfolio)* | 7 | 6 | 1 |
| ib_paper | interactive_brokers | live | paper | 9 | 8 | 1 |
| **ib_live** | interactive_brokers | **dry_run** | **real_money** | **0** | **0** | 0 |
| oanda_practice | oanda | dry_run | paper | 0 | 0 | 0 |
| alpaca_paper | alpaca | live | paper | 19 | 19 | 0 |
| alpaca_portfolio | alpaca | live | paper *(portfolio)* | 14 | 14 | 0 |
| **alpaca_live** | alpaca | **dry_run** | **real_money** | 16 | **16** | 0 |
| alpaca_options_paper | alpaca | live | paper | 3 | 3 | 0 |
| breakout_1 | breakout | live | prop | 5 | 5 | 0 |

47 strategies repo-wide are `enabled` + `execution: live`.

## 2. The three real-money candidates

**`bybit_2`** — the only live real-money account. **$279.89** (recorded
2026-08-14T04:00Z). 4 symbols, 6 live strategies: `trend_donchian`,
`ict_scalp_5m`, `eth_pullback_2h`, `xrp_pullback_2h`, `trend_donchian_eth_4h`,
`trend_donchian_xrp_4h`.

**`alpaca_live`** — `dry_run`, **$0.10**, 11 symbols, **16 live-declared
strategies** (the 1d/1h ETF trend+pullback family across
SPY/QQQ/GLD/IWM/TLT/IEF/SLV/USO/GDX/SPLG/IAUM). **This is the largest single block
of strategy capability in the system attached to no capital.**

**`ib_live`** — `dry_run`, no recorded balance, 1 symbol (MES), and **zero
strategies routed**. Say the consequence plainly rather than listing it as an
option: activating it would trade **nothing**. It is not a capital decision at
all until strategies are routed to it.

## 3. Is the evidence good enough to decide on? — measured, and it corrects an earlier claim

An activation case for `alpaca_live` rests on its declared paper mirror
`alpaca_portfolio` (`paper_role: portfolio`, $99,653.60). So: **is that mirror's
PnL record trustworthy?**

Measured on the live journal 2026-08-14 with the canonical classifier
(`src/runtime/provenance.py::classify_pnl` — imported, not re-derived), over the
population **closed · non-backtest · `pnl NOT NULL` · last 30d = 293 rows**:

| account | rows | MEASURED | ESTIMATED | FABRICATED | UNVERIFIED | coverage | raw sum |
|---|--:|--:|--:|--:|--:|--:|--:|
| alpaca_live | 1 | 1 | 0 | 0 | 0 | 1.000 | −$0.43 |
| alpaca_paper | 20 | 15 | 0 | 0 | 5 | 0.750 | −$6,257.62 |
| **alpaca_portfolio** | **16** | 12 | 0 | **0** | 4 | **0.750** | **+$2,656.48** |
| bybit_1 | 203 | 69 | 49 | **57** | 28 | **0.340** | −$18,101.71 |
| bybit_2 | 27 | 23 | 2 | 2 | 0 | 0.852 | −$28.97 |
| bybit_portfolio | 21 | 10 | 9 | 2 | 0 | 0.476 | −$11,241.10 |
| ib_paper | 5 | 4 | 0 | 0 | 1 | 0.800 | −$55,759.75 |

⚠️ **This corrects an earlier draft of this packet, and the correction is the
point.** That draft said the paper book's coverage of **0.119** made the evidence
base untrustworthy, and treated raising it as a precondition. That figure is the
`/performance` **aggregate `paper` block**, and per-account it is dragged down
almost entirely by **`bybit_1`** — 203 of the 293 rows (69%) at 0.340 coverage
with 57 fabricated rows. `bybit_1` is the data-only **soak** account; it is not
`alpaca_live`'s mirror and has no bearing on this decision.

The right denominator for this decision is `alpaca_portfolio` alone, and it reads
**0.750 coverage with ZERO fabricated rows**. The two numbers do not contradict
each other — they answer different questions over different populations — but
quoting the aggregate here would have blocked the decision for the wrong reason.
*Always state the population*, including when the population is your own.

## 4. So what actually blocks it

Not coverage. **Sample size.**

`alpaca_portfolio` has **16 closed trades in 30 days**. Sixteen trades cannot
support a funding decision for **sixteen strategies** — that is roughly one
resolved trade per strategy, and the +$2,656.48 sum is a number whose sign a
handful of trades could flip. This is the same shape as the workplan's warning
about `ict_scalp_5m`'s +35.4R over 4 trades: a sample, not an edge.

Blocking conditions, in order:

1. **Sample.** `alpaca_portfolio` needs enough resolved trades for its record to
   mean something. At the current ~16/30d, a meaningful read on 16 strategies is
   quarters away, not weeks — which is itself a finding the operator should weigh:
   *the mirror may never accumulate evidence fast enough to justify the account it
   mirrors.*
2. **Lane 0 must close.** `alpaca_paper`/`alpaca_portfolio` still throw
   `balance() returned None` (6 in 30d, 4 of them clustered on 2026-08-13 across
   three accounts — `BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE`).
   Funding a real-money account whose paper twin intermittently cannot read its own
   balance puts money behind an unresolved fault.
3. **The venue-max clamp must land** (M20). `risk.py::position_size` has no
   `max_qty` clamp, so the margin pre-flight cap is free to exceed the venue
   ceiling. It is neither AVAX-specific nor paper-specific.
4. **Per-account compat matrix.** `scripts/prop/account_compat_matrix.py` is
   mandatory per the `backtesting` / `new-strategy` skills before a strategy is
   routed to an account it was not evaluated against. **I found no current artifact
   for the ETF family** — the only compat outputs on disk are the 2026-06-17
   perp-validation SOLUSDT set. Absence of the artifact is not evidence the work
   was skipped, but it is not evidence it was done either, and the skill requires it.

## 5. If the operator wants to move anyway

The cheapest honest step is **not** flipping `alpaca_live` to `live`.

It is **funding `alpaca_live` to a nominal amount while leaving `mode: dry_run`**.
Today its $0.10 balance makes it refuse on `zero_balance` before any other signal
can be observed — 120 such refusals in 30d, `alpaca_live` alone, across 16
separate days (measured; see the workplan's dead-leg section, where this reading
was challenged and upheld). Funding it without activating it converts the account
from **structurally silent** to **observable**, costs no tier flip, needs no code,
and puts no capital behind unproven legs.

That is a funding action, entirely operator-side, and it is the one move here with
a good ratio of information gained to risk taken.

## 6. A question that is genuinely the operator's

**Is `ib_live` intended to be a live venue at all?** It carries zero strategies and
one symbol (MES). M15 explicitly superseded the futures-first direction in favour
of Alpaca + OANDA. If `ib_live` is a leftover of the pre-M15 plan, the honest move
is to **retire the declaration** rather than carry it as a perpetual "candidate"
that every future review has to re-examine and re-dismiss.

---

## Provenance of every number here

- Account/strategy/routing table — `config/accounts.yaml` + `config/strategies.yaml`, read 2026-08-14.
- Balances — `trade_journal.db::balance_snapshots`, latest row per account, 2026-08-14T04:00:58Z (trainer-diag #9293).
- Coverage table — `classify_pnl` over the live journal, 30d closed population, n=293 (trainer-diag #9301).
- `zero_balance` refusal counts + their burst-vs-trickle discrimination — trainer-diag #9293.
- ⚠️ All journal reads used `<repo>/data/trade_journal.db` **explicitly**. The
  canonical resolver on the trainer points at an empty stray journal
  (`BL-20260814-TRAINER-CANONICAL-RESOLVER-POINTS-AT-EMPTY-JOURNAL`); a read that
  trusted it would have returned a clean, confident zero for every row above.
