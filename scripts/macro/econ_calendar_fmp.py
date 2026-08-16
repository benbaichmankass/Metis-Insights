#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — FMP economic-calendar source (the free, runner-autonomous feed).

The **autonomous** data source for the economic-calendar spine — Financial
Modeling Prep's economic calendar (free tier + an API key), reachable directly
from a GitHub-hosted runner over plain HTTPS. This removes the session-bound
Bigdata.com MCP dependency (a scheduled Claude session): the fetch is now a
normal scheduled workflow step, exactly like the FRED valuation producer.

    https://financialmodelingprep.com/api/v3/economic_calendar?from=&to=&apikey=
        → one JSON list of events over [from, to] (≤ 90 days), each carrying
          date · country · event · previous · estimate (consensus) · actual · impact
        → normalize_fmp → the SAME structured {upcoming, released} shape the
          Bigdata tearsheet parser produces (econ_calendar_data.parse_tearsheet)
        → to_event_rows → point-in-time macro_events-schema snapshots

**Why this fits with zero rework:** the parser/PIT-mapper/store/tests are
source-agnostic at the ``to_event_rows`` boundary. FMP is just a second producer
of the same normalized event dicts (``event_name``/``country``/``scheduled_at``/
``impact``/``actual``/``consensus``/``previous``/``surprise_pct``). The Bigdata
tearsheet stays a richer cross-check (curve/VIX/CFTC), just not the load-bearing
autonomous feed.

**Point-in-time (§6):** FMP's ``estimate`` is the pre-release consensus and isn't
revised; ``surprise = actual − consensus`` keys on it. A forward event (no actual
yet) → a ``scheduled`` capture carrying the PIT consensus; a printed event → a
``released`` capture. Each fetch is stamped with its ``observed_at`` (the run
instant), so a revision is a NEW line — never an overwrite.

**Off-VM guard + key:** the fetch refuses the network unless ``ICT_OFFVM_BUILD_HOST``
is set (or a ``urlopen`` is injected — tests) so the live VM never opens an FMP
socket, and needs the ``FMP_API_KEY`` env var (the free key, an Actions secret).
The pure ``normalize_fmp`` needs neither. No order path, no DB write.

Usage (off-VM, in the workflow):
    ICT_OFFVM_BUILD_HOST=1 FMP_API_KEY=… python scripts/macro/econ_calendar_fmp.py \
        --countries US --days-back 45 --days-forward 14 \
        --out-dir comms/macro/econ_calendar_captures
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from econ_calendar_data import classify_kind, impact_to_float  # noqa: E402

_FMP_URL = "https://financialmodelingprep.com/api/v3/economic_calendar?from={frm}&to={to}&apikey={key}"
_TRUTHY = {"1", "true", "yes", "on"}


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Pure normalization (FMP JSON → the structured shape parse_tearsheet produces).
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


