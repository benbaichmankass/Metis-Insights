# Live-trade management contract — whole-repo audit + design plan (2026-06-16)

**Status: DESIGN — for operator approval. No production code in this PR.**
Supersedes the narrower IB-only draft. Ref: operator direction 2026-06-16
("this isn't an IB fix — it's a two-sided contract that must hold for every
strategy and every integration, baked into how we build them").

## The spec (the contract this audit measures against)

While a trade / order-package is live, the system must, for **every** strategy
and **every** integration:

1. **Strategy side** — the owning strategy continuously **monitors** its live
   trade and **emits update verdicts** (adjust SL, adjust TP, partial close,
   full/thesis close, time-decay exit) as conditions change.
2. **Integration side** — the integration **applies** those verdicts to the
   live exchange orders (modify SL/TP, close / partial-close) **and reconciles**
   live exchange state back into the journal (detect fills/closes/orphans, fill
   realised PnL).

Both halves must be **uniform and enforced by construction** — a new strategy
or a new integration should be unable to ship without satisfying its half.

## Current reality (audited 2026-06-16, against `main`)

### Strategy side — broad and uniform (the healthy axis)
Every configured strategy resolves to a unit module with a `monitor()`
(CI-guarded by `tests/test_strategy_monitor_unit_resolution.py`). Verdicts range
from full (turtle_soup: partial closes + TP1/TP2 roll + BE) through trail+close
(trend_donchian, htf_pullback_trend_2h, fade/squeeze) to minimal (ict_scalp:
BE-only; fvg_range: time-decay-only). **Gap:** `monitor()` is a *convention*,
not part of `StrategyInterface`/`StrategyBase`; the CI guard checks the method
*exists*, not its verdict shape, and `new-strategy` doesn't state the verdict
contract. Verdicts reach the exchange only as well as the integration allows —
which is the broken axis:

### Integration side — Bybit-first; update-application is Bybit-only
| Integration | Account mode | Entry | **Apply: modify SL/TP** | **Apply: close/partial** | Reconcile: order_status | Reconcile: open_positions | Realised PnL |
|---|---|---|---|---|---|---|---|
| **bybit** | live + demo | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ broker |
| **interactive_brokers** (`ib_paper`) | **live** | ✅ | ❌ `no_client` | ❌ `no_client` | ❌ | ✅ | ✅ local (#3761) |
| **alpaca** (`alpaca_paper`) | **live** | ✅ | ❌ `no_client` | ❌ `no_client` | ❌ | ❌ | ✅ local (#3761) |
| **oanda** (`oanda_practice`) | dry_run | ✅ | ❌ (masked by dry skip) | ❌ | ❌ | ❌ | ✅ local (#3761) |
| **binance** | (unused) | ✅ | ❌ unsupported | ❌ unsupported | ❌ | ✅ | n/a |

Chokepoints (all on `main`):
- `order_monitor.py::_build_account_client` builds clients only for `bybit` /
  `binance` (`:1040-1044`) → `_send_modify_to_exchange` / `_send_close_to_exchange`
  / partial return `{'ok':False,'error':'no_client'}` for everything else.
- `execute.py::modify_open_order` / `close_open_position` are *"bybit only in
  v1"* (`:1126`, `:1197`).
- `clients.py::account_order_status` is `if ex != "bybit": return None` (forward
  reconciler skips all non-Bybit rows).
- `clients.py::account_open_positions` covers bybit + IB + binance, **not**
  oanda/alpaca (reverse reconciler can't even adopt/close their orphans).
- IB has no `close`/`modify` primitive — only `IBClient.place_protective`
  (bracket re-arm, used by naked-autoprotect, not the verdict path) + `cancel`.

**Already fixed (#3761, merged 2026-06-16):** realised-PnL reconciliation is now
universal (`BROKER_PNL_READER_EXCHANGES` + `_sweep_local_pnl_for_unpriced` +
the dashboard mark-to-market unrealised fallback) — so the *PnL* half of
"reconcile" is spec-compliant and provides the **template pattern** for this plan
(a declared per-integration capability with a safe default).

### Severity-ranked gaps (the update-application + reconciliation half)
1. **CRITICAL — Alpaca live positions are unmanaged.** `alpaca_paper` is
   `mode: live` running `spy/qqq_trend_long_1d` + `gld_pullback_1d`; their
   trail/close verdicts hit `no_client` every tick and never reach Alpaca. Only
   the entry bracket protects them. (This is the operator's "not IB-specific"
   point, confirmed.)
2. **CRITICAL — IB (`ib_paper`) live positions are unmanaged.** 4 trail/close
   strategies; verdicts hit `no_client`; IB has no close/modify primitive.
   Live evidence: MGC #2597's trailing-stop modify `no_client`-errors every tick
   for ~2 days. Partially mitigated by reverse-reconcile + naked-autoprotect +
   entry bracket.
3. **MEDIUM — reconciliation is Bybit-centric** (forward order-status Bybit-only;
   `account_open_positions` omits oanda/alpaca).
4. **MEDIUM — the build workflow doesn't enforce either half** — `new-broker`
   step 4 requires only entry wiring; `new-strategy` doesn't mandate `monitor()`
   or its verdict schema; no CI test asserts management/reconciliation coverage
   (existing tests *codify* the Bybit-only limitation).
5. **LOW — verdict-shape divergence** among Bybit strategies (acceptable; broker
   bracket backstops them).

## Design plan (make the contract real + enforced, not patched)

The fix mirrors the #3761 template on **both** axes: declare the capability,
default safe, route through one uniform resolver, enforce in CI + the build
skills.

### 1. Strategy side — `monitor()` becomes a first-class, enforced contract
- Promote `monitor(cfg, candles_df, open_pkg) -> Verdict|None` into
  `StrategyInterface` / `StrategyBase` with a **documented, validated verdict
  schema** (`{sl}`, `{tp}`, `{action:'close', reason, close_qty_pct?}`).
- A `Verdict` validator + a CI test asserting every registered strategy's
  `monitor()` returns schema-valid verdicts on representative candles (not just
  that the method exists).
- `new-strategy` skill: a dedicated **"implement `monitor()` + its verdict
  contract"** step (today it only mentions `monitor_breakeven_sl` in passing).

### 2. Integration side — a uniform management interface + declared capability
- Define a **management capability** per integration (sibling of
  `BROKER_PNL_READER_EXCHANGES`): which of `{modify, close, partial_close,
  order_status, open_positions}` each integration implements. One resolver
  (`account_management_caps(account)`), no scattered `== "bybit"` checks.
- Route `_build_account_client` + the senders through the **`<Broker>Adapter`
  facade** that `new-broker` step 1 already mandates but the management path
  bypasses — so modify/close/reconcile call a uniform adapter method, not a
  Bybit-only branch.
- **Wire the missing live integrations:**
  - **IB**: add `modify` (re-arm GTC OCA bracket via `place_protective` at the
    new SL/TP) + `close` (cancel resting bracket + opposing reduce order) on
    `IBClient`; build an IBClient in `_build_account_client`.
  - **Alpaca**: add modify/close via the Alpaca client (replace / close-position
    APIs) + `account_open_positions` coverage.
  - **OANDA**: same, before it's promoted off `dry_run`.
- **Universal reconciliation**: make **position-snapshot** reconciliation
  (reuse the IB/Alpaca-aware `account_open_positions`) the default path for
  integrations without an order-status reader — close a DB-open row only when
  its `(symbol,side)` is absent across the existing 2-observation confirm window,
  never on a read failure. Order-status stays the Bybit enhancement.

### 3. Enforcement (so we stop patching)
- CI guard: **every `EXCHANGE_MAP` integration must implement the management
  interface** (or explicitly declare an op `unsupported` with a reason) — a
  failing test, not a codified gap.
- CI guard: **every strategy's `monitor()` returns schema-valid verdicts.**
- `new-broker` skill: add required steps for the management primitives +
  reconciliation (today step 4 is entry-only) + a post-ping verification that a
  live trade can be modified/closed/reconciled end-to-end.

### 4. Real vs paper performance — kept strictly separate (operator directive)
- No combined real+paper totals anywhere. Each metric (open count, win rate,
  expectancy, realised/unrealised PnL, equity curve) computed and displayed per
  funding class. Even per-strategy PnL% is split by real/paper, not blended.
- Bot side: `/api/bot/stats` + `/api/bot/performance` expose a separate `paper`
  block (the latter already does); `openTrades` stays real-money, with a
  distinct paper open-count rather than a merged number. Dashboard/Android
  render them as separate sections (the merged paper-visibility PRs only *label*
  list rows — consistent; the aggregate separation is the remaining work).

### Rollout
Phased, each behind a kill-switch mirroring `LOCAL_PNL_COMPUTE_DISABLED`, with
unit tests before VM enablement; Tier-2 (live order-management path), so each
phase is operator-gated:
- **P1** Strategy-side contract + CI enforcement (no live-path risk).
- **P2** Integration management interface + capability + adapter routing
  (refactor, behavior-preserving for Bybit).
- **P3** Wire IB + Alpaca modify/close + universal position-snapshot reconcile
  (the live-management gap — the CRITICAL items).
- **P4** Real/paper metric separation (bot + clients).
- **P5** Build-workflow skills + CI guards (lock it in).

## Open decisions for the operator
1. Approve this whole-repo plan + phasing (vs. a narrower first cut on just the
   two live integrations IB+Alpaca)?
2. IB/Alpaca management: implement **modify (trailing SL)** now, or first just
   **close** (thesis/SL-cross exits) since the entry bracket already holds a
   static stop? (Close is the higher-value safety item; trailing-modify is
   optimization.)
3. Reconciliation default for non-Bybit: confirm **position-snapshot** (reuses
   existing IB/Alpaca-aware `account_open_positions`, robust to missing
   order-ids) over per-order order-status.
