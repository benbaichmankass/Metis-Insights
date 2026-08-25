# The mirror-leg design meets a system-wide invariant: signal symbol == order symbol

**Measured 2026-08-25** · scope: `BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED`,
M15 alpaca short proxies · **Tier-3 · nothing built, nothing wired, no config touched**

---

## Why this document exists

The chosen design (operator, 2026-08-25) is **"mirror legs on index data"**: a proxy leg
computes its signal on the PARENT index candles (SPY/QQQ) exactly as the parent does, and
emits a **LONG** order on the proxy (SH/PSQ) where the parent would have gone SHORT.

The record already flags the risk in one line — *"NOTE THE NEW PATTERN THIS INTRODUCES:
signal symbol != order symbol. No existing leg does this. It is the part of the wiring most
likely to be got wrong."* This is the measurement behind that line.

**The headline is stronger than "most likely to be got wrong".** The repo carries one
unbroken invariant end to end:

```
tick symbol == candle symbol == signal symbol == intent symbol
            == order-package symbol == monitor-fetch symbol
```

There is **no place in the live path where a signal symbol and an order symbol exist as two
separate variables** — no `signal_symbol`/`order_symbol` pair, no `data_symbol`/`trade_symbol`
pair, nothing of that shape anywhere in `src/`. The design does not extend the system; it
introduces a distinction the system does not have.

Two consequences below are **blockers** (the design cannot work as recorded without changing
shared code), one is a **correctness break** (it would run and be wrong), and one is a
**semantic shift the operator should rule on** before anything is built.

---

## 1. BLOCKER — the intent aggregator drops a cross-symbol intent, and the emitter overwrites the symbol

Two independent stops, either of which alone is fatal.

| site | what it does |
|---|---|
| [`src/runtime/intents.py:1265-1268`](../../src/runtime/intents.py) | `aggregate_intents` filters `if i.symbol == norm_symbol`. An intent whose symbol differs from the tick symbol is **silently dropped** — the documented rule ([`intents.py:1202-1204`](../../src/runtime/intents.py)) is *"the aggregator never mixes symbols"*. Result: `no_intents_for_symbol` ([`intents.py:1300`](../../src/runtime/intents.py)). |
| [`src/runtime/intent_multiplexer.py:684`, `:718`](../../src/runtime/intent_multiplexer.py) | `_desired_to_pipeline_signal` **stamps the TICK symbol onto the outgoing signal**, discarding `winning_intent.symbol` entirely. |

So an `SH` intent raised on a `SPY` tick is dropped at the aggregator; and if it somehow
survived, the signal that leaves the multiplexer would say `SPY` anyway. The order would be
placed on SPY.

`aggregate_intents` is **shared by every strategy on every tick**. Changing its symbol rule to
accommodate two legs is a change to the core routing path for the whole fleet.

## 2. BLOCKER — `supported_symbols()` refuses an undeclared symbol at intent construction

[`src/runtime/intents.py:127`](../../src/runtime/intents.py) — `supported_symbols()` returns a
static base (`BTCUSDT`, `MES`, `MGC`, `MHG`) **∪ every symbol declared in any account's
`symbols:` in `config/accounts.yaml`** ([`intents.py:140-147`](../../src/runtime/intents.py)).
It validates against **`accounts.yaml`, never `strategies.yaml`**.

[`intents.py:542`](../../src/runtime/intents.py) — `StrategyIntent.__post_init__` **raises
`ValueError`** for an unsupported symbol. The raise happens inside `_collect_intents`, which
has per-strategy isolation, so the leg is **silently skipped that tick** rather than erroring
loudly.

This one is tractable — `SH`/`PSQ` must appear in an account's `symbols:` list — but note it
is `accounts.yaml`, a **Tier-3** file, and that adding them there is also what makes the
symbol tickable at all (§5).

## 3. CORRECTNESS BREAK — the monitor would re-evaluate the exit on the WRONG price series

This is the one that would run, produce output, and be wrong.

[`src/runtime/order_monitor.py:10001-10005`](../../src/runtime/order_monitor.py):

```python
candles = ohlcv_fetcher(normalised.get("symbol"), tf_used, strategy_name)
```

