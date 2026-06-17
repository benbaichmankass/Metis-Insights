#!/usr/bin/env python3
r"""Backfill ``trades.closed_at`` for historical closed rows (P1-E, Tier-2).

The canonical ``closed_at`` column (added 2026-06-16, P1-B) is the single
source of truth for a trade's close timestamp — every close path now stamps
it going forward (see ``src/units/db/database.py::_migrate_add_closed_at`` and
``docs/audits/dashboard-truth-and-persistence-2026-06-16.md`` defect S2). Rows
that closed BEFORE that column existed have ``closed_at IS NULL`` and the read
path derives the value on the fly. This one-shot repair pass writes the same
derived value into the column so old data matches the new write-path.

Derivation (mirrors the read path verbatim)
--------------------------------------------
The legacy derivation lives in
``src/web/api/routers/trades_closed.py`` — both the SELECT ordering key and
``_row_to_wire``:

    # SELECT ... LEFT JOIN order_packages op ON op.linked_trade_id = t.id
    closed_at = row["closed_at"] or row["op_updated_at"] or notes_closed_at

i.e. prefer the canonical column (here always NULL — we only touch NULLs),
else the linked ``order_packages.updated_at``, else the ``closed_at`` key
parsed out of the trade's ``notes`` JSON. If none of those resolve, the row
is left NULL (we never fabricate a timestamp).

This script mirrors that chain, with one widening: the read path joins only
``op.linked_trade_id = t.id``, but the canonical persistence model also links
a trade to its package via ``trades.order_package_id = order_packages.order_package_id``
(the newer slot — see ``_migrate_add_order_package_id``). We resolve
``op.updated_at`` through EITHER link so a package that was only ever attached
by the canonical id is still found. The ``notes.closed_at`` parse uses the
same ``_decode_notes_closed_at`` helper the router uses.

What it does
------------
For every ``trades`` row with ``status='closed' AND closed_at IS NULL``
(non-backtest), resolve a ``closed_at`` from the chain above and, when one is
found, write it. Rows that can't be derived are left NULL and counted.

Safety
------
DRY-RUN by default: prints counts (scanned / fillable / left-NULL) and a small
sample of what WOULD change, and exits WITHOUT writing. Pass ``--apply`` (alias
``--commit``) to perform the UPDATE.

Idempotent: only rows where ``closed_at IS NULL`` are SELECTed and the UPDATE
re-asserts ``AND closed_at IS NULL``, so a re-run after an apply is a no-op.

Resolves the canonical DB via ``src.utils.paths.trade_journal_db_path()``;
``--db`` overrides it (tests / one-off tooling only). The ``canonical-db-resolver``
CI guard forbids a CWD-relative fallback — there is none here.

**Do NOT run ``--apply`` against the live DB without operator sign-off — this
is a Tier-2 data-mutation job.**

Usage
-----
::

    python scripts/ops/backfill_closed_at.py                 # dry-run
    python scripts/ops/backfill_closed_at.py --apply         # write closed_at
    python scripts/ops/backfill_closed_at.py --db /tmp/x.db --apply
    # also run the sibling account_class backfill in the same pass:
    python scripts/ops/backfill_closed_at.py --also-account-class --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The script lives in scripts/ops/; the repo root is two levels up. Add it to
# sys.path so `from src...` resolves when the wrapper invokes this by absolute
# path (system python3, cwd != repo root) — mirrors backfill_account_class.py /
# backfill_orphan_pnl.py. Without it: ModuleNotFoundError: No module named 'src'.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _decode_notes_closed_at(notes: Any) -> Optional[str]:
    """Parse ``closed_at`` out of a trade's ``notes`` JSON.

    A verbatim mirror of ``trades_closed.py::_decode_notes_closed_at`` — the
    reconciler-close path stuffs ``closed_at`` into the trade's ``notes`` JSON
    (``src/runtime/order_monitor.py``); we use it as the final fallback when
    the trade has no linked order_packages row. Best-effort: any malformed /
    non-dict blob yields ``None`` (never raises).
    """
    if not isinstance(notes, str) or not notes:
        return None
    try:
        decoded = json.loads(notes)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    val = decoded.get("closed_at")
    return str(val) if val is not None else None


def _load_account_class_module():
    """Load the sibling backfill_account_class.py by file path.

    ``scripts/ops`` has no ``__init__.py`` so it isn't an importable package;
    load it the same way ``tests/ops/test_backfill_account_class.py`` does.
    """
    import importlib.util

    script = Path(__file__).resolve().parent / "backfill_account_class.py"
    spec = importlib.util.spec_from_file_location("backfill_account_class", script)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"could not load {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _candidate_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Closed, non-backtest rows whose ``closed_at`` is still NULL.

    ``op_updated_at`` resolves ``order_packages.updated_at`` through EITHER
    link — the read-path join (``op.linked_trade_id = t.id``) OR the canonical
    ``t.order_package_id = op.order_package_id`` slot — via COALESCE over two
    LEFT JOINs, so a package attached by only one of the two links is found.
    """
    cur = conn.execute(
        """
        SELECT t.id,
               t.symbol,
               t.account_id,
               t.notes,
               COALESCE(op1.updated_at, op2.updated_at) AS op_updated_at
        FROM trades t
        LEFT JOIN order_packages op1 ON op1.linked_trade_id = t.id
        LEFT JOIN order_packages op2 ON op2.order_package_id = t.order_package_id
        WHERE t.status = 'closed'
          AND t.closed_at IS NULL
          AND COALESCE(t.is_backtest, 0) = 0
        ORDER BY t.id ASC
        """
    )
    return cur.fetchall()


