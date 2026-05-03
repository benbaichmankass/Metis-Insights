# Monitor-loop reconciler runbook

Runbook for `_reconcile_open_trades` in `src/runtime/order_monitor.py`.
Sprint: BUG-042 PR 3/3.

---

## What the reconciler does

On every monitor tick (when `MONITOR_RECONCILE_ENABLED=true`), the
reconciler:

1. Reads every row from `trades` where `status='open'` and
   `is_backtest=0`.
2. Groups those rows by `account_id`.
3. For each live account, calls `account_open_positions(cfg)` to fetch
   the exchange's current open-position snapshot.
4. Compares the DB-open set against the exchange snapshot. Any trade
   that is **DB-open but exchange-flat** is an *orphan*.
5. For each orphan:
   - Sets `trades.status = 'orphaned'` and
     `trades.exit_reason = 'reconciler'`.
   - Cascades the linked `order_packages` row to `status = 'closed'`.
   - Enqueues one diagnostic ping via `execution_diagnostics.py` (cap:
     10 individual pings + 1 roll-up per tick).

The reconciler makes **no exchange writes** — it only updates DB rows
and enqueues informational pings.

---

## What it does NOT do

- It does not close positions on the exchange.
- It does not cancel orders.
- It does not modify `position_size`, `entry_price`, or `exit_price`.
- It is not a replacement for the monitor's normal close-detection path
  (`_apply_update`). It is a safety net for rows that slipped through
  that path (e.g. exchange-side close during a bot restart, race between
  tick and exchange settlement).

---

## Skip rules

The reconciler skips an account's trades (leaves them untouched) when:

| Condition | Counter |
|---|---|
| Account `mode` is `dry_run` / `paper` | `skipped_dry` |
| `account_open_positions` returned `None` (missing creds or exchange error) | `skipped_no_creds` |
| `account_id` not found in `accounts.yaml` (disabled / removed account) | `skipped_no_cfg` |

An account appearing in the DB but absent from `accounts.yaml` is never
orphaned automatically — the operator cleans up those rows manually.

---

## The `MONITOR_RECONCILE_ENABLED` flag

| Value | Behaviour |
|---|---|
| `false` (default, pre-PR-3) | Reconciler is a no-op. All counters stay `0`. |
| `true` | Reconciler runs on every monitor tick. |

The flag is re-read on **every tick** (no restart required). An operator
can flip it live via `export MONITOR_RECONCILE_ENABLED=true` in the VM's
systemd override or via a `.env` edit + `systemctl daemon-reload` +
`systemctl restart ict-trader-live.service`.

**Current default (post-PR-3): `true`.** See `.env.master` /
`.env.live` templates.

---

## Interpreting an orphan ping

When you receive a Telegram ping with subject
`orphan_reconciliation` for account `X`, symbol `Y`, side `Z`:

> "The reconciler found that trade `id=N` for `X`/`Y`/`Z` was
> `status='open'` in the DB but absent from the exchange's open-
> position list. It has been marked `status='orphaned'`. No exchange
> action was taken."

**This is expected and self-healing** when:

- The exchange filled the TP/SL while the bot was restarted.
- The position was manually flattened by the operator.
- A network glitch caused the monitor tick to miss the close event.

**Investigate if**:

- The same symbol fires repeatedly across multiple ticks.
- The orphan ping arrives but you still have an open position on the
  exchange UI (suggests `account_open_positions` has a parsing bug).

---

## Manual override SQL

If the reconciler orphaned a row incorrectly (rare — usually a race
between a bot tick and exchange settlement), flip it back with:

```sql
UPDATE trades
SET status = 'open', exit_reason = NULL
WHERE status = 'orphaned' AND id = ?;

UPDATE order_packages
SET status = 'open'
WHERE linked_trade_id = ?;
```

Replace `?` with the trade `id` from the ping. Run this on the VM:

```bash
sqlite3 ~/ict-trading-bot/data/trade_journal.db
```

Then monitor the next tick — if the reconciler immediately re-orphans
it, the exchange truly has no open position and the row was stale.

---

## Cross-references

| Item | Detail |
|---|---|
| PR #357 | Canonical `_log_trade_to_journal` write-back path (prevention) |
| PR #367 | One-shot ghost-trade cleanup notebook |
| PR #384 | BUG-042 PR 1/3 — `account_open_positions` lifted to accounts unit |
| PR #385 | BUG-042 PR 2/3 — `_reconcile_open_trades` implementation |
| This PR (BUG-042 PR 3/3) | Runbook + `MONITOR_RECONCILE_ENABLED` flip to `true` |
| CP-2026-05-03-22 | Sprint kickoff checkpoint (PRs 1+2 merged) |
| `docs/claude/bug-log.md` BUG-042 | Architectural analysis |
