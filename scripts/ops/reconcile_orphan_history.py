"""Historical orphan-flap reconciliation — collapse phantom flap duplicates
so every physical position is ONE reconciled row, and no trade rests silently
in an orphan state.

Orphan-flap hardening **item #5** (operator directive 2026-06-24): "across all
accounts, all trades should have one reconciled row — the orphan status is a
red flag to be taken down, not a status to be accepted as legitimate. Anything
that is orphaned should be reconciled against the real trade/order package it
belongs to. No rows should stay as orphaned. ONLY if we've exhausted every
investigative avenue and are still not able to reconcile it should it have a
status that indicates that." Method: **void-flag, don't delete.**

Background: a single physical position can flap into N journal rows. The
classic case (BL-20260624-MHG-CLOSE-CONFIRM and the MGC −$20,127 incident): a
position is adopted as a bare ``orphan_adopt`` row, "closed" at an ``sl_cross``
that never actually flattened the broker position, re-orphaned, re-adopted …
looping into many phantom ``adopted_orphan`` closed trades — each carrying a
fabricated realised PnL that polluted the aggregates. The live-path fixes
(items #1-#3: confirm-flatten on close, bracket self-cancel, re-adopt flap
guard) stop NEW flaps; item #4 added the explicit ``reconcile_status`` column.
This tool cleans up the **historical** footprint those bugs already wrote.

What it does (per cluster of one physical position):
  * Groups orphan-flagged rows by ``(account_id, symbol, normalised
    direction)`` and splits each group into time-contiguous clusters (a gap
    larger than ``--cluster-gap-hours`` starts a new cluster, so two genuinely
    distinct positions on the same symbol/side are never merged).
  * Picks ONE canonical row to keep — the live OPEN row if the cluster has one
    (never hide a live position), else the earliest row.
  * Reconciles the canonical to its originating order package (symbol +
    normalised direction + entry within ``--entry-tol`` relative) → its
    ``reconcile_status`` becomes ``'reconciled'`` (and a missing
    ``order_package_id`` is filled). If no package is recoverable it stays
    ``'unreconciled'`` — the honest red-flag terminal state.
  * Void-flags the phantom duplicate rows as ``reconcile_status='superseded'``
    (excluded from analytics) — but ONLY rows that are phantom-like (no
    distinct real order-package link); a duplicate that itself links to a
    *different* real package is a genuinely distinct trade and is preserved
    (reconciled in place, never superseded). An ``status='open'`` row is never
    superseded.
  * Preserves the original row entirely (status, pnl, …) and records the merge
    in ``notes`` (``superseded_by`` / ``superseded_at`` / ``superseded_reason``
    on the duplicate; ``reconciled_at`` / ``reconciled_by`` on the canonical) —
    void-flag, not delete, so the full audit trail survives.

What it does NOT do:
  * It never deletes a row.
  * It never recomputes a PnL. For broker-API accounts (bybit) run
    ``backfill_orphan_pnl.py`` FIRST to recover the real exit/PnL on the
    canonical row from Bybit's closed-pnl window; this tool only removes the
    duplicate phantom rows around it. For manual-bridge / no-API accounts
    (ib_paper) the canonical simply carries whatever it had, flagged.
  * It never touches ``is_backtest=1`` rows or rows already
    ``reconcile_status='superseded'`` (idempotent — a second run is a no-op).
  * It never closes / opens an exchange position. Pure journal hygiene.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/reconcile_orphan_history.py            # dry-run report
    python3 scripts/ops/reconcile_orphan_history.py --apply    # write (gated)

Safety:
  * **Dry-run by default.** ``--apply`` is required to write, and the operator
    action wrapper gates it (the live apply touches a real-money ``bybit_2``
    cluster).
  * On ``--apply`` a timestamped ``cp`` backup of the DB is taken first.
  * Every UPDATE is guarded so a row can only move INTO a terminal reconcile
    state once; re-runs are no-ops.
  * Conservative clustering + the distinct-package guard mean a genuine trade
    is never collapsed into another.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# scripts/ops/ → repo root is two levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── orphan identification ────────────────────────────────────────────────

# A trade row is "orphan-flagged" — a candidate for this pass — when ANY of
# these markers is set. Mirrors every path that mints an orphan in
# src/runtime/order_monitor.py: status='orphaned' (forward reconciler
# _mark_orphaned + stuck watchdog), setup_type='adopted_orphan' /
# strategy_name='orphan_adopt' (reverse reconciler adopt), and the explicit
# reconcile_status='unreconciled' column (item #4). Inferring from multiple
# markers — not one — is deliberate so pre-#4 rows (reconcile_status NULL) are
# still caught.
_ORPHAN_PREDICATE = (
    "(status = 'orphaned' "
    " OR setup_type = 'adopted_orphan' "
    " OR strategy_name = 'orphan_adopt' "
    " OR reconcile_status = 'unreconciled')"
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _canon_dir(direction: Any) -> Optional[str]:
    """buy/long → 'long', sell/short → 'short', else None.

    Same normalisation the live reconciler uses
    (``order_monitor._canon_dir``) so cluster grouping + package matching
    agree with the runtime path.
    """
    d = str(direction or "").lower()
    if d in ("buy", "long"):
        return "long"
    if d in ("sell", "short"):
        return "short"
    return None


def _parse_ts_to_ms(value: Any) -> Optional[int]:
    """Best-effort ISO-8601 / sqlite-CURRENT_TIMESTAMP → epoch ms."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Raw epoch-ms string (the reconciler-filled close path writes these).
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _decode_notes(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _orphan_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Every orphan-flagged, non-backtest, not-already-superseded trade."""
    cur = conn.execute(
        f"""
        SELECT id, timestamp, symbol, direction, entry_price, exit_price,
               position_size, status, setup_type, strategy_name, pnl,
               exit_reason, account_id, created_at, closed_at, notes,
               order_package_id, reconcile_status, is_backtest
        FROM trades
        WHERE COALESCE(is_backtest, 0) = 0
          AND COALESCE(reconcile_status, '') != 'superseded'
          AND {_ORPHAN_PREDICATE}
        ORDER BY account_id, symbol, direction, id ASC
        """
    )
    return cur.fetchall()


# ── order-package recovery (mirror of order_monitor._recover_orphan_…) ────

def _recover_package(
    conn: sqlite3.Connection, *, symbol: str, direction: str,
    entry_price: Optional[float], entry_tol: float,
) -> Optional[Dict[str, Any]]:
    """Find the order package that originally opened this position.

    Newest-first on symbol + normalised direction, entry within ``entry_tol``
    relative — the same confident-match rule as
    ``order_monitor._recover_orphan_order_package`` so we never mis-attribute a
    position to the wrong strategy. Returns the package dict or None.
    """
    want = _canon_dir(direction)
    if not want or not entry_price:
        return None
    try:
        cur = conn.execute(
            "SELECT order_package_id, strategy_name, symbol, direction, "
            "       entry, sl, tp, status, linked_trade_id "
            "FROM order_packages WHERE symbol = ? "
            "ORDER BY datetime(created_at) DESC LIMIT 60",
            [symbol],
        )
    except sqlite3.Error:
        return None
    for c in cur.fetchall():
        cd = dict(c)
        if _canon_dir(cd.get("direction")) != want:
            continue
        pe = cd.get("entry")
        if pe is None:
            continue
        try:
            if abs(float(pe) - entry_price) / entry_price <= entry_tol:
                return cd
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return None


# ── clustering ────────────────────────────────────────────────────────────

def _sort_ts(row: sqlite3.Row) -> int:
    """Sortable open-time for a row (created_at → timestamp → id-fallback)."""
    for key in ("created_at", "timestamp"):
        ms = _parse_ts_to_ms(row[key])
        if ms is not None:
            return ms
    # No usable timestamp — fall back to row id so order is at least stable.
    return int(row["id"])


def _cluster(
    rows: List[sqlite3.Row], gap_hours: float,
) -> List[List[sqlite3.Row]]:
    """Split one (account, symbol, direction) group into time-contiguous
    clusters. A gap > ``gap_hours`` between consecutive open-times starts a
    new cluster, so two genuinely distinct positions are never merged into
    one physical-position flap."""
    if not rows:
        return []
    gap_ms = int(gap_hours * 3_600_000)
    ordered = sorted(rows, key=_sort_ts)
    clusters: List[List[sqlite3.Row]] = [[ordered[0]]]
    last_ts = _sort_ts(ordered[0])
    for row in ordered[1:]:
        ts = _sort_ts(row)
        if gap_ms > 0 and (ts - last_ts) > gap_ms:
            clusters.append([row])
        else:
            clusters[-1].append(row)
        last_ts = ts
    return clusters


def _group_key(row: sqlite3.Row) -> Tuple[str, str, str]:
    return (
        str(row["account_id"] or ""),
        str(row["symbol"] or ""),
        _canon_dir(row["direction"]) or str(row["direction"] or ""),
    )


# ── planning ──────────────────────────────────────────────────────────────

class RowPlan:
    """One row's planned reconcile action."""

    __slots__ = ("trade_id", "action", "reconcile_status", "extra", "note")

    def __init__(self, trade_id: int, action: str, reconcile_status: str,
                 extra: Optional[Dict[str, Any]] = None, note: str = ""):
        self.trade_id = trade_id
        self.action = action  # 'canonical' | 'superseded' | 'distinct'
        self.reconcile_status = reconcile_status
        self.extra = extra or {}
        self.note = note


def _plan_cluster(
    conn: sqlite3.Connection, cluster: List[sqlite3.Row], *,
    entry_tol: float, now_iso: str,
) -> List[RowPlan]:
    """Produce the per-row plan for one physical-position cluster.

    Canonical selection: the single OPEN row if exactly one exists (never hide
    a live position), else the earliest row. The canonical is reconciled to its
    originating package when recoverable, else flagged ``unreconciled``. Every
    OTHER row is superseded UNLESS it links to a *distinct* real package (then
    it is a genuinely separate trade, reconciled in place) or is itself
    ``status='open'`` (never void-flag a live position).
    """
    ordered = sorted(cluster, key=_sort_ts)
    open_rows = [r for r in ordered if str(r["status"] or "") == "open"]

    if len(open_rows) == 1:
        canonical = open_rows[0]
    else:
        # 0 open (all closed) → earliest closed canonical.
        # >1 open → ambiguous; keep earliest open as canonical but never
        # supersede the other open rows (handled per-row below).
        canonical = open_rows[0] if open_rows else ordered[0]

    # Reconcile the canonical to its originating package.
    try:
        entry = float(canonical["entry_price"]) if canonical["entry_price"] else None
    except (TypeError, ValueError):
        entry = None
    pkg = _recover_package(
        conn, symbol=str(canonical["symbol"] or ""),
        direction=str(canonical["direction"] or ""), entry_price=entry,
        entry_tol=entry_tol,
    )
    canon_pkg_id = canonical["order_package_id"] or (
        pkg.get("order_package_id") if pkg else None)

    plans: List[RowPlan] = []
    if pkg is not None or canonical["order_package_id"]:
        canon_status = "reconciled"
        canon_extra: Dict[str, Any] = {
            "reconciled_at": now_iso,
            "reconciled_by": "reconcile_orphan_history",
            "reconciled_to_package": canon_pkg_id,
        }
        # Fill a missing package link (don't overwrite an existing one).
        if pkg is not None and not canonical["order_package_id"]:
            canon_extra["_set_order_package_id"] = pkg.get("order_package_id")
        canon_note = f"reconciled to package {canon_pkg_id}"
    else:
        canon_status = "unreconciled"
        canon_extra = {
            "reconcile_investigated_at": now_iso,
            "reconcile_investigated_by": "reconcile_orphan_history",
            "reconcile_outcome": "no_recoverable_order_package",
        }
        canon_note = "no recoverable package — flagged unreconciled"
    plans.append(RowPlan(int(canonical["id"]), "canonical", canon_status,
                         canon_extra, canon_note))

    canon_pkg = str(canon_pkg_id) if canon_pkg_id else None
    for row in ordered:
        if int(row["id"]) == int(canonical["id"]):
            continue
        row_pkg = row["order_package_id"]
        row_open = str(row["status"] or "") == "open"
        # A duplicate that links to a DISTINCT real package is a genuinely
        # separate trade — reconcile it in place, never supersede it.
        distinct_pkg = (
            row_pkg is not None and canon_pkg is not None
            and str(row_pkg) != canon_pkg
        )
        if distinct_pkg:
            plans.append(RowPlan(
                int(row["id"]), "distinct", "reconciled",
                {"reconciled_at": now_iso,
                 "reconciled_by": "reconcile_orphan_history",
                 "reconciled_to_package": str(row_pkg)},
                f"distinct package {row_pkg} — kept as its own trade",
            ))
            continue
        if row_open:
            # Never void-flag a live position. Leave it; flag for manual review.
            plans.append(RowPlan(
                int(row["id"]), "distinct", "unreconciled",
                {"reconcile_investigated_at": now_iso,
                 "reconcile_investigated_by": "reconcile_orphan_history",
                 "reconcile_outcome": "second_open_row_in_cluster_manual_review"},
                "second OPEN row in cluster — left open, flagged for review",
            ))
            continue
        # Phantom flap duplicate → supersede (void-flag, excluded from analytics).
        plans.append(RowPlan(
            int(row["id"]), "superseded", "superseded",
            {"superseded_by": int(canonical["id"]),
             "superseded_at": now_iso,
             "superseded_reason": "phantom_orphan_flap_duplicate"},
            f"phantom duplicate → superseded by {canonical['id']}",
        ))
    return plans


# ── apply ─────────────────────────────────────────────────────────────────

def _apply_plan(
    conn: sqlite3.Connection, row_by_id: Dict[int, sqlite3.Row],
    plan: RowPlan,
) -> int:
    """Write one row's plan. Guarded so a row can only move into its terminal
    reconcile state once (idempotent re-runs). Returns rowcount."""
    row = row_by_id[plan.trade_id]
    notes = _decode_notes(row["notes"])
    # Merge the audit fields into notes (preserve everything already there).
    for k, v in plan.extra.items():
        if k == "_set_order_package_id":
            continue
        notes[k] = v
    sets: Dict[str, Any] = {
        "reconcile_status": plan.reconcile_status,
        "notes": json.dumps(notes, ensure_ascii=False)[:4000],
    }
    set_pkg = plan.extra.get("_set_order_package_id")
    if set_pkg is not None:
        sets["order_package_id"] = set_pkg
    cols = ", ".join(f"{k} = ?" for k in sets)
    params = list(sets.values()) + [plan.trade_id]
    # Guard: only write a row that is NOT already in the target reconcile
    # state (so re-runs are no-ops) and never re-supersede.
    cur = conn.execute(
        f"UPDATE trades SET {cols} "
        "WHERE id = ? "
        "  AND COALESCE(reconcile_status, '') != 'superseded' "
        "  AND COALESCE(reconcile_status, '') != ?",
        params + [plan.reconcile_status],
    )
    return cur.rowcount


def _backup_db(db_path: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = f"{db_path}.reconcile-orphan-bak-{ts}"
    shutil.copy2(db_path, dest)
    return dest


# ── main ──────────────────────────────────────────────────────────────────

def _summarise(plans: List[RowPlan]) -> Dict[str, int]:
    out = {"canonical_reconciled": 0, "canonical_unreconciled": 0,
           "superseded": 0, "distinct_kept": 0, "open_manual_review": 0}
    for p in plans:
        if p.action == "canonical":
            if p.reconcile_status == "reconciled":
                out["canonical_reconciled"] += 1
            else:
                out["canonical_unreconciled"] += 1
        elif p.action == "superseded":
            out["superseded"] += 1
        elif p.action == "distinct":
            if "manual_review" in p.note:
                out["open_manual_review"] += 1
            else:
                out["distinct_kept"] += 1
    return out


def run(db_path: str, *, apply: bool, gap_hours: float,
        entry_tol: float) -> int:
    conn = _connect(db_path)
    rows = _orphan_rows(conn)
    if not rows:
        print(f"no orphan-flagged rows in {db_path} — nothing to reconcile")
        return 0

    row_by_id: Dict[int, sqlite3.Row] = {int(r["id"]): r for r in rows}
    now_iso = datetime.now(timezone.utc).isoformat()

    # Group → cluster → plan.
    groups: Dict[Tuple[str, str, str], List[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault(_group_key(r), []).append(r)

    all_plans: List[RowPlan] = []
    cluster_count = 0
    print(f"db: {db_path}")
    print(f"orphan-flagged rows: {len(rows)} across {len(groups)} "
          f"(account,symbol,direction) groups\n")
    for key, grp in sorted(groups.items()):
        for cluster in _cluster(grp, gap_hours):
            cluster_count += 1
            plans = _plan_cluster(conn, cluster, entry_tol=entry_tol,
                                  now_iso=now_iso)
            all_plans.extend(plans)
            if len(cluster) > 1 or plans[0].reconcile_status == "unreconciled":
                acct, sym, direction = key
                canon = next(p for p in plans if p.action == "canonical")
                print(f"  cluster {acct}/{sym}/{direction} "
                      f"({len(cluster)} row(s)):")
                for p in plans:
                    tag = {"canonical": "KEEP", "superseded": "VOID",
                           "distinct": "KEEP*"}[p.action]
                    print(f"    [{tag}] id={p.trade_id} "
                          f"→ {p.reconcile_status}: {p.note}")
                _ = canon

    summary = _summarise(all_plans)
    print(f"\nclusters: {cluster_count}")
    print(f"canonical reconciled : {summary['canonical_reconciled']}")
    print(f"canonical unreconciled: {summary['canonical_unreconciled']} "
          f"(red-flag — needs investigation)")
    print(f"superseded (void)    : {summary['superseded']}")
    print(f"distinct kept        : {summary['distinct_kept']}")
    print(f"open, manual review  : {summary['open_manual_review']}")

    if not apply:
        print("\ndry-run — pass --apply to write (a DB backup is taken first).")
        return 0

    backup = _backup_db(db_path)
    print(f"\nbackup: {backup}")
    written = 0
    for p in all_plans:
        written += _apply_plan(conn, row_by_id, p)
    conn.commit()
    print(f"wrote {written} row(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the reconciliation (default: dry-run). "
                             "Takes a timestamped DB backup first.")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or ./trade_journal.db).")
    parser.add_argument("--cluster-gap-hours", type=float, default=6.0,
                        help="A gap larger than this between consecutive "
                             "orphan open-times starts a new physical-position "
                             "cluster (default 6).")
    parser.add_argument("--entry-tol", type=float, default=0.02,
                        help="Relative entry-price tolerance for confident "
                             "order-package recovery (default 0.02 = 2%%).")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    return run(db_path, apply=args.apply, gap_hours=args.cluster_gap_hours,
               entry_tol=args.entry_tol)


if __name__ == "__main__":
    sys.exit(main())
