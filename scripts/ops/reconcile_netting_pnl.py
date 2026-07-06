#!/usr/bin/env python3
"""Netting-aware PnL reconciliation for a one-way netting account (e.g. bybit_2).

The problem this closes: on a **netting** account, N per-strategy journal legs
map onto ONE net exchange position, so the exchange only records a realised PnL
at each NET close — there is no per-leg close to match. The forward reconciler
therefore leaves the per-strategy legs as **orphans** (``status='orphaned'`` /
``reconcile_status='unreconciled'`` — the red-flag state), even though the
account is perfectly healthy and flat on the exchange.

This tool reconciles the journal against **exchange truth** (a Bybit UM
Transaction Log export, or any equivalent per-close realised-PnL source) using
the operator-chosen **validate-aggregate + re-tag** model:

  1. Per contract, sum the exchange's realised trade PnL (ground truth).
  2. Per symbol, sum the journal legs' own local-computed pnl.
  3. If the two agree within tolerance, the orphan legs for that symbol are
     genuine, accounted-for trades — re-tag them ``reconcile_status='reconciled'``
     (with ``--apply``). Each leg KEEPS its own local pnl; we only clear the
     orphan flag and validate the aggregate.
  4. Any symbol whose leg-sum DIVERGES from exchange truth is flagged loudly and
     left untouched (never silently re-tagged).

**Default is a dry-run report.** ``--apply`` performs the re-tag writeback and is
the only mutating path — a Tier-3 real-money journal change, so run it on the VM
only after operator approval.

The canonical LIVE exchange-truth source is the exchange-fills store
(``runtime_state/exchange_fills.sqlite``, ``src.runtime.exchange_fills_store``);
``--exchange-csv`` accepts the operator's manual Bybit UM export when the fills
store doesn't reach far enough back.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# --- exchange truth ---------------------------------------------------------

@dataclass
class ContractTruth:
    contract: str
    gross_pnl: float = 0.0     # sum of realised trade Cash Flow (price PnL, pre-fee)
    fees: float = 0.0          # sum of Fee Paid on trade rows (<= 0)
    funding: float = 0.0       # sum of Funding across all rows (settlements)
    net_change: float = 0.0    # sum of Change on trade rows (gross + fee)
    open_count: int = 0
    close_count: int = 0

    @property
    def net_all_in(self) -> float:
        return self.gross_pnl + self.fees + self.funding


def _to_float(x: str) -> float:
    x = (x or "").strip()
    if x in ("", "--"):
        return 0.0
    try:
        return float(x)
    except ValueError:
        return 0.0


def parse_bybit_um_csv(path: str) -> Dict[str, ContractTruth]:
    """Parse a Bybit UM Transaction Log CSV into per-contract realised truth.

    Columns used: ``Contract``, ``Type`` (TRADE / SETTLEMENT / TRANSFER_*),
    ``Action`` (OPEN / CLOSE), ``Cash Flow`` (realised price PnL of a close,
    pre-fee), ``Fee Paid``, ``Funding``, ``Change`` (net cash of the row).
    Only ``TRADE`` rows contribute realised trade PnL; ``Funding`` is summed
    across all rows (settlements) per contract.
    """
    out: Dict[str, ContractTruth] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            contract = (row.get("Contract") or "").strip()
            if not contract:
                continue
            t = out.setdefault(contract, ContractTruth(contract=contract))
            t.funding += _to_float(row.get("Funding", ""))
            if (row.get("Type") or "").strip() != "TRADE":
                continue
            t.gross_pnl += _to_float(row.get("Cash Flow", ""))
            t.fees += _to_float(row.get("Fee Paid", ""))
            t.net_change += _to_float(row.get("Change", ""))
            action = (row.get("Action") or "").strip().upper()
            if action == "OPEN":
                t.open_count += 1
            elif action == "CLOSE":
                t.close_count += 1
    return out


# --- journal side -----------------------------------------------------------

@dataclass
class SymbolLegs:
    symbol: str
    leg_ids: List[int] = field(default_factory=list)
    orphan_leg_ids: List[int] = field(default_factory=list)
    pnl_sum: float = 0.0            # sum of local pnl over ALL resolved legs
    orphan_pnl_sum: float = 0.0     # sum of local pnl over the orphan legs only
    orphan_null_pnl: int = 0        # orphan legs with no pnl recorded


def _is_orphan(row: sqlite3.Row) -> bool:
    status = str(row["status"] or "").strip().lower()
    recon = str(row["reconcile_status"] or "").strip().lower()
    return status == "orphaned" or recon == "unreconciled"


def aggregate_journal_legs(rows: List[sqlite3.Row]) -> Dict[str, SymbolLegs]:
    """Group real-money, non-backtest legs by symbol; split out the orphans."""
    out: Dict[str, SymbolLegs] = {}
    for r in rows:
        symbol = str(r["symbol"] or "").strip()
        if not symbol:
            continue
        sl = out.setdefault(symbol, SymbolLegs(symbol=symbol))
        sl.leg_ids.append(int(r["id"]))
        pnl = r["pnl"]
        if pnl is not None:
            sl.pnl_sum += float(pnl)
        if _is_orphan(r):
            sl.orphan_leg_ids.append(int(r["id"]))
            if pnl is None:
                sl.orphan_null_pnl += 1
            else:
                sl.orphan_pnl_sum += float(pnl)
    return out


def _load_journal_rows(db_path: str, account_id: str) -> List[sqlite3.Row]:
    """Real-money, non-backtest trades for *account_id* (read-only)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        # is_backtest / account_class guards only if the columns exist.
        where = ["account_id = ?"]
        if "is_backtest" in cols:
            where.append("COALESCE(is_backtest, 0) = 0")
        sql = (
            "SELECT id, account_id, symbol, status, pnl, "
            "reconcile_status, setup_type, strategy_name "
            "FROM trades WHERE " + " AND ".join(where)
        )
        return list(conn.execute(sql, (account_id,)))
    finally:
        conn.close()


