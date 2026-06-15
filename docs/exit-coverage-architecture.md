# Exit-Coverage Architecture — every open trade always has a live exit

> **Status:** design / audit (2026-06-15). Drives a phased rebuild; not yet
> fully implemented. Tracks `BL-20260615-MGCNAKED` and its follow-ups.
> **Scope:** the guarantee that every open trade is, at all times, governed by
> a rational exit — verified against the code as of `main` @ `c8458bd`.

## 1. The invariant

> **Every open trade must, at all times, have at least one live exit mechanism,
> and the system must continuously detect and self-heal any open trade that has
> drifted out of coverage.**

There are two exit layers. A trade is *covered* if it has **either**:

1. **A live strategy `monitor()`** — the *primary*, dynamic exit. Each tick the
   owning strategy re-evaluates the open position against fresh candles and
   emits verdicts: break-even / trailing stop moves, TP-ladder partials,
   thesis- or level-cross closes, time-decay closes. For many strategies this
   is the *real* exit; the hard SL/TP handed to the broker is a **backstop that
   is never expected to be hit**.
2. **A broker-side protective bracket** — the *backstop*. A GTC stop (+ limit)
   resting at the exchange that closes the position if price runs to it while
   the dynamic layer is, for any reason, not acting.

The **unacceptable state** is an open trade with **neither** — no live
`monitor()` and no resting broker stop — or a trade whose only coverage is a
static backstop with **no path back to a live monitor** (a position with a stop
but no brain). The system must treat that the same way it treats an orphaned
trade: a condition to be **rectified in real time**, not a status to rest in.

