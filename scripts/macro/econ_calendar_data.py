#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — pure parser + PIT-mapper for the economic-calendar spine.

Turns a Bigdata.com **country tearsheet** (its markdown body — the forward
*Economic Calendar - Upcoming Events* table + the *Macroeconomic Overview*
sector tables, sourced by Bigdata from FXStreet) into point-in-time
``macro_events``-schema rows (M28-P0 schema §2, the shape
:mod:`src.units.strategies.macro_thesis.event_calendar` /
:mod:`~.event_store` already use), so the existing macro-event engine
(``event_resolver`` DSL, ``TradeThesis`` ``on_outcome`` rules, ``thesis_replay``
as-of discipline) can consume the calendar with **no schema change**.

This is the **pure, offline-testable** half — it never fetches. The raw tearsheet
markdown is handed in as a string (this session + the scheduled producer call the
Bigdata MCP ``bigdata_country_tearsheet`` tool and pass the result text; tests
pass a committed fixture). Mirrors the split every other macro feed uses
(``fred_adapter`` parsing pure, network thin + off-VM; ``cot_data`` /
``crypto_signals_data`` pure readers).

**Point-in-time integrity — the #1 correctness rule (ROADMAP_MACRO §6):**
  - ``observed_at`` is the fetch instant (caller-supplied — no clock read here),
    stamped on every row, so a revision is a NEW line, never an overwrite.
  - ``surprise`` is computed from the release's **consensus**, and the consensus
    is the *pre-release forecast* — FXStreet never revises the consensus column
    (only the *Previous*/prior column is revised, shown as ``(Original X)``). So
    ``surprise = actual − consensus`` is genuinely point-in-time. We **never** use
    a revised consensus. The revised ``previous`` is preserved verbatim
    (incl. its pre-revision ``previous_original``) for reference but is NOT what
    the surprise keys on.

Honest-null throughout: a missing cell (``–`` / ``—`` / blank) parses to ``None``,
never a fabricated number; a non-numeric actual is preserved verbatim with a
``None`` surprise. Nothing here touches an order path or the network.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

# ---------------------------------------------------------------------------
# Cell / number parsing (robust to FXStreet's formatting quirks).
# ---------------------------------------------------------------------------

# Cells that mean "no value" — every dash variant FXStreet emits, plus blanks.
_NULL_CELLS = {"", "-", "–", "—", "−", "n/a", "na", "tbd", "..."}
# Strip these decorations before float-parsing a numeric cell.
_STRIP_CHARS = "%$ \t"


def parse_number(cell: Any) -> Optional[float]:
    """Parse one table cell to a float, or ``None`` (honest-null).

    Handles the FXStreet decorations: ``"3.75%"`` → ``3.75``; ``"$-226.80"`` →
    ``-226.80``; ``"7.59"`` → ``7.59``; a revised ``"129 (Original 172)"`` → the
    displayed (revised) ``129.0`` (the parenthetical is stripped — see
    :func:`previous_original`). A dash/blank/``n/a`` → ``None``. Never raises."""
    if cell is None:
        return None
    s = str(cell).strip()
    # Drop a trailing "(Original …)" revision annotation before parsing.
    paren = s.find("(")
    if paren != -1:
        s = s[:paren].strip()
    if s.lower() in _NULL_CELLS:
        return None
    s = s.strip(_STRIP_CHARS)
    if s.lower() in _NULL_CELLS or not s:
        return None
    # A lone dash after stripping is null, not a sign.
    if s in {"-", "–", "—", "−"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def previous_original(cell: Any) -> Optional[float]:
    """Extract the **pre-revision** figure from a ``"129 (Original 172)"`` cell.

    The tearsheet shows the revised *Previous* value with the original in
    parentheses when a revision occurred. Returns that original as a float, or
    ``None`` when the cell carries no ``(Original …)`` annotation."""
    if cell is None:
        return None
    m = re.search(r"\(\s*Original\s+([-−–—$%0-9.,]+)\s*\)", str(cell))
    if not m:
        return None
    return parse_number(m.group(1))


def _raw_cell(cell: Any) -> Optional[str]:
    """The verbatim cell text (trimmed) or ``None`` when it means no-value."""
    if cell is None:
        return None
    s = str(cell).strip()
    return None if s.lower() in _NULL_CELLS else s


# ---------------------------------------------------------------------------
# Date parsing — FXStreet's "YYYY-MM-DD HH:MM UTC".
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}):(\d{2}))?\s*(?:UTC)?", re.IGNORECASE
)


