# `exit_reason` is frozen at the moment the price was unknown

**Date:** 2026-08-22 · **Session:** `s4-pairs-control` · **Tier:** 1 (measurement + code trace; no write shipped)
**Closes the item-1.1 residue** (trades 4928 / 4733 / 4180) and names the mechanism behind
item 1.1's "substantially an ATTRIBUTION defect" conclusion.

---

## 1. What was being chased, and why the standing hypothesis is wrong

Item 1.1 left a three-row residue: trades **4928** (`AVAXUSDT`/`bybit_1`, paper),
**4733** and **4180** (both `BTCUSDT`/`bybit_2`, **real money**) each crossed their own
stop by a hair (0.0159 / 0.0074 / 0.0210 R) and kept the generic `reconciler_filled`.
Three candidates were already eliminated by the prior session (later price refinement,
reduce leg, netted-sibling cascade). The surviving one was:

> `_classify_broker_exit` falls back to `_resolve_protective_levels(symbol, direction)` —
> the most recent matching package, not THIS trade's — so on a netting account it can
> grade a trade against another trade's bracket.

**REFUTED, on the rows themselves.** Each trade's linked package exists, carries the
correct `linked_trade_id`, and its `sl`/`tp` are positive and **identical** to the trade's:

| trade | account | dir | entry | package `sl` | stored `exit_price` | `px <= sl`? |
|---|---|---|---|---|---|---|
| 4928 | `bybit_1` (paper) | long | 7.501 | 7.517259 | 7.517 | **yes** |
| 4733 | `bybit_2` (**real**) | long | 64108.7 | 64230.90205 | 64230.0 | **yes** |
| 4180 | `bybit_2` (**real**) | long | 64600.9 | 64110.32142857 | 64100.0 | **yes** |

`_classify_broker_exit` consults `_resolve_protective_levels` **only** when the own-package
lookup yields nothing or a non-positive level. That branch is unreachable for these rows.
And on the levels it *would* have used, all three satisfy the production inequality for
`'sl'`. So the levels were never the problem: **the classifier did not run.**

## 2. The mechanism, traced in code

1. `_reconcile_open_trades` (`src/runtime/order_monitor.py:3823`) observes the order filled
   and the position flat, and delegates to `_close_trade_from_order_status` (`:5832`).
2. That function asks `account_closed_pnl_for_trade` for the venue's close record. When no
   record with an `avg_exit_price` comes back — the steady-state case seconds after a close,
   because Bybit has not booked it yet — control reaches the `else` fallback at **`:6033`**,
   which leaves `exit_price` NULL and **hard-codes** `final_exit_reason = "reconciler_filled"`.
   The classifier is *correctly* not called here: there is no price to classify with.
3. Later, `_sweep_pending_pnl_from_bybit` (`:8323`) selects exactly those rows —
   `status='closed' AND COALESCE(is_backtest,0)=0 AND pnl IS NULL` — recovers the record,
   and writes `exit_price`, `pnl`, `pnl_percent`, `notes.exit_price_source` and the
   closed-pnl note key (`:8488`).
4. **It never touches `exit_reason`, and never calls `_classify_broker_exit`. Nothing else
   revisits the label.**

The price becomes known and the label that depends on it is never recomputed. This is the
`exit_price_source` family one turn further on: not *written and never read*, but
**read once, at the only moment the answer could not be known.**

### The signature that confirms it

The classifying branch (`:5923`) stamps `notes.exit_reason_source` — `price_vs_pkg_bracket`
when it resolved, `unresolved` when it looked and found the fill mid-range. The fallback
branch stamps neither. So the note key is a clean marker for *"did this row ever reach the
classifier at all"*, and it is decisive below.

## 3. Population and measurement

**POPULATION.** Every `trades` row with `exit_reason = 'reconciler_filled'`, read via
`/api/bot/db/table/trades` with `filter_state` asserted `applied` on both pages:
**572 rows**, of which 572 are `status='closed'` and 572 are non-backtest.

Of those, **395 are gradeable** — they carry an `exit_price > 0` **and** a linked
`order_packages` row with at least one positive level. The 177 excluded are 172 with no
linked package and 5 with no exit price; they are **excluded, not counted either way**.

Applying `_classify_broker_exit`'s own inequality to the row's stored exit price and its
**own** package's levels:

| | n | would grade `sl` | would grade `tp` | genuinely between |
|---|---|---|---|---|
| **broker-truth price** (`bybit_closed_pnl` 71 · `exchange_fill` 20) | **155** | 83 (53.5%) | 8 (5.2%) | 64 (**41.3%**) |
| estimated or worse (`local_markprice` 40 · `candle_at_close` 35 · `netted_duplicate_unattributed` 10 · `recorded_exit_price` 5) | 240 | 70 (29.2%) | 20 (8.3%) | 150 (62.5%) |
| **total gradeable** | **395** | 153 | 28 | 214 |

**On broker truth, 91 of 155 (58.7%) of rows labelled `reconciler_filled` actually reached
a declared bracket level.** By account, the 181 total mislabelled rows are `bybit_1` 155,
`bybit_2` 14, `bybit_portfolio` 12.

⚠️ **State the population when quoting these.** The two halves of the table are *not*
interchangeable: the estimated half's exit prices include `local_markprice`, which is the
FABRICATED class `src/runtime/provenance.py` exists to distrust, so a level crossing
computed from one is a claim about a manufactured number. **Quote the 91, not the 181**,
unless the wider figure is explicitly labelled as resting on estimated prices.

### The confirming check, and one candidate it kills

**181 of 181 mislabelled rows carry no `exit_reason_source` key at all** — not
`price_vs_pkg_bracket`, not `unresolved`. Every one of them bypassed the classifier. That
is a 100% signature, not a tendency.

Separately, **14** rows in the gradeable population *did* reach the classifier. All 14 are
`exit_reason_source = 'unresolved'`, and **all 14 still grade `None` on today's stored
price** — none crossed a bracket after the fact. So *"the classifier ran on a price that
later moved"* contributes **zero** rows: the prior session's elimination of later price
refinement stands, and the mechanism in §2 is the sole one.

### Corroboration with item 1.1

Item 1.1 measured 41.0% of broker-truth main-path closes as genuinely exiting between the
brackets. This population — differently scoped (all `reconciler_filled` rows, all Bybit
account classes) — independently returns **41.3%**. Two different cuts, same answer. This
is corroboration of 1.1's *"substantially an attribution defect, not a mechanism one"*,
and §2 is the attribution defect's name.

## 4. Scope, and what is NOT claimed

- All 181 rows are Bybit accounts. `_sweep_pending_pnl_from_bybit` is Bybit-specific, so
  this is the shape of the defect **on Bybit**; whether an equivalent late-price writer
  exists for IBKR or Alpaca is **not measured here** and is not asserted.
- The 172 rows with no linked package are not evidence of anything in either direction —
  they cannot be graded, and are reported as excluded rather than folded in.
- No claim is made that relabelling would change any PnL. It changes the **exit record**,
  which is what every downstream exit-mechanism measurement reads.

## 5. The fix, prepared and NOT shipped

`_sweep_pending_pnl_from_bybit` already holds everything the classifier needs at the moment
it writes the price — `db`, the row, and `avg_exit_price`. The change is to call
`_classify_broker_exit(db, row, avg_exit_price, is_reduce_leg=...)` there and, when it
resolves, add `exit_reason` to the `updates` dict and stamp `notes.exit_reason_source`.

**Why it is not shipped in this PR:** it is a **money-DB writeback (Tier-2)** and it needs
one operator OK. Three things the operator should weigh:

1. **Blast radius is a label, not an order.** `exit_reason` is written on rows already
   `status='closed'`; the sweep already writes `exit_price` and `pnl` to these same rows.
   No order is placed, modified or cancelled.
2. **`is_reduce_leg` must be carried across.** The classifier returns `None` for a reduce
   leg because a reduce's bracket can be inverted relative to the order direction. The
   sweep's SELECT does not currently read `setup_type`, so the fix must add it — omitting
   it would mislabel reduces as `sl`/`tp`, which is the failure the exclusion exists to
   prevent.
3. **The 181-row backlog is a SEPARATE decision.** Forward-fixing changes new closes only.
   Retroactively relabelling history is a second Tier-2 write over 181 rows and should be
   decided on its own, with the provenance split above in view — the 90 rows resting on
   estimated prices are a weaker case than the 91 on broker truth.

---

**Evidence commands.** Populations re-derivable from
`/api/bot/db/table/trades?filter_col=exit_reason&filter_op=eq&filter_val=reconciler_filled`
(2 pages, `filter_state` asserted `applied`) joined client-side to all 3,963
`order_packages` rows. Journal confirmation of the close path for trade 4928:
`/api/diag/journalctl?unit=ict-trader-live.service&since=2026-08-22T13:37:00Z&until=2026-08-22T13:42:00Z`
→ `_reconcile_open_trades: ... closed=1` at 13:39:27Z, with
`_watchdog_stuck_strategies` reporting `recovered_closed=0` on the same tick.
