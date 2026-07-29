#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — economic-calendar spine PRODUCER (captures → PIT snapshots).

The off-VM runner that turns committed Bigdata.com country-tearsheet **captures**
into the point-in-time economic-event log the macro engine reads — the M1
"clean joined dataset" gate's calendar/consensus/surprise half
(``MB-20260723-M28-VALUATION-PRODUCER-UNWIRED`` for the value sibling).

    comms/macro/econ_calendar_captures/<COUNTRY>-<observed_at>.md   (raw tearsheets)
        → parse_tearsheet + to_event_rows  (scripts/macro/econ_calendar_data.py)
        → FULL REGEN comms/macro/econ_calendar_snapshots.jsonl   (append-only PIT log)
        → comms/macro/econ_calendar_upcoming.json                (latest forward calendar)
        → [--emit-config] config/economic_calendar.yaml          (news-layer events; GATED)

**Why captures + a pure producer (not a direct-fetch script like the FRED feed):**
the calendar data comes through the **Bigdata.com MCP** (``bigdata_country_tearsheet``),
which is bound to a Claude session — a GitHub-hosted runner can't call it, unlike
the keyless-HTTP feeds (FRED/CFTC/Bybit/Open-Meteo). So the fetch is a thin
Claude-session step (this session + a scheduled producer session) that saves the
raw tearsheet markdown to a capture file; the parse → PIT-map → land is this pure,
committed, CI-reproducible, fully-tested script. The capture files are the
committed audit trail (the honest point-in-time source) and the deterministic
input the ``econ-calendar-produce`` workflow re-lands from.

**Compute invariant (ROADMAP_MACRO §1c):** heavy work is off the live VM (a
Claude session / hosted runner). The live tick only ever *reads* the pre-computed
``econ_calendar_snapshots.jsonl`` — it never fetches, never parses on the money box.

**Point-in-time discipline (§6):** each capture is stamped with its fetch instant
``observed_at``; every row carries it, so a revision is a NEW line. Full regen is
idempotent — re-running over the same captures reproduces the same log byte-for-
byte; a new capture adds its rows. ``surprise`` keys on the pre-release consensus
(never revised) — see ``econ_calendar_data``.

**config/economic_calendar.yaml is a LIVE-PATH config** (read by the news-influence
layer, ``src/news/news_events.py``). Its population is **opt-in** (``--emit-config``)
and NOT done by the scheduled workflow — a cadence must not silently mutate a
live-path config. This session emits it once for operator review (Tier-3 gate).

No order path, no DB write, no live-VM touch.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from econ_calendar_data import (  # noqa: E402
    KIND_TO_NEWS_CLASS,
    parse_tearsheet,
    to_event_rows,
)
from econ_calendar_fmp import normalize_fmp  # noqa: E402
from econ_calendar_fxstreet import normalize_fxstreet  # noqa: E402

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_CAPTURES_DIR = REPO_ROOT / "comms" / "macro" / "econ_calendar_captures"
DEFAULT_SNAPSHOTS = REPO_ROOT / "comms" / "macro" / "econ_calendar_snapshots.jsonl"
DEFAULT_UPCOMING = REPO_ROOT / "comms" / "macro" / "econ_calendar_upcoming.json"
DEFAULT_CONFIG = REPO_ROOT / "config" / "economic_calendar.yaml"

# A capture filename encodes country + observed_at:  US-2026-07-29T063800Z.md
_CAPTURE_RE = re.compile(r"^(?P<country>[A-Za-z]{2,4})-(?P<ts>\d{8}T\d{6}Z)\.md$")
# An in-file header (authoritative when present):  <!-- country: US  observed_at: 2026-07-29T06:38:00Z -->
_HEADER_RE = re.compile(
    r"<!--\s*country:\s*(?P<country>[A-Za-z]{2,4})\s+observed_at:\s*(?P<obs>[0-9T:\-Z]+)\s*-->",
    re.IGNORECASE,
)