def parse_dt(cell: Any) -> tuple[Optional[str], Optional[str]]:
    """Parse a ``"2026-07-30 12:30 UTC"`` cell → ``(date, iso_ts)``.

    ``date`` is ``YYYY-MM-DD`` (the event's calendar day, the ``scheduled_for``
    key); ``iso_ts`` is the full ``YYYY-MM-DDThh:mm:00Z`` instant (or the day at
    ``00:00:00Z`` when no time is given). ``(None, None)`` when unparseable."""
    if cell is None:
        return None, None
    m = _DATE_RE.search(str(cell))
    if not m:
        return None, None
    day, hh, mm = m.group(1), m.group(2), m.group(3)
    if hh is not None and mm is not None:
        return day, f"{day}T{hh}:{mm}:00Z"
    return day, f"{day}T00:00:00Z"


# ---------------------------------------------------------------------------
# Impact + event-kind classification.
# ---------------------------------------------------------------------------

# FXStreet impact label → the [0,1] `event_risk` weight the news-influence layer
# folds in (config/economic_calendar.yaml). FOMC/rate decisions get the ceiling.
_IMPACT_FLOAT = {"HIGH": 0.9, "MEDIUM": 0.5, "MED": 0.5, "LOW": 0.2, "NONE": 0.0}


def impact_to_float(label: Any, *, kind: Optional[str] = None) -> Optional[float]:
    """Map an ``HIGH``/``MEDIUM``/``LOW``/``NONE`` impact label to a ``[0,1]``
    weight; ``fomc`` events get ``1.0`` (the widest-moving scheduled catalyst).
    ``None`` for an unknown label (honest-null, never a fabricated 0)."""
    if kind == "fomc":
        return 1.0
    if label is None:
        return None
    return _IMPACT_FLOAT.get(str(label).strip().upper())


_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Strip the trailing period/vintage qualifier so "(Jun)"/"(Q1)"/"(Prel)" don't
# fragment the kind (the period lives on `scheduled_for`, not the kind).
_PERIOD_RE = re.compile(
    r"\s*\((?:Prel|Final|Adv|Advance|Q[1-4]|H[12]|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[^)]*\)\s*",
    re.IGNORECASE,
)

# Curated canonical kinds for the highest-signal releases (stable slugs the
# thesis engine + the news config key on). Order matters — first match wins;
# more specific patterns precede their generic parents.
_KIND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"eia natural gas storage", re.I), "eia_natgas_storage"),
    (re.compile(r"eia crude oil", re.I), "eia_crude_stocks"),
    (re.compile(r"api weekly crude", re.I), "api_crude_stocks"),
    (re.compile(r"fed interest rate|fomc|monetary policy statement", re.I), "fomc"),
    (re.compile(r"core personal consumption.*price index.*mom", re.I), "core_pce_mom"),
    (re.compile(r"core personal consumption.*price index.*yoy", re.I), "core_pce_yoy"),
    (re.compile(r"personal consumption expenditures.*price index.*mom", re.I), "pce_mom"),
    (re.compile(r"personal consumption expenditures.*price index.*yoy", re.I), "pce_yoy"),
    (re.compile(r"consumer price index ex food.*mom", re.I), "core_cpi_mom"),
    (re.compile(r"consumer price index ex food.*yoy", re.I), "core_cpi_yoy"),
    (re.compile(r"consumer price index.*mom", re.I), "cpi_mom"),
    (re.compile(r"consumer price index.*yoy", re.I), "cpi_yoy"),
    (re.compile(r"producer price index.*mom", re.I), "ppi_mom"),
    (re.compile(r"producer price index.*yoy", re.I), "ppi_yoy"),
    (re.compile(r"nonfarm payrolls(?!.*benchmark)", re.I), "nfp"),
    (re.compile(r"unemployment rate", re.I), "unemployment_rate"),
    (re.compile(r"initial jobless claims(?! 4)", re.I), "initial_jobless_claims"),
    (re.compile(r"average hourly earnings.*yoy", re.I), "avg_hourly_earnings_yoy"),
    (re.compile(r"gross domestic product annualized", re.I), "gdp_annualized"),
    (re.compile(r"gross domestic product price index", re.I), "gdp_price_index"),
    (re.compile(r"ism manufacturing pmi", re.I), "ism_manufacturing"),
    (re.compile(r"ism services pmi", re.I), "ism_services"),
    (re.compile(r"s&p global manufacturing pmi", re.I), "spglobal_manufacturing"),
    (re.compile(r"retail sales control", re.I), "retail_sales_control"),
    (re.compile(r"retail sales \(mom\)", re.I), "retail_sales_mom"),
    (re.compile(r"michigan consumer sentiment", re.I), "michigan_sentiment"),
    (re.compile(r"consumer confidence", re.I), "consumer_confidence"),
    (re.compile(r"durable goods orders(?! ex)", re.I), "durable_goods_orders"),
]

