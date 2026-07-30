# Tier-2 proposal — stop pricing a CONFIRMED CLOSE from a live mark

**Status:** PROPOSED — **not merged, not deployed.** Needs one operator OK.
**Tier:** 2 (changes a runtime write path in `order_monitor.py`).
**Date:** 2026-07-30
**Companion Tier-1 work (already shipped in this PR):** the INV-2 relaxation +
INV-2b counter in `scripts/check_db_integrity.py`, and the
`provenance.UNMEASURED_MARKER` vocabulary entry this diff writes.

---

## The defect, in one line

`order_monitor._sweep_local_pnl_for_unpriced` prices a trade that **already
closed** using `last_mark_price()` — the market **at sweep time**, up to 6 hours
later — and books `pnl` from it.

## Why it was written that way (this is not a stupid mistake)

Every link in the chain is individually correct and individually justified by a
real prior incident:

1. `clients.account_closed_pnl_for_trade` returns `None` for demo accounts
   (#4503 — a *correct* fix for demo closed-pnl records mis-mapping).
2. So `_close_trade_from_order_status` never recovers the real exit fill,
   leaves `exit_price` NULL and pins `exit_reason` to `reconciler_filled`.
3. `INV-2` (closed row with NULL `pnl` past the sweep grace) demanded a number
   and never asked what *kind* of number.
4. The sweep supplied one.

The result: **226 closed rows carrying +$247,683.78** of `local_markprice` PnL,
a fabricated share running 0.0% (May) → 30.5% (Jun) → **64.9% (Jul)**, and a
"−$6,358 Bybit scalp exit leak" that did not exist. Matched-pair proof: trade
4180 (`bybit_2`, real) **−$4.00** vs its mirror 4181 (`bybit_portfolio`) **−$2,589.78** —
same strategy, same symbol, same bracket, same minute. ~650×.

## The change

Anchor the reconstruction to the bar covering the recorded `closed_at` instead
of to "now", and label it honestly.

```diff
--- a/src/runtime/order_monitor.py
+++ b/src/runtime/order_monitor.py
@@ _sweep_local_pnl_for_unpriced
-        # Price the close from the current mark.
-        mark = last_mark_price(symbol)
-        if mark is None:
-            continue
-        pnl = _local_pnl(entry, mark, qty, direction, symbol)
-        notes["exit_price_source"] = "local_markprice"
-        notes["pnl_source"] = "local_compute"
+        # A CONFIRMED CLOSE is priced from the bar covering its recorded
+        # closed_at — never from the live mark, which is the market at SWEEP
+        # time (up to the grace window later) and has no relationship to where
+        # this order actually filled.
+        bar = candle_at(symbol, closed_at)
+        if bar is None:
+            # No anchor => DECLARE the gap instead of inventing a number.
+            # INV-2 accepts this marker and INV-2b counts it, so the row stays
+            # visible without being fabricated.
+            notes["pnl_source"] = provenance.UNMEASURED_MARKER
+            db.update_trade(trade_id, {"notes": dump_capped(notes, 500)})
+            continue
+        exit_price = bar["close"]
+        pnl = _local_pnl(entry, exit_price, qty, direction, symbol)
+        notes["exit_price_source"] = "candle_at_close"   # ESTIMATED, never MEASURED
+        notes["pnl_source"] = "candle_at_close"
```

Two properties matter more than the code:

* **`candle_at_close` classifies as `ESTIMATED`, never `MEASURED`.**
  `is_measured()` stays False for it, `require_measured()` still rejects it, and
  it does **not** count toward `pnlCoverage`. It is a better estimate, not a
  promotion to truth.
* **When there is no anchor, the sweep declares rather than invents.** That
  option only exists because of the Tier-1 INV-2 change shipped alongside;
  without it, "leave it NULL" was permanently red and fabrication was the only
  way to go green.

## Evidence the estimator is good enough to use

Validated against **known broker fills** (`scripts/research/exit_reconstruction_validator.py`):

| metric | value |
|---|---|
| median error | **1.33 bps** |
| p90 error | 16.05 bps |
| within 50 bps | **46 / 48** |
| Bybit candle coverage | 100% |
| IBKR candle coverage | **0%** |

Two hypotheses were **disproven** and are recorded as wrong rather than quietly
dropped: a decision-time-bracket variant and a break-even-replay variant both
made the estimator *worse*, and the trade-4076 "post-ratchet stop" reading was a
misread (it is an inverted bracket).

## Scope limits — read before approving

* **IBKR candle coverage is 0%**, so on `ib_paper` this diff mostly converts
  fabrication into a *declared* `unmeasured` rather than into an estimate. That
  is still a strict improvement (an honest gap beats a wrong number), and the
  real fix for IB is the **executions reader shipped in this PR**, which makes
  those rows `MEASURED` going forward. `get_ohlcv` takes no `since`;
  `reqHistoricalData` supports `endDateTime` and `pull_mes_ibkr_history.sh`
  already chunks ~80 such calls, so IBKR candle backfill is tractable but is its
  own piece of work.
* **The historical pass is RELABEL ONLY — never re-price** (operator decision,
  2026-07-30). The 226 existing rows get their provenance corrected so they stop
  polluting `pnlCoverage`; their `pnl` values are **not** recomputed. Re-pricing
  history would replace one set of manufactured numbers with another and destroy
  the audit trail of what the system actually believed at the time.
* A bar close is not a fill. On a gap or a thin bar the error is larger than the
  medians above. That is exactly why it is `ESTIMATED`.

## Rollback

Single-commit revert. The sweep is idempotent and writes only `notes` +
`pnl`/`exit_price` on rows that are already closed; no order path, no broker
call, no position state. Rows written by the new path are identifiable by
`notes.exit_price_source = 'candle_at_close'`.

## What I am NOT proposing here

Adding `interactive_brokers` to `clients.BROKER_PNL_READER_EXCHANGES` and
scheduling the IB executions puller on a timer. Both are **separate Tier-2
decisions** with their own blast radius, and the reader landed deliberately
inert so they can be taken independently.
