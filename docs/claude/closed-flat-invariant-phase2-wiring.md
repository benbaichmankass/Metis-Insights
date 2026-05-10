# Closed → exchange-flat invariant — Phase-2 wiring patch

> **Status:** Tier 2. Operator-acked 2026-05-10.
>
> **Why this doc:** Phase-2 of S-067 follow-up #3 ships the wiring
> for ``closed_flat_invariant`` (Phase-1 in PR #658). The wiring
> helper itself lives in ``src/runtime/_closed_flat_wiring.py``
> (this PR). The remaining 3-line edit to
> ``src/runtime/order_monitor.py`` is documented below; it should
> be applied by an operator with local clone access (the autonomous
> session shipping this PR didn't have direct git auth, and a
> 100KB full-file MCP push for a 3-line edit is fragile).

## Apply this patch to `src/runtime/order_monitor.py`

Locate the **end** of `run_monitor_tick`, immediately after the
S-060 orphan-position reconciler block and **before** the final
`return summaries` line. The relevant region today (around lines
2552-2565):

```python
    # S-060: companion of S-055 for the position leg — sweep
    # spot-margin accounts for non-USDT coin balances that no DB-open
    # long backs (e.g. BTC stranded by the stuck-strategy watchdog
    # force-clearing a vwap long after 30 min). Sells the residue
    # back to USDT so capital doesn't accumulate in stranded
    # inventory. Same MONITOR_RECONCILE_ENABLED gate as the rest.
    try:
        position_recon = _reconcile_orphan_positions(db)
        if position_recon.get("sold") or position_recon.get("errors"):
            summaries["__position_reconciler__"] = position_recon
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: position reconciler raised: %s", exc)

    return summaries
```

Insert the call to `maybe_run_closed_flat_check` between the
`_reconcile_orphan_positions` block and the `return summaries`.
The wiring helper is already imported lazily inside the helper
itself, so the order_monitor.py edit is just an `import` + a
single call:

```diff
     try:
         position_recon = _reconcile_orphan_positions(db)
         if position_recon.get("sold") or position_recon.get("errors"):
             summaries["__position_reconciler__"] = position_recon
     except Exception as exc:  # noqa: BLE001
         logger.warning("run_monitor_tick: position reconciler raised: %s", exc)
+
+    # S-067 follow-up #3 Phase-2: closed → exchange-flat invariant
+    # check. Gated by ``CLOSED_FLAT_INVARIANT_ENABLED`` env (default
+    # false). Alert-only for the first 7 days; promotion to
+    # auto-flatten is a separate PR after the soak window. See
+    # ``docs/claude/closed-flat-invariant.md`` for the full design.
+    from src.runtime._closed_flat_wiring import maybe_run_closed_flat_check
+    maybe_run_closed_flat_check(db, summaries)

     return summaries
```

## Why a separate helper module

`order_monitor.py` is ~100KB. Pushing the full file via MCP
`create_or_update_file` for a 3-line edit is fragile (large
inline content in a single tool call). Extracting the wiring
logic into `src/runtime/_closed_flat_wiring.py` keeps the
live-order-path edit tiny (3 lines) and lets the wiring be
**independently unit-tested** in `tests/test_closed_flat_wiring.py`
without round-tripping the entire `order_monitor` module through
the test harness.

The helper is import-time idempotent and **never raises** —
matches the never-raise contract documented at the top of
`closed_flat_invariant.py`. Even if `closed_flat_invariant.check()`
itself raises something unexpected, the helper catches and logs;
the orphan reconciler in the surrounding tick is the eventual
safety net during the soak window.

## Verification after applying

```bash
# 1. ruff lint
ruff check src/runtime/order_monitor.py
ruff check src/runtime/_closed_flat_wiring.py

# 2. helper unit tests
pytest tests/test_closed_flat_wiring.py -q

# 3. full closed-flat invariant tests (Phase-1 still passes)
pytest tests/test_closed_flat_invariant.py -q

# 4. integration: run_monitor_tick still ships
pytest tests/test_s030_pr3_monitor_loop.py -q

# 5. live-mode invariant — no other forbidden-file edits
git diff --stat origin/main..
# expected diff:
#   src/runtime/_closed_flat_wiring.py            | +N  (new file)
#   src/runtime/order_monitor.py                  | +6 -0
#   tests/test_closed_flat_wiring.py              | +N  (new file)
#   docs/claude/closed-flat-invariant-phase2-wiring.md | +N  (this file)
```

## Rollout plan (post-merge)

| Day | Action |
|---|---|
| D0 | Operator merges this PR. `CLOSED_FLAT_INVARIANT_ENABLED` stays unset → no behaviour change on live VM. |
| D1 | Operator sets `CLOSED_FLAT_INVARIANT_ENABLED=true` in `/etc/ict-trader/closed_flat.env` (or whatever env-shipping mechanism the deploy uses). Restart `ict-trader-live.service`. The check now runs every tick in alert-only mode. |
| D1 → D7 | Soak. Observe `runtime_logs/invariant_violations.jsonl` and Telegram alerts. Expected count: **0** in steady state — the orphan reconciler should keep the DB and exchange in sync. Any non-zero count is a bug surface to investigate. |
| D8+ | If the soak is clean (≤ 1 violation/week, all explainable as known reconciler-races), file the auto-flatten promotion PR: add a per-account `closed_flat_auto_flatten` flag in `config/accounts.yaml` and wire `closed_flat_invariant.check` to call `account_open_positions` + `close_open_position` instead of just alerting. Tier 2; needs operator ack of the per-account-flag PR before merge. |

If a violation fires during the soak, the alert body identifies the
trade id, account, symbol, and signed exchange residual qty.
Investigate by:

```bash
# Inspect the violation log
tail -50 runtime_logs/invariant_violations.jsonl | jq

# Cross-reference against the trade row
sqlite3 trade_journal.db "SELECT id, status, symbol, direction,
    entry_price, exit_price, pnl, COALESCE(notes, '') FROM trades
    WHERE id = <trade_id>;"

# Check the orphan reconciler's view
sqlite3 trade_journal.db "SELECT id, status, linked_trade_id,
    strategy_name, updated_at FROM order_packages
    WHERE linked_trade_id = <trade_id>;"
```

The expected resolution path during the soak is "the orphan
reconciler caught it within 1-2 ticks" — i.e. the violation log
shows it but the next tick's reconciler pass flattens the residue.
If a violation persists across multiple ticks, the orphan
reconciler has a bug that should be hardened before promoting to
auto-flatten.

## Cross-references

* `src/runtime/closed_flat_invariant.py` — Phase-1 module (PR #658).
* `src/runtime/_closed_flat_wiring.py` — Phase-2 wiring helper (this PR).
* `tests/test_closed_flat_wiring.py` — Phase-2 wiring tests (this PR).
* `tests/test_closed_flat_invariant.py` — Phase-1 module tests (PR #658).
* `docs/claude/closed-flat-invariant.md` — full design memo (PR #658).
* `docs/claude/checkpoints/CP-2026-05-10-04-s067-phase2-followups.md` — session ledger that filed this PR (with operator ack).
