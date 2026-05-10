# Closed → exchange-flat invariant reconciler

S-067 follow-up #3 — design memo. **Tier 2** (touches the live-order
path); requires operator ack pre-merge.

## Why this exists

The 2026-05-10 24h trade-performance review surfaced trade #1049: a
row with `status='closed'` in `trade_journal.db::trades` while the
position was still open on the exchange. The position consumed
margin until the existing orphan-position reconciler swept it ~25
minutes later.

The orphan reconciler (`order_monitor._reconcile_orphan_positions`)
is the eventual safety net — it walks open exchange positions that
have no matching `status='open'` DB row and closes them. It's a
slow / loose reconciliation: it runs per tick (15 min cadence) and
checks the open-on-exchange direction.

The **invariant** this PR adds is the tight / fast direction: for
every DB row that just flipped to `status='closed'`, the exchange
should have zero residual size on the same symbol within seconds.
If the exchange still shows a non-zero position, that's a contract
violation — the close path failed silently or partially.

Symmetric to the existing orphan reconciler:

| Direction | Existing | New (this PR) |
|---|---|---|
| open in DB, no exchange position | order_monitor reconciler (alert + close) | unchanged |
| no DB row, open on exchange | orphan-position reconciler (alert + close) | unchanged |
| **closed in DB, open on exchange** | orphan reconciler eventually catches it | **new fast-path check (this PR)** |

## Phase-1 contract: alert-only

This PR ships the invariant in **alert-only mode**:

* Tick loop calls `closed_flat_invariant.check()` after the close
  path runs.
* For every trade row that flipped `status='closed'` in the last N
  seconds (default 60), query exchange residual size for the
  symbol on the matching account.
* On mismatch:
  - Append a structured row to
    `runtime_logs/invariant_violations.jsonl`.
  - Telegram alert via `outcomes.report` (operator-visible, not
    auto-actioned).
  - Return the violation count from `check()`.
* The check **does not auto-flatten** in phase-1. Auto-flatten
  requires: (a) one full week of clean alert-only operation, (b)
  per-account opt-in flag, (c) a separate Tier-2 PR with operator
  ack.

Trade-off: the orphan reconciler is still the eventual close path
during phase-1. The invariant just gives the operator earlier
visibility (seconds vs. up to 30 minutes).

## Phase-2 contract (deferred): auto-flatten gated per account

After the soak window, a follow-up PR adds:

```yaml
# config/accounts.yaml (per-account)
bybit_2:
  closed_flat_auto_flatten: false  # default off; opt-in per account
```

When set to `true`, on a confirmed mismatch the invariant submits
a market close for the residual size with a hard qty cap (mirror
the smoke-test cap pattern). The orphan reconciler remains the
fallback for any case the auto-flatten itself fails.

Auto-flatten is **not** in this PR.

## Phase-1 module API

```python
# src/runtime/closed_flat_invariant.py

@dataclass
class InvariantViolation:
    trade_id: int
    account_id: str
    symbol: str
    db_status: str        # 'closed'
    exchange_qty: float   # non-zero
    detected_at: str      # ISO-8601 UTC

def check(
    db,
    account_resolver: Callable[[str], Optional[Any]],
    *,
    window_seconds: int = 60,
    now: Optional[datetime] = None,
) -> List[InvariantViolation]:
    """Return violations detected in the last `window_seconds`.

    Never raises (same never-raise contract as
    runtime_status.write_status). On internal failure, logs +
    returns an empty list — the orphan reconciler is still the
    safety net.
    """
```

The `account_resolver` callable accepts an `account_id` and
returns a TradingAccount (or equivalent) capable of
`open_positions()`. Injected for testability — the production
caller passes the same resolver the dispatch loop uses.

## Tick-loop wiring (separate PR)

This DRAFT PR ships the **module + tests + memo only**. The
tick-loop wiring is a separate small Tier-2 PR after operator ack
on this design. Wiring lives at `src/runtime/order_monitor.py`'s
post-close hook (where the orphan reconciler runs today) and is
gated by `CLOSED_FLAT_INVARIANT_ENABLED` (default `false`).

