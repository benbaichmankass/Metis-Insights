# One-look packet — netting partial-close rows never reduced (BL-20260801)

> **Tier-2 order-path decision. Operator picks (a) / (b) / (c); I implement the pick.**
> Root-cause item: `BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED`.
> Parent (resolved): `BL-20260731-W1-JOURNAL-EXCHANGE-DIVERGENCE-MAP`.

## The bug, confirmed in code

Under Bybit **one-way netting**, several journal `trades` rows (often different
strategies) share **one** exchange position. A **position-level exit that only
PARTIALLY shrinks** that netted position is attributed to at most one journal
row, and the siblings keep their full `position_size` and stay `status='open'`.

Confirmed paths in `src/runtime/order_monitor.py`:

- **`_close_trade_from_order_status`** (≈L5510) — closes the row whose own
  bracket/leg fired. Correct for that row; blind to siblings.
- **`_cascade_close_netted_siblings`** (≈L5353) — DOES close every sibling on
  the `(account, symbol, direction)` — **but only when Bybit reports the netted
  position FLAT** ("the ONE reliable moment we know every share closed", per its
  own docstring). A **partial** shrink (155→55) is not flat, so the cascade
  never fires and the siblings survive until either (i) an eventual full-flat
  cascade, or (ii) a same-moment snapshot reconcile, or (iii) forever.
- **`journal_qty_divergent`** (the 2026-07-30 detect-only Bybit broker-naked
  sweep, `_check_broker_naked_bybit_positions`) fires **per tick** when
  `sum(open journal size) > netted exchange size` — i.e. it already *detects*
  every instance of this class live, but remediates nothing.

**So the gap is precisely: partial (non-flat) netted shrink → no sibling
attribution.** Everything to detect it exists; nothing acts on it.

## Measured impact (state the population)

