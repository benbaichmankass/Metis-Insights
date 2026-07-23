#!/usr/bin/env python3
"""M28 P1 — off-VM FRED valuation-snapshot **producer**.

The missing scheduler between the (already-complete, live-validated) value
pipeline and the point-in-time snapshot log the sleeve + P4 gate read:

    config/macro_valuation.yaml
        + free, keyless FRED series (fred_adapter, off-VM-guarded)
        → run_valuation_feed  (fetch → per-metric history → cheap/rich reads)
        → append the rows to the committed point-in-time log
          (comms/macro/valuation_snapshots.jsonl — append-only)

The whole value spine (core → composition → runner → FRED adapter) was built and
live-validated in M28 P1, but nothing ever *ran* it on a cadence, so
``valuation_snapshots.jsonl`` never accrued and the P4 value-thesis gate printed
``n=0`` (``MB-20260723-M28-VALUATION-PRODUCER-UNWIRED``). This script is that
runner. It is dispatched **off the money box** by the ``macro-valuation-snapshot``
GitHub Actions workflow (a GitHub-hosted runner), which commits the appended log;
the live VM picks it up via ``ict-git-sync`` and the sleeve reads it through
``valuation_store.read_latest_snapshots()``.

**Why a script, not a package module:** the ``macro_thesis`` package is a pure
Signals+Strategy island locked against Execution in ``.importlinter`` (and it
must never open a network socket on the live VM). The FRED *fetch* — the only
network touch — lives here, outside the package, exactly like the P4 runner
(``thesis_backtest_run.py``) keeps its price-CSV IO outside the package.

**Point-in-time discipline (the M28 correctness invariant):** the log is
**append-only**. Each run appends a fresh line per ``(symbol, metric)`` stamped
with the run's ``observed_at`` — a revised macro value is a NEW line, never an
overwrite — so a backtest can reconstruct exactly what was known as-of any past
instant and the live "latest" read is just the newest ``observed_at`` per key.

**Off-VM guard:** the FRED fetch refuses to hit the network unless
``ICT_OFFVM_BUILD_HOST`` is set (the workflow sets it) or a ``urlopen`` is
injected (tests) — so importing/running this on the live trading VM never opens a
FRED socket. No order path, no DB write.

Usage (in the workflow, off-VM):
    ICT_OFFVM_BUILD_HOST=1 python scripts/macro/valuation_snapshot_produce.py

    # explicit output + a dry run (compute + print, append nothing):
    ICT_OFFVM_BUILD_HOST=1 python scripts/macro/valuation_snapshot_produce.py \
        --path comms/macro/valuation_snapshots.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Optional

# Repo root on path so ``python scripts/...`` works without install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.strategies.macro_thesis.fred_adapter import fred_fetch_and_history  # noqa: E402
from src.units.strategies.macro_thesis.valuation_feed import (  # noqa: E402
    load_valuation_config,
    run_valuation_feed,
)
from src.units.strategies.macro_thesis.valuation_store import (  # noqa: E402
    committed_snapshot_log_path,
    read_snapshot_records,
    write_snapshots,
)


def _utc_now_iso() -> str:
    """Current instant as an ISO-8601 UTC stamp (``...Z``) — the ``observed_at``
    the point-in-time log keys on (lexical == chronological, so the sleeve's
    newest-per-key read is correct)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def produce(
    *,
    config_path: Optional[str] = None,
    out_path=None,
    observed_at: Optional[str] = None,
    dry_run: bool = False,
    urlopen=None,
    timeout: float = 25.0,
) -> dict:
    """Run one production pass. Returns a summary dict — never raises on a data
    problem (the value spine is fail-permissive: a fetch/history miss honest-nulls
    that read rather than aborting the whole run).

    - ``out_path`` — where to append (default: the committed
      ``comms/macro/valuation_snapshots.jsonl``).
    - ``urlopen`` — injected by tests so no network is hit; ``None`` uses the real
      off-VM-guarded FRED fetch (needs ``ICT_OFFVM_BUILD_HOST``).
    """
    now = observed_at or _utc_now_iso()
    config = load_valuation_config(config_path)
    if not config:
        return {"error": "empty_config", "rows": 0, "observed_at": now}

    # One FRED pull → the (fetch_fn, history_fn) pair the runner consumes.
    fetch_fn, history_fn = fred_fetch_and_history(config, urlopen=urlopen, timeout=timeout)
    rows = run_valuation_feed(
        config, fetch_fn, observed_at=now, as_of=now, history_fn=history_fn
    )

    resolved = out_path if out_path is not None else committed_snapshot_log_path()
    written = 0
    if not dry_run:
        written = write_snapshots(rows, path=resolved)

    # Honest read-back: label distribution + how many are decision-grade (not
    # ``unknown``) so a run that produced only honest-null reads is legible in
    # the log rather than looking like a success.
    labels: dict[str, int] = {}
    for r in rows:
        labels[str(r.get("label"))] = labels.get(str(r.get("label")), 0) + 1
    known = sum(n for lbl, n in labels.items() if lbl not in ("unknown", "None"))

    return {
        "observed_at": now,
        "rows": len(rows),
        "written": written,
        "known_reads": known,
        "labels": labels,
        "path": str(resolved),
        "dry_run": dry_run,
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="M28 P1 off-VM FRED valuation-snapshot producer (append-only, point-in-time)"
    )
    ap.add_argument("--config", default=None, help="config/macro_valuation.yaml override")
    ap.add_argument(
        "--path", default=None,
        help="snapshot JSONL to append to (default: comms/macro/valuation_snapshots.jsonl)",
    )
    ap.add_argument("--timeout", type=float, default=25.0, help="per-series FRED fetch timeout (s)")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; append nothing")
    ap.add_argument("--json", default=None, help="also write the run summary JSON here")
    args = ap.parse_args(argv)

    summary = produce(
        config_path=args.config,
        out_path=args.path,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    print("M28 valuation-snapshot producer")
    print("=" * 40)
    print(f"observed_at : {summary.get('observed_at')}")
    print(f"path        : {summary.get('path')}")
    if summary.get("error"):
        print(f"ERROR       : {summary['error']}")
        return 1
    print(f"rows        : {summary.get('rows')}  (written={summary.get('written')}"
          f"{'  [dry-run]' if summary.get('dry_run') else ''})")
    print(f"known reads : {summary.get('known_reads')}  "
          "(non-unknown cheap/rich reads — the rest honest-null until their sources wire)")
    print(f"labels      : {summary.get('labels')}")

    if not args.dry_run:
        total = len(read_snapshot_records(path=args.path))
        print(f"log total   : {total} record(s) in the point-in-time log")

    if args.json:
        from pathlib import Path
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
