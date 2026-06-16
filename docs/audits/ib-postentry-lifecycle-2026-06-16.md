# IB post-entry lifecycle gap — design proposal (2026-06-16)

**Status: DESIGN — Tier-2, pending operator approval. No code in this PR.**
Ref: operator report 2026-06-16 (ib_paper MGC/MHG trades lingering `open`);
sibling of the merged local-PnL fallback (`local-pnl-fallback-2026-06-16.md`).

## Problem

IBKR **entry** is fully wired (`execute.py::_submit_order` `interactive_brokers`
branch + `IBClient`). But the order-monitor's **post-entry** exchange primitives
are Bybit/binance-only, so they silently no-op for `interactive_brokers`:

| Primitive | File:line | Scope | Effect on IB |
|---|---|---|---|
| `_build_account_client` | `order_monitor.py:1007` | bybit/binance → else `None` | `_send_modify_to_exchange` **and** `_send_close_to_exchange` / partial-close return `{'ok':False,'error':'no_client'}` |
| `account_order_status` | `clients.py:790` | `if ex != "bybit": return None` | forward reconciler `_reconcile_open_trades` skips every IB row (`skipped_no_creds`) |
| `account_closed_pnl_for_trade` | `clients.py` | bybit-only | no broker realised PnL — **now covered** by the merged local-PnL fallback |

Reverse reconciler `_reconcile_orphan_exchange_positions` only closes
`strategy_name='orphan_adopt'` rows on disappear — never a normal strategy
position.

### Live evidence (2026-06-16)
- **MGC #2597** (`pkg-81602a3b78e14f7e`, `mgc_trend_1h`): `monitor()` runs every
  tick (`candles=200`) and emits a trailing-stop `verdict={'sl':4308.58}` →
  `exchange modify … account=ib_paper → {'ok':False,'error':'no_client'}` →
  `errors=1`, repeating for ~2 days. The position can't be managed.
- **MGC #2596** (same strategy): watchdog-orphaned (`stuck_strategy_watchdog`).
- **MHG #2578**: `monitor()` runs, `verdict=None` (daily strategy holding) —
  legitimately open, but additionally `order_package_id=NULL` (link gap; the
  merged coordinator fix + backfill re-link address this).

### Consequence
An IB position opens fine but the monitor cannot **modify** (trail SL), **close**
(exchange-side), or **reconcile** it against the exchange. So when the broker-side
bracket fills (SL/TP hit) the bot never learns — the DB row lingers `open` until
`monitor()` emits a close (which itself can't reach the exchange) or the
stuck-strategy watchdog orphans it. Trailing-stop verdicts spam `errors=1` every
tick. IB is a second-class citizen in the lifecycle layer — the exact structural
shape of the PnL gap just fixed.

## Good news: the IB capability already exists

`IBClient` (`src/units/accounts/ib_client.py`) already implements the needed
surface: `status(order_id)`, `positions()`, `place()`, `place_protective()`,
`cancel()`, `balance()`. And `account_open_positions` (`clients.py:840`) is
**already IB-aware** (line 863). So this is **wiring**, not new broker plumbing.

## Proposed fix — make post-entry reconciliation a declared per-integration capability

Mirror the PnL pattern (`BROKER_PNL_READER_EXCHANGES` + `account_has_broker_pnl_reader`).
Two declared capabilities, each defaulting to the safe option:

### 1. Build an IB client in `_build_account_client`
Extend `_build_account_client` to construct an `IBClient` for
`exchange in ("interactive_brokers","ib")` (reuse `execute.py::get_ib_client` /
the entry-path construction). Then `_send_modify_to_exchange` /
`_send_close_to_exchange` reach IB:
- **Modify (trailing SL):** map to `IBClient.place_protective` (re-arm the GTC
  OCA bracket at the new SL) — `modify_open_order` (`execute.py:1055`) needs an
  IB branch.
- **Close:** `close_open_position` (`execute.py:1129`) needs an IB branch —
  cancel the resting bracket + place an opposing reduce market order (or use
  `IBClient.positions()` to size the flatten).

Guard: a logged-out/unreachable gateway must surface as a *failure* (leave the
DB row untouched, retry next tick) — never a false success. The breaker +
`IB_FETCH_TIMEOUT_S` already bound the hang.

### 2. Reconcile IB open trades against the exchange — **position-snapshot, not order-status**
Order-status reconciliation (the Bybit model via `account_order_status`) is a
poor fit for IB: IB order-id tracking through the journal is brittle, and the
fill/close lands on a separate order the bot doesn't track (the same reason the
Bybit closed-pnl lookup exists). **Recommended: position-snapshot reconciliation
for IB**, reusing the already-IB-aware `account_open_positions`:

- Per tick, for each DB-`open` IB trade, check whether its `(symbol, side)` is
  present in a **successful** `account_open_positions` snapshot.
- If **absent across the existing 2-observation confirm window**
  (`RECONCILER_CLOSE_CONFIRM_SECONDS`, already used by the reverse reconciler),
  close the DB row. The merged local-PnL fallback then fills realised PnL
  (mark-to-market) on the same/next tick.
- **Never** act on a read failure / empty-on-error (the `None` sentinel) — only
  on a confirmed flat snapshot. This is the same conservative contract the
  reverse reconciler's close-on-disappear already follows for `orphan_adopt`.

Declare this as a per-integration **reconciliation mode** capability:
`{order_status, position_snapshot}`, default `position_snapshot` for any
integration without an order-status reader (so a new broker reconciles safely by
default), `order_status` for Bybit (unchanged).

### 3. Interim noise reduction (optional, ship-with-either)
Until (1) lands, suppress the per-tick `ERROR … exchange modify failed … no_client`
for an integration with no modify wiring — log once per `(pkg, reason)` — so a
known-unsupported path isn't alarming every tick.

## Why this is structural, not a band-aid
- The broker-vs-local **PnL** decision and the order-status-vs-position
  **reconciliation** decision both become *declared integration capabilities*
  with safe defaults, resolved through one helper each — not hardcoded
  `== "bybit"` / `!= "bybit"` checks scattered across the monitor.
- Adding a future broker declares both capabilities in one place; the
  `new-broker` skill gains a "declare reconciliation mode" step (sibling of the
  PnL step 2b just added).

## Scope, risk, sequencing
- **Tier-2** (live order-management path) — operator-gated. Propose to implement
  behind a kill-switch mirroring `LOCAL_PNL_COMPUTE_DISABLED`, with unit tests
  (IB client build; position-snapshot close happy-path; no-close-on-read-failure;
  2-observation confirm) before enabling on the VM.
- Independent of, and complementary to, the merged PnL fallback and the
  paper-visibility front-end work.
- Open question for the operator: confirm **position-snapshot** (recommended) vs
  **order-status** reconciliation for IB, and whether to also wire IB trailing-SL
  modify now (1) or defer it (positions stay protected by the entry bracket
  regardless).
