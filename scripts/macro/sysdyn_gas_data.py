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
import math
import os
import sys
from typing import Optional

# Repo root on path so ``python scripts/macro/...`` resolves ``src.*``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.strategies.macro_thesis.fred_adapter import (  # noqa: E402
    fetch_fred_series_history_dated,
)

# Weekly Henry Hub natural-gas spot price, $/MMBtu (FRED, keyless, ~1997→).
HENRY_HUB_WEEKLY_SERIES = "WHHNGSP"

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
