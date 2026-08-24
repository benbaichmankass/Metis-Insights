#!/usr/bin/env python3
"""Measure how much of the journal's PnL is MEASURED vs MANUFACTURED.

The standing instrument for the question the 2026-07-30 audit had to answer by
hand: *what fraction of the numbers we make decisions on are actually
measurements?* Re-runnable, so coverage can be tracked over time instead of
rediscovered during the next incident.

Reports, per source and per account and per month:

* the **ML label population** (`status='closed' AND pnl IS NOT NULL`, non-backtest)
  — the exact rows `ml/datasets/families/trade_outcomes.py` turns into its
  `won = pnl > 0` label, which it currently does with NO provenance filter;
* **label-flip risk** — how many `won` labels rest on a mark-substituted price;
* the **INV-2 population** (closed rows with `pnl IS NULL`) and how many have
  been explicitly declared `unmeasured`;
* **malformed-notes rows**, the `json_extract`-crash class.

Read-only: opens SQLite `mode=ro` and issues SELECTs only. Safe to run against
the live journal, a trainer copy, or any snapshot.

Every `json_extract` here is `json_valid`-guarded — unguarded, ONE malformed row
would abort the whole statement (see `scripts/ci/check_json_extract_guarded.py`).

Usage:
    python3 scripts/ops/provenance_exposure_audit.py [--db PATH] [--json]

With no `--db` it picks the LARGEST `trade_journal*.db` it can find under the
usual roots and prints every candidate's size + mtime, so the choice is
auditable — a repo-root 8 MB decoy next to a 677 MB real store is a real
configuration that has been observed on the trainer VM.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

_SEARCH_ROOTS = ("/home/ubuntu", "/data", "/opt", ".")

# Guarded notes extraction — see the module docstring.
_SRC = ("COALESCE(CASE WHEN json_valid(notes) "
        "THEN json_extract(notes,'$.pnl_source') END,'(none)')")
_XSRC = ("COALESCE(CASE WHEN json_valid(notes) "
         "THEN json_extract(notes,'$.exit_price_source') END,'(none)')")
_POP = "status='closed' AND COALESCE(is_backtest,0)=0 AND pnl IS NOT NULL"
_FAB = f"{_XSRC} IN ('local_markprice','markprice_local')"


def _candidates() -> List[str]:
    found = set()
    for root in _SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        found.update(glob.glob(f"{root}/**/trade_journal*.db", recursive=True))
    return sorted(found, key=lambda p: -os.path.getsize(p))


def _mtime(path: str) -> str:
    return datetime.datetime.utcfromtimestamp(
        os.path.getmtime(path)
    ).isoformat() + "Z"


def _query(conn: sqlite3.Connection, sql: str) -> Dict[str, Any]:
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return {"columns": cols, "rows": [list(r) for r in cur.fetchall()]}
    except Exception as exc:  # noqa: BLE001 — one bad section never kills the report
        return {"error": f"{type(exc).__name__}: {exc}"}


def audit(db: str) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            "db": db,
            "db_bytes": os.path.getsize(db),
            "db_mtime_utc": _mtime(db),
            "sections": {
                # M39(A), 2026-08-24 — first, because it is the split that
                # reframes every section below it.
                "0a_coverage_by_close_path": _close_path_coverage(conn),
                "0_staleness": _query(conn,
                    "SELECT MAX(COALESCE(closed_at,created_at,timestamp)) newest_closed, "
                    "COUNT(*) closed_rows FROM trades WHERE status='closed'"),
                "1_label_population_by_pnl_source": _query(conn,
                    f"SELECT {_SRC} AS pnl_source, COUNT(*) n, ROUND(SUM(pnl),2) total_pnl "
                    f"FROM trades WHERE {_POP} GROUP BY 1 ORDER BY n DESC"),
                "2_by_exit_price_source": _query(conn,
                    f"SELECT {_XSRC} AS exit_price_source, COUNT(*) n, "
                    f"ROUND(SUM(pnl),2) total_pnl "
                    f"FROM trades WHERE {_POP} GROUP BY 1 ORDER BY n DESC"),
                "3_label_flip_risk": _query(conn,
                    f"SELECT {_XSRC} AS src, SUM(pnl>0) won, SUM(pnl<=0) lost, COUNT(*) n "
                    f"FROM trades WHERE {_POP} GROUP BY 1 ORDER BY n DESC"),
                "4_per_account": _query(conn,
                    f"SELECT account_id, SUM({_FAB}) fabricated, COUNT(*) n, "
                    f"ROUND(100.0*SUM({_FAB})/COUNT(*),1) pct_fab, "
                    f"ROUND(SUM(CASE WHEN {_FAB} THEN pnl ELSE 0 END),2) fab_pnl "
                    f"FROM trades WHERE {_POP} GROUP BY 1 ORDER BY n DESC"),
                "5_by_month": _query(conn,
                    "SELECT substr(COALESCE(closed_at,created_at,timestamp),1,7) ym, "
                    f"COUNT(*) n, SUM({_FAB}) fab, "
                    f"ROUND(100.0*SUM({_FAB})/COUNT(*),1) pct_fab "
                    f"FROM trades WHERE {_POP} "
                    "AND length(COALESCE(closed_at,created_at,timestamp))>=7 "
                    "GROUP BY 1 ORDER BY 1"),
                "6_inv2_population": _query(conn,
                    f"SELECT COUNT(*) closed_null_pnl, SUM({_SRC}='unmeasured') declared "
                    "FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
                    "AND pnl IS NULL"),
                "7_malformed_notes": _query(conn,
                    "SELECT COUNT(*) total, SUM(CASE WHEN notes IS NOT NULL "
                    "AND NOT json_valid(notes) THEN 1 ELSE 0 END) malformed FROM trades"),
                # --- Population-definition sensitivity -----------------------
                # The 2026-07-30 root-cause recorded "+$247,683.78 of
                # local_markprice PnL, the bulk of it ib_paper". Against the
                # canonical label population that does NOT reproduce (the
                # fabricated total is NEGATIVE and concentrated in bybit_1 /
                # bybit_portfolio, with ib_paper a rounding error). Before
                # accusing either number of being wrong, show how much the
                # answer MOVES with the population definition — a headline
                # figure whose sign flips on a filter is exactly the kind of
                # number this whole workstream exists to stop trusting.
                "8_population_sensitivity": _query(conn,
                    "SELECT 'canonical (closed, non-backtest, pnl NOT NULL)' AS population, "
                    f"COUNT(*) n, SUM({_FAB}) fab, "
                    f"ROUND(SUM(CASE WHEN {_FAB} THEN pnl ELSE 0 END),2) fab_pnl "
                    f"FROM trades WHERE {_POP} "
                    "UNION ALL SELECT 'incl. backtest rows', COUNT(*), "
                    f"SUM({_FAB}), ROUND(SUM(CASE WHEN {_FAB} THEN pnl ELSE 0 END),2) "
                    "FROM trades WHERE status='closed' AND pnl IS NOT NULL "
                    "UNION ALL SELECT 'any status, incl. backtest', COUNT(*), "
                    f"SUM({_FAB}), ROUND(SUM(CASE WHEN {_FAB} THEN pnl ELSE 0 END),2) "
                    "FROM trades WHERE pnl IS NOT NULL "
                    "UNION ALL SELECT 'ABS(pnl) over canonical', COUNT(*), "
                    f"SUM({_FAB}), ROUND(SUM(CASE WHEN {_FAB} THEN ABS(pnl) ELSE 0 END),2) "
                    f"FROM trades WHERE {_POP} "
                    "UNION ALL SELECT 'positive-only over canonical', COUNT(*), "
                    f"SUM({_FAB}), "
                    f"ROUND(SUM(CASE WHEN {_FAB} AND pnl>0 THEN pnl ELSE 0 END),2) "
                    f"FROM trades WHERE {_POP}"),
                "9_ib_paper_detail": _query(conn,
                    "SELECT status, COALESCE(is_backtest,0) bt, COUNT(*) n, "
                    f"SUM({_FAB}) fab, ROUND(SUM(pnl),2) total_pnl "
                    "FROM trades WHERE account_id='ib_paper' GROUP BY 1,2 ORDER BY n DESC"),
            },
        }
    finally:
        conn.close()


#: Exit reasons written by CLEANUP machinery (reconcilers, orphan adoption,
#: netting attribution, backfills, half-open pair cleanup) rather than by a
#: strategy/bracket/intent DECISION. Substring match, because the reasons are
#: composed (`reconciler_filled`, `exchange_flat_reconciled`,
#: `adopted_orphan_disappeared`, `backfill_closed_pnl_recovery`, …).
_JANITOR_MARKERS = (
    "reconcil", "exchange_flat", "orphan", "backfill", "netting",
    "half_open_cleanup", "superseded", "adopt",
)


def _close_path(exit_reason: Any) -> str:
    """`janitor` (cleanup found it) vs `decided` (a decision closed it)."""
    k = str(exit_reason or "").lower()
    if not k:
        return "unlabelled"
    return "janitor" if any(m in k for m in _JANITOR_MARKERS) else "decided"


def _close_path_coverage(conn: sqlite3.Connection) -> Dict[str, Any]:
    """M39(A): provenance coverage split by WHO closed the trade.

    The axis `provenance_exposure_audit` did not have. It exists because the
    intuitive reading is WRONG and only a measurement settles it: on 2026-08-24
    the DECIDED path measured **27.0%** against the janitor path's **52.0%**,
    with 41.8% of decided closes carrying no stamp at all — i.e. the path whose
    quality M20 exists to improve was the one that could not be measured.

    Bucketing is done in PYTHON through `provenance.classify_pnl`, deliberately
    NOT re-implemented in SQL. A second definition of "measured" is exactly how
    this repo's `_regime_score_semantics` incident happened — two probes derived
    the same answer independently and both got it wrong on the same day.

    Refuses loudly rather than returning an empty section if the provenance
    module cannot be imported: a silently absent split reads as "nothing to
    report", which is the `silent-empty-guard` failure this repo already owns.
    """
    try:
        sys.path.insert(0, os.getcwd())
        from src.runtime import provenance as _prov
    except Exception as exc:  # noqa: BLE001
        return {"error": f"provenance module unavailable: {exc!r} — split NOT computed"}

    try:
        cur = conn.execute(
            f"SELECT exit_reason, notes, pnl FROM trades WHERE {_POP}"
        )
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        return {"error": f"query failed: {exc!r}"}

    tally: Dict[str, Dict[str, int]] = {}
    for exit_reason, notes, pnl in rows:
        path = _close_path(exit_reason)
        bucket, _key = _prov.classify_pnl({"notes": notes, "pnl": pnl})
        tally.setdefault(path, {})[bucket] = tally.setdefault(path, {}).get(bucket, 0) + 1

    out_rows: List[List[Any]] = []
    for path in sorted(tally):
        c = tally[path]
        n = sum(c.values())
        measured = c.get(_prov.MEASURED, 0)
        out_rows.append([
            path, n, measured, c.get(_prov.ESTIMATED, 0),
            c.get(_prov.FABRICATED, 0), c.get(_prov.UNVERIFIED, 0),
            round(100.0 * measured / n, 1) if n else None,
        ])
    return {
        "columns": ["close_path", "n", "measured", "estimated", "fabricated",
                    "unverified", "pct_measured"],
        "rows": out_rows,
        "population": (
            "closed, non-backtest, pnl NOT NULL — the decision population; "
            "WHOLE history, not a window"
        ),
    }


def _print(report: Dict[str, Any]) -> None:
    print(f"DB: {report['db']}")
    print(f"    {report['db_bytes']:,} bytes, mtime {report['db_mtime_utc']}")
    for name, sec in report["sections"].items():
        print(f"\n===== {name} =====")
        if "error" in sec:
            print("  ERROR:", sec["error"])
            continue
        print("  " + " | ".join(sec["columns"]))
        for row in sec["rows"]:
            print("  " + " | ".join("" if v is None else str(v) for v in row))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="journal path (default: largest found)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    db = args.db
    if not db:
        cands = _candidates()
        if not cands:
            print("no trade_journal*.db found", file=sys.stderr)
            return 2
        # Print every candidate: a small repo-root decoy beside the real store
        # is a real configuration, and picking silently would hide it.
        if not args.json:
            print("===== candidates (largest first) =====")
            for p in cands:
                print(f"  {os.path.getsize(p):>13,}  {_mtime(p)}  {p}")
            print()
        db = cands[0]

    report = audit(db)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
