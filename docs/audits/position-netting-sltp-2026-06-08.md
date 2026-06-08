# Position netting + per-trade SL/TP — root cause & Tier-3 remediation proposal

**Date:** 2026-06-08
**Status:** PROPOSAL — analysis only, no live-path changes until operator-approved (Tier-3).
**Origin:** BL-20260601-001 (orphan-PnL) → BL-20260608-DEMOPNL. Operator asked to
investigate the SL/TP-non-firing root cause before any build.

---

## TL;DR

The bot trades Bybit in **one-way mode** and attaches each trade's SL/TP as
**position-level** TP/SL. When a strategy re-enters the same direction while a
position is open, the entries **net into a single position** whose single SL/TP
is **overwritten by each new entry**. Individual journal "trades" are therefore
not independent positions: they can't stop out per-trade, and they have no
per-trade close to attribute realised PnL from. On the **demo** account
(`bybit_1`) this produced a growing net short during the 2026-06-07 BTC rally
(#2482/#2484/#2490/#2492/#2494 …) flattened in aggregate by one `Buy 0.505` —
hence `pnl=NULL` on every row. The **same dynamic exists on live** (`bybit_2`);
it is currently masked only because live trades happened to be sequential
round-trips.

This is a **position-management / order-path** issue → **Tier-3**.

---

## Evidence

**Trades (diag #2974 / #2984, 2026-06-08 window):**

| trade | account | strategy | dir | size | pnl | note |
|---|---|---|---|---|---|---|
| #2482/#2484/#2490/#2492/#2494 | bybit_1 (demo) | htf_pullback | short | 0.008–0.064 | **NULL** | netted into one short |
| #2491 | bybit_2 (live) | **intent_reduce** | long | 0.003 | NULL | reduce leg sharing pkg-8596863669584ed5 with demo #2490 |
| #2489 / #2493 | bybit_2 (live) | ict_scalp / htf | short | 0.003 / 0.001 | +2.10 / −1.70 | clean per-trade closes |

**Closed-pnl / executions (inspect-closed-pnl #2992/#2994/#2997, demo #2490):**
- `/v5/position/closed-pnl` → **0 records** (demo venue doesn't populate it).
- `/v5/execution/list` → opens are incremental `Sell … closedSize=0`; the whole
  ~0.505 net short was closed by **one** `Buy 0.505 @ 63483.4` (`closedSize=0.505`).

**Pipeline audit (audit_query #3002, htf_pullback_trend_2h / BTCUSDT, 9h window):**
- **33 `sell` + 28 `short`** actionable signals; only **3 `bar_debounce_blocked`**.
  → the strategy repeatedly re-entered the same direction; the debounce caught
  only a fraction.

**Code:**
- SL/TP attached to the entry Market order: `src/units/accounts/execute.py:659-660`
  (`kwargs["stopLoss"]`, `kwargs["takeProfit"]`). No demo-specific stripping.
- **No `positionIdx` / hedge / position-mode handling anywhere** in
  `src/units/accounts/` → one-way mode (positionIdx=0). bybit_1 = bybit_2 =
  `market_type: linear`, one-way.
- Reconciler close trigger: `src/runtime/order_monitor.py:2017-2029` — closes a
  trade when its `(symbol, side)` is **absent from the net open-position set**
  (`_exchange_position_set(account_open_positions(cfg))`). This is a **net**
  check, not per-trade.

> **Correction to an earlier note:** an initial read said "demo opens carry no
> SL/TP (no `stopOrderType`)." That was wrong — attached **position-level** SL/TP
> never appears as a separate stop *order* in `order/history`. SL/TP **are** sent
> on demo entries.

---

## Root cause (the cycle)

1. **One-way mode, one position, one SL/TP.** Each new same-direction entry's
   attached `stopLoss`/`takeProfit` **overwrites** the position's single SL/TP.
   Per-trade stop intent does not survive re-entry.
2. **Reconciler closes on net-flat, not per-trade.** When the intent-multiplexer's
   reduce/flip legs (e.g. #2491) momentarily take the net position flat — or the
   open-positions index lags — the reconciler marks **all** open same-side trades
   "closed" at once.
3. **Premature close releases the per-strategy monocle**, so the strategy
   re-enters → adds to the net position → GOTO 1. The net short grows; its single
   SL/TP reflects only the latest entry; nothing stops out per-trade.

Demo exposed it (sustained rally → sustained accumulation). Live is exposed in
principle whenever a strategy re-enters the same side while its position is open.

---

## Options

| # | Approach | Pros | Cons / risk |
|---|---|---|---|
| **A** | **Suppress same-direction re-entry while a position is open** — tighten the strategy monocle so a netted add can't be created, and make the reconciler's close robust to transient net-flat (grace / per-order confirmation) so it can't prematurely free the monocle. | Smallest change; keeps one-way mode; directly breaks the cycle; per-trade = per-position again → closed-pnl repopulates (incl. demo). | Changes execution semantics (no pyramiding/adds); must confirm no strategy *intends* to scale in. Tier-3 + walk-forward. |
| **B** | **Hedge mode + `positionIdx` per entry** | True per-trade positions w/ independent SL/TP. | Large Bybit-wide change (every order/close/reconcile path); demo/live parity risk; biggest blast radius. |
| **C** | **Accept netting; track PnL at the net-position level** (stop pretending each signal is a trade). | Honest accounting; no order-path change. | Big journal/dashboard/stats refactor; loses per-strategy per-trade attribution. |
| **D** | **Per-trade SL/TP as separate conditional orders** (not position-level) | Each trade's stop survives re-entry. | Still nets the underlying position in one-way mode; partial fix only; more orders to manage. |

**Recommendation: Option A.** It's the smallest, most direct fix, keeps the
current one-way architecture, and restores the per-trade = per-position invariant
the journal/closed-pnl/stats all assume — which also makes demo PnL attributable
again (closed-pnl repopulates once positions close per-trade). The two sub-changes:
1. **Monocle:** block a new live entry for `(strategy, account, symbol)` while an
   open trade/position exists (don't rely on the reconciler having marked the
   prior one closed).
2. **Reconciler:** require stronger evidence than a single net-flat snapshot
   before closing (e.g. per-orderId close confirmation or an extra grace tick) so
   reduce/flip churn and index lag can't prematurely close + free the monocle.

---

## Validation plan (before any merge)

- Mirror the flip-policy precedent (`docs/audits/walkforward-flip-policy-2026-05-30.md`):
  walk-forward the re-entry-suppression rule across the strategy/symbol matrix;
  confirm trade count, win rate, expectancy, and PnL don't regress vs current.
- Replay the 2026-06-06→08 demo window to confirm the net-short would instead
  have been a sequence of discrete per-trade closes with attributable PnL.
- Stage behind an env kill-switch (like `FLIP_POLICY` / `REGIME_ROUTER_ENABLED`)
  for instant rollback without redeploy.
- Tier-3 gate: explicit operator approval before merge; deploy + verify post-state.

---

## Scope / tier

Touches the order path + reconciler + monocle (live-VM-consumed) → **Tier-3**.
This document is analysis + proposal only. No `src/` / `config/` changes are made
here. On approval, implement Option A behind a kill-switch with the validation
above. Tracked under **BL-20260608-DEMOPNL** + ROADMAP "Items Under Consideration".
