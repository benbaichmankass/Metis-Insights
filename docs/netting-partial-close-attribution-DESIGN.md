# Netting partial-close attribution — DESIGN packet

> **Status: PROPOSAL. Nothing here is implemented.** Written 2026-08-06 to close the
> design half of `BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED`
> (`WORKPLAN-2026-08-05.md` §3 P0 item 1). The design work is Tier-1; **every fix
> option below is Tier-2** (order-monitor close path + journal writeback) and needs
> one operator OK before it ships.
>
> **New finding while scoping this — see §2.** The `journal_qty_divergent` detector
> shipped in PR #8000 **cannot fire in the worst case it exists to catch.** That is a
> separate, smaller, and strictly-Tier-1 fix, and it should land *first*.

## 1. The defect, restated from the code

Under Bybit one-way netting a symbol is **one** exchange position holding **N** journal
rows. A position-level exit — bracket fire, `intent_reduce`, manual close — shrinks that
single netted position. But close detection is **per-order**:

- `_reconcile_open_trades` (`order_monitor.py:3597`) reconciles *"each DB-open trade
  against its specific Bybit order via `account_order_status`"*. A sibling row whose own
  order filled long ago and was never individually closed has no order event to observe.
- `_apply_update` (`:673`) closes the row whose own bracket/leg fired.

So a partial close reduces the exchange by X and closes **at most one** journal row.
Every sibling keeps its full `position_size` and stays open indefinitely.

Measured consequences (W1, `BL-20260731-W1-JOURNAL-EXCHANGE-DIVERGENCE-MAP`, same-moment
check 2026-08-01T06:40Z): `bybit_1` open-size inflation **up to 155×** (BTC journal 1.553
vs exchange 0.01); netting-guard suppression of legitimate re-entries for strategies
holding a phantom open; polluted ML/paper analytics.

**This compounds with the leg problem, it is not the same problem.**
`BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING` is the *protection* side (Partial-mode
`set_trading_stop` is ADD-only, so legs outlive the qty they were sized for — 444.7% →
830% over-coverage in 41 minutes). This item is the *journal* side. A single partial
close makes **both** worse at once: it strands a leg **and** orphans a row.

## 2. FIRST — the detector is blind where divergence is total (Tier-1, ship separately)

`_check_broker_naked_bybit_positions` computes `journal_qty_by_key` and fires
`journal_qty_divergent` when open journal rows sum above the netted exchange size
(`:6817`). But the anomaly block is preceded, at **`order_monitor.py:6777`**, by:

```python
size = float(state["size"])
covered = float(state["covered_qty"])
if size <= 0:
    continue                      # flat
eps = size * _BYBIT_COVERAGE_EPS_FRAC
# ---- symbol-level ANOMALY detection (once per symbol, no action) --
```

`_bybit_position_protection` returns `_flat` (`size: 0.0`) when Bybit reports no position
(`:6552`, `:6559`). So **when the exchange is fully flat and journal rows are still open —
the maximum-divergence case, and precisely the W1 finding — the detector `continue`s
before it ever compares.** It reports clean because it never looked.

That is the unasserted-denominator shape (`CLAUDE.md` § Diagnostic provenance, sub-class
**C**): an empty result read as a clean negative. A detector cheaper to satisfy than to
trip is the `new-table-wiring-guard` lesson repeating.

**Fix (Tier-1, no order-path change):** hoist the divergence comparison above the
`size <= 0` guard, so a flat exchange position with a non-zero journal sum fires at
maximal severity rather than silently passing. The protection/re-arm logic below it must
still `continue` on flat — there is nothing to protect. Two behaviours currently share one
`continue`; they need separating.

**Second defect in the same block, same fix:** `journal_qty_by_key` is keyed
`(account_id, symbol)` with **no direction** (`:6741`) while the backlog states the unit
of attribution as `(account, symbol, direction)`. Under one-way netting only one direction
is live at a time, so the sum is *usually* right — but stale rows on the opposite side
inflate it, which is the phantom case itself. Key it by direction and compare signed.

## 3. Fix options for the attribution itself (Tier-2 — operator decision)

Shared precondition for all three: **the reduction must be attributed, and its P&L must
not be manufactured.** `CLAUDE.md` § Number provenance is binding — a close with no
broker close record is `UNMEASURED_MARKER`, never a mark read at sweep time. That was the
`_sweep_local_pnl_for_unpriced` defect and it must not be re-introduced through this door.

### (a) FIFO reduce oldest-first, pnl UNMEASURED absent a broker record

When netted exchange size drops below the journal sum for `(account, symbol, direction)`,
reduce/close rows oldest-first until they reconcile.