def _fmp_dt(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """FMP ``date`` (``"2026-07-30 12:30:00"`` or ISO) → ``(day, iso_ts)``."""
    if not raw:
        return None, None
    s = str(raw).strip().replace("T", " ")
    day = s[:10]
    if len(day) != 10 or day[4] != "-":
        return None, None
    hhmm = "00:00"
    if len(s) >= 16 and s[10:11] == " ":
        hhmm = s[11:16]
    return day, f"{day}T{hhmm}:00Z"


# FMP impact strings ("Low"/"Medium"/"High") reuse the shared weight map.
def _impact_label(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def normalize_fmp(
    rows: Iterable[Mapping[str, Any]],
    *,
    countries: Optional[set[str]] = None,
    country: str = "US",
) -> dict:
    """Map an FMP economic-calendar JSON list into the structured
    ``{country, upcoming, released}`` shape :func:`econ_calendar_data.to_event_rows`
    consumes (identical to :func:`~.parse_tearsheet`'s output).

    A row with an ``actual`` value is a **released** event; one without is
    **upcoming** (the pre-release consensus captured PIT). ``countries`` filters by
    FMP's ``country`` field (default: keep ``country``). Robust to FMP field-name
    drift (``estimate``/``consensus``, ``impact``); a row missing date/event is
    skipped. Pure — no network."""
    keep = {c.upper() for c in (countries or {country})}
    upcoming: list[dict] = []
    released: list[dict] = []
    for r in rows or []:
        if not isinstance(r, Mapping):
            continue
        ctry = str(r.get("country", "") or "").upper() or country
        if keep and ctry not in keep:
            continue
        name = r.get("event")
        day, ts = _fmp_dt(r.get("date"))
        if not name or not day:
            continue
        kind = classify_kind(name)
        impact = _impact_label(r.get("impact"))
        actual = _num(r.get("actual"))
        consensus = _num(r.get("estimate", r.get("consensus")))
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
            "consensus_raw": None if consensus is None else str(r.get("estimate", r.get("consensus"))),
        }
        if actual is None:
            upcoming.append(common)
        else:
            surprise_pct = None
            cp = _num(r.get("changePercentage"))  # FMP's own change%, not a surprise% — left out
            _ = cp
            released.append({
                **common,
                "section": None,
                "actual": actual,
                "actual_raw": str(r.get("actual")),
                "previous": prev,
                "previous_original": None,
                "surprise_pct": surprise_pct,
            })
    return {"country": country, "upcoming": upcoming, "released": released}


# ---------------------------------------------------------------------------
# Thin network layer (off-VM-guarded, key + urlopen injectable).
# ---------------------------------------------------------------------------


def fetch_economic_calendar(
    frm: str, to: str, *, api_key: Optional[str] = None, urlopen=None, timeout: float = 25.0
) -> list[dict]:
    """Fetch the FMP economic calendar over ``[frm, to]`` (``YYYY-MM-DD``, ≤ 90d).

    Off-VM-guarded (needs ``ICT_OFFVM_BUILD_HOST`` unless ``urlopen`` is injected)
    and needs ``FMP_API_KEY`` (or an explicit ``api_key``). Best-effort: returns
    ``[]`` on any failure rather than raising (the spine degrades honestly)."""
    key = api_key or os.environ.get("FMP_API_KEY")
    if not key:
        raise RuntimeError("fetch_economic_calendar: FMP_API_KEY not set (add the free key as an Actions secret)")
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_economic_calendar: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request
        urlopen = urllib.request.urlopen
    url = _FMP_URL.format(frm=frm, to=to, key=key)
    try:
        with urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        print(f"FMP economic-calendar fetch failed ({exc})", file=sys.stderr)
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
    api_key: Optional[str] = None,
    urlopen=None,
    timeout: float = 25.0,
) -> dict:
    """Fetch the FMP window once and write ONE ``<COUNTRIES>-<observed_at>.fmp.json``
    capture (header: observed_at/country/source + the raw rows) into *out_dir* — the
    committed audit trail the producer regenerates the PIT log from. Returns a
    summary. The producer (``econ_calendar_produce``) then maps captures → snapshots."""
    now = observed_at or _utc_now_iso()
    today = _dt.date.fromisoformat(now[:10])
    frm = (today - _dt.timedelta(days=days_back)).isoformat()
    to = (today + _dt.timedelta(days=days_forward)).isoformat()
    rows = fetch_economic_calendar(frm, to, api_key=api_key, urlopen=urlopen, timeout=timeout)
    out_dir = Path(out_dir)
    label = "-".join(c.upper() for c in countries) or "US"
    base = {"observed_at": now, "window": [frm, to],
            "countries": [c.upper() for c in countries], "fetched_rows": len(rows)}
    # A ZERO-ROW WINDOW IS A FAILED FETCH, NOT A QUIET "no events" (2026-08-16).
    # A ~60-day US window with no economic events is not a real reading, so writing
    # the capture anyway produced a fresh, well-formed, entirely vacuous artifact that
    # the producer then globbed into the PIT ledger contributing nothing — the Class-B
    # vacuity shape (BL-20260730-PRODUCER-VACUITY-GUARD). Exactly one such capture was
    # ever written (US-20260729T073711Z.fmp.json, the FMP free-tier NO-BUILD probe
    # #7888); it was pruned and its KNOWN_VACUOUS grandfather entry removed, so this
    # refusal is what stops the same artifact being re-seeded. We decline to write and
    # SAY SO — "we looked and the feed returned nothing" is reported, never disguised
    # as a capture that exists.
    if not rows:
        return {**base, "path": None, "wrote": False,
                "reason": "fetch returned zero rows — refusing to write a vacuous "
                          "capture (see BL-20260730-PRODUCER-VACUITY-GUARD)"}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}-{_compact(now)}.fmp.json"
    path.write_text(json.dumps({
        "source": "fmp",
        "observed_at": now,
        "countries": [c.upper() for c in countries],
        "window": {"from": frm, "to": to},
        "rows": rows,
    }, indent=2, default=str), encoding="utf-8")
    return {**base, "path": str(path), "wrote": True, "reason": None}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="ROADMAP_MACRO M1 — FMP economic-calendar capture writer (off-VM)")
    ap.add_argument("--out-dir", default=str(Path("comms") / "macro" / "econ_calendar_captures"))
    ap.add_argument("--countries", default="US", help="comma-separated FMP country codes (default US)")
    ap.add_argument("--days-back", type=int, default=45)
    ap.add_argument("--days-forward", type=int, default=14)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    countries = [c.strip() for c in str(args.countries).split(",") if c.strip()]
    summary = write_capture(
        out_dir=Path(args.out_dir), countries=countries,
        days_back=args.days_back, days_forward=args.days_forward, timeout=args.timeout,
    )
    print("ROADMAP_MACRO M1 — FMP economic-calendar capture")
    print("=" * 48)
    print(f"observed_at : {summary['observed_at']}")
    print(f"window      : {summary['window'][0]} … {summary['window'][1]}")
    print(f"countries   : {summary['countries']}")
    if summary.get("wrote"):
        print(f"fetched     : {summary['fetched_rows']} rows → {summary['path']}")
    else:
        print(f"fetched     : {summary['fetched_rows']} rows — NO CAPTURE WRITTEN")
        print(f"reason      : {summary['reason']}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    # Exit non-zero on the refusal so a caller cannot read a green run as a capture.
    return 0 if summary.get("wrote") else 1


if __name__ == "__main__":
    raise SystemExit(main())
