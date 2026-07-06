# Spot-margin operations runbook (S-047 D8)

How to inspect, recover, and live-smoke-test the **Bybit V5 Spot Margin**
account (`bybit_2` today). Read this when:

- a borrow line is sitting in the wallet without an open trade row,
- a VWAP short on `bybit_2` is stuck open,
- you're preparing the T6 mainnet live smoke,
- or you want to know what the reconciler stack does and doesn't catch.

Companion docs:
- [`docs/sprint-plans/S-047-bybit2-spot-margin.md`](../sprint-plans/S-047-bybit2-spot-margin.md) — full sprint plan.
- [`docs/runbooks/monitor-reconciler.md`](monitor-reconciler.md) — the broader monitor + reconciler design.
- [`docs/runbooks/live-smoke-test.md`](live-smoke-test.md) — the generic live smoke (cash-spot + futures).
- [`docs/claude/closed-flat-invariant.md`](../claude/closed-flat-invariant.md) — Phase-1 alert-only invariant on `closed in DB / open on exchange` mismatches.

---

## 1. What spot-margin is on Bybit

A short on a Bybit Spot Margin account is **a borrow + spot sell**. It
does NOT show up in `get_positions(category=...)` because spot-margin
isn't a derivative — there's no on-exchange `position` record.

The borrow line is the position. Concretely:

| DB direction (on `<COIN>USDT`) | Wallet shape                              | What the reconciler reads          |
|---|---|---|
| `short` | `<COIN>.borrowAmount > 0`                  | "the position is alive"           |
| `long`  | `<COIN>.walletBalance > 0` (over zero baseline) | "the position is alive"      |
| `flat`  | both at/below epsilon                       | "OK to orphan / repay"            |

The synthesiser at
[`src/units/accounts/clients.py::_spot_margin_open_positions`](../../src/units/accounts/clients.py)
turns the wallet response into a position-shaped list so the reconciler
can use the same matching code as cash-spot and linear futures.

There is **no exchange-side SL/TP** on spot-margin. Closing a
spot-margin trade means: the strategy's `monitor()` decides to close,
the dispatcher fires a market order on the opposite side, and the
post-close path repays any residual borrow. If the strategy's
`monitor()` doesn't fire, nothing closes.

---

## 2. Borrow-fee accrual visibility

Borrow fees accrue per Bybit's published spot-margin rate (interest
debited continuously, settled on close / repay). To inspect:

### Live wallet snapshot (what the bot sees)

The bot reads the wallet via pybit's `get_wallet_balance(accountType="UNIFIED")`.
The relevant fields per coin row:

```text
coin            BTC | USDT | ...
walletBalance   on-account holdings (positive)
borrowAmount    outstanding borrow line (positive when short)
accruedInterest cumulative interest debited (Bybit-side; not always
                returned — check your account's Spot Margin settings page)
```

From a sandbox / PM-side session, fetch the same view via the diag
relay:

```text
[diag-request] /api/diag/journal?table=trades&limit=20
```

That shows the trade rows; cross-reference against the wallet by
opening a `vm-diag-snapshot` issue for `/api/diag/snapshot?limit=200`,
which bundles `signal_audit.jsonl` tail + journal + status.

### On the VM directly (operator / VM-resident Claude)

```bash
# Wallet snapshot via pybit (what the reconciler reads)
cd /home/ubuntu/ict-trading-bot
PYTHONPATH=. python -c "
from src.units.accounts.clients import account_for_id, _spot_margin_open_positions
acc = account_for_id('bybit_2')
print(_spot_margin_open_positions(acc.client))
"
```

Expected steady state: empty list when no trade is open. A short on
`BTCUSDT` shows as `{symbol: 'BTCUSDT', side: 'short', size: <borrow>}`.

### Bybit web-UI

Account → Assets → Spot Margin → **Borrow** tab. Shows live
`borrowAmount` and current accrued interest per coin. Reconcile
against the bot's view above; any divergence is a reconciler bug.

---

## 3. The reconciler stack

Three independent layers run on every monitor tick (15-min cadence by
default; per-tick when `TICK_INTERVAL_SECONDS` is shorter). **Historical
note (BL-20260706-SPOTMARGIN-RUNBOOK-STALE-GATE):** these were originally
gated by `MONITOR_RECONCILE_ENABLED=true`; that flag was **removed
2026-06-15** (BL-20260615-MGCNAKED) — the reconciler is now unconditional
baseline behaviour (no enable gate, per the Prime Directive) and a
leftover value in `.env` is ignored. Each layer is still best-effort —
failures log and skip; the next tick re-attempts.

