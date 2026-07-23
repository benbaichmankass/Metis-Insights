#!/usr/bin/env python3
"""M29 — off-VM historical backfill of point-in-time CFTC-COT positioning snapshots.

Reconstructs years of weekly large-spec positioning snapshots (valuation-snapshot
schema) from the CFTC Legacy Futures-Only COT report, so the M28 P4 value gate +
the horizon-IC scan can grade the positioning sleeve on real history immediately —
the positioning analogue of ``valuation_snapshot_backfill.py``. Because both scans
consume the same schema, they grade these rows **unchanged**:

    CFTC Legacy COT (keyless Socrata)  →  per-market weekly spec-net series
        →  rolling COT-index percentile per week (trailing lookback, leakage-safe)
        →  point-in-time snapshot rows (observed_at = report + release lag)
        →  comms/macro/cot_snapshots.jsonl (full regen each run)

    python scripts/macro/thesis_backtest_run.py \
        --snapshots comms/macro/cot_snapshots.jsonl \
        --candles-dir data/macro_candles --rebalance-every 7 --horizon-days 30
    python scripts/macro/horizon_ic_scan.py \
        --snapshots comms/macro/cot_snapshots.jsonl --candles-dir data/macro_candles

Off-VM-guarded (needs ICT_OFFVM_BUILD_HOST or an injected urlopen). Full
regeneration each run — idempotent. No order path, no DB write.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cot_data import (  # noqa: E402
    COT_MARKETS,
    COT_SOCRATA_BASE,
    DEFAULT_LOOKBACK_WEEKS,
    DEFAULT_RELEASE_LAG_DAYS,
    build_cot_snapshots,
    fetch_cot_market_history,
)

DEFAULT_BACKFILL_PATH = os.path.join("comms", "macro", "cot_snapshots.jsonl")


def backfill(
    *,
    markets=COT_MARKETS,
    market_rows=None,
    urlopen=None,
    base: str = COT_SOCRATA_BASE,
    lookback: int = DEFAULT_LOOKBACK_WEEKS,
    release_lag_days: int = DEFAULT_RELEASE_LAG_DAYS,
    min_history: int = 52,
    limit: int = 5000,
    timeout: float = 30.0,
) -> dict:
    """Build the full point-in-time snapshot set across ``markets``. ``market_rows``
    (``{key: [parsed rows]}``) injects fetched data for tests; absent, fetch off-VM.
    Returns ``{rows, by_market, markets_ok, markets_total}`` — never raises."""
    all_rows: list[dict] = []
    by_market: dict[str, int] = {}
    for m in markets:
        key = m["key"]
        rows = (market_rows or {}).get(key)
        if rows is None:
            rows = fetch_cot_market_history(m["name"], base=base, limit=limit, urlopen=urlopen, timeout=timeout)
        snaps = build_cot_snapshots(
            m, rows, lookback=lookback, release_lag_days=release_lag_days, min_history=min_history,
        )
        by_market[key] = len(snaps)
        all_rows.extend(snaps)
    all_rows.sort(key=lambda r: (str(r.get("observed_at")), str(r.get("symbol"))))
    return {
        "rows": all_rows,
        "by_market": by_market,
        "markets_ok": sum(1 for n in by_market.values() if n > 0),
        "markets_total": len(markets),
    }


def write_snapshots_fresh(rows, path) -> int:
    """Full-regen writer: truncate + write all rows (idempotent per run)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in rows or []:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows or [])


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M29 off-VM CFTC-COT positioning snapshot backfill")
    ap.add_argument("--path", default=DEFAULT_BACKFILL_PATH, help=f"snapshot JSONL out (default {DEFAULT_BACKFILL_PATH})")
    ap.add_argument("--socrata-base", default=COT_SOCRATA_BASE, help="CFTC Socrata resource URL override")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_WEEKS, help="COT-index trailing window (weeks)")
    ap.add_argument("--release-lag-days", type=int, default=DEFAULT_RELEASE_LAG_DAYS, help="report→release lag")
    ap.add_argument("--min-history", type=int, default=52, help="min trailing weeks before a row is emitted")
    ap.add_argument("--limit", type=int, default=5000, help="Socrata row cap per market")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    args = ap.parse_args(argv)

    result = backfill(
        base=args.socrata_base, lookback=args.lookback, release_lag_days=args.release_lag_days,
        min_history=args.min_history, limit=args.limit,
    )

    print("M29 CFTC-COT positioning snapshot backfill")
    print("=" * 44)
    for m in COT_MARKETS:
        n = result["by_market"].get(m["key"], 0)
        print(f"  {m['key']:>7} ({m['symbol']:>4}): {n} snapshot(s)" + ("" if n else "  (EMPTY — check market name / socrata base)"))
    print(f"{result['markets_ok']}/{result['markets_total']} markets with history; {len(result['rows'])} total rows")

    if not args.dry_run:
        n = write_snapshots_fresh(result["rows"], args.path)
        print(f"wrote {n} rows → {args.path}")
    return 0 if result["markets_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