- **+** Terminates; needs no leg tracking; fixes historical rows with dead legs.
- **−** Attribution is an *assumption*. Any per-row `pnl` so derived is `FABRICATED` by
  the repo's own vocabulary, so rows must close carrying `UNMEASURED_MARKER` — which
  means **fixing the size divergence deliberately widens the measured-P&L gap**. That is
  the honest trade and it should be stated to the operator up front, not discovered on
  the next `pnlCoverage` read.
- **−** FIFO is not what actually happened when a *specific* trade's bracket fired.

### (b) Leg-id attribution first, FIFO for the residual

`trades.sl_order_id` / `.tp_order_id` have been populated since `BYBIT_TPSL_MODE=partial`
went live (~2026-07-21; 75 of 211 pre-flip opens already carried a non-NULL
`sl_order_id`). Close the row whose tracked leg actually fired — a **measured** close —
and FIFO only the residual shrink.

- **+** Correct attribution *and* honest provenance for the rows it covers.
- **−** Coverage is partial by construction: NULL for pre-migration rows, NULL where the
  entry-time snapshot diff was ambiguous, and the W1 receipts show at least one row with
  a **dead** tracked leg (`bybit_1` BTCUSDT 1.543 `ict_scalp_5m`). So (b) **needs** (a) as
  its fallback — it is not an alternative to it.
- **Coverage must be measured before this is chosen.** Count non-NULL `sl_order_id` among
  currently-open rows, per account. If it is low, (b) buys little over (a).

### (c) Periodic same-moment auto-reconcile job

Automate what the W1 manual batch did: snapshot journal and exchange at one instant,
reconcile the delta.

- **+** Catches every divergence source, not just partial closes; no monitor-tick cost.
- **−** Treats the symptom on a cadence; divergence still exists between runs, and the
  netting guard reads the journal *continuously*. Also needs its own same-moment
  guarantee — a journal read and an exchange read taken minutes apart reconcile a
  difference that never existed.

**Recommendation: (b) with (a) as the declared fallback, plus (c) as a periodic backstop.**
(b) alone under-covers; (a) alone manufactures attribution; (c) alone leaves the live
window wrong. But **§2 ships first and separately** — it is Tier-1, it is small, and
without it there is no trustworthy signal to tell whether any of (a)/(b)/(c) worked.

## 4. Hard constraints any implementation must respect