### Layer 1 — main reconciler (`_reconcile_open_trades`)

Walks every `trades.status='open'` row, queries the matching account's
"open positions" list (which for spot-margin is the synthesised view
above), and:

- **DB-open AND exchange-flat for ≥ grace window** → mark `status='orphaned'`, exit_reason='reconciler'`. The orphan reconciler in Layer 3 reaps any non-USDT residue.
- **DB-open AND exchange-open** → no-op.
- **No DB row AND exchange-open** → see Layer 3.

### Layer 2 — borrow-orphan reconciler (`_reconcile_orphan_borrows`, S-055)

Walks every spot-margin account's coin rows. For any
`coin.borrowAmount > epsilon` with no open DB trade backing it
(matched per the `_open_trade_backs_borrow` shape in
`order_monitor.py`), force a `_spot_margin_repay(client, coin=…, qty=…)`.

Skips when:
- account has a recent trade inside the grace window (post-close repay
  may still be racing the read),
- an open trade does back the borrow (it's not orphaned),
- creds are missing.

Wraps `/v5/account/repay` (pybit's `HTTP.repay`). Audit appended to
`runtime_logs/operator_actions/...borrow_orphan_repaid.json`.

### Layer 3 — orphan-position reconciler (`_reconcile_orphan_positions`, S-060)

Companion of Layer 2 for the long leg: walks spot-margin accounts for
non-USDT `walletBalance > 0` that no DB-open long backs. Sells the
residue back to USDT so capital doesn't accumulate in stranded base
coin (e.g. BTC stranded by a stuck-strategy watchdog force-clearing a
VWAP long).

### Layer 4 — closed-flat invariant (S-067 fu #3, currently in alert-only soak)

Tight / fast-direction check: for every trade row that just flipped to
`status='closed'` in the last 60 s, verify the exchange residual is
zero on the same `(symbol, side)`. On mismatch: append to
`runtime_logs/invariant_violations.jsonl` and Telegram via
`outcomes.report`. **No auto-flatten in Phase-1.** Layers 1–3 remain
the eventual safety net during the soak.

---

## 4. How to manually flatten a stuck borrow

Use this when:
- the wallet shows a borrow line that's been sitting longer than 30 min,
- AND the reconciler isn't repaying it (check `runtime_logs/operator_actions/`
  for the most recent `borrow_orphan_repaid` audit),
- AND there's no open DB trade you'd be racing.

### Sequence

1. **Confirm** what's actually outstanding.

   ```bash
   cd /home/ubuntu/ict-trading-bot
   PYTHONPATH=. python -c "
   from src.units.accounts.clients import account_for_id
   acc = account_for_id('bybit_2')
   resp = acc.client.get_wallet_balance(accountType='UNIFIED')
   for c in resp['result']['list'][0]['coin']:
       if float(c.get('borrowAmount') or 0) > 0 or float(c.get('walletBalance') or 0) > 1e-9:
           print(c['coin'], 'borrow=', c.get('borrowAmount'), 'wallet=', c.get('walletBalance'))
   "
   ```

2. **Decide on the action**.

   - **Short borrow** (e.g. `BTC.borrowAmount = 0.001`):
     market BUY 0.001 BTCUSDT spot with `isLeverage=1`. The buy
     consumes the borrow line. Use `safe_place_order` so the trader's
     audit + reconciler ledger sees it:

     ```python
     from src.units.accounts.execute import safe_place_order
     safe_place_order(
         account_id="bybit_2",
         symbol="BTCUSDT",
         side="buy",
         qty=0.001,
         is_leverage=1,
         dry_run=False,
         meta={"strategy_name": "manual_flatten_borrow", "reason": "operator manual flatten"},
     )
     ```

   - **Long residue** (e.g. `BTC.walletBalance = 0.0008` with no DB-open long):
     wait one tick — Layer 3 will sell it. If still stuck after 2 ticks,
     market SELL the same qty.

   - **Residual borrow after the close** (e.g. `BTC.borrowAmount = 1e-7`
     dust): call `_spot_margin_repay` directly:

     ```python
     from src.units.accounts.clients import account_for_id
     from src.units.accounts.execute import _spot_margin_repay
     acc = account_for_id("bybit_2")
     _spot_margin_repay(acc.client, coin="BTC")  # qty=None → repay all
     ```

3. **Verify the wallet went flat** by re-running step 1. The borrow
   line should be at/below `_BORROW_REPAY_EPSILON` (1e-8).

4. **Record the audit**. The reconciler audit format is
   `runtime_logs/operator_actions/<ts>-borrow_orphan_repaid.json`; if
   you flattened by hand, append a parallel record so the next session
   has the trail:

   ```bash
   bash scripts/ops/_lib.sh   # not directly executable; use record_audit
   ```

   Or open an `system-action` issue with `action: status-check` so the
   workflow snapshots the post-flatten wallet state.

### What NOT to do

- **There is no `MONITOR_RECONCILE_ENABLED` toggle to disable anymore**
  (removed 2026-06-15, BL-20260615-MGCNAKED — the reconciler is
  unconditional baseline; a leftover env value is ignored). If the
  reconciler is misbehaving, fix the underlying bug — there is no
  flag-flip escape hatch. (Historically the flag shipped `true` in every
  `.env` rendered from `scripts/render_env_from_master.py` — BUG-048
  wired the contract test for that — before it was removed entirely.)
- **Don't manually delete `trade_journal.db` rows.** The reconciler
  uses them to decide whether a borrow is orphaned. Use
  `Database.update_trade(trade_id, {"status": "orphaned", ...})`
  through the operator console if a row needs reclassifying.
- **Don't repay through the Bybit web-UI** when the trader is running.
  The bot's wallet read may race the manual repay and momentarily
  see the borrow as "settled, no DB row" → trigger Layer 2 → emit a
  spurious audit. If you must use the web-UI, halt the trader first
  (`system-action: restart-bot-service` after the manual repay
  completes is sufficient).

---

## 5. Escalation triggers

| Symptom | First action | Escalate when |
|---|---|---|
| Borrow > 30 min with no DB trade | Wait 1 tick — Layer 2 should repay | Still present after 2 ticks; check `runtime_logs/operator_actions/` for `borrow_orphan_repaid` errors |
| `walletBalance > 0` on base coin with no DB-open long | Wait 1 tick — Layer 3 should sell | Still present after 2 ticks; check `_reconcile_orphan_positions` errors in `bot.log` |
| `closed_flat_invariant` Telegram alert fires | Check `runtime_logs/invariant_violations.jsonl` for the trade_id; cross-reference DB row + wallet | Phase-1 expectation is 0 alerts. Any non-zero is a bug surface in the close path |
| `pybit.UnifiedException: Cross-margin liability limit` on a fresh short | Bybit Spot Margin not enabled OR liability cap hit | Check Bybit web-UI Spot Margin settings; confirm `bybit_2` toggle is ON |
| Borrow line stable but `accruedInterest` climbing without a close | Strategy's `monitor()` isn't closing — VWAP HTF gate may be holding | Inspect `runtime_logs/signal_audit.jsonl` for the strategy's recent `monitor()` verdicts; if always `None`, the strategy has no exit logic for current conditions — file a Tier-3 ping |
| Bybit `ErrCode 170131` (Insufficient balance) on `Buy isLeverage=1` (LONG spot-margin) | The borrow gate is wedged — RiskManager's USDT view disagrees with what Bybit will accept. The coordinator's circuit breaker auto-flips the account to `dry_run` after 3 consecutive `exchange_rejected` (see `_EXCHANGE_REJECTION_PAUSE_THRESHOLD` in `src/core/coordinator.py`); `accounts auto-paused` critical alert fires on Telegram | Investigate the USDT-view mismatch before flipping the account back to live: stale balance read, locked USDT in open orders not subtracted, or borrow-pool actually exhausted at Bybit. Manual unpause: `/account_mode bybit_2 live` once you've confirmed available USDT > intended notional. |

---

## 6. T6 live smoke procedure (mainnet)

This is the canonical S-047 T6 acceptance: a 0.0005 BTC short on
`bybit_2` mainnet that opens via VWAP, cycles through monitor → close,
and leaves the journal + reconciler in agreement.

### Pre-conditions (operator)

- ✅ Bybit web-UI Spot Margin toggle ON for `bybit_2`.
- ✅ T1–T5 merged (D2..D7 — already done at sprint start of T6).
- ✅ Trader running on the VM (`ict-trader-live.service` active).
- ✅ `bybit_2.mode: live` in `config/accounts.yaml` (NOT `dry_run`).
- ✅ Reconciler runs unconditionally — no `MONITOR_RECONCILE_ENABLED` gate
  to check (the flag was removed 2026-06-15, BL-20260615-MGCNAKED).

### Run

The S-047 testnet smoke
[`scripts/sprint047/spot_margin_smoke.py`](../../scripts/sprint047/spot_margin_smoke.py)
ships at qty `0.0005 BTC` and is the testnet rehearsal. The mainnet
equivalent uses the **standard live-smoke harness** —
`scripts/smoke_test_trade.py` + `runtime_flags/run_smoke_once.flag` —
parameterised for `bybit_2` short:

```bash
# On the VM, as the operator (one-shot):
cd /home/ubuntu/ict-trading-bot
ALLOW_LIVE_TRADING=1 python scripts/smoke_test_trade.py \
    --account bybit_2 \
    --symbol BTCUSDT \
    --direction short \
    --qty 0.0005 \
    --strategy-tag s047-t6-mainnet