# Which canonical kinds map to a news-influence event class
# (config/economic_calendar.yaml `symbol_event_classes` values). Only these are
# written into the news config; the full snapshot keeps every event regardless.
KIND_TO_NEWS_CLASS: dict[str, str] = {
    "fomc": "fomc",
    "cpi_mom": "cpi", "cpi_yoy": "cpi",
    "core_cpi_mom": "cpi", "core_cpi_yoy": "cpi",
    "nfp": "nfp",
    "pce_mom": "pce", "pce_yoy": "pce",
    "core_pce_mom": "pce", "core_pce_yoy": "pce",
    "gdp_annualized": "gdp", "gdp_price_index": "gdp",
    "eia_natgas_storage": "eia", "eia_crude_stocks": "eia",
}


def classify_kind(event_name: Any) -> str:
    """Canonical event kind for a release name.

    Returns a curated slug for the high-signal releases (:data:`_KIND_PATTERNS`),
    else a deterministic slug of the name with its period/vintage qualifier
    stripped — so ``"Consumer Price Index (YoY) (Jun)"`` and its next-month
    sibling share a stable ``cpi_yoy`` kind while the period rides on
    ``scheduled_for``."""
    name = str(event_name or "").strip()
    for pat, kind in _KIND_PATTERNS:
        if pat.search(name):
            return kind
    stripped = _PERIOD_RE.sub(" ", name)
    return _SLUG_RE.sub("_", stripped.strip().lower()).strip("_") or "na"


# ---------------------------------------------------------------------------
# Markdown table extraction.
# ---------------------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    """Split a markdown table row ``| a | b | c |`` into trimmed cells."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(cells: Iterable[str]) -> bool:
    """A ``|----|----|`` header-separator row (only dashes/colons/spaces)."""
    return all(set(c) <= set("-: ") and c for c in cells)


def iter_tables(md: str):
    """Yield ``(h2, h3, header_cells, [data_row_cells, ...])`` for every markdown
    table in *md*, tagged with the ``##``/``###`` section headings it sits under.

    A table is a run of ``|``-delimited lines: a header row, a separator row,
    then data rows. Robust to blank lines and prose between tables."""
    h2 = h3 = None
    lines = str(md or "").splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("###"):
            h2, h3 = stripped[3:].strip(), None
            i += 1
            continue
        if stripped.startswith("### "):
            h3 = stripped[4:].strip()
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < n and _is_separator(_split_row(lines[i + 1])):
            header = _split_row(line)
            rows: list[list[str]] = []
            j = i + 2
            while j < n and lines[j].strip().startswith("|"):
                cells = _split_row(lines[j])
                if not _is_separator(cells):
                    rows.append(cells)
                j += 1
            yield h2, h3, header, rows
            i = j
            continue
        i += 1


def _col_index(header: list[str], *names: str) -> Optional[int]:
    """Index of the first header cell matching (case-insensitive) any of *names*."""
    low = [h.strip().lower() for h in header]
    for name in names:
        if name.lower() in low:
            return low.index(name.lower())
    return None


# ---------------------------------------------------------------------------
# Tearsheet → structured events.
# ---------------------------------------------------------------------------

