#!/usr/bin/env python3
r"""Backfill ``trades.account_class`` from config/accounts.yaml (Tier-2).

The ``account_class`` column (paper | real_money) was added 2026-06-15 as
the single source of truth for the paper/real-money reporting axis. Rows
written before then have ``account_class IS NULL`` and only the legacy
``is_demo`` boolean. Worse, the pre-fix ``ib_paper`` account carried NO
category stamp, so its PAPER trades were journaled ``is_demo=0`` (i.e.
indistinguishable from real money) — this script CORRECTS those rows.

What it does
------------
For every ``trades`` row, set ``account_class`` from a
``account_id → class`` map derived from ``config/accounts.yaml`` (via the
canonical ``load_accounts_dict`` reader). It also keeps ``is_demo`` in
sync (``1`` where class == paper, else ``0``) so legacy consumers stay
consistent. Account ids ABSENT from the current YAML (historical / removed
accounts) default to ``real_money`` EXCEPT the explicit historical-paper
override set below (currently empty — every current paper account
— bybit_1, ib_paper, oanda_practice, alpaca_paper — still exists in the
YAML, so no override is needed today).

Safety
------
DRY-RUN by default: prints a per-account before/after table of how many
rows WOULD change and exits WITHOUT writing. Pass ``--apply`` to perform
the UPDATE inside a single transaction. Resolves the canonical DB via
``src.utils.paths.trade_journal_db_path()``; ``--db`` overrides it (for
tests / one-off tooling only). **Do NOT run ``--apply`` against the live
DB without operator sign-off — this is a Tier-2 data-mutation job.**

Usage
-----
::

    python scripts/ops/backfill_account_class.py            # dry-run
    python scripts/ops/backfill_account_class.py --apply     # write
    python scripts/ops/backfill_account_class.py --db /tmp/x.db --apply
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

# The script lives in scripts/ops/; the repo root is two levels up. Add it
# to sys.path so `from src...` resolves when the wrapper invokes this by
# absolute path (system python3, cwd != repo root) — mirrors the bootstrap
# in backfill_orphan_pnl.py. Without it: ModuleNotFoundError: No module
# named 'src' (the 2026-06-15 dispatch failure, issue #3708).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Account ids that no longer exist in accounts.yaml but were PAPER money
# historically. Absent ids otherwise default to real_money (the safe
# assumption for a removed real-money account). Currently empty: every
# current paper account still exists in the YAML, so nothing needs a
# manual override. Add ``"<account_id>": "paper"`` here only if a paper
# account is ever removed from the YAML while its rows remain.
_HISTORICAL_CLASS_OVERRIDES: Dict[str, str] = {}

_VALID_CLASSES = frozenset({"paper", "real_money"})


def build_class_map(accounts_yaml: Optional[Path] = None) -> Dict[str, str]:
    """Return ``{account_id: account_class}`` from accounts.yaml + overrides."""
    from src.config.accounts_loader import load_accounts_dict

    accounts = load_accounts_dict(accounts_yaml)
    out: Dict[str, str] = dict(_HISTORICAL_CLASS_OVERRIDES)
    for name, cfg in accounts.items():
        if not isinstance(cfg, dict):
            continue
        raw = str(cfg.get("account_class") or "real_money").strip().lower()
        out[name] = raw if raw in _VALID_CLASSES else "real_money"
    return out


def class_for(account_id: Optional[str], class_map: Dict[str, str]) -> str:
    """Resolve an account_id to its class; absent → real_money (or override)."""
    if account_id is None:
        return "real_money"
    return class_map.get(str(account_id), "real_money")


def plan_and_apply(
    db_path: Path,
    class_map: Dict[str, str],
    *,
    apply: bool,
) -> Dict[str, Dict[str, int]]:
    """Compute (and optionally apply) the backfill.

    Returns ``{account_id: {"rows": N, "account_class_changes": N,
    "is_demo_changes": N, "target_class": "..."}}`` for reporting.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, account_id, account_class, COALESCE(is_demo, 0) AS is_demo "
            "FROM trades"
        )
        rows = cur.fetchall()

        summary: Dict[str, Dict[str, int]] = {}
        updates = []  # (id, target_class, target_is_demo)
        for r in rows:
            aid = r["account_id"]
            target_class = class_for(aid, class_map)
            target_is_demo = 1 if target_class == "paper" else 0
            bucket = summary.setdefault(
                str(aid),
                {
                    "rows": 0,
                    "account_class_changes": 0,
                    "is_demo_changes": 0,
                    "target_class": target_class,
                },
            )
            bucket["rows"] += 1
            cur_class = r["account_class"]
            if cur_class != target_class:
                bucket["account_class_changes"] += 1
            if int(r["is_demo"]) != target_is_demo:
                bucket["is_demo_changes"] += 1
            if cur_class != target_class or int(r["is_demo"]) != target_is_demo:
                updates.append((int(r["id"]), target_class, target_is_demo))

        if apply and updates:
            cur.executemany(
                "UPDATE trades SET account_class = ?, is_demo = ? WHERE id = ?",
                [(c, d, i) for (i, c, d) in updates],
            )
            conn.commit()
        return summary
    finally:
        conn.close()


def _print_table(summary: Dict[str, Dict[str, int]], *, applied: bool) -> None:
    verb = "changed" if applied else "would change"
    print(f"{'account_id':<22} {'class':<12} {'rows':>7} "
          f"{'class ' + verb:>16} {'is_demo ' + verb:>18}")
    print("-" * 80)
    for aid in sorted(summary):
        b = summary[aid]
        print(
            f"{aid:<22} {b['target_class']:<12} {b['rows']:>7} "
            f"{b['account_class_changes']:>16} {b['is_demo_changes']:>18}"
        )
    total_rows = sum(b["rows"] for b in summary.values())
    total_class = sum(b["account_class_changes"] for b in summary.values())
    total_demo = sum(b["is_demo_changes"] for b in summary.values())
    print("-" * 80)
    print(f"{'TOTAL':<22} {'':<12} {total_rows:>7} "
          f"{total_class:>16} {total_demo:>18}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db", default=None,
        help="Path to trade_journal.db (default: canonical resolver). "
             "Use for tests / one-off tooling only.",
    )
    parser.add_argument(
        "--accounts", default=None,
        help="Path to accounts.yaml (default: config/accounts.yaml).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Perform the UPDATE. Default is dry-run (no write).",
    )
    args = parser.parse_args(argv)

    if args.db is not None:
        db_path = Path(args.db)
    else:
        from src.utils.paths import trade_journal_db_path
        db_path = Path(trade_journal_db_path())

    if not db_path.exists():
        print(f"backfill_account_class: DB not found: {db_path}", file=sys.stderr)
        return 1

    class_map = build_class_map(
        Path(args.accounts) if args.accounts else None
    )

    summary = plan_and_apply(db_path, class_map, apply=args.apply)
    _print_table(summary, applied=args.apply)

    if args.apply:
        print("\nbackfill_account_class: APPLIED (committed).")
    else:
        print(
            "\nbackfill_account_class: DRY-RUN — no rows written. "
            "Re-run with --apply to commit."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