**The symbol comes from the ORDER PACKAGE row** (`order_packages.symbol`, decoded at
[`order_monitor.py:9979`](../../src/runtime/order_monitor.py)) — not from the trade row, and
not from the strategy config. The live `strategies.yaml` block IS loaded alongside
([`order_monitor.py:125`](../../src/runtime/order_monitor.py)) and carries `symbols:`, but is
**not consulted for the fetch**.

So a SPY-signalled / SH-ordered leg would have its `monitor()` — the Donchian channel, the
ADX read, the ATR trail — recomputed **on SH candles**, an inversely-correlated series, while
its entry geometry was computed on SPY. A channel breakout on SPY is a channel breakdown on
SH. The trailing stop would trail the wrong direction.

Nothing would error. The trade would open, an exit would be computed, and the exit would be
geometrically meaningless.

## 4. THE OPERATOR SHOULD RULE ON THIS — `side_filter: long` stops seeing the short

[`src/runtime/account_side_filter.py:59`](../../src/runtime/account_side_filter.py) —
`account_side_filter(account_id)` is keyed on **account only, never symbol**, enforced at
[`coordinator.py:1322-1328`](../../src/core/coordinator.py) on `pkg.direction`. Its
strategy-level sibling ([`strategy_signal_builders.py:147`, `:176`](../../src/runtime/strategy_signal_builders.py))
is likewise applied to a `direction` computed from the signal's candles.

Under the mirror design **"short SPY" becomes "long SH"** before either filter sees it. So
`side_filter: long` on `alpaca_live` would **permit** the inverse expression of exactly the
flow it was added to suppress. Nothing in either module inverts direction with the symbol.

**That is not a bug — it is the design working.** The whole point of a proxy is to express the
short as a long. But it does change what the guarantee MEANS, and the change is worth stating
before it is discovered later:

> Today, *"`alpaca_live` is long-only"* bounds both the instrument side **and** the economic
> exposure. Once proxies exist it bounds **only the instrument side**. A long SH position is
> short-SPY exposure by construction. The account never shorts; the book can still be net
> short the index.

Every downstream reader of "long-only" inherits that shift. Two that will not notice:

- [`src/runtime/portfolio_conflicts.py:133`, `:152`](../../src/runtime/portfolio_conflicts.py) —
  `opposing_same_symbol` groups by `symbol`, `self_opposing_strategy` by `(symbol, pattern)`.
  **A long SPY and a long SH held at once read as two unrelated longs**, not an offsetting
  pair. The conflict detector is blind to it.
- [`src/units/accounts/risk.py:1166`](../../src/units/accounts/risk.py) —
  `_gross_exposure_headroom(..., exclude_symbol=package.symbol)`, over
  `_open_notional_for_symbol` ([`risk.py:543`](../../src/units/accounts/risk.py)) which sums
  `WHERE symbol = ?`. SPY exposure and SH exposure are **two independent budgets** for what is
  one directional bet.

## 5. The rest of the surface, for whoever builds it

Not blockers, but each keys on a symbol that would become ambiguous.

