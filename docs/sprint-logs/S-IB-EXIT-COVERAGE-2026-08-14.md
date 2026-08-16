# Sprint Log: S-IB-EXIT-COVERAGE-2026-08-14

## Date Range
- Start: 2026-08-14
- End: 2026-08-14 (live verification 19:28:34Z; log written 2026-08-16 after a 34h idle gap — see *Gaps not yet verified*)

## Objective

**Primary:** answer the operator's question — *why did the MGC take-profit never
fire?* — and fix the cause, not the symptom.

**Secondary:**
- Merge PR #9240 (IB thread pinning) and clear the `EXIT_LOOP_DECOUPLE_DISABLED=1`
  mitigation so the M20 decouple runs again.
- Make the resulting sweep observable enough that the fix could be verified live.

## Tier

**Tier 3** for the substantive change (#9331 alters `IBClient.place_protective`,
a live order path on a money-at-risk broker). Prepared, tested, and held as a
draft; merged only on the operator's explicit "merge it" in-conversation.

Tier 2 for clearing `EXIT_LOOP_DECOUPLE_DISABLED` on the live VM (`set-env` +
restart) — the sanctioned rollback flag being returned to its default.

Tier 1 for #9342 (observability), #9354 (backlog), and every diag read.

## Starting Context

- Active: M20 (exit-loop work). Prior sprint: `S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13.md`.
- Open incident: recurring MONITOR BLIND / `candles_unavailable` alerts for MGC and MES.
- `EXIT_LOOP_DECOUPLE_DISABLED=1` was set on the live VM as a mitigation and had
  not been cleared.
- Known risk carried in: IB was believed **not** to net per contract per account.
  That belief is why PR #8000 fixed the protection-as-quantity class for Bybit
  and left IB alone. It was wrong.

## Repo State Checked