# --- reconciliation ---------------------------------------------------------

@dataclass
class SymbolReconcile:
    symbol: str
    exchange_gross: Optional[float]
    journal_pnl_sum: float
    delta: Optional[float]
    within_tol: bool
    orphan_leg_ids: List[int]
    orphan_null_pnl: int
    note: str = ""


def reconcile(
    truth: Dict[str, ContractTruth],
    legs: Dict[str, SymbolLegs],
    *,
    tol: float,
) -> List[SymbolReconcile]:
    """Compare, per symbol, journal leg-sum vs exchange GROSS realised PnL.

    Gross (price PnL, pre-fee) is the closest basis to the journal's own
    local-computed pnl (also price-based). Fees/funding drag is reported
    separately, not folded into the tolerance test.
    """
    out: List[SymbolReconcile] = []
    for symbol in sorted(set(truth) | set(legs)):
        t = truth.get(symbol)
        sl = legs.get(symbol)
        ex_gross = t.gross_pnl if t is not None else None
        j_sum = sl.pnl_sum if sl is not None else 0.0
        orphan_ids = list(sl.orphan_leg_ids) if sl is not None else []
        orphan_null = sl.orphan_null_pnl if sl is not None else 0
        if ex_gross is None:
            out.append(SymbolReconcile(
                symbol, None, j_sum, None, False, orphan_ids, orphan_null,
                note="no exchange-truth rows for this symbol — cannot validate",
            ))
            continue
        delta = j_sum - ex_gross
        within = abs(delta) <= tol
        note = ""
        if orphan_null:
            note = f"{orphan_null} orphan leg(s) carry NULL pnl (excluded from leg-sum)"
        out.append(SymbolReconcile(
            symbol, ex_gross, j_sum, delta, within, orphan_ids, orphan_null, note,
        ))
    return out


def apply_retag(db_path: str, leg_ids: List[int]) -> int:
    """Re-tag the given legs ``reconcile_status='reconciled'``. Returns rows changed."""
    if not leg_ids:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        qmarks = ",".join("?" for _ in leg_ids)
        cur = conn.execute(
            f"UPDATE trades SET reconcile_status='reconciled' WHERE id IN ({qmarks})",
            leg_ids,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# --- CLI --------------------------------------------------------------------

def _resolve_db_path(arg: Optional[str]) -> str:
    if arg:
        return arg
    try:
        from src.utils.paths import trade_journal_db_path
        return trade_journal_db_path()
    except Exception:  # noqa: BLE001 — fall back to the repo-root default
        return "trade_journal.db"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default="bybit_2", help="netting account id (default bybit_2)")
    ap.add_argument("--exchange-csv", required=True, help="Bybit UM Transaction Log CSV")
    ap.add_argument("--db", default=None, help="trade_journal.db path (default: canonical resolver)")
    ap.add_argument("--tol", type=float, default=0.50,
                    help="USD tolerance for the per-symbol aggregate match (default 0.50)")
    ap.add_argument("--apply", action="store_true",
                    help="RE-TAG reconciled orphans (Tier-3 writeback). Default is dry-run.")
    args = ap.parse_args(argv[1:])

    db_path = _resolve_db_path(args.db)
    truth = parse_bybit_um_csv(args.exchange_csv)
    rows = _load_journal_rows(db_path, args.account)
    legs = aggregate_journal_legs(rows)
    results = reconcile(truth, legs, tol=args.tol)

    print(f"Netting-aware PnL reconciliation — account={args.account} db={db_path}")
    print(f"Exchange truth: {args.exchange_csv}  |  tolerance=±${args.tol:.2f}  |  "
          f"{'APPLY (writeback)' if args.apply else 'DRY-RUN (report only)'}\n")
    header = f"{'symbol':10s} {'ex_gross':>10s} {'journal_sum':>12s} {'delta':>9s}  {'orphans':>7s}  verdict"
    print(header)
    print("-" * len(header))

    to_retag: List[int] = []
    diverged: List[str] = []
    for r in results:
        exg = f"{r.exchange_gross:+.4f}" if r.exchange_gross is not None else "   n/a"
        dl = f"{r.delta:+.4f}" if r.delta is not None else "   n/a"
        if r.exchange_gross is None:
            verdict = "SKIP (no exchange truth)"
        elif r.within_tol:
            verdict = f"OK — re-tag {len(r.orphan_leg_ids)} orphan(s)"
            to_retag.extend(r.orphan_leg_ids)
        else:
            verdict = "DIVERGES — left untouched"
            diverged.append(r.symbol)
        line = f"{r.symbol:10s} {exg:>10s} {r.journal_pnl_sum:>+12.4f} {dl:>9s}  {len(r.orphan_leg_ids):>7d}  {verdict}"
        print(line)
        if r.note:
            print(f"{'':10s} └─ {r.note}")

    print()
    print(f"Orphan legs eligible for re-tag: {len(to_retag)}")
    if diverged:
        print(f"⚠️  DIVERGING symbols (NOT re-tagged, investigate): {', '.join(diverged)}")

    if args.apply:
        changed = apply_retag(db_path, to_retag)
        print(f"\nAPPLIED: {changed} leg(s) set reconcile_status='reconciled'.")
    else:
        print("\nDry-run — no rows changed. Re-run with --apply (on the VM, after approval) to re-tag.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