_UPCOMING_H2 = "economic calendar - upcoming events"
_OVERVIEW_H2 = "macroeconomic overview"
_SOURCE = "bigdata:fxstreet"
_SOURCE_URL = "https://www.fxstreet.com"


def parse_tearsheet(md: str, *, country: str = "US") -> dict:
    """Parse a country-tearsheet markdown body into structured calendar events.

    Returns ``{country, upcoming:[...], released:[...]}`` where:
      - ``upcoming`` — forward *Economic Calendar - Upcoming Events* rows, each
        ``{event_name, country, scheduled_for, scheduled_at, frequency, impact,
        consensus, consensus_raw}`` (consensus is the pre-release forecast).
      - ``released`` — *Macroeconomic Overview* rows (already-printed releases),
        each additionally carrying ``actual``/``previous``/``previous_original``/
        ``surprise_pct`` + the ``section`` (GDP & Growth, Inflation & Prices, …).

    Pure + robust: an unparseable row is skipped, never fatal; a missing cell is
    honest-null. No dedup here (the producer handles PIT dedup by observed_at)."""
    upcoming: list[dict] = []
    released: list[dict] = []
    for h2, h3, header, rows in iter_tables(md):
        h2l = (h2 or "").strip().lower()
        if h2l == _UPCOMING_H2:
            ev_i = _col_index(header, "Event")
            fr_i = _col_index(header, "Frequency")
            im_i = _col_index(header, "Impact")
            co_i = _col_index(header, "Consensus")
            dt_i = _col_index(header, "Date")
            if ev_i is None or dt_i is None:
                continue
            for cells in rows:
                if max(x for x in (ev_i, dt_i) if x is not None) >= len(cells):
                    continue
                name = _raw_cell(cells[ev_i])
                day, ts = parse_dt(cells[dt_i])
                if not name or not day:
                    continue
                kind = classify_kind(name)
                cons_raw = _raw_cell(cells[co_i]) if co_i is not None and co_i < len(cells) else None
                impact = _raw_cell(cells[im_i]) if im_i is not None and im_i < len(cells) else None
                upcoming.append({
                    "event_name": name,
                    "kind": kind,
                    "country": country,
                    "scheduled_for": day,
                    "scheduled_at": ts,
                    "frequency": _raw_cell(cells[fr_i]) if fr_i is not None and fr_i < len(cells) else None,
                    "impact": impact,
                    "impact_score": impact_to_float(impact, kind=kind),
                    "consensus": parse_number(cons_raw),
                    "consensus_raw": cons_raw,
                })
        elif h2l == _OVERVIEW_H2:
            ind_i = _col_index(header, "Indicator", "Event")
            dt_i = _col_index(header, "Date")
            ac_i = _col_index(header, "Actual")
            co_i = _col_index(header, "Consensus")
            pv_i = _col_index(header, "Previous")
            im_i = _col_index(header, "Impact")
            fr_i = _col_index(header, "Frequency")
            su_i = _col_index(header, "Surprise")
            if ind_i is None or dt_i is None or ac_i is None:
                continue
            for cells in rows:
                need = [x for x in (ind_i, dt_i, ac_i) if x is not None]
                if max(need) >= len(cells):
                    continue
                name = _raw_cell(cells[ind_i])
                day, ts = parse_dt(cells[dt_i])
                if not name or not day:
                    continue
                kind = classify_kind(name)
                pv_cell = cells[pv_i] if pv_i is not None and pv_i < len(cells) else None
                cons_raw = _raw_cell(cells[co_i]) if co_i is not None and co_i < len(cells) else None
                actual_raw = _raw_cell(cells[ac_i])
                impact = _raw_cell(cells[im_i]) if im_i is not None and im_i < len(cells) else None
                released.append({
                    "event_name": name,
                    "kind": kind,
                    "country": country,
                    "section": h3,
                    "scheduled_for": day,
                    "scheduled_at": ts,
                    "frequency": _raw_cell(cells[fr_i]) if fr_i is not None and fr_i < len(cells) else None,
                    "impact": impact,
                    "impact_score": impact_to_float(impact, kind=kind),
                    "actual": parse_number(actual_raw),
                    "actual_raw": actual_raw,
                    "consensus": parse_number(cons_raw),
                    "consensus_raw": cons_raw,
                    "previous": parse_number(pv_cell),
                    "previous_original": previous_original(pv_cell),
                    "surprise_pct": parse_number(cells[su_i]) if su_i is not None and su_i < len(cells) else None,
                })
    return {"country": country, "upcoming": upcoming, "released": released}


