#!/usr/bin/env python3
"""Backfill the SURVEY-CONSENSUS side of the econ calendar from FXStreet history.

WHY
---
M3 (the M1 gate's satisfiability condition) compares the PIT expectation MODEL against real
SURVEY consensus on their overlap. The model side has 75 years (6,966 rows). The survey side
had **11 joinable rows** — not because anything was capped, but because the forward producer
had only ever pulled ONE window (2026-02 → 2026-08, captured in a single snapshot).

`econ_calendar_fxstreet.fetch_calendar(frm, to)` takes an **arbitrary date range** and is
**keyless**. So the survey window was never a data limit either; it was a scheduling artifact.
This is the same missing-backfill-sibling insight that fixed the model side, one layer up.

WHAT IT IS NOT
--------------
Not a model. Every row here carries a **real survey consensus** an actual poll produced, so it
is stamped ``expectation_source: "survey:fxstreet"`` — never ``model:``. That distinction is
load-bearing: `econ_expectation_validate.is_model_row` keys on it to decide which side of the
comparison a row belongs to. (It previously keyed on ``backfilled``, which would have
misclassified every row this script writes and silently dropped them from the survey side.)

PIT HONESTY — read this before trusting a surprise computed from these rows
--------------------------------------------------------------------------
A historical calendar fetch returns the CURRENT state of a past event, so the two halves have
DIFFERENT point-in-time standing and are stamped separately:

* ``consensus`` — genuinely **pre-release**. A survey is taken before the print and is not
  revised afterwards, so using it as the expectation is PIT-safe.
* ``actual`` — **may be a revision**, not the first print. FXStreet serves the current value.

Hence ``pit_basis: "fxstreet_current_state"`` and ``consensus_basis: "pre_release_survey"`` on
every row: the expectation side is trustworthy, the realized side may be revised. A consumer
comparing *surprises* inherits the revision risk on the actual — which is the same caveat the
model side already carries (``fred_current_vintage``), so the comparison is at least
apples-to-apples on that axis. ALFRED-style first prints remain the documented upgrade for both.

``observed_at`` is the FETCH time, not the pre-release moment — it would be a lie to backdate
it. ``backfilled: true`` says how the row was obtained.

Observe-only, stdlib-only, off-VM-guarded, Tier-1.

Usage::

    ICT_OFFVM_BUILD_HOST=1 python scripts/macro/econ_calendar_survey_backfill.py \\
        --start 2015-01-01 --end 2026-07-30 \\
        --out comms/macro/econ_calendar_snapshots_survey_backfill.jsonl
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

from econ_calendar_data import to_event_rows  # noqa: E402
from econ_calendar_fxstreet import fetch_calendar, normalize_fxstreet  # noqa: E402

SPEC_VERSION = "survey_backfill_fxstreet_v1"
EXPECTATION_SOURCE = "survey:fxstreet"
PIT_BASIS = "fxstreet_current_state"
CONSENSUS_BASIS = "pre_release_survey"
DEFAULT_OUT = os.path.join("comms", "macro", "econ_calendar_snapshots_survey_backfill.jsonl")
# The API is range-based but a multi-year single request is a bad citizen and risks a
# truncated/timed-out body. Chunk it; 90 days is comfortably inside a normal response.
DEFAULT_CHUNK_DAYS = 90


def date_chunks(start: str, end: str, chunk_days: int = DEFAULT_CHUNK_DAYS):
    """Inclusive [start, end] split into <= chunk_days windows, ascending."""
    a = _dt.date.fromisoformat(start)
    b = _dt.date.fromisoformat(end)
    if b < a:
        raise ValueError(f"end {end} precedes start {start}")
    step = max(1, int(chunk_days))
    out = []
    cur = a
    while cur <= b:
        nxt = min(cur + _dt.timedelta(days=step - 1), b)
        out.append((cur.isoformat(), nxt.isoformat()))
        cur = nxt + _dt.timedelta(days=1)
    return out


def stamp(row: dict, *, observed_at: str) -> dict:
    """Add survey-backfill provenance to one snapshot row."""
    row = dict(row)
    row["backfilled"] = True
    row["expectation_source"] = EXPECTATION_SOURCE
    row["pit_basis"] = PIT_BASIS
    row["consensus_basis"] = CONSENSUS_BASIS
    row["spec_version"] = SPEC_VERSION
    row["observed_at"] = observed_at
    return row


def _has_usable_consensus(row: dict) -> bool:
    """A row is only useful to M3 if it carries BOTH a consensus and an actual.

    An upcoming event has no actual; a released event with no published consensus has no
    survey expectation. Either way there is no survey-surprise to compare, so the row is
    dropped rather than emitted with a null that a consumer might read as zero.
    """
    def _num(v) -> bool:
        # bool is a subclass of int, so `True` would otherwise pass as a measurement.
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    ro = row.get("realized_outcome") or {}
    ex = row.get("expected") or {}
    cons = ro.get("consensus") if ro.get("consensus") is not None else ex.get("consensus")
    return _num(cons) and _num(ro.get("actual"))


def fetch_window(frm: str, to: str, *, country: str = "US", urlopen=None,
                 observed_at: str) -> tuple[list[dict], int]:
    """One chunk → (usable stamped rows, raw row count). Never raises on a fetch failure."""
    raw = fetch_calendar(frm, to, urlopen=urlopen)
    if not raw:
        return [], 0
    structured = normalize_fxstreet(raw, country=country)
    rows = to_event_rows(structured, observed_at=observed_at)
    usable = [stamp(r, observed_at=observed_at) for r in rows if _has_usable_consensus(r)]
    return usable, len(raw)


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per (kind, scheduled_for) — overlapping chunks would otherwise double-count."""
    seen: dict[tuple, dict] = {}
    for r in rows:
        seen[(r.get("kind"), r.get("scheduled_for"), r.get("event_name"))] = r
    return sorted(seen.values(), key=lambda r: (r.get("scheduled_for") or "", r.get("kind") or ""))


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", required=True, help="first calendar date (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="last calendar date (default: today UTC)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--country", default="US")
    ap.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    ap.add_argument("--kinds", default=None, help="CSV subset of kinds to keep")
    ap.add_argument("--observed-at", default=None,
                    help="fetch timestamp to stamp (default: now UTC)")
    ap.add_argument("--dry-run", action="store_true", help="print; write nothing")
    args = ap.parse_args(argv)

    end = args.end or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    observed_at = args.observed_at or _dt.datetime.now(
        _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chunks = date_chunks(args.start, end, args.chunk_days)
    want = {k.strip() for k in args.kinds.split(",")} if args.kinds else None

    all_rows: list[dict] = []
    empty_chunks = 0
    for frm, to in chunks:
        rows, raw_n = fetch_window(frm, to, country=args.country, observed_at=observed_at)
        if raw_n == 0:
            empty_chunks += 1
        if want:
            rows = [r for r in rows if r.get("kind") in want]
        all_rows.extend(rows)
        print(f"  {frm}..{to}  raw={raw_n:>5}  usable={len(rows):>4}", file=sys.stderr)

    rows = dedupe(all_rows)

    # A run that fetched nothing must FAIL, not write an empty file that reads as "no data
    # available". Same contract as the model-side backfill.
    if not rows:
        print(f"::error::survey backfill produced NO usable rows across {len(chunks)} chunks "
              f"({empty_chunks} fetched empty) — a consensus history of zero is not a result",
              file=sys.stderr)
        return 2
    if empty_chunks:
        print(f"::warning::{empty_chunks}/{len(chunks)} chunks returned no rows — the range "
              "may predate the source's coverage", file=sys.stderr)

    import collections
    per_kind = collections.Counter(r.get("kind") for r in rows)
    spans = [r.get("scheduled_for") for r in rows if r.get("scheduled_for")]
    print(f"survey-backfill rows: {len(rows)}  span {min(spans)} -> {max(spans)}")
    for k, n in per_kind.most_common(12):
        print(f"  {k:34s} {n}")

    if not args.dry_run:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
