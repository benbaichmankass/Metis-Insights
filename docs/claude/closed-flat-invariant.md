# Closed → exchange-flat invariant reconciler

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

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
soak is started and rolled back via the canonical system-actions
GitHub workflow — no SSH-from-laptop required:

* **Start the soak:** dispatch `enable-closed-flat-invariant`
  (Tier 2). Wrapper:
  `scripts/ops/enable_closed_flat_invariant.sh` — atomic write
  of `CLOSED_FLAT_INVARIANT_ENABLED=true` to
  `/home/ubuntu/ict-trading-bot/.env`, then
  `systemctl restart ict-trader-live.service`.
* **Roll back:** dispatch `disable-closed-flat-invariant`. Strips
  the env line from `.env` and restarts the trader. Symmetric
  companion of the enable action.

Both actions are allowlisted in
`.github/workflows/system-actions.yml` and follow the same
audit + Telegram-notify shape as `restart-bot-service`. See
`docs/claude/system-actions.md` for the dispatch flow.

Verification:

* `tests/test_closed_flat_wiring.py` — helper-level gate behavior
  (env off / on, no-violation, violation, never-raise, resolver shape).
* `tests/test_closed_flat_wiring_call_site.py` — pins that
  `run_monitor_tick` invokes the helper at the documented post-
  orphan-reconciler hook and that the gate short-circuit holds at
  the integration point.

Rollback paths, in order of cost:

1. **Soft rollback (preferred):** dispatch
   `disable-closed-flat-invariant`. Tick-loop call site stays in
   place; the helper short-circuits on the missing env var and the
   trader returns to pre-soak behaviour after the restart.
2. **Hard rollback (only if the wrapper itself is at fault):**
   revert the 9-line block added to
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
   gate). [done — PR #679, 2026-05-10]
4. Dispatch the `enable-closed-flat-invariant` operator action
   (Tier 2). The action atomically sets
   `CLOSED_FLAT_INVARIANT_ENABLED=true` in
   `/home/ubuntu/ict-trading-bot/.env` and restarts
   `ict-trader-live.service`. Still alert-only — there's no
   auto-flatten path yet.
5. 7-day soak. Operator monitors `runtime_logs/invariant_violations.jsonl`
   and Telegram alerts.
6. **If clean for 7 days:** file the phase-2 PR adding the
   per-account auto-flatten flag.
7. **If alerts fire:** investigate root cause first; the alert
   itself proves the invariant is working as designed but means
   the close path has bugs to fix. Soft rollback during
   investigation: dispatch `disable-closed-flat-invariant`.

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

---

## ⚠️ 2026-09-09 — what this memo claimed, and what was actually true

Full-system audit `docs/audits/full-system-audit-2026-09-09.md` §§ **F-11**,
**F-12**, **F-13**, operator-approved Tier-2 the same day (`chosen: all_three`
on `WO-20260909-DECISION-CLOSED-FLAT-INVARIANT-CANNOT-SEE-A-FAILED-READ`).
Built as **MI-228**. Four things were wrong at once, and **each one alone was
sufficient to make this invariant silent**. Nothing below touches an order
path: the check was alert-only and still is.

**1 · A failed exchange read graded FLAT (F-11).** Every failure path returned
`0.0` — the value that means *flat* — at 5 of 5 early-return sites, and the
caller reads `residual == 0.0` as *no violation*. So the one mechanism that can
independently contradict *"this trade is closed"* was cleared by exactly the
condition it exists to catch. Its own input already made the distinction —
`clients.py::account_open_positions` returns `None` **by contract** so callers
can tell `[]` from *could not read*, including an empty IB snapshot from a
Gateway not verified logged-in — and this module was the consumer that dropped
it. Now a three-state `ResidualRead`: `flat` / `residual` / `could_not_look`,
registered with `collapsed-state-guard` as `closed_flat.residual_state`.

**2 · It could not see a residual at all — NOT in the audit, found building
the fix.** The size was read from `qty` / `contracts`; production emits
**`size`** on 4 of 4 venue branches. `float(None or None or 0) == 0.0`, so a
live residual graded flat on every account from the module's first commit.
Verified empirically, not by reading: a 26.05 ETHUSDT row in the exact
production dict shape returned `0.0`. **Every fixture in
`tests/test_closed_flat_invariant.py` uses `qty`**, so 15 tests were green
throughout while exercising a shape production never produces.

**3 · The lookback was shorter than the invocation period (F-12).**
`DEFAULT_WINDOW_SECONDS = 60`, never overridden (1 of 1 call sites), against a
measured period of **≥ 101.9 s** — so ≥ 41 % of every period was examined by
nobody, *by arithmetic, not by failure*. The wiring now MEASURES the interval
between its own consecutive invocations and derives the window from it, and
publishes both on a `closed_flat_coverage` soak.

**4 · Nobody could read what it said (F-13).**
`runtime_logs/invariant_violations.jsonl` was on no diag allowlist and had no
alternate reader, and the alert was `Level.WARN`, which `outcomes.py` excludes
from Telegram. Both fixed; the alert is `Level.ERROR` (not `CRITICAL` — this
repo reserves that for a position that is UNPROTECTED or REVERSED).

### Corrections to the text above

- ⚠️ **The trade-#1049 retrospective's "~5 seconds" detection delay is
  wrong and must not be re-quoted.** It reads *"the configured
  `window_seconds`, plus one tick cadence"*, which mistakes the LOOKBACK for
  the LATENCY: the invariant runs once per tick, so the floor was always one
  tick period — measured at ≥ 101.9 s, ~20× the figure claimed. With the window
  now cadence-derived the coverage gap is closed, but the detection delay is
  still bounded below by the tick period, not by the window.
- ⚠️ **`disable-closed-flat-invariant` is named above as the soft rollback and
  no longer exists.** The `CLOSED_FLAT_INVARIANT_ENABLED` gate was removed
  2026-06-17 (a safety invariant behind a default-off flag is the
  Prime-Directive anti-pattern) and the check has been unconditional since. The
  rollback for a *noisy* invariant is to fix what it is reporting.

### What is proven, and what is not

- **Not proven on the fleet:** none of the four. A merge is not a deploy and a
  deploy is not an observation.
- **A green test suite clears nothing here** — the whole finding is that a
  harness cannot reach a failed exchange read on a live account.
- **A continued zero clears nothing either**, until a planted violation has
  been OBSERVED being reported. `run_residual_controls()` grades three planted
  inputs through the deployed classifier every pass and stamps `controls_ok` on
  the soak row — but its `controls_scope` ships beside it as
  `pure_classifier_only`, deliberately: it reaches no venue, no account
  resolver, no DB row and no alert path, so it is **not** evidence the
  invariant works end to end.
- ⚠️ **The first violation this reports is NOT a regression.** The zero was
  uninformative by construction, so `0 → non-zero` is the mechanism starting to
  work, not the book getting worse.
