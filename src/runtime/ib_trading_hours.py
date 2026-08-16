"""Is the IB venue actually open? — a pure parser for IBKR's session strings.

Nothing in this repo has ever known whether an IB venue could fill an order.
``src/runtime/market_hours.py`` models exactly three asset classes — its own
docstring says so: ``fx``, ``us_equity``, ``crypto`` — and **futures are not
among them**. So the Alpaca close path has had a session gate since
BL-20260716-ALPACA-MARKET-HOURS-EXIT while the IB close path fires a market
order at any hour and calls acceptance a placement
(BL-20260816-IB-CLOSE-HAS-NO-MARKET-HOURS-AWARENESS).

**This asks IBKR rather than modelling the calendar.** ``market_hours.py``
concedes in its own docstring that it models no holidays and approximates US
DST by month; a hand-rolled COMEX/CME calendar would inherit both flaws and add
roll dates, half-days, and per-product session breaks on top. IBKR publishes the
answer per contract in ``contractDetails.tradingHours`` / ``liquidHours``, and
the broker's own answer about its own venue cannot drift from it. This module is
the parser for that string and holds no IB import, so it is testable without a
gateway.

**tradingHours vs liquidHours — they answer different questions.** For a future,
``tradingHours`` is the near-24h electronic session and ``liquidHours`` is the
RTH/pit-equivalent window inside it. IBKR's ``outsideRth`` flag keys on the
LIQUID window: an order with ``outsideRth=False`` placed inside ``tradingHours``
but outside ``liquidHours`` is **held**, not filled — it sits ``PreSubmitted``
until the next RTH open. So a caller gating on ``tradingHours`` must also set
``outsideRth=True``, or its "open" verdict describes a venue state that its own
order does not act on. Gating on one and transmitting the other is the
semantic-substitution class the diagnostic-provenance rule names (§ "Diagnostic
provenance", sub-class A): the label names a quantity the code did not use.

**Three states, never collapsed** (§ "Collapsed states"):

- ``open``    — now falls inside a parsed session range.
- ``closed``  — now falls inside the span the string COVERS, and inside no range.
- ``unknown`` — *we could not look*. Empty/unparseable string, a timezone we
  cannot resolve, or an instant **outside the covered span**. That last case is
  the one a two-state design gets wrong: IBKR returns roughly a week, so a stale
  cached string or a clock outside its range yields "no range matched", which is
  indistinguishable from a real closure unless coverage is checked separately.
  Reporting that as ``closed`` would strand every close on a venue that is open.

``unknown`` is emphatically **not** ``closed``. Callers must fail-permissive on
it (place the order) — a gate bug must never strand a live capability, and for a
close specifically, refusing to flatten a live position because a *string* did
not parse would convert an observability defect into money at risk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Session verdicts. Exported so callers branch on a name, not a literal.
OPEN = "open"
CLOSED = "closed"
UNKNOWN = "unknown"
STATES = (OPEN, CLOSED, UNKNOWN)

# IBKR emits Java ``TimeZone`` ids, which are a mix of IANA names, IANA legacy
# links (``US/Eastern``), and Java-only abbreviations (``JST``, ``AEST``). The
# legacy links live in the FULL tz database but not in slim installs — measured
# in this repo's own sandbox, ``zoneinfo.ZoneInfo("US/Eastern")`` raises
# ``ZoneInfoNotFoundError`` while ``America/New_York`` resolves. Since COMEX and
# CME are exactly the venues that report ``US/Eastern`` / ``US/Central``, a
# resolver that only tried the raw id would return UNKNOWN for every futures
# contract we trade on a host with slim tzdata — a silent, total loss of the
# gate that would look like a working one.
_TZ_ALIASES = {
    "US/EASTERN": "America/New_York",
    "US/CENTRAL": "America/Chicago",
    "US/MOUNTAIN": "America/Denver",
    "US/PACIFIC": "America/Los_Angeles",
    "US/ARIZONA": "America/Phoenix",
    "EST5EDT": "America/New_York",
    "CST6CDT": "America/Chicago",
    "MST7MDT": "America/Denver",
    "PST8PDT": "America/Los_Angeles",
    "EST": "America/New_York",
    "CST": "America/Chicago",
    "MST": "America/Denver",
    "PST": "America/Los_Angeles",
    "JST": "Asia/Tokyo",
    "JAPAN": "Asia/Tokyo",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    "CTT": "Asia/Shanghai",
    "HKT": "Asia/Hong_Kong",
    "IST": "Asia/Kolkata",
    "MET": "Europe/Paris",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
    "BST": "Europe/London",
    "GB": "Europe/London",
    "GMT": "UTC",
    "UTC": "UTC",
    "Z": "UTC",
}


def resolve_timezone(tz_id: Optional[str]) -> Optional[Any]:
    """Resolve an IBKR ``timeZoneId`` to a tzinfo, or ``None`` if we cannot.

    ``None`` is the *we could not look* value and must never be silently
    replaced by UTC — the session strings are wall-clock local, so reading a
    ``US/Central`` string as UTC shifts every boundary by five or six hours and
    produces a confident, wrong verdict rather than an honest abstention.

    Tries, in order: ``zoneinfo`` on the raw id, ``zoneinfo`` on the alias,
    ``pytz`` on the raw id, ``pytz`` on the alias. ``pytz`` is a declared
    requirement (``requirements.txt``) and bundles its own copy of the tz
    database, so it resolves the legacy ids on a host whose system tzdata is
    slim; ``zoneinfo`` is tried first because it is stdlib and needs no import
    cost when the system database is complete.
    """
    raw = str(tz_id or "").strip()
    if not raw:
        return None
    candidates = [raw]
    alias = _TZ_ALIASES.get(raw.upper())
    if alias and alias != raw:
        candidates.append(alias)

    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore
    except ImportError:  # pragma: no cover - stdlib since 3.9
        ZoneInfo = None  # type: ignore
        ZoneInfoNotFoundError = Exception  # type: ignore
    if ZoneInfo is not None:
        for cand in candidates:
            try:
                return ZoneInfo(cand)
            except Exception:  # noqa: BLE001 - any resolution failure -> next
                continue

    try:
        import pytz  # type: ignore
    except ImportError:
        return None
    for cand in candidates:
        try:
            return pytz.timezone(cand)
        except Exception:  # noqa: BLE001
            continue
    return None


def _parse_stamp(day: str, hhmm: str) -> Optional[datetime]:
    """``("20260816", "1830")`` -> naive local datetime, or ``None``."""
    day, hhmm = day.strip(), hhmm.strip()
    if len(day) != 8 or not day.isdigit():
        return None
    if len(hhmm) != 4 or not hhmm.isdigit():
        return None
    try:
        return datetime(
            int(day[0:4]), int(day[4:6]), int(day[6:8]),
            int(hhmm[0:2]) % 24, int(hhmm[2:4]),
        ) + (timedelta(days=1) if hhmm[0:2] == "24" else timedelta(0))
    except ValueError:
        return None


def parse_hours(hours: Optional[str]) -> Tuple[List[Tuple[datetime, datetime]], List[str]]:
    """Parse an IBKR hours string into naive-local ranges + the dates it covers.

    Handles **both** formats IBKR has shipped, because which one arrives depends
    on the gateway's API version and we do not control it:

    - full   ``20260816:1800-20260817:1700;20260817:CLOSED``
    - legacy ``20260816:0700-1830,1830-2330;20260817:CLOSED``

    A legacy range whose end reads earlier than its start crosses midnight and
    is rolled to the next day (``1800-1700``). ``CLOSED`` contributes a covered
    date and no range — which is the whole point of returning the two lists
    separately: a day IBKR explicitly calls closed is *covered*, so "no range
    matched" on it is a real ``closed`` and not an absence of data.

    Malformed segments are skipped individually rather than failing the parse,
    so one unrecognised day cannot blind the gate for the rest of the week.
    """
    ranges: List[Tuple[datetime, datetime]] = []
    covered: List[str] = []
    text = str(hours or "").strip()
    if not text:
        return ranges, covered

    for segment in text.split(";"):
        segment = segment.strip()
        if not segment or ":" not in segment:
            continue
        day, _, body = segment.partition(":")
        day = day.strip()
        if len(day) != 8 or not day.isdigit():
            continue
        covered.append(day)
        body = body.strip()
        if not body or body.upper() == "CLOSED":
            continue
        for part in body.split(","):
            part = part.strip()
            if "-" not in part:
                continue
            lhs, _, rhs = part.partition("-")
            start = _parse_stamp(day, lhs)
            if start is None:
                continue
            if ":" in rhs:  # full form: the end carries its own date
                end_day, _, end_hhmm = rhs.partition(":")
                end = _parse_stamp(end_day, end_hhmm)
            else:           # legacy form: same day, rolling past midnight
                end = _parse_stamp(day, rhs)
                if end is not None and end <= start:
                    end += timedelta(days=1)
            if end is None or end <= start:
                continue
            ranges.append((start, end))
    return ranges, sorted(set(covered))


def session_state(
    hours: Optional[str],
    tz_id: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Grade an instant against an IBKR hours string.

    Returns ``(state, reason)`` where *state* is one of :data:`STATES`. The
    reason is never load-bearing for a decision — it exists so a log line says
    which of the several distinct paths to ``unknown`` was taken, since "we
    could not resolve the timezone" and "your clock is outside the week IBKR
    sent" call for completely different follow-up.
    """
    tz = resolve_timezone(tz_id)
    if tz is None:
        return UNKNOWN, f"unresolvable timezone {tz_id!r}"

    ranges, covered = parse_hours(hours)
    if not covered:
        return UNKNOWN, "no parseable session data"

    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        local = ts.astimezone(tz).replace(tzinfo=None)
    except Exception as exc:  # noqa: BLE001
        return UNKNOWN, f"timezone conversion failed: {type(exc).__name__}: {exc}"

    for start, end in ranges:
        if start <= local < end:
            return OPEN, f"inside {start:%Y%m%d %H:%M}-{end:%Y%m%d %H:%M} {tz_id}"

    # Not inside any range. That is only a CLOSURE if the string actually covers
    # this instant; otherwise we simply have no data for it. See the module
    # docstring — conflating the two is what would strand closes on an open
    # venue whenever the cached string aged out of range.
    span_start = _parse_stamp(covered[0], "0000")
    span_end = _parse_stamp(covered[-1], "0000")
    if span_start is None or span_end is None:
        return UNKNOWN, "covered span unparseable"
    span_end += timedelta(days=1)
    for _, end in ranges:  # a final range may run past its own day's midnight
        span_end = max(span_end, end)
    if span_start <= local < span_end:
        return CLOSED, (
            f"{local:%Y%m%d %H:%M} {tz_id} is inside the covered span "
            f"{covered[0]}..{covered[-1]} and inside no session"
        )
    return UNKNOWN, (
        f"{local:%Y%m%d %H:%M} {tz_id} is OUTSIDE the covered span "
        f"{covered[0]}..{covered[-1]} — no data for this instant"
    )


def session_state_from_details(
    details: Optional[Sequence[Any]],
    now: Optional[datetime] = None,
    prefer_liquid: bool = False,
) -> Tuple[str, str]:
    """Grade the first entry of an ib_insync ``reqContractDetails`` result.

    Defaults to ``tradingHours`` — the question a close asks is *"can this order
    fill at all"*, and for a future the answer is the electronic session, not
    the pit window. ``prefer_liquid=True`` grades the RTH window instead, which
    is the question an order with ``outsideRth=False`` actually gets answered by
    IBKR; the two must be chosen together with the flag transmitted on the order
    (see the module docstring).

    Attribute reads are defensive because this consumes a broker object we do
    not construct: anything unexpected degrades to ``unknown``, never to a
    verdict.
    """
    if not details:
        return UNKNOWN, "no contract details returned"
    first = details[0]
    tz_id = getattr(first, "timeZoneId", None)
    liquid = getattr(first, "liquidHours", None)
    trading = getattr(first, "tradingHours", None)
    hours = (liquid or trading) if prefer_liquid else (trading or liquid)
    which = "liquidHours" if prefer_liquid else "tradingHours"
    state, reason = session_state(hours, tz_id, now=now)
    return state, f"{which}: {reason}"
