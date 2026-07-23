#!/usr/bin/env python3
"""M29 P1b — off-VM data adapter for the ``gas_storage_price_v1`` seed calibration.

The seed stock-flow model (`src.sysdyn.seed_gas`) is pure and takes its data
INJECTED — the engine never fetches. This adapter is that injected reader: it
supplies the **observed target series** and the **exogenous drivers** the
identifier needs, on the model's weekly clock.

  - **Observed target** — the weekly **Henry Hub natural-gas price** (`WHHNGSP`,
    $/MMBtu), pulled **keyless** from FRED's `fredgraph.csv` via the M28
    `fred_adapter` fetcher (the same off-VM-guarded, urlopen-injectable path the
    valuation feed uses). This is what the seed model's ``price`` observation is
    fit against (predictor: `seed_gas.price_series`).
  - **Exogenous drivers** — `heating_demand` + `injection_season`, derived
    **deterministically from each observation's calendar week-of-year** (a cosine
    season peaking in deep winter, its complement filling in summer). The demand
    *amplitude* is arbitrary — the identifier absorbs it into `wd_rate` — so only
    the calendar *shape* matters here.

**Why price, not observed EIA storage (the honest scope line):** EIA's weekly
working-gas-in-storage series is not available keyless (FRED carries only monthly;
EIA's weekly feed is signed-URL / API-key gated). So P1b calibrates against the
keyless real **price** with a *calendar-seasonal* demand proxy. Injecting the
**observed EIA storage** as a second calibration target + **weather HDD** as the
real (surprise-carrying) demand driver is the documented **P1c** enhancement —
it needs a stable EIA source (a free `EIA_API_KEY` Actions secret, a real
operator hand-off) and is where the cold-snap-*surprise* linkage actually gets
tested. This adapter is structured so P1c only adds series, not a rewrite.

Off-VM-guarded (the fetch refuses on the live VM unless ``ICT_OFFVM_BUILD_HOST``
is set) and fully injectable for tests. No order path, no DB write, no clock read
(week-of-year is computed from the observation date string, not ``today``).
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import math
import os
import sys
from typing import Optional, Sequence

# Repo root on path so ``python scripts/macro/...`` resolves ``src.*``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.strategies.macro_thesis.fred_adapter import (  # noqa: E402
    fetch_fred_series_history_dated,
)

# Weekly Henry Hub natural-gas spot price, $/MMBtu (FRED, keyless, ~1997→).
HENRY_HUB_WEEKLY_SERIES = "WHHNGSP"

_TRUTHY = {"1", "true", "yes", "on"}


def _offvm_enabled() -> bool:
    """The same off-VM guard the FRED adapter uses — a network fetch here refuses
    on the live trading VM unless ``ICT_OFFVM_BUILD_HOST`` is set (or a ``urlopen``
    is injected in tests). Keeps the money box from ever opening an EIA/weather
    socket."""
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


# Calendar-seasonal demand-shape constants (amplitude is absorbed by the fitted
# ``wd_rate``, so these set the SHAPE, not a load-bearing magnitude).
_DEMAND_BASE = 3.0
_DEMAND_AMP = 5.0
_PEAK_WEEK = 2  # ISO week ~2 = mid-January = deep-winter heating-demand peak


def _norm_day(s: str) -> str:
    return str(s).strip()[:10]


def iso_week(day: str) -> int:
    """ISO week-of-year (1..53) for a ``YYYY-MM-DD`` date. Pure — reads the given
    date, never the clock. Falls back to 1 on an unparseable date (never raises)."""
    try:
        return _dt.date.fromisoformat(_norm_day(day)).isocalendar()[1]
    except (ValueError, TypeError):
        return 1


def calendar_exog_for_week(
    week: int, *, demand_base: float = _DEMAND_BASE, demand_amp: float = _DEMAND_AMP,
    peak_week: int = _PEAK_WEEK,
) -> dict:
    """The exogenous driver mapping for one calendar week — a deterministic season
    peaking at ``peak_week`` (deep winter) with the injection window as its
    complement (fills in summer). Mirrors ``seed_gas.seasonal_exogenous`` but keyed
    to the real calendar week, so the driver aligns to each observation's date."""
    phase = 2.0 * math.pi * ((week - peak_week) / 52.0)
    heating = max(0.0, demand_base + demand_amp * math.cos(phase))
    injection_season = max(0.0, 0.5 - 0.5 * math.cos(phase))
    return {"heating_demand": heating, "injection_season": injection_season}


def calendar_exog(dates, **kw) -> list:
    """Per-observation exogenous driver series, one mapping per date in order."""
    return [calendar_exog_for_week(iso_week(d), **kw) for d in dates]