1. **Provenance.** No close prices a P&L from a mark. Absent a broker close record the row
   closes `UNMEASURED_MARKER` (`CLAUDE.md` § "A confirmed close is anchored to its
   `closed_at`"). Expect `pnlCoverage` to *drop* when this ships — that is the fix
   revealing pre-existing debt, not creating it.
2. **The pairs sleeve owns its own rows.** `src.units.strategies.pairs_executor` is an
   isolated 2-leg order path with its own state; the BNB surplus lives in pairs rows.
   **Do not close them behind its back** — coordinate, or exclude pairs-owned rows.
3. **Runtime bounds.** This runs on the live trader's monitor tick. The 2026-06-09
   cold-start wedge came from an unbounded per-row fetch; reuse the existing
   `protection_cache` (one `get_positions` per symbol per tick), never one call per row.
4. **Fail-safe reads.** A `None`/could-not-confirm exchange read must **skip**, never
   close. Same contract as the three naked sweeps.
5. **Two-observation confirm.** Reuse `RECONCILER_CLOSE_CONFIRM_SECONDS`. A single
   observation of a smaller position is not proof — the Alpaca `RECONCILER_SNAPSHOT_MIN_FILL_AGE_S`
   incident (a just-placed order reads absent, then re-adopts) is the same shape.
6. **Baseline, not a flag.** Per the Prime Directive this is a *correctness* fix, so it
   ships unconditional. A tuning knob is fine; a default-off `*_ENABLED` gate is not — the
   netting guard was removed for exactly this reason after the Ampere migration dropped
   its env var.

## 5. What the operator is being asked to decide

| # | question | default if unanswered |
|---|---|---|
| 1 | Ship §2 (detector blind spot) now as its own Tier-1 PR? | **yes** — it is a prerequisite for verifying anything else |
| 2 | Which attribution option — (a), (b)+(a), or (b)+(a)+(c)? | **(b)+(a)**, with (c) queued |
| 3 | Accept that fixing size divergence **lowers** measured-P&L coverage? | must be explicit; it is the honest outcome, and it will move the R4 gate's `totalPnlMeasured` |
| 4 | Scope the first ship to `bybit_1` (paper) before `bybit_2` (real money)? | **yes by magnitude — but see §6(b): `bybit_2` is NO LONGER clean.** It was clean at the 2026-07-30 audit and is divergent as of 2026-08-06 (ETHUSDT 0.06 journal vs 0.04 exchange). Staging on `bybit_1` first is still right; describing this as paper-only is not. **Stage the WRITE, never the measurement — see the note below.** |

> **Implementation note on decision 4 (added 2026-08-09, PR #8666).** The
> decision above is correct and unchanged. Its *first implementation* was not:
> `NETTING_ATTRIBUTION_ACCOUNTS` intersected the account set at the top of the
> reconciler pass (`bybit_ids &= allow`), which staged the **observation** along
> with the write. While the allowlist was set, `bybit_2` was not merely
> un-written — it was **invisible**: no divergence check, no soak row, nothing
> accruing to review before widening the allowlist to it. So the account this
> staging exists to build toward was precisely the one that stopped being
> measured, and the soak whose whole purpose is to justify the widening could
> never contain the evidence for it.
>
> The allowlist now scopes the write and nothing else, via the single predicate
> `order_monitor._netting_may_write(account_id, mode, allow)`. **Anyone
> re-reading decision 4 and reaching for an account filter: filter at the WRITE
> site, not at the top of the pass.** A staging control that disables
> measurement of the thing you are staging toward is self-defeating — the same
> conflation of "no policy here" with "no data here" that
> [`gross-exposure-governance-DESIGN.md`](design/gross-exposure-governance-DESIGN.md)
> § 3 records for the exposure ceiling.

## 6. Pre-implementation measurements — TAKEN 2026-08-06

Same-moment, **2026-08-06T11:40Z** (relays #8516 journal / #8517 exchange). Population:
all **33** open non-backtest journal rows vs `/api/diag/exchange_positions`. Accounts with
no exchange read (`ib_live`, `oanda_practice`, `breakout_1`) are excluded — ungradeable,
not clean.

### (a) `sl_order_id` coverage — HIGH, so option (b) is worth building

| scope | tracked | share |
|---|---|--:|
| bybit accounts (the ones that net) | **16 / 19** | **84.2%** |
| — `bybit_1` | 11 / 14 | 78.6% |
| — `bybit_2` | 2 / 2 | 100% |
| — `bybit_portfolio` | 3 / 3 | 100% |
| all open rows incl. non-netting venues | 16 / 33 | 48.5% |

84% of the rows that can diverge carry a tracked leg, so (b) covers most cases with a
**measured** close. The 3 untracked rows are exactly what fallback (a) is for.

### (b) Divergence census — 4 divergent keys, all Bybit

| account | symbol | dir | journal | exchange | excess | rows |
|---|---|---|--:|--:|--:|--:|
| **`bybit_1`** | **SOLUSDT** | long | **2075.20** | **4.60** | **+2070.60** | 4 |
| `bybit_1` | BNBUSDT | short | 13.43 | 5.49 | +7.94 | 5 |
| **`bybit_2`** | **ETHUSDT** | short | **0.06** | **0.04** | **+0.02** | 1 |
| `bybit_portfolio` | ETHUSDT | short | 27.42 | 21.05 | +6.37 | 2 |

Three findings that change the plan:

1. **`bybit_1` SOLUSDT is inflated 451×** — worse than the 155× BTC case W1 measured on
   08-01, and it is a *different symbol*, so this is not the same instance persisting.
   The condition is confirmed self-worsening.
2. **`bybit_2` (REAL MONEY) is no longer clean.** §5 decision 4 was written on the
   2026-07-30 audit that found it clean; it is divergent now. Scoping the first ship to
   `bybit_1` is still right *by magnitude*, but this can no longer be described as a
   paper-only problem.
3. **Every non-Bybit key is EXACTLY clean** — 12/12 across `alpaca_paper`,
   `alpaca_portfolio`, `ib_paper`, to the unit. That is a strong confirmation of the
   diagnosis rather than a coincidence: those venues do not net, so the partial-close
   mechanism in §1 cannot fire there. A general journal-writing bug would have smeared
   across all of them.

### (c) Pairs-owned rows — none open

No `pairs_*` rows appear among the 33. Constraint 2 has an empty exclusion list *today*,
but the coordination requirement stands (the sleeve opens on its own cadence).

### (d) Incidental: the leg-cleanup action cannot clean a FLAT symbol

`cancel-stale-tpsl-legs` on `bybit_1`/XRPUSDT (dry-run #8518) returned
`action: "abort_flat"` — *"live XRPUSDT position on bybit_1 is flat (size=0) — out of
scope for this script; nothing to protect"*, and #8517 confirms `bybit_1` holds no XRP.
The script is scoped to protecting a **live** position, so any legs left resting after
the position closed are outside every existing cleanup path. Logged to
`BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING`; it does not block this design.
