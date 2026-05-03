# Sprint plan — BUG-042: monitor-loop write-back reconciler

**Status:** DRAFT — awaiting operator approval (this PR is the work-PR
per CLAUDE.md "Ping-PR vs work-PR" rule).
**Filed:** 2026-05-03 (CP-2026-05-03-20).
**Tier:** 2 — touches `src/runtime/order_monitor.py` +
`src/units/accounts/clients.py` (a per-account exchange-read helper
lifted from `src/units/ui/data_loaders.py`).

## Problem

When a trade is opened by `execute_pkg`, a row is inserted into
`trades` with `status='open'`. The exchange independently closes
positions on TP/SL/manual flatten. Today nothing reconciles the DB's
"open" view against the exchange's "flat" reality, so closed positions
linger as `status='open'` in the trade journal forever — visible in
`/trades`, `/last5`, and the package-level orphan rows that surface in
`/packages`. The cleanup notebook (`notebooks/operator/cleanup_ghost_trades.ipynb`,
PR #367) is the manual one-shot remediation; this sprint adds the
permanent automated equivalent.

This is the same shape as BUG-041 (pre-#357 ghost rows) but caused by
a different mechanism — BUG-041 was rows logged before the canonical
status flow existed; BUG-042 is rows that flow correctly into the DB
but never get the post-close status update.

## Approach (3 PRs)

All 3 PRs are **read-only on the exchange and the DB**. No new
live-order placement. Reconciliation only updates rows that the
exchange independently confirms are flat.

### PR 1 — Foundation: lift `account_open_positions` to clients.py

- **Move** the existing `account_open_positions(account)` helper from
  `src/units/ui/data_loaders.py:750-801` (UI unit) up to
  `src/units/accounts/clients.py` (accounts unit). The UI currently
  reaches across the unit boundary to read exchange state — that's
  the wrong direction per the CLAUDE.md unit-boundary rule. The helper
  belongs to the accounts unit; UI should call into the accounts unit
  to get the data.
- Update `src/units/ui/data_loaders.py` to import + delegate to the
  new location. Behaviour-preserving.
- Tests: `tests/test_accounts_clients_open_positions.py` (~5 tests):
  the lift preserves shape, missing-creds returns empty, exception
  paths return empty, dry-run accounts skip the exchange call, the
  legacy UI delegate still works.
- **No new behaviour.** Pure unit-boundary cleanup. Tier 1 — Tier 2
  classification only kicks in once we wire the reconciler into the
  monitor loop in PR 2.

### PR 2 — Reconciler: `_reconcile_open_trades(db)` in order_monitor.py

- **Add** `_reconcile_open_trades(db)` to `src/runtime/order_monitor.py`.
  Behaviour:
  1. `SELECT * FROM trades WHERE status='open'`.
  2. Group by `account_id`.
  3. For each account: load `account_cfg` from `accounts.yaml`, call
     `account_open_positions(account_cfg)` (now living in
     `src/units/accounts/clients.py` per PR 1).
  4. For every DB-open row whose `(symbol, side)` does not appear in
     the exchange's open-positions list:
     - `UPDATE trades SET status='orphaned', exit_reason='reconciler',
       updated_at=NOW() WHERE id=?`
     - Cascade: `UPDATE order_packages SET status='closed',
       exit_reason='reconciler', updated_at=NOW()
       WHERE linked_trade_id=?`
     - Emit a diagnostic ping via
       `src/runtime/execution_diagnostics.py::enqueue_orphan_reconciliation`
       (new helper, mirrors the existing `enqueue_execution_failure`
       shape). Operator gets one Telegram message per orphan within
       ~5s of the next bot-tick drain.
  5. Wire `_reconcile_open_trades(db)` into the existing per-strategy
     loop in `run_monitor_tick`, gated by env var
     `MONITOR_RECONCILE_ENABLED` (default `false` for PR 2 — flips to
     `true` in PR 3 after one full day of soak in dry mode).
- **Live-mode invariant check**: this PR touches `src/runtime/`
  (Tier-2 surface). Per CLAUDE.md it pings the operator regardless of
  test outcome. The reconciler's only write to the exchange is
  *reading* open-positions; the only DB write is `UPDATE` on
  `status` / `exit_reason`. No `place_order`, no `safe_place_order`,
  no `execute_pkg` call.
- Tests: `tests/test_monitor_reconciler.py` (~10 tests):
  - Empty `trades` table → no-op.
  - DB-open + exchange-flat → `status='orphaned'`, package cascaded,
    ping enqueued.
  - DB-open + exchange-open → no change.
  - Account with missing creds → skip account, log warning, no orphan
    sweep (don't mark rows orphaned just because we couldn't read).
  - Dry-run account → skip (don't reconcile against an exchange we
    don't talk to in dry mode).
  - `MONITOR_RECONCILE_ENABLED=false` (default) → reconciler returns
    immediately, no DB writes.
  - `MONITOR_RECONCILE_ENABLED=true` → full happy path.
  - Symbol-side dedup: same symbol, both buy + sell rows in DB, only
    sell open on exchange → buy gets orphaned, sell stays open.
  - Idempotency: running the reconciler twice in a row with no state
    change between calls → second call is a no-op.
  - Ping payload shape pinned: `account_id`, `symbol`, `side`,
    `db_trade_id`, `linked_package_id`, `reason='reconciler'`.

### PR 3 — Runbook + flag flip + bug-log

- Add `docs/runbooks/monitor-reconciler.md` documenting:
  - What the reconciler does and what it doesn't do (no exchange
    writes, no auto-close).
  - The env var `MONITOR_RECONCILE_ENABLED` and how to toggle it
    (operator notebook + restart command).
  - How to interpret the orphan ping ("the DB and exchange disagreed
    about position X; we marked it orphaned, no action required —
    but if this fires repeatedly for the same symbol, investigate
    `execute_pkg` or the exchange's open-position read path").
  - Manual override: how to flip a row back to `status='open'` if the
    reconciler made the wrong call (only happens with race conditions
    or stale exchange snapshot; runbook explains the SQL).
- Set `MONITOR_RECONCILE_ENABLED=true` in `.env.master` template (the
  flag flip).
- Append BUG-042 row to `docs/claude/bug-log.md` with this PR as the
  fix-PR. Cross-references: PR #357 (canonical `_log_trade_to_journal`
  introduction), PR #367 (one-shot cleanup notebook), CP-2026-05-03-20
  P2 (this sprint kickoff), and the BUG-042 plan filed at
  `~/.claude/plans/bug-042-monitor-loop-write-back.md` (out-of-repo).

## Risk inventory

- **Wrong-direction reconciliation** — DB says open, exchange says
  flat, but the exchange snapshot is stale. Mitigation: the
  reconciler only marks rows as `orphaned` (a terminal-but-suspect
  status), not as `closed_with_pnl`. The runbook covers manual
  override. Cleanup notebook (PR #367) already proves this status
  works end-to-end.
- **Cred misconfig orphaning everything** — an account with bad creds
  returns empty `open_positions`, which would orphan every open trade
  for that account. Mitigation: PR 2 explicitly skips accounts that
  return an exception or whose creds are missing; only accounts whose
  exchange call returned a real (possibly empty) list trigger the
  sweep.
- **Dry-run account collisions** — dry-run accounts have no exchange
  state to read. Mitigation: PR 2 explicitly skips them (the existing
  per-account `mode: live | dry_run` field gates the call).
- **Soak-time before the flag flips** — PR 2 ships with the env var
  defaulting to `false`, so the reconciler is dormant. PR 3 flips it
  on after the operator confirms PR 2's dry-mode soak (one full day
  of running the reconciler in `MONITOR_RECONCILE_ENABLED=false` mode
  on the live VM, watching the diagnostic ping queue stay empty,
  before flipping). The flip itself is a one-line PR.
- **Ping fatigue** — if 50 trades are simultaneously orphaned (e.g.
  the cleanup notebook hasn't run in weeks), the operator gets 50
  Telegram pings. Mitigation: PR 2 caps the ping batch at 10 per
  reconciler tick and emits a single roll-up ping for the rest
  ("…and 40 more — see /last5 for the full list").

## Out of scope

- Any **new live-order placement**. The reconciler is read-only on the
  exchange and write-only on `trades.status` / `order_packages.status`.
- Any **strategy-level** logic. Reconciliation is at the trade-journal
  layer only.
- Backfilling existing orphans. The cleanup notebook (PR #367) is the
  one-shot remediation tool. The reconciler prevents future orphans
  from accumulating; the operator decides when to run the notebook
  for the historical backlog.
- Replacing the cleanup notebook with the reconciler. The notebook
  remains the canonical "I want to inspect + commit a one-shot
  cleanup" tool; the reconciler is the always-on safety net.

## Operator decision points

The operator should respond on this PR with one of:

1. **Approve sprint shape (3 PRs as described)** — I will start PR 1
   in the next session.
2. **Approve with modifications** — comment what should change (e.g.
   "skip the accounts unit-boundary lift, just use the existing
   data_loaders helper", or "ping every orphan, not capped at 10").
3. **Reject** — comment why and I will re-scope.

This PR stays as DRAFT until the operator replies. The ping-PR that
references it (`docs/claude/pending-pings.jsonl` append, separate
branch) is what will fire the Telegram notification on its self-merge.