def fetch_weekly_ng_price_dated(
    *, series: str = HENRY_HUB_WEEKLY_SERIES, urlopen=None, timeout: float = 25.0,
) -> list:
    """Dated weekly Henry Hub price ``[(date, price), ...]`` ascending, keyless from
    FRED. Off-VM-guarded + urlopen-injectable (inherited from `fred_adapter`).
    Best-effort: an empty list on any fetch failure (never fatal)."""
    got = fetch_fred_series_history_dated([series], urlopen=urlopen, timeout=timeout)
    return list(got.get(series, []))


def build_calibration_series(
    dated_price, *, window_years: Optional[float] = None, start_date: Optional[str] = None,
    **exog_kw,
):
    """Turn a dated price series into the aligned ``(dates, observed, exog)`` the
    identifier consumes.

    - ``window_years`` keeps only the last N years of observations (relative to the
      LAST observation date, not ``today`` — so the slice is deterministic + PIT).
    - ``start_date`` (``YYYY-MM-DD``) is an alternative explicit lower bound.
    - ``observed`` is the price list; ``exog`` is the calendar-seasonal driver per
      date; ``dates`` are the aligned ISO date strings.
    """
    rows = sorted(((_norm_day(d), float(v)) for d, v in (dated_price or [])), key=lambda r: r[0])
    if not rows:
        return [], [], []

    lo = start_date and _norm_day(start_date)
    if window_years and window_years > 0:
        last = _dt.date.fromisoformat(rows[-1][0])
        cutoff = last - _dt.timedelta(days=int(round(window_years * 365.25)))
        cutoff_iso = cutoff.isoformat()
        lo = max(lo, cutoff_iso) if lo else cutoff_iso
    if lo:
        rows = [r for r in rows if r[0] >= lo]

    dates = [d for d, _ in rows]
    observed = [v for _, v in rows]
    exog = calendar_exog(dates, **exog_kw)
    return dates, observed, exog


# ===========================================================================
# M29 P1c — observed EIA storage (2nd calibration target) + real weather HDD
# (the surprise-carrying heating_demand driver, replacing the calendar proxy).
# ===========================================================================

# EIA weekly working gas in underground storage, Lower 48, Bcf (EIA v2 API,
# key-gated). The v2 ``seriesid`` compatibility route accepts this legacy id.
EIA_STORAGE_SERIES = "NG.NW2_EPG0_SWO_R48_BCF.W"
_EIA_V2_SERIESID_URL = "https://api.eia.gov/v2/seriesid/{series}?api_key={key}&out=json&length=5000"

# ---------------------------------------------------------------------------
# EIA storage — the 2nd calibration target (anchors the stock trajectory).
# ---------------------------------------------------------------------------


