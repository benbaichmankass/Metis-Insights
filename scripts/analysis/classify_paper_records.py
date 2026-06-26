#!/usr/bin/env python3
"""Bucket paper (and real) trade records into gradeable / artifact /
reconstructable, and optionally reconstruct the would-be outcome of
broker-truncated trades.

This is the offline pre-filter the ``performance-review`` skill should run before
computing per-strategy aggregates so technical artifacts (intent reduce/flip legs,
netting-guard / hold-policy suppressions, orphan flaps, credential refusals) don't
pollute win-rate / expectancy. See
``docs/audits/order-packages-zero-qty-2026-06-26.md`` § Follow-up.

Sources (pick one):
  --db PATH         read trade_journal.db directly (default: canonical resolver)
  --json PATH       read a JSON array of trade rows (e.g. a diag-relay dump) — use
                    this from a sandbox that can't reach the DB.

Examples
--------
  # Classify the last 500 closed/rejected records straight from the journal:
  python -m scripts.analysis.classify_paper_records --limit 500

  # Classify a diag-relay trades dump and reconstruct bucket C from candles:
  python scripts/analysis/classify_paper_records.py --json trades.json --reconstruct

  # Paper only, markdown report to stdout:
  python scripts/analysis/classify_paper_records.py --paper-only --format md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

# Allow `python scripts/analysis/classify_paper_records.py` (repo root on path).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_from_db(db_path: Optional[str], limit: int, paper_only: bool) -> List[Dict[str, Any]]:
    import sqlite3
    if db_path is None:
        from src.utils.paths import trade_journal_db_path
        db_path = trade_journal_db_path()
    uri = "file:%s?mode=ro" % db_path
    where = "WHERE is_backtest IS NOT 1"
    if paper_only:
        where += " AND (account_class='paper' OR (account_class IS NULL AND is_demo=1))"
    sql = (
        "SELECT * FROM trades " + where +
        " ORDER BY id DESC LIMIT ?"
    )
    with sqlite3.connect(uri, uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]
    return rows


def _load_from_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        # accept a diag envelope {rows:[...]} or {trades:[...]}
        for k in ("rows", "trades", "data"):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    return data if isinstance(data, list) else []


def _render_md(result: Dict[str, Any], recon: Dict[str, Any]) -> str:
    s = result["summary"]
    lines = [
        "# Paper-record bucketing",
        "",
        f"- total: **{s['total']}**  ·  gradeable (A): **{s['by_bucket']['A']}** "
        f"({s['gradeable_pct']}%)  ·  artifact (B): **{s['by_bucket']['B']}**  ·  "
        f"reconstructable (C): **{s['by_bucket']['C']}**",
        "",
        "## By category",
    ]
    for cat, n in s["by_category"].items():
        lines.append(f"- `{cat}`: {n}")
    lines += ["", "## By strategy (A / B / C)"]
    for strat, d in sorted(result["summary"]["by_strategy"].items()):
        lines.append(f"- **{strat}**: A={d['A']} · B={d['B']} · C={d['C']}")
    if recon:
        lines += ["", "## Bucket-C reconstruction"]
        rc = recon.get("counts", {})
        lines.append(
            f"- reconstructed_win: {rc.get('reconstructed_win', 0)}  ·  "
            f"reconstructed_loss: {rc.get('reconstructed_loss', 0)}  ·  "
            f"open_at_window_end: {rc.get('open_at_window_end', 0)}  ·  "
            f"unresolved/no-candles: {rc.get('unresolved', 0)}  ·  "
            f"ambiguous(intrabar): {rc.get('ambiguous', 0)}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--db", default=None, help="trade_journal.db path (default: canonical resolver)")
    src.add_argument("--json", default=None, help="JSON array / diag envelope of trade rows")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--paper-only", action="store_true")
    ap.add_argument("--reconstruct", action="store_true",
                    help="reconstruct bucket-C outcomes from candles (needs pandas + connector)")
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--candle-limit", type=int, default=500)
    ap.add_argument("--optimistic", action="store_true",
                    help="resolve intrabar straddles to TP (default: pessimistic SL)")
    ap.add_argument("--format", choices=("json", "md"), default="md")
    ap.add_argument("--out", default=None, help="write report to this path (else stdout)")
    args = ap.parse_args(argv)

    from src.analysis.paper_record_classifier import classify_records

    if args.json:
        records = _load_from_json(args.json)
    else:
        records = _load_from_db(args.db, args.limit, args.paper_only)

    result = classify_records(records)
    classified = result["classified"]

    recon_summary: Dict[str, Any] = {}
    recon_by_id: Dict[Any, Dict[str, Any]] = {}
    if args.reconstruct:
        from src.analysis.trade_reconstruction import reconstruct_record
        counts = {"reconstructed_win": 0, "reconstructed_loss": 0,
                  "open_at_window_end": 0, "unresolved": 0, "ambiguous": 0}
        by_id = {r.get("id"): r for r in records}
        for c in classified:
            if c.bucket != "C":
                continue
            rec = by_id.get(c.trade_id)
            res = reconstruct_record(
                rec, timeframe=args.timeframe, limit=args.candle_limit,
                pessimistic=not args.optimistic,
            ) if rec else None
            if res is None:
                counts["unresolved"] += 1
                continue
            counts[res.label] = counts.get(res.label, 0) + 1
            if res.ambiguous:
                counts["ambiguous"] += 1
            recon_by_id[c.trade_id] = {
                "outcome": res.outcome, "label": res.label,
                "bars_to_resolution": res.bars_to_resolution,
                "ambiguous": res.ambiguous, "r_multiple": res.r_multiple,
            }
        recon_summary = {"counts": counts}

    if args.format == "json":
        payload = {
            "summary": result["summary"],
            "reconstruction": recon_summary,
            "records": [
                {
                    "trade_id": c.trade_id, "strategy": c.strategy, "symbol": c.symbol,
                    "account_id": c.account_id, "account_class": c.account_class,
                    "status": c.status, "exit_reason": c.exit_reason,
                    "bucket": c.bucket, "category": c.category,
                    "gradeable": c.gradeable, "reconstructable": c.reconstructable,
                    "reason": c.reason, "pnl": c.pnl,
                    "reconstruction": recon_by_id.get(c.trade_id),
                }
                for c in classified
            ],
        }
        text = json.dumps(payload, indent=2, default=str)
    else:
        text = _render_md(result, recon_summary)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
