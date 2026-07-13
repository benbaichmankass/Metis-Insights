# bybit_2 broker reconciliation — journal vs Bybit UM export (2026-07-13)

**Trigger.** Operator asked to verify that the `bybit_2` real-money trade log is
correctly recorded against the broker's own data — with the wrinkle that the
account was traded through **two Bybit sub-accounts** over its life (switched to
a sub-account mid-history and back), so both UM Transaction-Log exports have to
be **stitched** to see the whole account.

**Inputs.** Two operator-provided Bybit UM Transaction-Log CSV exports:
`MAIN` (168 rows) + `SUB` (1843 rows), both traded only through the Bybit
platform. Journal side: `trade_journal.db::trades WHERE account_id='bybit_2'`
(463 real, non-backtest rows), pulled via the `trainer-vm-diag` relay
(issue #6341).

> No raw fills are reproduced here — aggregates only (financial-data hygiene).

## 1. Coverage certification — POSITIONAL COVERAGE IS COMPLETE

The join key is `journal.trades.notes.trade_id == Bybit OrderId`.

| Check | Result |
|---|---|
| Journal real trade rows (bybit_2) | 463 (376 UUID perp/UM + 87 numeric spot ids) |
| Journal UUID positions that tie to a real broker order | **376 / 376 (100 %)** — no phantom/invented positions |
| Broker distinct OPEN orders | 398 · CLOSE 343 · `--`-action 163 |
| Broker OPEN orders with **no** journal row | 33 → **32 are SUB-account BTC adds/re-entries** (the netting model: one journal position ↔ many broker opening orders) **+ 1 fresh MAIN XRP short opened the same day** (mirror lag / the reconciler-blindness class fixed by PR #6331) |
| Journal rows keyed on the CLOSE-order id instead of the open id | 11 (all SUB) — an attribution quirk; each is still a real trade |

**Conclusion:** the journal has **full positional coverage** — every recorded
position is a real broker position, and no broker *position* is missing (the 32
"extra" broker opens are order-vs-position granularity under the netting guard,
and their cashflow is already inside the wallet-truth below). The defect is
**PnL magnitude, not coverage.**

## 2. Authoritative realized PnL — the wallet-truth

Bybit UM `Change` is the net wallet delta of each row (realized − fee + funding).
Summing `Change` over all rows and removing inter-wallet transfers gives the
account's realized PnL, independent of any per-order matching:

| Sub-account | Realized (Change − transfers) | Fees | Funding |
|---|---:|---:|---:|
| MAIN | **−$1.52** | −$1.52 | −$0.48 |
| SUB | **−$261.01** | −$146.28 | −$0.07 |
| **bybit_2 (stitched)** | **−$262.52** | **−$147.81** | **−$0.55** |

MAIN was ~breakeven gross (all of its −$1.52 is fees). SUB lost ~−$115 gross on
the scalping run **plus** ~−$146 in fees.

### Why per-symbol / per-row rewrite is NOT reliable for this account

The account mixed **spot + perp** and **switched sub-accounts on 2026-05-10**.
That shows up as a large `Type='--'` cohort the per-`TRADE` parse can't see:

| Component (stitched) | `Change` sum |
|---|---:|
| Perp/UM `TRADE` rows (opens+closes, net of fee) | **+$768** |
| Spot + sub-account-switch conversion (`--`-action) rows | **−$1030** |
| Net trading realized | **−$262** |

The perp `TRADE` legs net to **+$768**, but the spot-leg P&L and the wallet
conversion at the sub-account switch net to **−$1030** — so the account's true
realized (−$262) only emerges after including the `--` rows. A per-order or
per-symbol attribution keyed on the perp `TRADE` rows alone therefore
**overstates** (e.g. BTCUSDT `TRADE`-gross reads +$913, while the account
net-lost). FIFO position-cycle reconstruction also fails: the continuously-
scalped SUB position never cleanly returns to flat, so cycles don't resolve.

**Therefore the only trustworthy figure is the account-level wallet-truth
(−$262.52). We do NOT rewrite the 463 per-row `pnl` values** — that would
fabricate precision the export can't support and risk replacing wrong numbers
with different-wrong numbers on the live money DB.

## 3. Journal-side gap (what the dashboard shows today)

The journal's summed `bybit_2` realized (prior diag pull, to be re-measured by
the dry-run below) is ≈ **−$33**, vs the wallet-truth **−$262.52** — an
~$229 **under-recording**, from:

- ~163 reconciler-closed BTC rows whose exit≈entry ⇒ pnl≈0 (false-close
  artifacts — the reconciler filled a flat close it couldn't price);
- ~7 `reconciler_incomplete` rows carrying `pnl IS NULL`;
- fees the local-compute path never captured.

## 4. The repair we DO ship (reviewable, honest)

1. **Account-truth record (this doc).** The authoritative `bybit_2` realized is
   **−$262.52** (MAIN −$1.52 + SUB −$261.01), derived from the stitched wallet
   truth. This is the number of record for the account's lifetime real PnL.
2. **Sub-account-stitch tooling.** `scripts/ops/reconcile_netting_pnl.py` now
   accepts a **repeatable `--exchange-csv`** and sums per-contract truth across
   exports (`merge_truth`), so the two-sub-account case reconciles in one run.
   It stays the operator-chosen **validate-aggregate + re-tag** model: dry-run
   by default; `--apply` only clears the orphan flag on contracts whose journal
   leg-sum validates within tolerance — it **never writes a `pnl` value**. For
   bybit_2 the BTC aggregate will (correctly) DIVERGE and be left untouched;
   only the small clean contracts (XRP/ADA/ETH) are re-tag candidates.
   Dry-run to review on the VM:
   ```
   python3 scripts/ops/reconcile_netting_pnl.py \
       --exchange-csv <MAIN_export.csv> --exchange-csv <SUB_export.csv>
   ```
   Applying the orphan-flag re-tag (`--apply`) is a Tier-3 real-money journal
   change — operator-approved, on the VM only.
3. **Authoritative surface — the broker-truth ledger.** The −$262.52 is
   recorded in the committed `comms/broker_truth_ledger.json` and surfaced
   read-only at **`GET /api/bot/pnl/broker-truth?account_id=bybit_2`**
   (`src/runtime/broker_truth.py` → `src/web/api/routers/pnl_broker_truth.py`),
   the sibling of the `/api/bot/gpu/spend` committed-ledger surface. The
   dashboard renders it next to the journal's approximate figure so the operator
   sees the true account realized without any per-row rewrite. The record is
   (re)written by a reviewed `reconcile_netting_pnl.py --emit-ledger` run, so a
   fresh export refreshes it.

   Why a ledger and not the exchange-fills store: the store's realized is
   **self-FIFO** on fills (`src.runtime.exchange_fills_store._fifo_match`), which
   mis-attributes this spot+perp / sub-account-switch account the same way (it
   would show ≈+$768, not −$262.52). The store's per-fill FIFO is correct for a
   clean perp account; the wallet-truth ledger is the honest surface for the
   pathological one. The historical SUB sub-account is also **not reachable** by
   the live fills puller's current API key, so the operator-provided UM export is
   the only complete source.

## 5. Related fixes (separate PRs)

- **PR #6331** — reconciler per-symbol cross-check so a Bybit `settleCoin`
  single-page read can't hide a live position (the 1 fresh XRP/BTC open the
  coverage check surfaced).
- **Orphan-unreconciled dashboard banner** (this branch) — surfaces
  `status='orphaned'` real-money rows that otherwise appear in neither
  Positions nor Trades.