def parse_eia_v2_series(payload) -> list:
    """Parse an EIA **v2** ``seriesid`` JSON body → ``[(date, value), ...]``
    ascending. The v2 shape is ``{"response": {"data": [{"period": "YYYY-MM-DD",
    "value": <num|str>, ...}, ...]}}`` (values are strings since v2.1.6). Honest:
    a missing/unparseable row is skipped, never raises; a non-dict payload → ``[]``."""
    try:
        data = (payload or {}).get("response", {}).get("data", [])
    except AttributeError:
        return []
    out: list = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        d = row.get("period")
        v = row.get("value")
        if d is None or v is None:
            continue
        try:
            out.append((_norm_day(d), float(v)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r[0])
    return out


def fetch_eia_storage_dated(
    *, series: str = EIA_STORAGE_SERIES, api_key: Optional[str] = None,
    urlopen=None, timeout: float = 25.0,
) -> list:
    """Dated weekly EIA working-gas-in-storage ``[(date, bcf), ...]`` ascending.

    Off-VM-guarded + ``urlopen``-injectable (tests). The key comes from ``api_key``
    or the ``EIA_API_KEY`` env var (the Actions secret the workflow injects).
    Best-effort: no key / any fetch failure → ``[]`` (never fatal — the caller
    reports the honest ``insufficient_history`` envelope)."""
    key = api_key or os.environ.get("EIA_API_KEY") or ""
    if not key:
        return []
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_eia_storage_dated: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request
        urlopen = urllib.request.urlopen
    url = _EIA_V2_SERIESID_URL.format(series=series, key=key)
    try:
        with urlopen(url, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return []
    return parse_eia_v2_series(payload)


# ---------------------------------------------------------------------------
# Weather HDD — the real, surprise-carrying heating_demand driver.
#
# Keyless daily mean temperature per major US heating-demand city from Open-Meteo's
# historical archive (no key, well-documented, reliable JSON), turned into national
# heating degree days. This is what carries the *cold-snap surprise* the B1 loop's
# research edge lives in — the calendar-seasonal proxy (P1b) could only reach the
# smooth seasonal shape, never the surprise. Weights are a coarse gas-heating-demand
# proxy (not exact populations); the *shape/surprise* is what matters — the fitted
# ``wd_rate`` absorbs the amplitude, exactly as it did for the calendar proxy.
# ---------------------------------------------------------------------------

HDD_BASE_F = 65.0  # US standard heating-degree-day base temperature (°F).

# (name, latitude, longitude, weight) — a small national heating-demand basket.
HDD_CITIES = (
    ("chicago", 41.85, -87.65, 0.18),
    ("new_york", 40.71, -74.01, 0.22),
    ("boston", 42.36, -71.06, 0.08),
    ("minneapolis", 44.98, -93.27, 0.10),
    ("detroit", 42.33, -83.05, 0.09),
    ("philadelphia", 39.95, -75.17, 0.09),
    ("denver", 39.74, -104.99, 0.06),
    ("kansas_city", 39.10, -94.58, 0.06),
    ("atlanta", 33.75, -84.39, 0.12),
)

_OPEN_METEO_URL = (
    "https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
    "&start_date={start}&end_date={end}&daily=temperature_2m_mean"
    "&temperature_unit=fahrenheit&timezone=UTC"
)


def daily_hdd_from_temp(temp_f, *, base: float = HDD_BASE_F) -> float:
    """Heating degree days for one day = ``max(0, base - mean_temp_F)``. A
    non-finite temp contributes 0 (honest — a missing reading is no demand signal,
    never a fabricated one)."""
    try:
        t = float(temp_f)
    except (TypeError, ValueError):
        return 0.0
    if t != t:  # NaN
        return 0.0
    return max(0.0, base - t)


def parse_open_meteo_daily(payload) -> list:
    """Parse an Open-Meteo archive JSON body → ``[(date, temp_f), ...]`` ascending.

    Shape is ``{"daily": {"time": [...], "temperature_2m_mean": [...]}}``. A
    ``None`` temperature (Open-Meteo's gap marker) is kept as ``None`` so the
    caller can treat it as 0 HDD. Non-dict / missing keys → ``[]``."""
    try:
        daily = (payload or {}).get("daily", {})
        times = daily.get("time") or []
        temps = daily.get("temperature_2m_mean") or []
    except AttributeError:
        return []
    out: list = []
    for d, t in zip(times, temps):
        if d is None:
            continue
        out.append((_norm_day(d), t))
    out.sort(key=lambda r: r[0])
    return out


def fetch_city_daily_temps(
    lat: float, lon: float, start: str, end: str, *, urlopen=None, timeout: float = 25.0,
) -> list:
    """One city's daily mean temperature ``[(date, temp_f), ...]`` from Open-Meteo.
    Off-VM-guarded + injectable. Best-effort: any failure → ``[]``."""
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_city_daily_temps: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request
        urlopen = urllib.request.urlopen
    url = _OPEN_METEO_URL.format(lat=lat, lon=lon, start=_norm_day(start), end=_norm_day(end))
    try:
        with urlopen(url, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return []
    return parse_open_meteo_daily(payload)


def national_daily_hdd(
    start: str, end: str, *, cities=HDD_CITIES, base: float = HDD_BASE_F,
    urlopen=None, timeout: float = 25.0,
) -> list:
    """Weight-averaged national daily HDD ``[(date, hdd), ...]`` over ``cities``.

    Fetches each city's daily temps, converts to daily HDD, and forms the
    weight-average per date (weights renormalised over the cities that actually
    returned data that day, so a single failed city just drops out rather than
    zeroing the national read). Best-effort: all cities failing → ``[]``."""
    per_city: list = []
    total_w = 0.0
    for _name, lat, lon, w in cities:
        temps = fetch_city_daily_temps(lat, lon, start, end, urlopen=urlopen, timeout=timeout)
        if not temps:
            continue
        per_city.append((w, {d: daily_hdd_from_temp(t, base=base) for d, t in temps}))
        total_w += w
    if not per_city:
        return []
    # Union of dates across cities; per-date weighted mean over present cities.
    all_dates = sorted({d for _w, m in per_city for d in m})
    out: list = []
    for d in all_dates:
        num = 0.0
        wsum = 0.0
        for w, m in per_city:
            if d in m:
                num += w * m[d]
                wsum += w
        if wsum > 0:
            out.append((d, num / wsum))
    return out


def weekly_hdd_ending(daily_hdd, week_end_dates, *, days: int = 7) -> dict:
    """Aggregate a daily-HDD series into the **weekly MEAN daily HDD** ending at
    each date in ``week_end_dates`` (the storage/price weekly clock).

    For each week-end date ``D`` the window is the ``days`` calendar days ending on
    ``D`` (``(D-days, D]``); the value is the *mean* of the national daily HDD in
    that window (mean, not sum, so its magnitude tracks the calendar proxy's O(0–40)
    range — the fitted ``wd_rate`` absorbs the scale either way). A week with no
    covered days maps to ``None`` (dropped by the series builder). Pure."""
    by_day = {}
    for d, v in daily_hdd or []:
        try:
            by_day[_norm_day(d)] = float(v)
        except (TypeError, ValueError):
            continue
    out: dict = {}
    for we in week_end_dates or []:
        wed = _norm_day(we)
        try:
            end_d = _dt.date.fromisoformat(wed)
        except (ValueError, TypeError):
            out[wed] = None
            continue
        vals = []
        for k in range(days):
            day = (end_d - _dt.timedelta(days=k)).isoformat()
            if day in by_day:
                vals.append(by_day[day])
        out[wed] = (sum(vals) / len(vals)) if vals else None
    return out


# ---------------------------------------------------------------------------
# Dual-target aligned series (storage + price + real-HDD exog on one weekly clock).
# ---------------------------------------------------------------------------


def _as_of_prior(sorted_pairs: Sequence, target: str):
    """Value in ``sorted_pairs`` ([(date, val)] ascending) as-of-or-prior ``target``
    (the value ON the date else the last STRICTLY BEFORE it). ``None`` when the
    target precedes all history — never a future/fabricated value (leakage-safe)."""
    tgt = _norm_day(target)
    lo, hi, ans = 0, len(sorted_pairs) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if sorted_pairs[mid][0] <= tgt:
            ans = sorted_pairs[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def build_dual_calibration_series(
    dated_storage, dated_price, daily_hdd, *,
    window_years: Optional[float] = None, start_date: Optional[str] = None,
    hdd_window_days: int = 7,
):
    """Turn observed storage + price + daily HDD into the aligned inputs the
    dual-target identifier consumes, on the **storage weekly clock** (the anchor
    target's native cadence).

    Returns ``(dates, observed_storage, observed_price, exog, meta)`` where each
    ``exog[i]`` is ``{"heating_demand": <weekly mean daily HDD>, "injection_season":
    <calendar complement>}`` — ``heating_demand`` is now the REAL weather driver
    (P1c), ``injection_season`` stays the calendar operational window (there is no
    clean observed injection-intent series; only the demand side gets the weather
    upgrade, per the P1c scope). ``meta`` carries the derived ``initial_storage``
    (= first observed storage, the anchor) and ``storage_normal`` (= mean observed
    storage, the price-gap reference level).

    A storage date lacking an as-of price OR an HDD read is dropped (never a
    fabricated input). Point-in-time clean: price/HDD are as-of-or-prior lookups."""
    storage_rows = sorted(
        ((_norm_day(d), float(v)) for d, v in (dated_storage or [])), key=lambda r: r[0]
    )
    if not storage_rows:
        return [], [], [], [], {}

    lo = start_date and _norm_day(start_date)
    if window_years and window_years > 0:
        last = _dt.date.fromisoformat(storage_rows[-1][0])
        cutoff = (last - _dt.timedelta(days=int(round(window_years * 365.25)))).isoformat()
        lo = max(lo, cutoff) if lo else cutoff
    if lo:
        storage_rows = [r for r in storage_rows if r[0] >= lo]
    if not storage_rows:
        return [], [], [], [], {}

    price_rows = sorted(
        ((_norm_day(d), float(v)) for d, v in (dated_price or [])), key=lambda r: r[0]
    )
    week_ends = [d for d, _ in storage_rows]
    hdd_by_week = weekly_hdd_ending(daily_hdd, week_ends, days=hdd_window_days)

    dates: list = []
    obs_storage: list = []
    obs_price: list = []
    exog: list = []
    for d, storage_v in storage_rows:
        price_v = _as_of_prior(price_rows, d)
        hdd_v = hdd_by_week.get(d)
        if price_v is None or hdd_v is None:
            continue
        inj = calendar_exog_for_week(iso_week(d))["injection_season"]
        dates.append(d)
        obs_storage.append(storage_v)
        obs_price.append(float(price_v))
        exog.append({"heating_demand": float(hdd_v), "injection_season": inj})

    meta = {}
    if obs_storage:
        meta = {
            "initial_storage": obs_storage[0],
            "storage_normal": sum(obs_storage) / len(obs_storage),
            "span": [dates[0], dates[-1]],
        }
    return dates, obs_storage, obs_price, exog, meta