This is **baseline correctness, not a feature.** Per the Prime Directive
([`CLAUDE.md`](../CLAUDE.md), [`docs/CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md)),
a required capability is never hidden behind a default-off `*_ENABLED` flag.

## 2. Current architecture (verified against code)

All references below are to `src/runtime/order_monitor.py` unless noted.

### 2.1 The two layers and where they run

| Layer | Mechanism | Entry point | Gate (today) |
|---|---|---|---|
| Primary (dynamic) | strategy `monitor()` | `run_monitor_tick` → `_call_strategy_monitor` → `pipeline.monitor_unit_for` → `src/units/strategies/<unit>.py::monitor` | **Unconditional** (runs every tick) |
| Backstop (broker) | naked-position re-arm | `run_monitor_tick` → `_check_naked_positions` → `_attempt_naked_autoprotect` → `IBClient.place_protective` | **Unconditional** since PR #3674 (was the `NAKED_POSITION_AUTOPROTECT` flag; removed) |
| Re-association (heal) | orphan detect / adopt / **reattach to strategy** / stuck-watchdog / unlinked sweep | `_reconcile_open_trades`, `_reconcile_orphan_exchange_positions` (+ `_reattach_adopted_orphans`, `_adopt_orphan_position`, `_recover_orphan_order_package`), `_watchdog_stuck_strategies`, `_sweep_unlinked_packages` | **`MONITOR_RECONCILE_ENABLED`** — `_reconcile_enabled()`, **code-default `false`** |

### 2.2 How the dynamic exit executes each tick

`run_monitor_tick` iterates each loaded strategy's open packages. Per package it:
fetches fresh candles via the injected `ohlcv_fetcher`; resolves the owning unit
module via `pipeline.monitor_unit_for(strategy_name)` (handles aliased
strategies — the IB/FX symbol sleeves — that reuse a base unit's `monitor()`);
calls `monitor(cfg, candles_df, open_pkg)`; and routes the verdict through
`_apply_update` / `_apply_partial_close` to the exchange. For a trade's dynamic
exit to actually fire on a given tick, **all** of these must hold:

1. the strategy is loaded and has an open package row;
2. `monitor_unit_for` resolves to an importable module **that defines
   `monitor()`** (the gap PR #3662 fixed for the sleeves);
3. `ohlcv_fetcher` returns non-`None` candles (exchange reachable, symbol/timeframe available);
4. `monitor()` does not raise (exceptions are caught at `_call_strategy_monitor` and swallowed to `None`).

If any fail, the call returns `None` → **no dynamic action this tick**. The
broker backstop, *if armed*, still protects the position.

### 2.3 What's already baseline-correct

- The core `monitor()` loop is unconditional.
- The **backstop re-arm** is unconditional (PR #3674): `_check_naked_positions`
  runs every tick (call site in `run_monitor_tick`, **not** behind the reconcile
  gate), scans open live trades with missing/non-positive SL/TP, resolves levels
  from the originating order package (`_resolve_protective_levels`, matching
  direction + symbol-or-base-futures-root), and re-arms a GTC OCA bracket on IB;
  non-IB brokers attach brackets atomically at entry, so they no-op and fall back
  to a one-shot alert.
- PR #3662 made the IB/FX sleeves (`mgc_trend_1h`, `xauusd_trend_1h`,
  `spy/qqq/gld/eth_*`) resolve their `monitor()` (they were importing a
  non-existent same-name module → `verdict=None (no action)` → unmanaged).

### 2.4 Production state

`MONITOR_RECONCILE_ENABLED` is **active on the live VM** (verified 2026-06-15 by
observing `_watchdog_stuck_strategies` summaries in the trader journal — that
helper early-returns unless `_reconcile_enabled()`), set via `.env`. The
**code default is `false`**, so the guarantee currently depends on an
environment override.

## 3. Failure-mode taxonomy (where the invariant breaks)

For each: is it **detected**? **self-healed** (monitor re-associated)? does the
**backstop still hold**? and what **gates** the heal.

| # | Mode | Code path | Detected | Monitor re-associated | Backstop holds | Gate |
|---|---|---|---|---|---|---|
| 1 | Strategy module unresolvable | `_call_strategy_monitor` import fails | logs WARNING only | no | yes, if armed | — (now mostly closed by #3662) |
| 2 | `monitor()` absent on module | `getattr(mod,"monitor",None)` is None | **silent** | no | yes, if armed | — |
| 3 | Candle fetch returns `None` | `run_monitor_tick` ohlcv path | logs INFO | n/a (one-tick) | yes, if armed | — |
| 4 | `monitor()` raises | `_call_strategy_monitor` except | logs WARNING | retry next tick | yes, if armed | — |
| 5 | Orphan-adopt, no recoverable package | `_adopt_orphan_position` bare path → `strategy_name='orphan_adopt'` | adoption logged | **only via reattach; never if package gone** | re-armed by `_check_naked_positions` | reconcile gate for adopt/reattach |
| 6 | Reattach to wrong package | `_recover_orphan_order_package` confidence match | no | wrong exit thresholds | yes | reconcile gate |
| 7 | Forward reconciler orphans a row | `_reconcile_open_trades` marks `orphaned`, closes package | ping | **no — terminal** | n/a (position read flat) | reconcile gate |
| 8 | Whole heal subsystem dormant | `_reconcile_enabled()` false | **none** | no | backstop still re-arms (unconditional) | **`MONITOR_RECONCILE_ENABLED` default-off** |
| 9 | Non-IB naked position | `_attempt_naked_autoprotect` IB-only | alert | no | **no auto-rearm** (manual) | — |
| 10 | dry_run account | reconciler skips dry accounts | n/a | by design | static SL/TP only | account mode |

The structurally significant clusters:

- **A — The monitor re-association layer is behind a default-off gate (#8).**
  The backstop is baseline, but the path that gives a stranded trade its *brain*
  back (adopt → reattach → stuck-watchdog) only runs when
  `MONITOR_RECONCILE_ENABLED` is true. A fresh deploy / lost `.env` line silently
  reverts the whole heal subsystem to dormant. This is the Prime-Directive
  anti-pattern, one level up.
- **B — A stranded trade can be backstop-only forever (#5, #7).** An
  `orphan_adopt` with no recoverable package, or a row the forward reconciler
  marked `orphaned`, has a backstop (re-armed) but **no live monitor and no
  generic fallback** — a stop with no brain, indefinitely.
- **C — Loss of the dynamic exit is invisible (#2, #3, #4).** Module-missing,
  candle-outage, and `monitor()` exceptions only log; nothing asserts "this
  trade just lost its primary exit" and surfaces it.
- **D — There is no single assertion of the invariant.** Coverage is *emergent*
  from several independent sweeps; nothing classifies every open trade as
  covered/uncovered each tick.

## 4. Target architecture

Make exit-coverage a **first-class, always-on, single assertion** per tick, and
de-gate the machinery that enforces it.

### 4.1 Per-tick exit-coverage classification

Add one pass (call it `_assert_exit_coverage`) that, for every open non-dry
trade, classifies coverage and drives healing:

| State | Meaning | Action |
|---|---|---|
| `LIVE_MONITOR` | resolvable strategy module with `monitor()` **and** the loop is calling it | none |
| `BACKSTOP_ONLY` | no resolvable live monitor (e.g. unrecovered `orphan_adopt`) | **reattach to a live order package; if none is found, CLOSE the trade** (§4.3). Keep the backstop armed only for the brief window until reattach-or-close resolves. |
| `NAKED` | no armed SL/TP | re-arm backstop now (already unconditional) to protect the window; then resolve as `BACKSTOP_ONLY` (reattach-or-close) |
| `UNCOVERED` | naked **and** no resolvable monitor **and** backstop re-arm failed (e.g. non-IB) | **alert + close** — no rational exit exists, so flatten |

This turns "covered if six conditions happen to hold" into "covered, asserted,
and healed every tick."

### 4.2 De-gate the heal subsystem (Prime-Directive fix) — DECIDED

`MONITOR_RECONCILE_ENABLED` is **removed entirely** (operator decision
2026-06-15). Not converted to a kill-switch — *removed*. The heal subsystem
(orphan detect, adopt, **reattach-to-strategy**, stuck-watchdog, unlinked sweep,
pending-PnL sweep) runs **unconditionally** every tick, exactly like the core
`monitor()` loop and the backstop re-arm already do. This retires
`_reconcile_enabled()`, its env read, every `if not _reconcile_enabled(): return`
guard, the renderer pin in `scripts/render_env_from_master.py`, and the
env-gate-survivor / render-contract tests that pinned the flag. The live `.env`
value is already `true`, so the deploy is a behavioural no-op on prod; the change
is what makes the guarantee survive a fresh deploy or a lost `.env` line.
Tier-3.

### 4.3 Un-attributable orphan → reattach, else CLOSE — DECIDED

A trade without a live `monitor()` must **always first try to reattach to a live
order package** (`_recover_orphan_order_package` / `_reattach_adopted_orphans`).
If a relevant package **can** be found, it regains its real strategy's dynamic
exit. If **no** relevant package exists, the trade has no rational exit strategy
and is **closed (flattened) on the exchange** — operator decision 2026-06-15: we
do **not** rest it on a static backstop or a generic time-stop monitor. A
position with no brain is exited, not held. The close goes through the existing
exchange-close path (broker-agnostic), is journalled with an explicit reason, and
is confirmed across the reconciler's existing 2-observation window so a transient
read can't trigger a spurious flatten. This **replaces** the bare-`orphan_adopt`
(NULL SL/TP, held forever) fallback in `_adopt_orphan_position` and the
"orphaned-but-untouched" terminal state of `_reconcile_open_trades`.

### 4.4 Surface monitor-blindness — DONE

Escalate the currently-silent degradations (module-missing, `monitor()` absent,
candle-`None` for N consecutive ticks, `monitor()` raised) from logs to a real
signal, so a trade losing its *primary* exit is visible in real time even while
the backstop holds. **Implemented:** `_call_strategy_monitor` returns
`(verdict, status)` (`status="ok"` = ran, else the blindness reason);
`run_monitor_tick` feeds `blind = candles is None or status != "ok"` into
`_track_monitor_blindness`, a per-package consecutive-blind-tick counter that
fires a one-shot `enqueue_monitor_blindness_alert` once blindness persists past
`MONITOR_BLINDNESS_ALERT_TICKS` (default 3; a tuning knob, not an enable gate)
and resets on any healthy tick. Observe-only — the order path is untouched.

## 5. Phased implementation plan

Each phase is a separate, reviewed PR; live order/monitor-path phases are Tier-3
(operator-approved before merge). Order is by leverage and risk.

| Phase | Change | Tier | Verification |
|---|---|---|---|
| **0 (done)** | #3662 sleeve `monitor()` resolution; #3674 unconditional backstop re-arm | — | merged + deployed; verified on VM |
| **1** | **Remove `MONITOR_RECONCILE_ENABLED` entirely** (§4.2): retire `_reconcile_enabled()` + all guards, the renderer pin, and the gate's contract/no-op-when-disabled tests | 3 | prod `.env` already true → behavioural no-op; soak: orphans still detected/healed; no false orphaning |
| **2** | `_assert_exit_coverage` single per-tick classification + reattach-or-**close** for `BACKSTOP_ONLY`/`UNCOVERED` (§4.1, §4.3), with the existing 2-observation confirm before any flatten | 3 | unit tests for each state incl. the close path; soak on a deliberately-stranded paper position; diag surfaces per-trade coverage |
| **3 (done)** | Surface monitor-blindness (§4.4) — `_call_strategy_monitor` now returns `(verdict, status)`; the loop tracks per-package consecutive blind ticks (`_track_monitor_blindness`) and fires a one-shot `enqueue_monitor_blindness_alert` past `MONITOR_BLINDNESS_ALERT_TICKS` (default 3); a healthy tick resets | 2 | `tests/test_monitor_blindness.py`; observe-only, never touches the order path |

## 6. Decisions (operator, 2026-06-15) — RESOLVED

1. **De-gating shape** → **remove the gate entirely.** No kill-switch; the heal
   subsystem is not gated at all.
2. **Un-attributable orphan** → **reattach if a relevant live order package
   exists; otherwise close (flatten) the trade.** No generic fallback monitor,
   no resting on a static stop. A trade with no rational exit is exited.
3. **`UNCOVERED` response** → **close** (consistent with #2) plus an alert.
4. **Non-IB backstop** → the close fallback is broker-agnostic, so an
   un-attributable non-IB orphan is closed rather than left alert-only.

## 7. References

- Code: `src/runtime/order_monitor.py` (`run_monitor_tick`,
  `_call_strategy_monitor`, `_check_naked_positions`, `_attempt_naked_autoprotect`,
  `_rearm_broker_protection_after_recovery`, `_reconcile_open_trades`,
  `_reconcile_orphan_exchange_positions`, `_reattach_adopted_orphans`,
  `_recover_orphan_order_package`, `_adopt_orphan_position`,
  `_watchdog_stuck_strategies`, `_sweep_unlinked_packages`, `_reconcile_enabled`);
  `src/runtime/pipeline.py::monitor_unit_for`; `src/units/strategies/*.py::monitor`;
  `src/units/accounts/ib_client.py::{place, place_protective}`.
- Docs: [`docs/ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md),
  [`docs/TRADE-PIPELINE.md`](TRADE-PIPELINE.md),
  [`docs/runbooks/monitor-reconciler.md`](runbooks/monitor-reconciler.md),
  [`docs/CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) (Prime Directive).
- Prior PRs: #3662 (sleeve monitor resolution), #3674 (unconditional backstop re-arm).
