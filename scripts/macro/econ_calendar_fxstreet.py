#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — FXStreet economic-calendar source (the KEYLESS autonomous feed).

The autonomous data source for the economic-calendar spine — FXStreet's own
public calendar API (`calendar-api.fxstreet.com`), the exact upstream Bigdata.com
resells. **Keyless**, reachable directly from a GitHub-hosted runner, so the fetch
is a normal scheduled workflow step with no session dependency and no PAYG.

Chosen after an empirical probe of every free source (docs/research/
M1-econ-calendar-source-probe-2026-07-29.md): FXStreet returned 92 US events with
34 consensus + 43 actual + revised in one call — the richest free feed. FMP's free
tier 403s the whole `/api/v3/` path (legacy) and its `/stable/economics-calendar`
is 404/premium; ForexFactory's faireconomy JSON works but only a 1-week window with
no actuals at fetch time; Trading Economics `guest:guest` is 410 Gone; EODHD demo 403.

    https://calendar-api.fxstreet.com/en/api/v1/eventDates/{fromISO}/{toISO}
        → one JSON list of events, each: dateUtc · name · countryCode · actual ·
          consensus · previous · revised · volatility · unit · ratioDeviation
        → normalize_fxstreet → the SAME {country,upcoming,released} shape the
          Bigdata tearsheet parser produces (econ_calendar_data.parse_tearsheet)
        → to_event_rows → point-in-time macro_events-schema snapshots

Zero rework: the parser/PIT-mapper/store/tests are source-agnostic at the
``to_event_rows`` boundary. FXStreet is just another producer of the same
normalized event dicts.

**Point-in-time (§6):** FXStreet's ``consensus`` is the pre-release forecast and
isn't revised; ``surprise = actual − consensus`` keys on it. An event with no
``actual`` yet → a ``scheduled`` capture carrying the PIT consensus; a printed one
→ a ``released`` capture. Each fetch is stamped with its ``observed_at`` (the run
instant), so a revision is a NEW line — never an overwrite. FXStreet's ``revised``
(a revision of the prior value) is carried as ``previous_original`` for reference
but never keys the surprise.

**Off-VM guard:** the fetch refuses the network unless ``ICT_OFFVM_BUILD_HOST`` is
set (or a ``urlopen`` is injected — tests), so the live VM never opens an FXStreet
socket. No key needed. No order path, no DB write.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from econ_calendar_data import classify_kind, impact_to_float  # noqa: E402

