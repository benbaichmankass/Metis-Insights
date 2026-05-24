# Runbook — backfill-orphan-pnl

One-shot Tier-2 operator action that recovers `exit_price` + realised
`pnl` for orphaned trades closed via Bybit V5 broker-side SL/TP. Pairs
with PR #1299 (`account_closed_pnl_for_trade`), which makes new
orphans of this shape impossible going forward.

## When to fire

Whenever the dashboard shows orphaned trades with the watchdog reason
and no exit price — i.e. rows matching:

```sql
SELECT COUNT(*) FROM trades
WHERE status='orphaned'
  AND exit_reason='stuck_strategy_watchdog'
  AND exit_price IS NULL
  AND COALESCE(is_backtest,0)=0;
```

The 2026-05-15/16 cluster (trade ids 1450, 1454–1466 on `bybit_2`
vwap) is the founding case. Going forward, this should land at zero
post-#1299; if it ever creeps up, fire the action again — it's
idempotent.

## Constraints

- **Bybit's `/v5/position/closed-pnl` retains records for 7 days only.**
  Orphan rows older than that will be listed in the "skipped" section
  of the script output and remain `status='orphaned'`. They are not
  recoverable from Bybit; manual cleanup is the only option.
- The action targets **derivatives** accounts (`market_type=linear` /
  `inverse`). Spot accounts have no closed-pnl endpoint and their rows
  are skipped.
- The action only touches rows with `exit_reason='stuck_strategy_watchdog'`.
  Other orphan reasons (e.g. `adopted_orphan_disappeared`) need different
  recovery and are out of scope.

## Dispatch — Claude-driven (default)

Claude fires `system-actions.yml` with `action: backfill-orphan-pnl`
after operator approval in chat. The issue-driven path:

1. Operator says "yes, fire backfill-orphan-pnl" (or equivalent).
2. Claude opens an issue with label `system-action`:

   ```
   action: backfill-orphan-pnl
   reason: <short why>
   ```

3. The workflow runs `scripts/ops/backfill_orphan_pnl_action.sh` on the
   live VM, which:
   - Counts candidate rows pre-run (logged)
   - Runs `backfill_orphan_pnl.py --apply` (prints per-row preview +
     skip reasons before writing)
   - Counts candidate rows post-run (should be 0 if every candidate
     was within Bybit's 7-day window)
   - Records an audit row + comments back on the issue
   - Closes the issue
4. Claude reads the comment to confirm the row count delta and any
   "skipped" rows. If the post-count is non-zero, the operator gets a
   ping with the list of unrecoverable trade ids.

## Dispatch — workflow_dispatch (manual)

Operator clicks "Run workflow" on `system-actions.yml`:

- **Action**: `backfill-orphan-pnl`
- **Reason**: short string explaining why (audit requirement)

Same script path; output streams to the run page instead of an issue
comment.

## Dry-run preview

The script supports `--dry-run` (the default when called without
`--apply`). The action wrapper always passes `--apply` — there is no
dry-run flag exposed at the action level by design, because the
script's own preview-then-write flow is the audit. To preview without
writing, run locally against a DB copy:

```bash
python3 scripts/ops/backfill_orphan_pnl.py --db /path/to/copy.db
```

## What gets written per row

| Column | Before | After |
|---|---|---|
| `status` | `orphaned` | `closed` |
| `exit_reason` | `stuck_strategy_watchdog` | `backfill_closed_pnl_recovery` |
| `exit_price` | `NULL` | recovered `avgExitPrice` from Bybit |
| `pnl` | `NULL` | recovered `closedPnl` (net of fees, from Bybit) |
| `pnl_percent` | `NULL` | `pnl / (entry * size) * 100` |
| `notes` JSON | existing | + `backfilled_at`, `backfilled_by`, `backfilled_source='bybit_closed_pnl'`, `backfilled_pnl`, `backfilled_closed_at`, `exit_price_source='bybit_closed_pnl_backfill'` (existing `orphaned_at` / `orphaned_by` / `orphaned_reason` preserved as audit trail) |

The `exit_reason='backfill_closed_pnl_recovery'` marker makes
backfilled rows distinguishable from native reconciler-closes
(`reconciler_filled`) and from clean monitor-verdict closes
(`tp_cross` / `sl_hit` / etc.). Use it when grepping for
backfilled rows post-action.

## Idempotency

Re-running is a no-op. The SQL guard on every UPDATE is
`WHERE id = ? AND status = 'orphaned'`, so once a row moves to
`closed` it falls out of the candidate set.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| All candidates skipped with `account_closed_pnl_for_trade returned None` | API key missing / wrong env vars on the VM | Inspect `/etc/ict-trader/web-api.env`; check `account_id` matches accounts.yaml |
| Some skipped with `recovered avg_exit_price=0.0` | Bybit returned a malformed record | Operator follow-up; manual close from execution-list endpoint |
| Some skipped with `account_closed_pnl_for_trade returned None`, but only for older trades | Bybit 7-day window expired | Unrecoverable; manual close decision |
| Action fails to start | `accounts.yaml` not present, PyYAML missing, DB path wrong | Inspect the action's audit JSON artifact |

## Related

- PR #1268 — UUID-orderid acceptance fix (made the reconciler actually
  process bybit_2 trades)
- PR #1294 — cascade close via `linked_trade_id` (removed the
  `stuck_cascade_recovered` red-herring path)
- PR #1299 — `account_closed_pnl_for_trade` (real exit_price on
  every future close, so this backfill becomes unnecessary)
- `scripts/ops/backfill_pnl_nulls.py` — sibling backfill for the
  other "closed but pnl=NULL" failure mode (computes PnL locally
  from already-present prices)