def _resolve_closed_at(row: sqlite3.Row) -> Tuple[Optional[str], str]:
    """Return ``(closed_at, source)`` mirroring the read-path derivation.

    Precedence: linked ``order_packages.updated_at`` → ``notes.closed_at`` →
    None (underivable). ``source`` is one of ``op_updated_at`` /
    ``notes_closed_at`` / ``none`` for reporting.
    """
    op_updated_at = row["op_updated_at"]
    if op_updated_at is not None and str(op_updated_at).strip():
        return str(op_updated_at), "op_updated_at"
    notes_closed_at = _decode_notes_closed_at(row["notes"])
    if notes_closed_at is not None and str(notes_closed_at).strip():
        return notes_closed_at, "notes_closed_at"
    return None, "none"


def plan_and_apply(
    db_path: Path,
    *,
    apply: bool,
) -> Dict[str, Any]:
    """Compute (and optionally apply) the ``closed_at`` backfill.

    Returns a summary dict::

        {
          "scanned": int,            # closed rows with closed_at NULL
          "fillable": int,           # of those, a closed_at was derived
          "left_null": int,          # underivable (no package, no notes)
          "by_source": {"op_updated_at": int, "notes_closed_at": int},
          "applied": int,            # rows actually written (0 in dry-run)
          "sample": [ {id, symbol, account_id, closed_at, source}, ... ],
        }
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = _candidate_rows(conn)

        updates: List[Tuple[str, int]] = []  # (closed_at, id)
        by_source: Dict[str, int] = {"op_updated_at": 0, "notes_closed_at": 0}
        sample: List[Dict[str, Any]] = []
        left_null = 0
        for r in rows:
            closed_at, source = _resolve_closed_at(r)
            if closed_at is None:
                left_null += 1
                continue
            by_source[source] += 1
            updates.append((closed_at, int(r["id"])))
            if len(sample) < 10:
                sample.append({
                    "id": int(r["id"]),
                    "symbol": r["symbol"],
                    "account_id": r["account_id"],
                    "closed_at": closed_at,
                    "source": source,
                })

        applied = 0
        if apply and updates:
            cur = conn.cursor()
            # Re-assert closed_at IS NULL so a concurrent / repeat run can't
            # clobber a value another writer already stamped (idempotent).
            cur.executemany(
                "UPDATE trades SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
                updates,
            )
            conn.commit()
            applied = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(updates)

        return {
            "scanned": len(rows),
            "fillable": len(updates),
            "left_null": left_null,
            "by_source": by_source,
            "applied": applied,
            "sample": sample,
        }
    finally:
        conn.close()


def _print_summary(summary: Dict[str, Any], *, applied: bool) -> None:
    verb = "filled" if applied else "would fill"
    print(f"closed_at backfill: scanned {summary['scanned']} closed rows "
          f"with closed_at NULL")
    print(f"  {verb}: {summary['fillable']} "
          f"(op.updated_at={summary['by_source']['op_updated_at']}, "
          f"notes.closed_at={summary['by_source']['notes_closed_at']})")
    print(f"  left NULL (underivable): {summary['left_null']}")
    if summary["sample"]:
        print(f"\n  sample ({verb}):")
        for s in summary["sample"]:
            print(f"    id={s['id']} {str(s['symbol']):<10} "
                  f"acct={str(s['account_id']):<14} "
                  f"closed_at={s['closed_at']} [{s['source']}]")
        if summary["fillable"] > len(summary["sample"]):
            print(f"    ... and {summary['fillable'] - len(summary['sample'])} more")
    if applied:
        print(f"\n  committed: {summary['applied']} rows.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db", default=None,
        help="Path to trade_journal.db (default: canonical resolver). "
             "Use for tests / one-off tooling only.",
    )
    parser.add_argument(
        "--apply", "--commit", dest="apply", action="store_true",
        help="Perform the UPDATE. Default is dry-run (no write).",
    )
    parser.add_argument(
        "--also-account-class", action="store_true",
        help="Also run the sibling backfill_account_class pass against the "
             "same DB (account_class + is_demo from config/accounts.yaml). "
             "Reuses scripts/ops/backfill_account_class.py — no duplicate logic.",
    )
    parser.add_argument(
        "--accounts", default=None,
        help="Path to accounts.yaml for --also-account-class "
             "(default: config/accounts.yaml).",
    )
    args = parser.parse_args(argv)

    if args.db is not None:
        db_path = Path(args.db)
    else:
        from src.utils.paths import trade_journal_db_path
        db_path = Path(trade_journal_db_path())

    if not db_path.exists():
        print(f"backfill_closed_at: DB not found: {db_path}", file=sys.stderr)
        return 1

    summary = plan_and_apply(db_path, apply=args.apply)
    _print_summary(summary, applied=args.apply)

    if args.also_account_class:
        # Reuse the existing, tested account_class backfill rather than
        # re-implementing it (keeps a single source of truth for the
        # account_id -> class mapping + the is_demo sync). Loaded by file
        # path (scripts/ops has no __init__.py, so it isn't an importable
        # package) — same approach test_backfill_account_class.py uses.
        bac = _load_account_class_module()
        print("\n--- account_class backfill (delegated) ---")
        class_map = bac.build_class_map(
            Path(args.accounts) if args.accounts else None
        )
        ac_summary = bac.plan_and_apply(db_path, class_map, apply=args.apply)
        bac._print_table(ac_summary, applied=args.apply)

    if args.apply:
        print("\nbackfill_closed_at: APPLIED (committed).")
    else:
        print(
            "\nbackfill_closed_at: DRY-RUN — no rows written. "
            "Re-run with --apply to commit."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