```

Or fire the full 4-trade smoke (`run_smoke_once.flag`) and inspect the
`bybit_2` short slice — the existing harness covers `bybit_2` real qty
already.

### Acceptance

The smoke is **OK** when, within 2–3 monitor ticks of the open:

1. `trades` row for the smoke trade flips `open → closed` with
   `exit_reason` set (typically `monitor_close: tp_partial` or
   `vwap_close`, depending on the close path that fired).
2. `bybit_2.coin.BTC.borrowAmount` returns to `≤ _BORROW_REPAY_EPSILON`.
3. `_reconcile_orphan_borrows` summary shows `repaid: 0` AND
   `skipped_holding_trade: 0` for the next tick — confirming the close
   path repaid the borrow itself, not the reconciler.
4. No `closed_flat_invariant` violation row appears in
   `runtime_logs/invariant_violations.jsonl`.
5. No `borrow_orphan_repaid` audit appears in
   `runtime_logs/operator_actions/` for the same time window.

### If it fails

| Failure mode | Likely cause | Recovery |
|---|---|---|
| Smoke trade rejected at submit | Spot Margin toggle off, or `bybit_2.mode == dry_run` | Verify pre-conditions; do NOT retry until both confirmed |
| Trade opens but never closes (sits open > 30 min) | VWAP `monitor()` not firing close — check HTF gate / killzone state | Inspect `signal_audit.jsonl`; if the strategy's hold conditions are correct, manual flatten per § 4 |
| Trade closes but borrow line persists | Post-close repay raced the wallet read; or `_spot_margin_repay` returned `ok=False` | Wait one tick — Layer 2 (S-055) reaps. If still stuck, manual repay per § 4 step 2c |
| `closed_flat_invariant` alert fires | Genuine close-path bug — DB says closed, exchange still has residual | DO NOT promote the soak to Phase-2. File a bug, investigate the close path |

---

## 7. Cross-references

- BUG-046 / BUG-049 / BUG-048 (the family that motivated S-047):
  - BUG-046 — strategy gate over-broad: open packages without trades silenced strategies.
  - BUG-049 — VWAP stopped sending signals because of accumulated
    BUG-046 packages on `bybit_2`.
  - BUG-048 — `MONITOR_RECONCILE_ENABLED` env render drift made the
    reconciler no-op for ~8 h; trade #24 sat open while the wallet was
    flat. (The flag was removed entirely 2026-06-15, BL-20260615-MGCNAKED
    — the reconciler is now unconditional baseline, so this class of
    drift can't recur.)
- All three were structural symptoms of the missing spot-margin handling that S-047 fixes. See the bug-log entry referencing S-047.
- Live-smoke generic doc: [`docs/runbooks/live-smoke-test.md`](live-smoke-test.md).
- Monitor + reconciler design: [`docs/runbooks/monitor-reconciler.md`](monitor-reconciler.md).
- Closed-flat invariant Phase-1: [`docs/claude/closed-flat-invariant.md`](../claude/closed-flat-invariant.md).