| area | site | note |
|---|---|---|
| tick symbols | [`src/main.py:490`, `:528`](../../src/main.py) | `_resolve_tick_symbols` unions every **account's** `symbols` from `accounts.yaml`. A symbol absent there never gets a tick at all. |
| per-strategy scope | [`intent_multiplexer.py:394`, `:462-471`](../../src/runtime/intent_multiplexer.py) | `_strategy_symbol_scope`; a strategy whose `symbols` excludes the tick symbol is **skipped before evaluation**. So the scope decides "which candles" and "what to trade" as one decision. |
| builders (pattern A, 32 sites) | e.g. [`strategy_signal_builders.py:2790`, `:3380`](../../src/runtime/strategy_signal_builders.py) | `symbol = settings.get("SYMBOL", ...)` — taken from the **tick**. |
| builders (pattern B) | [`:762-763`, `:1713-1714`, `:5377-5382`](../../src/runtime/strategy_signal_builders.py) | pinned from `symbols[0]`. |
| signal emission | [`:843`](../../src/runtime/strategy_signal_builders.py) | the emitted `sig["symbol"]` is **the same variable** passed to `fetch_candles`. |
| order bridge | [`src/runtime/order_bridge.py:54`](../../src/runtime/order_bridge.py) | one field, carried straight to `OrderPackage.symbol`. |
| instrument profile | `config/instruments.yaml` | 25 profiles; **`SPY:276` and `QQQ:291` exist, `SH` and `PSQ` do not.** Unprofiled ⇒ `connector_for_symbol` falls back to the process `EXCHANGE` ([`market_data.py:366-368`](../../src/runtime/market_data.py)), `contract_value_usd_for` defaults to `1.0` ([`risk.py:213`](../../src/units/accounts/risk.py)), `instrument_lot` falls back. |
| dispatch eligibility | [`coordinator.py:1069-1090`](../../src/core/coordinator.py) | gates on instrument-vs-account exchange **only when IB is on either side**; does not check the account's `symbols:`. An unprofiled `SH` package would reach every Alpaca account naming the leg. |
| netting / re-entry | [`positions.py:92`, `:137`](../../src/runtime/positions.py) · [`pipeline.py:612`, `:643`, `:689`](../../src/runtime/pipeline.py) · [`intent_multiplexer.py:587`](../../src/runtime/intent_multiplexer.py) | all key on the ORDER symbol — monocle, bar debounce, refusal cooldown, emission debounce. |
| regime vol gate | [`intents.py:859`, `:931`](../../src/runtime/intents.py) | `ml_vol_regime_for_symbol(intent.symbol)`. `SH` has no advisory head ⇒ `unknown` ⇒ **permissive** ([`intents.py:1141-1144`](../../src/runtime/intents.py)). The SPY leg's gating would silently stop applying. |
| shadow models | [`strategy_signal_builders.py:265-268`](../../src/runtime/strategy_signal_builders.py) · [`coordinator.py:575-577`](../../src/core/coordinator.py) | `discover_shadow_stage_model_ids(registry, symbol=symbols[0])`. |
| news layer | [`src/web/api/_asset_class.py:16-20`](../../src/web/api/_asset_class.py) | `news_group_for_symbol` selects which RSS feeds are read, and the news layer **can veto a signal**. |
| protective re-arm | [`order_monitor.py:1476`, `:1505`](../../src/runtime/order_monitor.py) | `_find_trade_by_match(strategy, symbol)`. |

---

## What this means for the plan

The evidence gate already says the backtest runs before anything is built. This does not
change that; it changes what "built" would cost, and it surfaces one thing the backtest
**cannot** settle.

1. **§3 is not a wiring detail, it is a design hole.** The design specifies where the signal
   comes from and where the order goes; it does not say what the EXIT re-evaluates on. Today
   the answer is forced (the order package's symbol) and it is the wrong one. Whoever writes
   the backtest must decide this explicitly, because the backtest will silently assume an
   answer — and the natural assumption (evaluate the exit on the SPY series that produced the
   entry) is the OPPOSITE of what the live monitor would do.
2. **§1 means shared routing code changes.** `aggregate_intents` is on every tick for every
   strategy. That is a materially bigger change than "a new leg", and it argues for weighing
   the rejected alternative (a) — translate at routing — on cost as well as on principle,
   since §1–§3 mean the mirror design also does not avoid touching shared code.
3. **§4 is an operator question, not an engineering one**, and it is worth answering before
   the backtest rather than after: is *"alpaca_live is long-only"* intended to bound the
   instrument side only, or the economic exposure? If the latter, the proxy programme needs a
   netting rule that sees SPY and SH as one book — which nothing currently does.
4. The **79 packages** of historical flow at stake (two legs) should be weighed against that
   cost, per the measured basis already on the row.

## Confidence, and what was NOT checked

Every site above was **read**, not inferred, and is cited to `file:line`. What was not done:

- **Not all 32 pattern-A builders were read in full.** Three were (`spy_trend_long_1d` in
  full); the identical `symbol = settings.get("SYMBOL", ...)` line was confirmed at the other
  29 offsets, but that each then passes the same variable to BOTH `fetch_candles` and the
  emitted `sig` was verified only for the three read in full.
- **The pairs sleeve was not traced** (`src/units/strategies/pairs_executor.py`). It is an
  isolated 2-leg order path outside `multi_account_execute` and would need its own pass if the
  mirror design ever touches it.
- **No claim is made about whether the design is right** — only about what it would collide
  with. The operator chose it over two alternatives for stated reasons; this is cost
  information for that decision, not a counter-proposal.