Splitting the wiring into a separate PR lets the operator review
the design + tests first without committing to tick-loop changes.

### Wiring applied — post-canon-followups (2026-05-10)

The 3-line wiring patch from
`docs/claude/closed-flat-invariant-phase2-wiring.md` is now applied
to `src/runtime/order_monitor.py::run_monitor_tick`, immediately
after the orphan-position reconciler block and before the final
`return summaries`. The call site uses
`src.runtime._closed_flat_wiring.maybe_run_closed_flat_check`,
which:

* reads `CLOSED_FLAT_INVARIANT_ENABLED` (default `false`) — no-op
  when unset;
* when enabled, builds the account resolver from
  `_load_account_cfgs_for_reconcile` and calls
  `closed_flat_invariant.check(...)` with the
  `runtime_logs/invariant_violations.jsonl` violations log and the
  `outcomes.report` alerter as defaults;
* never raises — the orphan reconciler in the same tick is the
  eventual safety net during the soak.

The env var is **not** set in any deploy or config file. The
operator flips `CLOSED_FLAT_INVARIANT_ENABLED=true` directly on
the VM after merging this DRAFT, then begins the 7-day soak
described in § Soak plan.

Verification:

* `tests/test_closed_flat_wiring.py` — helper-level gate behavior
  (env off / on, no-violation, violation, never-raise, resolver shape).
* `tests/test_closed_flat_wiring_call_site.py` — pins that
  `run_monitor_tick` invokes the helper at the documented post-
  orphan-reconciler hook and that the gate short-circuit holds at
  the integration point.

Rollback: revert the 9-line block added to
`src/runtime/order_monitor.py::run_monitor_tick` (the block
is bracketed by the `# S-067 follow-up #3 Phase-2` comment).

## Output: `runtime_logs/invariant_violations.jsonl`

One JSON object per line, one violation per object:

```json
{"detected_at": "2026-05-10T10:00:00+00:00",
 "trade_id": 1049,
 "account_id": "bybit_2",
 "symbol": "BTCUSDT",
 "db_status": "closed",
 "exchange_qty": 0.001,
 "phase": "alert_only"}
```

Operator alerting consumes this file (not the Telegram channel)
for forensic queries; the Telegram channel only gets the summary
line.

## Soak plan

1. Land this PR (module + tests + memo, no tick-loop wiring).
2. Operator reviews; approves.
3. Land the wiring PR (small, just adds the call site + the env
   gate).
4. On the live VM, set `CLOSED_FLAT_INVARIANT_ENABLED=true` (still
   alert-only — there's no auto-flatten path yet).
5. 7-day soak. Operator monitors `runtime_logs/invariant_violations.jsonl`
   and Telegram alerts.
6. **If clean for 7 days:** file the phase-2 PR adding the
   per-account auto-flatten flag.
7. **If alerts fire:** investigate root cause first; the alert
   itself proves the invariant is working as designed but means
   the close path has bugs to fix.

## Trade #1049 retrospective

The bug that motivated this sprint:

* **Detected**: 25 minutes after close, when the orphan reconciler
  swept it.
* **Detection delay with this invariant** (alert-only): would have
  been ~5 seconds (the configured `window_seconds`, plus one tick
  cadence, plus the Telegram fanout latency).
* **What an alert-only response would have looked like**: operator
  Telegram message at T+5s saying "Trade #1049: closed in DB, but
  BTCUSDT has 0.001 open on bybit_2". Operator could have manually
  flattened immediately rather than waiting 25 min.

## Cross-references

* `docs/sprint-summaries/sprint-067-summary.md` § Hand-off — this
  is item #3.
* `docs/claude/next-session-prompt.md` § Pickup queue § 3 — Tier 2
  classification + soak plan.
* `src/runtime/order_monitor.py::_reconcile_orphan_positions` —
  the existing fallback this invariant complements.
* `src/web/runtime_status.py::write_status` — the never-raise
  contract pattern this module follows.
