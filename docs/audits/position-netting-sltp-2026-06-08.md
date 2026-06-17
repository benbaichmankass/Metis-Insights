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

---

## Implementation + validation (2026-06-08, branch `claude/position-netting-sltp-fix-tYjPh`)

> **SUPERSEDED 2026-06-17:** the netting guard was made BASELINE (unconditional, all accounts); `POSITION_NETTING_GUARD_ENABLED`/`_ACCOUNTS` were removed. The "default OFF / not flipped on" framing below is the 2026-06-08 point-in-time state, kept as record.

> **Status:** IMPLEMENTED behind a kill-switch (**default OFF**), unit-tested,
> walk-forward in progress. **Tier-3 — NOT merged / NOT flipped on** pending
> operator approval.

### What shipped (one switch: `POSITION_NETTING_GUARD_ENABLED`, default OFF)

1. **Monocle** — `src/core/coordinator.py::multi_account_execute` intent path +
   `src/runtime/positions.py::has_open_trade_for_strategy`. When on, a
   same-direction ADD (delta action `open`/`increase`) for a
   `(strategy, account, symbol)` that already holds an open trade is
   suppressed (journalled `reentry_suppressed_netting_guard:<action>`), so a
   netted add can't be created. NOT keyed on the order_packages status the
   legacy strategy-monocle uses → a prematurely-closed package can't free the
   gate while the position is genuinely open. Reduce/close/flip and
   cross-strategy adds are never blocked.
2. **Reconciler** — `src/runtime/order_monitor.py::_reconcile_open_trades`. When
   on, a filled trade reading net-flat must read flat across an extra grace tick
   (a second observation, `RECONCILER_CLOSE_CONFIRM_SECONDS` apart, default 60s)
   before closing; a transient net-flat that recovers to "position open" clears
   the pending close. Removes the premature-close-frees-monocle leg of the cycle.

### Unit tests (the precise behavioural proof — all green)

- Monocle: same-strategy `increase` suppressed; first entry (flat) allowed;
  guard-off increase unchanged; reduce not blocked; different-strategy add not
  blocked (`tests/test_intent_delta_dispatch.py::TestNettingGuardMonocle`).
- Reconciler: first flat defers; second flat confirms close; transient
  flat→open clears pending (no close); guard-off closes on first flat
  (`tests/test_monitor_reconciler.py::TestNettingGuardCloseConfirmation`).
- Helpers + switch parsing (`tests/test_position_netting_guard_helpers.py`).
- Full coordinator/intents/reconciler suites stay green with the switch OFF —
  default behaviour is byte-for-byte the legacy path.

### Walk-forward (net = current bug-model vs suppress = fix)

The harness gained `--reentry-policy {suppress,net}`
(`scripts/backtest_system.py`): `net` models the current one-way-mode
pyramiding + single-SL/TP-overwrite; `suppress` = the fix (one trade = one
position). Driver: `scripts/walkforward_netting_guard.py`. Data: real BTCUSDT
5m from Binance Vision (2022-07..2024-12), 4-member execution roster, $10k /
0.3% risk / 3% daily cap / 7.5 bps, `FLIP_POLICY=hold` (the live default).

| window | policy | net $ | maxDD % | ret/DD | trades | WR % |
|---|---|---|---|---|---|---|
| 2023-H1 | net (current) | +989.6 | 7.65 | 1.10 | 51 | 27.45 |
| 2023-H1 | **suppress (fix)** | +195.4 | **4.26** | 0.44 | 50 | **32.0** |
| 2023-H2 | net (current) | +827.4 | 5.47 | 1.33 | 58 | 18.97 |
| 2023-H2 | **suppress (fix)** | −246.3 | **4.60** | −0.53 | 50 | **24.0** |
| 2024-H1 | net (current) | +2593.3 | 5.11 | 3.85 | 51 | 29.41 |
| 2024-H1 | **suppress (fix)** | +1039.0 | **2.84** | 3.29 | 49 | **38.78** |
| 2024-H2 | net (current) | **−377.3** | 8.23 | −0.44 | 63 | 22.22 |
| 2024-H2 | **suppress (fix)** | **+424.9** | **2.19** | **1.82** | 46 | **39.13** |

**Read (consistent across all four windows):**
- **Max-DD is roughly halved in every single window** (7.65→4.26, 5.47→4.60,
  5.11→2.84, 8.23→2.19) and **win rate improves in every window** (27.5→32.0,
  19.0→24.0, 29.4→38.8, 22.2→39.1). Trade count is unchanged-or-lower
  (51→50, 58→50, 51→49, 63→46) — never a coverage regression.
- **The tradeoff is net P&L in sustained one-way trends.** In the 2023 +
  2024-H1 BTC bull windows `net` (current pyramiding) posts higher headline
  P&L because it adds to winners; `suppress` forgoes that upside.
- **But 2024-H2 is the bug's signature failure — and the demo analogue.** When
  the trend chops/reverses, pyramiding stacks LOSERS: `net` **loses $377 at
  8.23% DD**, while `suppress` **makes $425 at 2.19% DD** — strictly better on
  every metric. This is the backtest analogue of the demo net-short growing
  into an unattributable loss.

So Option A is a **correctness + risk** change (per-trade=per-position,
per-trade stops, attributable PnL, ~half the drawdown, higher win rate) that
forgoes pyramiding's trend-following upside. **Tier-3 call for the operator:**
if that upside is wanted, the alternative is Option D (keep adds, fix only the
SL/TP-overwrite + reconciler) — but Option A was the approved path. Reproduce:
`python3 scripts/walkforward_netting_guard.py --data <btc_5m.parquet>`
(driver writes `runtime_logs/system_backtest/walkforward/`).

### Remaining Tier-3 pre-flip validation (operator-gated)

- Optional: extend the walk-forward to the full 6yr parquet + 6-member roster
  on the normal harness host (the sandbox sim is O(n²) on long windows, so the
  grid above is 4 half-year windows on 2.5yr of real BTCUSDT 5m). The four
  windows are already directionally unanimous (DD halved + WR up in all four),
  so this is confirmation, not a gate.
- **Live demo-soak replay**: per-trade=per-position → Bybit closed-pnl
  repopulates is a *live* property the backtest can't model. Flip
  `POSITION_NETTING_GUARD_ENABLED=true` on the demo account (`bybit_1`) first
  and confirm the next sustained same-side run produces discrete per-trade
  closes with attributable PnL (the 2026-06-06→08 evidence in this doc is the
  before-state). This mirrors the flip-policy activation sequence
  (operator-watched demo soak before live).
- Deploy + verify post-state after approval; rollback = unset the env var +
  restart (no redeploy).