def _obs_from_compact(ts: str) -> str:
    """``20260729T063800Z`` → ``2026-07-29T06:38:00Z`` (ISO the PIT log keys on)."""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"


def _capture_meta(path: Path, text: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(country, observed_at)`` for a capture — the in-file header wins,
    else the filename convention. ``(None, None)`` when neither is present."""
    m = _HEADER_RE.search(text)
    if m:
        return m.group("country").upper(), m.group("obs")
    fm = _CAPTURE_RE.match(path.name)
    if fm:
        return fm.group("country").upper(), _obs_from_compact(fm.group("ts"))
    return None, None


def _rows_from_md(path: Path, text: str, skipped: list[dict]) -> list[dict]:
    """A Bigdata.com tearsheet ``.md`` capture → PIT rows."""
    country, observed_at = _capture_meta(path, text)
    if not country or not observed_at:
        skipped.append({"file": path.name, "reason": "no_country_or_observed_at"})
        return []
    return to_event_rows(parse_tearsheet(text, country=country), observed_at=observed_at)


def _rows_from_json_capture(path: Path, text: str, skipped: list[dict], normalize) -> list[dict]:
    """A JSON capture (``{observed_at, countries, rows:[...]}``) → PIT rows via the
    given ``normalize`` fn (FMP or FXStreet). One capture can span several
    countries — each normalized separately so every event carries its own
    country/entity."""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        skipped.append({"file": path.name, "reason": f"bad_json:{exc}"})
        return []
    observed_at = doc.get("observed_at")
    raw_rows = doc.get("rows") or []
    countries = [c.upper() for c in (doc.get("countries") or ["US"])]
    if not observed_at or not isinstance(raw_rows, list):
        skipped.append({"file": path.name, "reason": "no_observed_at_or_rows"})
        return []
    out: list[dict] = []
    for ctry in countries:
        parsed = normalize(raw_rows, countries={ctry}, country=ctry)
        out.extend(to_event_rows(parsed, observed_at=observed_at))
    return out


def rows_from_captures(captures_dir: Path) -> tuple[list[dict], list[dict]]:
    """Parse every capture → ``(pit_rows, skipped)`` — both source formats:
    ``*.md`` (Bigdata.com tearsheet) and ``*.fmp.json`` (FMP economic calendar).

    ``pit_rows`` are the ``macro_events``-schema PIT rows across all captures,
    sorted deterministically (``observed_at``, ``event_id``, ``status``).
    ``skipped`` records any capture we couldn't stamp/parse so the run summary is
    honest rather than silently dropping data."""
    pit_rows: list[dict] = []
    skipped: list[dict] = []
    for path in sorted(captures_dir.glob("*")):
        if path.suffix.lower() not in {".md", ".json"} or path.name.startswith("."):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            skipped.append({"file": path.name, "reason": f"read_error:{exc}"})
            continue
        if path.name.endswith(".fxstreet.json"):
            pit_rows.extend(_rows_from_json_capture(path, text, skipped, normalize_fxstreet))
        elif path.name.endswith(".fmp.json"):
            pit_rows.extend(_rows_from_json_capture(path, text, skipped, normalize_fmp))
        elif path.suffix.lower() == ".md":
            pit_rows.extend(_rows_from_md(path, text, skipped))
    pit_rows.sort(key=lambda r: (str(r.get("observed_at")), str(r.get("event_id")), str(r.get("status"))))
    return pit_rows, skipped


def _latest_upcoming(pit_rows: list[dict]) -> list[dict]:
    """The newest-observed forward (``scheduled``) events per (country, event_id) —
    the current forward calendar, for the observe-only ``upcoming.json``."""
    latest: dict[tuple, dict] = {}
    for r in pit_rows:
        if r.get("status") != "scheduled":
            continue
        key = (r.get("country"), r.get("event_id"))
        prev = latest.get(key)
        if prev is None or str(r.get("observed_at", "")) >= str(prev.get("observed_at", "")):
            latest[key] = r
    out = list(latest.values())
    out.sort(key=lambda r: (str(r.get("scheduled_at")), str(r.get("event_id"))))
    return out


def build_news_config_events(
    upcoming: list[dict], *, as_of: str, horizon_days: int = 45, min_impact: float = 0.5
) -> list[dict]:
    """Forward events for ``config/economic_calendar.yaml`` — the ``{class, time,
    impact}`` rows the news-influence layer reads (``src/news/news_events.py``).

    Only events that (a) map to a known news class (:data:`KIND_TO_NEWS_CLASS`),
    (b) are within ``horizon_days`` ahead of ``as_of``, and (c) clear
    ``min_impact`` are emitted — kept pruned to the upcoming window per the config's
    own guidance. Deterministic, sorted by time. This is the GATED live-path output
    (emitted only under ``--emit-config``)."""
    try:
        cutoff = _dt.date.fromisoformat(as_of[:10]) + _dt.timedelta(days=horizon_days)
        floor = _dt.date.fromisoformat(as_of[:10])
    except ValueError:
        return []
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in upcoming:
        cls = KIND_TO_NEWS_CLASS.get(str(r.get("kind")))
        if not cls:
            continue
        score = r.get("impact_score")
        if score is None or score < min_impact:
            continue
        ts = r.get("scheduled_at")
        day = str(r.get("scheduled_for", ""))[:10]
        try:
            d = _dt.date.fromisoformat(day)
        except ValueError:
            continue
        if d < floor or d > cutoff:
            continue
        key = (cls, ts)
        if key in seen:
            continue
        seen.add(key)
        out.append({"class": cls, "time": ts, "impact": round(float(score), 2)})
    out.sort(key=lambda e: str(e["time"]))
    return out


def _emit_config_yaml(events: list[dict], *, out_path: Path, base_path: Path) -> int:
    """Rewrite ``config/economic_calendar.yaml`` preserving everything above the
    ``events:`` key (the header docs + ``defaults`` + ``symbol_event_classes``)
    and replacing only the ``events:`` list. Returns the event count written."""
    base = base_path.read_text(encoding="utf-8") if base_path.exists() else ""
    # Keep every line up to (but excluding) the top-level `events:` declaration.
    head_lines: list[str] = []
    for ln in base.splitlines():
        if re.match(r"^events:\s*(\[\s*\]|)\s*$", ln) or re.match(r"^events:\s*$", ln):
            break
        head_lines.append(ln)
    head = "\n".join(head_lines).rstrip() + "\n\n"
    if events:
        lines = ["# Auto-populated from the Bigdata.com economic-calendar producer",
                 "# (scripts/macro/econ_calendar_produce.py --emit-config). Forward",
                 "# high/medium-impact events mapped to the news-layer classes above.",
                 "events:"]
        for e in events:
            lines.append(f'  - {{class: {e["class"]}, time: "{e["time"]}", impact: {e["impact"]}}}')
        body = "\n".join(lines) + "\n"
    else:
        body = "events: []\n"
    out_path.write_text(head + body, encoding="utf-8")
    return len(events)


def produce(
    *,
    captures_dir: Path = DEFAULT_CAPTURES_DIR,
    snapshots_path: Path = DEFAULT_SNAPSHOTS,
    upcoming_path: Optional[Path] = DEFAULT_UPCOMING,
    emit_config: Optional[Path] = None,
    as_of: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Full-regen the PIT snapshot log from all captures. Returns a summary dict.

    Never raises on a data problem — a bad capture is skipped + reported, not
    fatal (fail-permissive like the rest of the macro spine)."""
    captures_dir = Path(captures_dir)
    if not captures_dir.exists():
        return {"error": "no_captures_dir", "captures_dir": str(captures_dir), "rows": 0}

    pit_rows, skipped = rows_from_captures(captures_dir)
    upcoming = _latest_upcoming(pit_rows)
    resolved_n = sum(1 for r in pit_rows if r.get("status") == "resolved")
    with_surprise = sum(
        1 for r in pit_rows
        if r.get("status") == "resolved" and (r.get("realized_outcome") or {}).get("surprise") is not None
    )
    obs = sorted({r.get("observed_at") for r in pit_rows if r.get("observed_at")})
    countries = sorted({r.get("country") for r in pit_rows if r.get("country")})

    written = 0
    config_events = 0
    if not dry_run:
        snapshots_path = Path(snapshots_path)
        snapshots_path.parent.mkdir(parents=True, exist_ok=True)
        with snapshots_path.open("w", encoding="utf-8") as fh:
            for r in pit_rows:
                fh.write(json.dumps(r, default=str) + "\n")
                written += 1
        if upcoming_path is not None:
            up = Path(upcoming_path)
            up.parent.mkdir(parents=True, exist_ok=True)
            up.write_text(json.dumps(
                {"generated_from_observed_at": obs[-1] if obs else None,
                 "countries": countries, "count": len(upcoming), "events": upcoming},
                indent=2, default=str), encoding="utf-8")
        if emit_config is not None:
            ev = build_news_config_events(upcoming, as_of=as_of or (obs[-1] if obs else "1970-01-01"))
            config_events = _emit_config_yaml(ev, out_path=Path(emit_config), base_path=DEFAULT_CONFIG)

    return {
        "captures": len([p for p in captures_dir.glob("*")
                         if p.name.endswith((".fxstreet.json", ".fmp.json")) or p.suffix.lower() == ".md"]),
        "skipped": skipped,
        "rows": len(pit_rows),
        "written": written,
        "scheduled": len(pit_rows) - resolved_n,
        "resolved": resolved_n,
        "resolved_with_surprise": with_surprise,
        "upcoming": len(upcoming),
        "countries": countries,
        "observed_at_span": [obs[0], obs[-1]] if obs else [None, None],
        "config_events": config_events,
        "snapshots_path": str(snapshots_path),
        "dry_run": dry_run,
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="ROADMAP_MACRO M1 economic-calendar spine producer")
    ap.add_argument("--captures-dir", default=str(DEFAULT_CAPTURES_DIR))
    ap.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOTS))
    ap.add_argument("--upcoming", default=str(DEFAULT_UPCOMING))
    ap.add_argument("--emit-config", default=None,
                    help="ALSO rewrite this economic_calendar.yaml (GATED live-path; omit in scheduled runs)")
    ap.add_argument("--as-of", default=None, help="reference date for the config forward window (default: newest capture)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    summary = produce(
        captures_dir=Path(args.captures_dir),
        snapshots_path=Path(args.snapshots),
        upcoming_path=Path(args.upcoming),
        emit_config=Path(args.emit_config) if args.emit_config else None,
        as_of=args.as_of,
        dry_run=args.dry_run,
    )

    print("ROADMAP_MACRO M1 economic-calendar spine producer")
    print("=" * 50)
    if summary.get("error"):
        print(f"ERROR: {summary['error']}")
        return 1
    print(f"captures    : {summary['captures']}  countries={summary['countries']}")
    print(f"rows        : {summary['rows']}  (written={summary['written']}{'  [dry-run]' if summary['dry_run'] else ''})")
    print(f"  scheduled : {summary['scheduled']}")
    print(f"  resolved  : {summary['resolved']}  (with surprise={summary['resolved_with_surprise']})")
    print(f"upcoming    : {summary['upcoming']}")
    print(f"observed_at : {summary['observed_at_span'][0]} … {summary['observed_at_span'][1]}")
    if args.emit_config:
        print(f"config      : {summary['config_events']} event(s) → {args.emit_config}")
    if summary["skipped"]:
        print(f"skipped     : {summary['skipped']}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
