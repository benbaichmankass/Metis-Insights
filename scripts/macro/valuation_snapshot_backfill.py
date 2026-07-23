#!/usr/bin/env python3
"""M28 P1 — off-VM **historical backfill** of point-in-time valuation snapshots.

The producer (``valuation_snapshot_produce.py``) records ONE snapshot row per run
going forward, so a value backtest can't run until weeks of history accrue. But
the whole value spine is pure + deterministic + takes its data INJECTED, and FRED
returns each series' FULL history — so we can reconstruct years of point-in-time
snapshots in one shot instead of waiting. This is the value-sleeve analogue of the
ML ``backfill-shadow-predictions`` replay: generate the past so models/gates can be
tested + promoted on real history immediately.

    config/macro_valuation.yaml
        + full FRED series history (dated, off-VM-guarded)
        → for each as-of date D (at --cadence-days): compute each metric's value
          read using ONLY the history slice `observed_at <= D` (no lookahead)
        → point-in-time valuation_snapshots rows stamped observed_at=as_of=D
        → comms/macro/valuation_snapshots_backfill.jsonl (full regen each run)

The M28 P4 value-thesis gate then runs on real history:
    python scripts/macro/thesis_backtest_run.py \
        --snapshots comms/macro/valuation_snapshots_backfill.jsonl \
        --candles-dir data/macro_candles --rebalance-every 30 --horizon-days 30

**Point-in-time honesty (the M28 correctness invariant + one caveat):**
  - The reconstruction is leakage-safe BY CONSTRUCTION: the value read at D uses
    the as-of-or-prior series value and the history slice ending at D — never a
    future observation.
  - CAVEAT: FRED ``fredgraph.csv`` returns the series' CURRENT (latest-revision)
    values, not the as-of-published vintage. For the wired metrics — real yield
    (DFII10), term slope (DGS10/DGS3MO), credit spread (BAMLH0A0HYM2) — those are
    **market rates that are never revised**, so this backfill IS genuinely
    point-in-time. For REVISED series (future EIA storage / earnings), true PIT
    needs FRED's ALFRED vintage API; until then a revised-series metric's backfill
    carries mild revised-data lookahead — flagged, not hidden. (The `source` field
    on every backfilled row is ``fred_backfill`` so a consumer can tell replayed
    rows from live ones.)

Off-VM-guarded (needs ICT_OFFVM_BUILD_HOST or an injected urlopen); no order path,
no DB write. Full regeneration each run — idempotent, safe to re-run.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Repo root + scripts/macro (sibling adapters) on path so ``python scripts/...`` works.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from macro_sources import fetch_source_series_dated  # noqa: E402
from src.units.strategies.macro_thesis.fred_adapter import (  # noqa: E402
    fetch_fred_series_history_dated,
    metric_histories,
)
from src.units.strategies.macro_thesis.valuation_feed import (  # noqa: E402
    build_valuation_reads,
    load_valuation_config,
    required_series,
)
from src.units.strategies.macro_thesis.valuation_store import write_snapshots  # noqa: E402

DEFAULT_BACKFILL_PATH = os.path.join("comms", "macro", "valuation_snapshots_backfill.jsonl")


def _norm_day(s: str) -> str:
    return str(s).strip()[:10]


def backfill_rows(
    config,
    series_dated,
    *,
    cadence_days: int = 7,
    start_date: Optional[str] = None,
    source: str = "fred_backfill",
) -> list[dict]:
    """Reconstruct point-in-time snapshot rows from dated FRED history.

    - ``series_dated`` — ``{series_id: [(date, val), ...]}`` ascending.
    - For each as-of date D (from the earliest observation, or ``start_date``, to
      the latest, every ``cadence_days``): the series value is the last obs with
      ``date <= D`` (as-of-or-prior) and the metric history is the slice of values
      with ``date <= D`` — so the read is exactly what was knowable at D.

    Never raises on a data gap: a metric whose inputs have no obs at/before D
    honest-nulls (``unknown``) rather than fabricating a value.
    """
    # Pre-split each series into parallel (dates, values) for bisect.
    split = {
        sid: ([_norm_day(d) for d, _ in rows], [v for _, v in rows])
        for sid, rows in (series_dated or {}).items()
    }
    all_dates = [d for dates, _ in split.values() for d in dates]
    if not all_dates:
        return []
    lo = min(all_dates)
    hi = max(all_dates)
    if start_date and _norm_day(start_date) > lo:
        lo = _norm_day(start_date)

    step = max(1, int(cadence_days))
    cur = _dt.date.fromisoformat(lo)
    end = _dt.date.fromisoformat(hi)
    rows_out: list[dict] = []
    while cur <= end:
        d_iso = cur.isoformat()
        series_values: dict[str, float] = {}
        series_hist: dict[str, list[float]] = {}
        for sid, (dates, vals) in split.items():
            pos = bisect.bisect_right(dates, d_iso)  # # of obs with date <= D
            if pos == 0:
                continue  # series has no observation at/before D yet
            series_values[sid] = vals[pos - 1]
            series_hist[sid] = vals[:pos]
        metric_hist = metric_histories(config, series_hist)
        rows_out.extend(
            build_valuation_reads(
                config, series_values, metric_hist,
                observed_at=d_iso, as_of=d_iso, source=source,
            )
        )
        cur = cur + _dt.timedelta(days=step)
    return rows_out


def run_backfill(
    *,
    config_path: Optional[str] = None,
    out_path=DEFAULT_BACKFILL_PATH,
    cadence_days: int = 7,
    start_date: Optional[str] = None,
    dry_run: bool = False,
    urlopen=None,
    timeout: float = 25.0,
    with_sources: bool = True,
    source_fetchers: Optional[dict] = None,
) -> dict:
    """Fetch full FRED history once and regenerate the point-in-time backfill log.
    Full-regen (truncates the file first) → idempotent. Returns a summary dict.

    ``with_sources`` also fetches the non-FRED sources (metal prices, earnings yield)
    so ERP + gold/silver resolve; ``source_fetchers`` injects them for tests."""
    config = load_valuation_config(config_path)
    if not config:
        return {"error": "empty_config", "rows": 0}
    req = required_series(config)
    series_dated = fetch_fred_series_history_dated(req["series"], urlopen=urlopen, timeout=timeout)
    # Merge the non-FRED source series (metal prices → gold/silver ratio; Shiller
    # earnings yield → ERP) so those metrics resolve too. Best-effort: if a source
    # can't be fetched it's simply absent and that metric honest-nulls (as before).
    if with_sources:
        try:
            src_series = fetch_source_series_dated(
                config, start=start_date or "2005-01-01", timeout=timeout, **(source_fetchers or {})
            )
            for name, pairs in src_series.items():
                series_dated[name] = pairs
        except Exception as exc:  # noqa: BLE001
            print(f"non-FRED sources unavailable ({exc}); ERP + gold/silver honest-null")
    rows = backfill_rows(config, series_dated, cadence_days=cadence_days, start_date=start_date)

    written = 0
    if not dry_run:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")  # truncate — full regen, not append
        written = write_snapshots(rows, path=p)

    obs = sorted({r.get("observed_at") for r in rows if r.get("observed_at")})
    labels: dict[str, int] = {}
    for r in rows:
        labels[str(r.get("label"))] = labels.get(str(r.get("label")), 0) + 1
    return {
        "rows": len(rows),
        "written": written,
        "as_of_dates": len(obs),
        "span": [obs[0], obs[-1]] if obs else [None, None],
        "series_fetched": {sid: len(v) for sid, v in series_dated.items()},
        "labels": labels,
        "cadence_days": cadence_days,
        "path": str(out_path),
        "dry_run": dry_run,
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="M28 P1 off-VM historical backfill of point-in-time valuation snapshots"
    )
    ap.add_argument("--config", default=None, help="config/macro_valuation.yaml override")
    ap.add_argument("--path", default=DEFAULT_BACKFILL_PATH,
                    help=f"backfill JSONL to (re)write (default: {DEFAULT_BACKFILL_PATH})")
    ap.add_argument("--cadence-days", type=int, default=7,
                    help="as-of date spacing in days across the FRED history span (default 7 = weekly)")
    ap.add_argument("--start-date", default=None,
                    help="earliest as-of date (YYYY-MM-DD); default = earliest FRED observation")
    ap.add_argument("--timeout", type=float, default=25.0, help="per-series FRED fetch timeout (s)")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    ap.add_argument("--json", default=None, help="also write the run summary JSON here")
    args = ap.parse_args(argv)

    summary = run_backfill(
        config_path=args.config, out_path=args.path, cadence_days=args.cadence_days,
        start_date=args.start_date, dry_run=args.dry_run, timeout=args.timeout,
    )

    print("M28 valuation-snapshot historical backfill")
    print("=" * 44)
    if summary.get("error"):
        print(f"ERROR: {summary['error']}")
        return 1
    print(f"as-of dates : {summary['as_of_dates']}  span={summary['span'][0]} … {summary['span'][1]}"
          f"  (cadence {summary['cadence_days']}d)")
    print(f"rows        : {summary['rows']}  (written={summary['written']}"
          f"{'  [dry-run]' if summary['dry_run'] else ''})")
    print(f"path        : {summary['path']}")
    print(f"labels      : {summary['labels']}")
    print(f"series      : {summary['series_fetched']}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