- Branch `claude/candle-data-unavailable-uvvl4p`; base `origin/main`.
- Commits reviewed/created: `f4528fc` (#9331), `6f75bfe` (#9342), `be7c8cd` (#9354).
- Deployment state: live VM `ict-bot-arm` deployed to `6f75bfe`, trader PID 2065168,
  restarted 2026-08-14T19:13:45Z, `active`.
- Canonical docs reviewed: `CLAUDE.md` § "Dashboard REST API" + § Permission Tiers +
  the naked-autoprotect paragraph under *Important Notes*;
  `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states", § "RULE ONE — Always verify".

## Files and Systems Inspected

**Code**
- `src/units/accounts/ib_client.py` — `place_protective` / `_locked_place_protective`,
  `has_protective_orders`, `_cancel_resting_orders_for_symbol`, `replace_protective`,
  `_req_positions_snapshot`, `_req_all_open_orders`.
- `src/runtime/order_monitor.py` — `_check_broker_naked_ib_positions` (L6486),
  `_attempt_naked_autoprotect` (L6238), `_rearm_broker_protection_after_recovery` (L6198),
  the cadence latch `_LAST_IB_BROKER_NAKED_CHECK_MONO` (L6133), and the call site
  inside `run_reconciliation_tick` (L9123).
- `src/units/accounts/execute.py::modify_open_order` (L2303) — checked for an
  `oca_key`/trade-id parameter; it has none.

**Tests**
- `tests/test_ib_protection_quantity.py` (new, 11 tests).
- `tests/test_ib_naked_rearm.py` (modified — `_FakeIBClient`, and one renamed test).

**Config / journal**
- `order_packages` row `pkg-dbcc140fd43c4a79` and `trades` row 4487 (MGC, 105 lots).

**Services**
- `ict-trader-live.service` via `/api/diag/journalctl`, `/api/diag/status`,
  `/api/diag/tick_cost`, `/api/diag/ib_state`.

**GitHub Actions**
- `vm-diag-snapshot` (issues #9350–#9353), `system-actions` (`set-env`, `pull-and-deploy`).

## Work Completed

1. **Root-caused the un-fired take-profit.** Two defects compounding on IB's
   per-contract netting:
   - `has_protective_orders` returned `True` on the *first* matching leg, so a
     one-third-covered position read as fully covered — and the sweep cached one
     verdict per `(account, symbol)`, suppressing every sibling behind it.
   - `place_protective` cancelled **every** resting order on the symbol root
     before arming one bracket sized to the calling trade, deleting siblings'
     take-profit legs. Net effect: A's TP is cancelled by B's re-arm, and the
     boolean then reports "protected" because B's new leg exists.
2. **#9331 (Tier-3).** Added `IBClient.protection_coverage(symbol)` returning
   `{size, covered_qty, legs, unknown_qty_legs, oca_groups, source}` or `None` —
   the PR #8000 Bybit shape. Legs sharing an OCA group count **once** (max, not
   sum): a group's STOP and LIMIT protect the same quantity. `place_protective`
   now takes an `oca_key` and pre-cancels only that trade's deterministic group
   (`oca-protect-t<trade_id>`) via a new `_cancel_oca_group_for_symbol`.
   `has_protective_orders` and `_cancel_resting_orders_for_symbol` are deliberately
   **unchanged** — the former has 7 dependent tests plus Alpaca parity, and the
   latter is legitimately cancel-all for `close()`.
3. **#9342 (Tier-1).** The sweep was silent when it worked — a bare `continue` on
   an unreadable coverage made "the sweep ran and could not read" indistinguishable
   from "the sweep never ran". Added `read_failed`/`ungradeable`/`covered`/
   `partially_naked` counters and a per-sweep summary line.
4. **Merged #9240 and cleared `EXIT_LOOP_DECOUPLE_DISABLED`** via `set-env` +
   `pull-and-deploy` + restart.
5. **#9354 (Tier-1).** Landed the live evidence and two incidental findings in the
   health-review backlog.

## Validation Performed

**Tests:** 11 new tests in `tests/test_ib_protection_quantity.py`, all pass.
They assert properties, not mechanism.

**Falsification, done deliberately** because a test shipped earlier in the same
session had passed against broken code: reverted *only* the scoped cancel —
`test_rearm_keeps_sibling_take_profit` failed; restoring it made it pass.

**A pre-existing test was asserting the bug.**
`test_ib_sweep_dedupes_read_per_symbol` asserted `rearmed == 1` over two naked
trades, commented "only the first re-arm (the symbol is protected after it)".
A green test whose expected value *was* the defect. Renamed to
`test_ib_sweep_dedupes_read_but_rearms_every_uncovered_sibling` and inverted to
`rearmed == 2`.

**Guards:** PASS 14 · FAIL 0 on the final diff. `artifact-validity-guard` caught a
tracking reference I had written to a `BL-2026 0624-MHG-FLIP`-style id that was
never filed — I had reached for it from memory; the real 2026-06-24 MHG row is
`BL-20260624-MHG-CLOSE-CONFIRM-VERIFY`, which is about close-confirm and does
*not* cover bracket stacking. Corrected to state the property directly rather
than attach an item that does not cover it.

(The bad id is deliberately written with a space above. Quoting it verbatim
re-trips the guard — it cannot distinguish an id cited as an example from one
cited as tracking, and it is right not to try. It caught the same id twice, once
in the backlog note and once here.)

**Live verification** — `2026-08-14T19:28:34.849Z`, trader PID 2065168, HEAD `6f75bfe`:

```
_check_broker_naked_ib_positions: swept 2 open IB position(s) —
covered=2 naked=0 partially_naked=0 rearmed=0 read_failed=0 ungradeable=0 errors=0
```

Immediately preceded (19:28:34.764 / .805) by the `ib_insync` position reads the
denominator comes from — MGC 105.0, MES 15.0 — so `protection_coverage` ran its
real path, not a degraded fallback. `ungradeable=0` is the field that mattered:
the pre-deploy risk was that live `ib_insync` order objects would not expose a
parseable quantity the way the test stubs did, which would have left the sweep
fail-safe but **inert**. `read_failed=0` rules out the breaker-open path.

**Cost:** `/api/diag/tick_cost`, same process, `ticks_measured: 5` —
`monitor.check_broker_naked_ib_positions` n=5, mean 3627.8 ms, max 6836.8 ms,
2.8% of tick. Inside the cadence-gated budget (BL-20260609 pacing class).

**No live caller uses the legacy pre-cancel.** `IBClient.replace_protective` has
**zero** callers repo-wide (grep, not assumption), and
`_rearm_broker_protection_after_recovery` routes through `_attempt_naked_autoprotect`,
which passes `oca_key`. The symbol-wide branch is defensive-only.

### Gaps not yet verified

- **`covered=2` does not discriminate the OCA dedup.** It cannot separate
  "counted MGC's pair once as 105" from "double-counted as 210" — both clear
  `covered >= size - 0.5`. The max-dedup is proven by
  `test_oca_pair_counts_once_not_twice`, not by the live line. Discriminating
  live requires a partially-covered position, which is the state the fix exists
  to prevent.
- **No `partially_naked` or `rearmed` event has been observed live.** Those
  branches are test-covered only.
- **`partially_naked` / `rearmed` remain unobserved live** — test-covered only.

### Follow-up read, 2026-08-16T05:42Z (34h after deploy)

The session went idle ~34h; rather than leave the above as the last word, live
state was re-read before closing.

**The sweep holds up at real sample size.** `monitor.check_broker_naked_ib_positions`
n=**116**, mean 3247.8 ms, max 6756.8 ms, 2.3% of tick — one process, 6.6h,
`git_sha 5d5bbb67`, heartbeat 4.1 s, `ict-trader-live.service` active. It ran on
every tick. The n=5 reading was not a fluke. All three IB clients `connected`,
`consecutive_failures: 0`.

**Two things got worse, and one is a finding this sprint did not have:**
- Tick is **143.7 s mean / 195.6 s max at n=116** (was 129.5/167.3 at n=5).
  `fetch.1d` alone is 31.0% at a **14825.4 ms mean** over n=348. Appended to
  `BL-20260814-TICK-2X-SLOWER-AFTER-IB-PIN`.
- **The exit-evaluation interval max is 58.9 s** (`monitor.strategy_monitor_loop`,
  n=694, mean 28852.0 ms, max 58940.8 ms). Because the loop is period-targeting,
  the interval is `max(30 s, pass)` — so a live trade went 58.9 s without
  re-evaluation, against the 60 s requirement M20 exists to guarantee. 1.1 s of
  margin. Filed as `BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT` (high).
- Noted while reading: several unrelated maxima sit on ~**29000 ms** (`fetch.1h`
  29000.5, `fetch.1d` 29000.3, off-loop `fetch.15m` 29000.4). A shared ceiling
  across different timeframes and both loops looks like a timeout, not organic
  latency. If so the tail is a *failure* population, not a slow one — which
  changes what the means mean. Unresolved; recorded on both rows.

## Documentation Updated

- `docs/claude/health-review-backlog.json`:
  - `BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY` → `partially_resolved`
    (criteria 2/3/4 shipped + verified; criterion 1 stays open as
    `BL-20260814-NO-IB-OPEN-ORDERS-READ-SURFACE`; criterion 5 is Tier-3, operator's).
  - `BL-20260814-IB-LIVENESS-PROBE-TIMES-OUT-EVERY-CONNECT` — second independent
    confirmation appended; severity unchanged.
  - `BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING` — spread to a second symbol.
  - `BL-20260814-BYBIT-PORTFOLIO-ETH-JOURNAL-DIVERGENCE` — new.
- Coordination board #6927 — `▶️ START` and `✅ DONE` posted.
- This sprint log.
- **Not updated:** `ARCHITECTURE-CANONICAL.md` and `docs/TRADE-PIPELINE.md` — no
  pipeline *stage* changed; the change is inside an existing reconciler and an
  existing broker client method. `CLAUDE.md`'s naked-autoprotect paragraph already
  described protection-as-quantity for Bybit and did not assert the IB behaviour
  that was wrong, so it needed no correction.

## Contradictions or Drift Found

1. **The repo believed IB does not net per contract per account.** It does. That
   belief is the reason PR #8000 fixed this class for Bybit only. Corrected in
   the backlog row; the code now treats IB the same way.
2. **A green test encoded the defect** (`rearmed == 1`, see above). The comment
   explained the bug as if it were the design.
3. **I filed a false confound earlier in the session** — "US cash mostly closed"
   on the tick regression. US RTH is 13:30–20:00 UTC in August and both windows
   were fully inside it; I had inferred it from a 13:20Z journal slice taken
   *before* the window. Corrected in #9330.
4. **I introduced a collapsed state while fixing one.** The first version of the
   coverage branch used a silent `continue` on an unreadable read. Fixed in #9342.
5. **Five verification windows missed** because they were aimed at exit-loop
   passes. The sweep lives in `run_reconciliation_tick` — the M20 decouple moved
   only the *exit* half to the 30 s loop; the reconcilers stayed on the ~131 s
   main tick. Worth stating plainly: post-decouple, "which loop is this on?" is
   now a question with two answers and the code is the only place that says which.

## Risks and Follow-Ups

**Technical**
- The main tick measured **mean 129.5 s / max 167.3 s** (n=5), with
  `pipeline.signal_build` at 64.7% and `fetch.1d` alone at 31.5%. Off-loop
  `monitor.strategy_monitor_loop` n=26, mean 24.7 s, max 47.5 s. Not caused by
  this sprint (the IB sweep is 2.8%) but it is the largest open item.
- `bybit_1/ETHUSDT` SL legs at 311% of position size; `cancel-stale-tpsl-legs`
  (Tier-2, dry-run by default) still has not been run.
- `bybit_portfolio/ETHUSDT` journal 35.01 vs exchange 21.05.
- Both Bybit findings are **incidental sightings in one 20 s window**. Extent
  across accounts and symbols is unmeasured.

**Tier-3 product decisions (operator)**
- Disposition of the 105-lot MGC position. Separately: a 15 m scalp sizing 105
  MGC contracts is `risk_per_unit 16.84 × 105 × multiplier 10 ≈ $17.7k` of risk.

## Deferred Items

- `BL-20260814-NO-IB-OPEN-ORDERS-READ-SURFACE` — the Tier-1 `/api/diag/ib_open_orders`
  read surface. Partially mitigated by the sweep's summary line, not replaced by it.
- Widening `_protective_leg_qty`'s attribute list — not needed (`ungradeable=0`).

## Next Recommended Sprint

**Attack the tick regression.** It is the largest measured degradation and it
reaches exit decisions, not just charts.

Why: `pipeline.signal_build` is 64.7% of a 129.5 s tick and `fetch.1d` is 31.5%.
The `fetchby.*` cut already exists to say which consumer pays. The 2026-08-13
finding that 1d cannot cache because 16 one-symbol strategies give a ~692 s
revisit interval against a 300 s TTL is the specific lead.

Required verification before any Tier-3 TTL change: a distribution, not a reading
— `ticks_measured` well above 5, and the price-staleness argument made explicitly,
since strategies read `close.iloc[-1]` as the current price for entry geometry.

## Wrap-Up Check

- [x] Code inspected directly (paths + line numbers above), not inferred from docs.
- [x] Canonical docs reviewed.
- [x] TRADE-PIPELINE not updated — no pipeline stage changed (justified above).
- [x] Roadmap checked — this is backlog-tracked defect work under M20, not a new
      milestone row.
- [x] Contradictions recorded, including the three I caused.
- [x] Unknowns stated rather than smoothed over — see *Gaps not yet verified*.
- [x] Live verification obtained before claiming the fix works, and its limits stated.