From the resolved parent map (same-moment `exchange_positions` vs open journal,
2026-08-01T06:40Z, vm-diag #8218 / trainer-diag #8227):

- **`bybit_1` (demo/paper):** open-size inflation up to **155×** (BTC journal
  1.553 vs exchange 0.01; SOL 51×; ETH 35×). 4 `ict_scalp` rows were the entire
  surplus; **cleaned 2026-08-01** by the one-shot `reconcile-netting-phantom-rows`
  batch (superseded, pnl UNMEASURED). Post-cleanup per-symbol sums matched the
  exchange to the last decimal.
- **`bybit_2` (REAL money):** **clean** — verified twice; every open row matched
  the broker. The real-money book is NOT affected by this class today.
- **Pairs sleeve (`pairs_bnb_btc_a`, BNBUSDT):** 5 rows sum 13.43 vs exchange
  9.72 — surplus 3.71 spread across 5 rows, **unattributable to any single
  row**. This proves the class ALSO operates inside the isolated 2-leg pairs
  path; the one-shot batch deliberately left it alone.

Harm: netting-guard re-entry suppression for strategies carrying phantom opens;
polluted ML/paper analytics. **Not** a real-money-loss vector today (bybit_2
clean); it is a data-integrity + strategy-availability bug on the paper/demo
books, with a latent path to real money if a future real-money strategy nets.

## Constraints any fix MUST honour

1. **Provenance contract** — a phantom close happened at an *unknown* moment
   inside a position-level exit. **Never price it from a mark** (`src/runtime/
   provenance.py`). `pnl`/`exit_price` stay NULL (UNMEASURED) unless a matching
   **broker closed-pnl record** exists for the shrink.
2. **Pairs-sleeve isolation** — `pairs_executor` owns its rows and its own state
   (`config/pairs.yaml`, `src.units.strategies.pairs_executor`). A general
   reconcile must **not** close pairs rows behind its back — either exclude
   pairs-owned `(account,symbol,direction)` groups, or route through
   pairs_executor. The BNB surplus is pairs-owned; leave it to the pairs sleeve.
3. **Live-trader tick safety** — anything on the monitor tick must be bounded
   (the 2026-06-09 cold-start wedge class). A same-moment exchange read per
   `(account,symbol,direction)` is the cost; gate its cadence.
4. **`bybit_2` real-money is clean** — the fix must not *introduce* churn on a
   currently-correct book. Fail-safe: a `None`/could-not-confirm exchange read
   skips, never closes.

## The three options

### (a) FIFO oldest-first reduction, in the monitor
When `netted exchange size < sum(open journal size)` for a group, reduce/close
the **oldest** open rows first until the sum matches the exchange, pnl UNMEASURED
unless a broker close record covers the shrink.
- **+** General (covers bracket fire, reduce, manual close — every shrink).
  Simple, deterministic. Attribution error is bounded to *which row's metadata*
  gets the close, never a fabricated number.
- **−** Oldest-first is an *assumption* about which share died; on a book where
  strategy attribution matters for research this mis-labels the survivor set.
  Runs on the hot tick → needs a bounded exchange read per divergent group.

### (b) Leg-id-attributed close + FIFO residual
Close the specific row whose **tracked SL/TP leg id fired**
(`trades.sl_order_id`/`tp_order_id`, populated since `BYBIT_TPSL_MODE=partial`,
2026-07-21), then attribute any *residual* shrink FIFO as in (a).
- **+** Most **precise** — a leg-fire close lands on the row that actually
  chose that stop. Uses infrastructure that already exists.
- **−** Only leg fires carry an id; reduces/manual/pre-migration closes fall
  through to the FIFO residual anyway, so (b) = (a) + a precision layer on the
  leg-fire subset. More code; same hot-tick read cost.

### (c) Periodic same-moment auto-reconcile (sidecar, generalize the W1 batch)
A cadence-gated job (like the snapshot reconciler / broker-naked sweeps) that
does what the manual 08-01 batch did: read exchange + journal same-moment, close
the surplus oldest-first, pnl UNMEASURED, `reconcile_status='superseded'`,
`exit_reason='netting_phantom_reconciled'`, **excluding pairs-owned groups**.
- **+** **Lowest blast radius** — observe-and-sweep, off the hot decision path,
  mirrors an established, proven pattern; does not touch `_close_trade_from_
  order_status`/cascade order logic. Reactive lag (rows phantom until next
  sweep) is acceptable — the detector already flags them meanwhile.
- **−** Reactive, not at-the-moment; a fresh exchange read per sweep (bounded by
  cadence). Leaves the underlying attribution gap in the monitor (the cascade
  still only fires on full-flat) — (c) papers over rather than fixes the tick.

## Recommendation

**(c) as the shipping fix, with the (b) precision layer folded into the same
reconcile as an enhancement** — i.e. the sweep attributes a leg-fire shrink to
its tracked-leg row when an id matches, and the rest FIFO. Rationale:

- The real-money book is clean, so this is a **data-integrity** fix, not a
  money-at-risk fix — the lowest-risk mechanism (a bounded sidecar mirroring the
  proven broker-naked sweeps + the already-run one-shot batch) is the right
  altitude. Touching `_close_trade_from_order_status`/`_cascade_*` on the hot
  tick (pure a/b) risks the currently-correct `bybit_2` path for a paper-book
  gain.
- (c) reuses the **exact repair policy already operator-approved** on 08-01
  (superseded + UNMEASURED), generalized from a signature-pinned one-shot to a
  cadence job — a small, reviewable delta.
- Folding (b)'s leg-id attribution in gives the precision of (b) for the common
  bracket-fire case at no extra read cost.
- Pairs-owned groups are **excluded** (constraint 2); the BNB surplus is filed
  to the pairs-sleeve owner separately.

**Wire-up regardless of pick:** connect the live `journal_qty_divergent`
detector firings to this item (resolution criterion: detector silent 7
consecutive days on all bybit accounts).

**If the operator prefers the gap fixed AT the tick** (not papered over), pick
**(b)** — but scoped to fire the cascade on a *partial* shrink too (attribute
the leg-fire row + FIFO residual), which is the larger, higher-risk change and
should carry its own walk-forward/soak before touching `bybit_2`'s path.

## One-look decision

- [ ] **(a)** FIFO in the monitor tick
- [ ] **(b)** leg-id + FIFO in the monitor tick (fixes the gap at the moment)
- [x] **(c) + (b) precision layer** — periodic same-moment sidecar reconcile,
      pairs-excluded, UNMEASURED, leg-id-attributed where possible **(recommended)**
- [ ] Do nothing beyond the detector (accept the paper-book phantoms)

Implementation on pick begins with a **fresh same-moment read** (the state
drifts ~1 day since 08-01); I'll quantify current phantoms first, then ship the
chosen mechanism as a Tier-2 PR + validate on a throwaway DB before any live run.