# ---------------------------------------------------------------------------
# Structured events → point-in-time macro_events-schema rows.
# ---------------------------------------------------------------------------


def _event_id(kind: str, day: str, entity: str) -> str:
    """Deterministic ``evt-<kind>-<day>-<entity>`` id (matches
    :func:`event_calendar.event_id_for`) so a scheduled row and its later
    resolved row — same (kind, day, entity) — share one id."""
    def s(x: Any) -> str:
        return _SLUG_RE.sub("-", str(x).strip().lower()).strip("-") or "na"
    return f"evt-{s(kind)}-{s(day)}-{s(entity)}"


def to_event_rows(parsed: Mapping[str, Any], *, observed_at: str) -> list[dict]:
    """Map a :func:`parse_tearsheet` result into ``macro_events``-schema PIT rows.

    - Each *upcoming* event → a ``scheduled`` row (its pre-release ``consensus``
      captured point-in-time, ``realized_outcome=None``).
    - Each *released* event → a ``resolved`` row carrying a ``realized_outcome``
      ``{metric, actual, prior, consensus, surprise, surprise_pct, change,
      direction}`` where ``surprise = actual − consensus`` (both PIT; consensus is
      never the revised value — ROADMAP_MACRO §6).

    Every row is stamped with the caller's ``observed_at`` (the fetch instant) and
    shares the ``event_id`` scheme, so ``event_store``/``thesis_replay`` read them
    unchanged and a revision is a new line, never an overwrite."""
    country = str(parsed.get("country", "US"))
    rows: list[dict] = []

    for ev in parsed.get("upcoming", []) or []:
        day = ev["scheduled_for"]
        kind = ev["kind"]
        rows.append({
            "event_id": _event_id(kind, day, country),
            "kind": kind,
            "event_name": ev["event_name"],
            "entity": country,
            "country": country,
            "scheduled_for": day,
            "scheduled_at": ev.get("scheduled_at"),
            "status": "scheduled",
            "impact": ev.get("impact"),
            "impact_score": ev.get("impact_score"),
            "frequency": ev.get("frequency"),
            "expected": {
                "metric": kind,
                "consensus": ev.get("consensus"),
                "consensus_raw": ev.get("consensus_raw"),
                "prior": None,
            },
            "realized_outcome": None,
            "resolved_at": None,
            "source": _SOURCE,
            "source_url": _SOURCE_URL,
            "observed_at": observed_at,
        })

    for ev in parsed.get("released", []) or []:
        day = ev["scheduled_for"]
        kind = ev["kind"]
        actual = ev.get("actual")
        consensus = ev.get("consensus")
        prior = ev.get("previous")
        surprise = (actual - consensus) if (actual is not None and consensus is not None) else None
        change = (actual - prior) if (actual is not None and prior is not None) else None
        rows.append({
            "event_id": _event_id(kind, day, country),
            "kind": kind,
            "event_name": ev["event_name"],
            "entity": country,
            "country": country,
            "section": ev.get("section"),
            "scheduled_for": day,
            "scheduled_at": ev.get("scheduled_at"),
            "status": "resolved",
            "impact": ev.get("impact"),
            "impact_score": ev.get("impact_score"),
            "frequency": ev.get("frequency"),
            "expected": {"metric": kind, "consensus": consensus, "prior": prior},
            "realized_outcome": {
                "metric": kind,
                "actual": actual if actual is not None else ev.get("actual_raw"),
                "prior": prior,
                "previous_original": ev.get("previous_original"),
                "consensus": consensus,
                "surprise": surprise,
                "surprise_pct": ev.get("surprise_pct"),
                "change": change,
                "direction": None,  # orientation is a thesis-side concern (event_resolver)
            },
            "resolved_at": ev.get("scheduled_at") or day,
            "source": _SOURCE,
            "source_url": _SOURCE_URL,
            "observed_at": observed_at,
        })

    return rows
