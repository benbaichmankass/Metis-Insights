# Netting-aware PnL reconciliation (bybit_2 orphans)

**What it fixes.** On a one-way **netting** account (`bybit_2`), N per-strategy
journal legs map onto ONE net exchange position. The exchange records a realised
PnL only at each **net** close, so there is no per-leg close to match — the
forward reconciler leaves the per-strategy legs as **orphans**
(`trades.status='orphaned'` / `reconcile_status='unreconciled'`, the red-flag
state) even though the account is healthy and flat on the exchange. This is the
recurring bybit_2 "orphan ping" class (trades 3088 / 3171, …).

Going forward, the stuck-strategy watchdog now finalises a net-flat leg as
`status='closed'` with its local pnl instead of orphaning it (#5730), so **new**
legs don't accumulate as orphans. This runbook is for reconciling the
**existing backlog** of orphan legs against exchange truth.

## The model (operator-chosen: validate-aggregate + re-tag)

`scripts/ops/reconcile_netting_pnl.py` implements the least-invasive model:

1. Per contract, sum the exchange's realised trade PnL (**ground truth** — from a
   Bybit UM Transaction Log export, or the exchange-fills store).
2. Per symbol, sum the journal legs' own local-computed pnl.
3. If the two agree within `--tol` (default ±$0.50), the orphan legs are genuine,
   accounted-for trades → re-tag them `reconcile_status='reconciled'` (with
   `--apply`). **Each leg keeps its own pnl**; only the orphan flag is cleared.
4. Any symbol whose leg-sum **diverges** from exchange truth is flagged loudly and
   **left untouched** — never silently re-tagged.

> **Caveat you will hit.** Per-leg local pnl on a netting account can diverge from
> the netted exchange realised PnL (e.g. a single BTC orphan leg carried −1.48
> while the account's *entire* BTC realised history was −0.07). When a symbol
> diverges, the tool refuses to re-tag it — investigate before forcing anything.
> Divergence means the per-leg pnl is not trustworthy, not that a re-tag is safe.

## Exchange-truth sources

- **Canonical / live:** the exchange-fills store
  (`runtime_state/exchange_fills.sqlite`, `src.runtime.exchange_fills_store`,
  surfaced at `/api/bot/pnl/exchange`). Use this when it reaches back far enough.
- **Manual:** a **Bybit UM Transaction Log** CSV export (Bybit → Assets →
  Transaction Log → export), passed via `--exchange-csv`. Use when the fills
  store doesn't cover the full orphan history.

## Running it (on the live VM)

The tool reads the **full** live journal, so it must run on the VM — the
read-only diag relay truncates a full `trades` pull (55 KB cap) and can't
reassemble the whole orphan set.

```bash
# 1. DRY-RUN report (safe, read-only). Default.
python scripts/ops/reconcile_netting_pnl.py \
    --account bybit_2 \
    --exchange-csv /path/to/BybitUMTransactionLog.csv
# Prints per-symbol: exchange gross vs journal leg-sum vs delta, and the verdict
# (OK → N orphans re-taggable / DIVERGES → left untouched / SKIP → no truth).

# 2. APPLY the re-tag (Tier-3 real-money journal writeback — operator-approved
#    only). Re-tags reconcile_status='reconciled' ONLY for within-tolerance
#    symbols; diverging symbols are never touched.
python scripts/ops/reconcile_netting_pnl.py \
    --account bybit_2 \
    --exchange-csv /path/to/BybitUMTransactionLog.csv \
    --apply
```

`--tol` widens/narrows the aggregate-match tolerance (USD). `--db` overrides the
journal path (default: the canonical `src.utils.paths.trade_journal_db_path()`).

### Sub-account stitch (repeatable `--exchange-csv`)

When an account was traded through **more than one Bybit sub-account** over its
life (the same journal `account_id` spans all of them), pass `--exchange-csv`
**once per export** — per-contract truth is summed across all of them
(`merge_truth`):

```bash
python scripts/ops/reconcile_netting_pnl.py --account bybit_2 \
    --exchange-csv /path/to/MAIN_UMLog.csv \
    --exchange-csv /path/to/SUB_UMLog.csv
```

**Caveat for spot+perp / sub-account-switch accounts:** the per-symbol truth
here is `TRADE`-row gross only. If the account also has a spot leg or a
sub-account-switch conversion (Bybit `Type='--'` rows), that gross can differ
from the account's **wallet-truth** realized — the tool will then (correctly)
report DIVERGES and leave those symbols untouched. For such accounts the
authoritative realized is the account-level wallet delta (`Change` − transfers),
not the per-symbol gross. See
[`docs/audits/bybit2-broker-reconciliation-2026-07-13.md`](../audits/bybit2-broker-reconciliation-2026-07-13.md)
for a worked example (bybit_2: perp gross +$768 vs wallet-truth −$262.52).

The run always prints the account **wallet-truth** line (`Σ Change − transfers`)
regardless of the per-symbol verdicts.

### Emitting the broker-truth ledger (`--emit-ledger`)

For an account whose per-row journal `pnl` can't be trusted (the spot+perp /
sub-account-switch case above), record the authoritative wallet-truth into the
committed **broker-truth ledger** so the dashboard can surface it next to the
journal figure — **without** rewriting any money-DB row:

```bash
python scripts/ops/reconcile_netting_pnl.py --account bybit_2 \
    --exchange-csv MAIN_UMLog.csv --exchange-csv SUB_UMLog.csv \
    --emit-ledger --ledger-as-of 2026-07-13
```

This upserts one record (keyed by `account_id`) into
`comms/broker_truth_ledger.json`, surfaced at
`GET /api/bot/pnl/broker-truth?account_id=bybit_2`
(`src/runtime/broker_truth.py`). `--emit-ledger` is **Tier-1** (writes a
committed file, no money-DB / live-state mutation) and works even when the
journal DB isn't reachable (the per-symbol reconcile is then skipped). Commit
the updated ledger; the VM mirrors it via `ict-git-sync`.

## Tier

- The **dry-run report** is Tier-1 (read-only).
- The **`--apply` re-tag** is Tier-3 (mutates real-money `trades` rows). Run it
  only after the operator has reviewed the dry-run report and approved.

There is no `system-action` for this yet; run it over SSH on the VM (operator),
or add a dry-run-only allowlist entry if it needs to be self-dispatchable.