_URL = "https://calendar-api.fxstreet.com/en/api/v1/eventDates/{frm}/{to}"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_TRUTHY = {"1", "true", "yes", "on"}


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Pure normalization (FXStreet JSON → the shape parse_tearsheet produces).
# ---------------------------------------------------------------------------


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().strip("%$ \t")
    if not s or s.lower() in {"-", "–", "—", "n/a", "na", "null", "none"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _dt_from_utc(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """``"2026-07-30T12:30:00Z"`` → ``(day, iso_ts)``. FXStreet's ``dateUtc`` is
    already ISO-8601 Z; keep it verbatim as the point-in-time stamp."""
    if not raw:
        return None, None
    s = str(raw).strip()
    day = s[:10]
    if len(day) != 10 or day[4] != "-":
        return None, None
    ts = s if s.endswith("Z") else f"{day}T00:00:00Z"
    return day, ts


def normalize_fxstreet(
    rows: Iterable[Mapping[str, Any]],
    *,
    countries: Optional[set[str]] = None,
    country: str = "US",
) -> dict:
    """Map an FXStreet ``eventDates`` JSON list into the structured
    ``{country, upcoming, released}`` shape :func:`econ_calendar_data.to_event_rows`
    consumes (identical to :func:`~.parse_tearsheet`'s output).

    A row with an ``actual`` value is a **released** event; one without is
    **upcoming** (the pre-release consensus captured PIT). ``countries`` filters by
    the ``countryCode`` field (default: keep ``country``). Robust to missing
    fields; a row without a date/name is skipped. Pure — no network."""
    keep = {c.upper() for c in (countries or {country})}
    upcoming: list[dict] = []
    released: list[dict] = []
    for r in rows or []:
        if not isinstance(r, Mapping):
            continue
        ctry = str(r.get("countryCode", "") or "").upper() or country
        if keep and ctry not in keep:
            continue
        name = r.get("name")
        day, ts = _dt_from_utc(r.get("dateUtc"))
        if not name or not day:
            continue
        kind = classify_kind(name)
        # FXStreet volatility HIGH/MEDIUM/LOW → the shared [0,1] impact weight.
        vol = r.get("volatility")
        impact = str(vol).strip().upper() if vol else None
        actual = _num(r.get("actual"))
        consensus = _num(r.get("consensus"))
        prev = _num(r.get("previous"))
        common = {
            "event_name": str(name),
            "kind": kind,
            "country": ctry,
            "scheduled_for": day,
            "scheduled_at": ts,
            "frequency": None,
            "impact": impact,
            "impact_score": impact_to_float(impact, kind=kind),
            "consensus": consensus,
            "consensus_raw": None if consensus is None else str(r.get("consensus")),
        }
        if actual is None:
            upcoming.append(common)
        else:
            released.append({
                **common,
                "section": None,
                "actual": actual,
                "actual_raw": str(r.get("actual")),
                "previous": prev,
                # FXStreet's `revised` = a revision of the prior value; keep it for
                # reference (never keys the surprise — that's consensus-only).
                "previous_original": _num(r.get("revised")),
                # `ratioDeviation` is FXStreet's normalized surprise, not a clean %;
                # surprise = actual − consensus is computed downstream in to_event_rows.
                "surprise_pct": None,
            })
    return {"country": country, "upcoming": upcoming, "released": released}


# ---------------------------------------------------------------------------
# Thin network layer (off-VM-guarded, KEYLESS, urlopen-injectable).
# ---------------------------------------------------------------------------


def fetch_calendar(
    frm: str, to: str, *, urlopen=None, timeout: float = 30.0
) -> list[dict]:
    """Fetch the FXStreet calendar over ``[frm, to]`` (``YYYY-MM-DD``). Keyless.

    Off-VM-guarded (needs ``ICT_OFFVM_BUILD_HOST`` unless ``urlopen`` is injected).
    Best-effort: returns ``[]`` on any failure rather than raising."""
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_calendar: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        urlopen = urllib.request.urlopen
    frm_iso = f"{frm}T00:00:00Z"
    to_iso = f"{to}T00:00:00Z"
    req = urllib.request.Request(
        _URL.format(frm=frm_iso, to=to_iso),
        headers={"User-Agent": _UA, "Accept": "application/json",
                 "Origin": "https://www.fxstreet.com", "Referer": "https://www.fxstreet.com/"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        print(f"FXStreet calendar fetch failed ({exc})", file=sys.stderr)
        return []


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact(iso: str) -> str:
    return iso.replace("-", "").replace(":", "")


def write_capture(
    *,
    out_dir: Path,
    countries: list[str],
    days_back: int = 45,
    days_forward: int = 14,
    observed_at: Optional[str] = None,
    urlopen=None,
    timeout: float = 30.0,
) -> dict:
    """Fetch the FXStreet window once and write ONE ``<COUNTRIES>-<observed_at>.fxstreet.json``
    capture (header: observed_at/countries/source + the raw rows) into *out_dir* — the
    committed audit trail the producer regenerates the PIT log from."""
    now = observed_at or _utc_now_iso()
    today = _dt.date.fromisoformat(now[:10])
    frm = (today - _dt.timedelta(days=days_back)).isoformat()
    to = (today + _dt.timedelta(days=days_forward)).isoformat()
    rows = fetch_calendar(frm, to, urlopen=urlopen, timeout=timeout)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = "-".join(c.upper() for c in countries) or "US"
    path = out_dir / f"{label}-{_compact(now)}.fxstreet.json"
    path.write_text(json.dumps({
        "source": "fxstreet", "observed_at": now,
        "countries": [c.upper() for c in countries],
        "window": {"from": frm, "to": to}, "rows": rows,
    }, indent=2, default=str), encoding="utf-8")
    return {"path": str(path), "observed_at": now, "window": [frm, to],
            "fetched_rows": len(rows), "countries": [c.upper() for c in countries]}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="ROADMAP_MACRO M1 — FXStreet economic-calendar capture writer (keyless, off-VM)")
    ap.add_argument("--out-dir", default=str(Path("comms") / "macro" / "econ_calendar_captures"))
    ap.add_argument("--countries", default="US", help="comma-separated ISO country codes (default US)")
    ap.add_argument("--days-back", type=int, default=45)
    ap.add_argument("--days-forward", type=int, default=14)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    countries = [c.strip() for c in str(args.countries).split(",") if c.strip()]
    summary = write_capture(
        out_dir=Path(args.out_dir), countries=countries,
        days_back=args.days_back, days_forward=args.days_forward, timeout=args.timeout,
    )
    print("ROADMAP_MACRO M1 — FXStreet economic-calendar capture")
    print("=" * 53)
    print(f"observed_at : {summary['observed_at']}")
    print(f"window      : {summary['window'][0]} … {summary['window'][1]}")
    print(f"countries   : {summary['countries']}")
    print(f"fetched     : {summary['fetched_rows']} rows → {summary['path']}")
    if summary["fetched_rows"] == 0:
        print("::warning::FXStreet returned 0 rows — check the endpoint/headers")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
