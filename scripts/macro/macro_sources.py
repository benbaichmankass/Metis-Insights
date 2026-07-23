#!/usr/bin/env python3
"""M28 P1 — off-VM adapter for the NON-FRED valuation ``source`` inputs.

``config/macro_valuation.yaml`` declares a few value inputs as ``source:`` (not
``series:``) because they aren't free FRED series — equity earnings yield + metal
prices. Until they're wired, the metrics that need them (equity risk premium,
gold/silver ratio) honest-null. This adapter supplies them as **dated** series
keyed by the source name, so both the historical backfill and the live producer
can resolve those metrics too:

  - ``price_<sym>`` (e.g. ``price_gld`` / ``price_slv``) → the ETF's daily closes
    (yfinance→Stooq, via the candle fetcher). The gold/silver ratio read is a
    percentile/z over its OWN history, so the ETF-price proxy (vs pure ounce
    price) is fine — the z-score is scale-invariant.
  - ``sp500_earnings_yield`` → S&P 500 trailing earnings yield = Earnings / SP500
    from Shiller's monthly dataset (keyless datahub mirror), **lagged by
    ``EARNINGS_PUBLICATION_LAG_MONTHS`` (3)** for point-in-time honesty (earnings
    for month M aren't reported until ~a quarter later).

**PIT caveat (same class as the FRED revised-series caveat):** Shiller's earnings
are the current (revised) values; the 3-month lag approximates publication delay
but not later revisions. Rates/prices are unrevised; earnings carry mild residual
revised-data lookahead — flagged, not hidden.

Off-VM-guarded via the candle fetcher; injectable for tests. No order path.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from typing import Optional

# Sibling-script import (candle fetch helpers) — add scripts/macro to the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fetch_macro_candles import _resolve_fetchers, symbol_close_pairs  # noqa: E402
from src.units.strategies.macro_thesis.valuation_feed import required_series  # noqa: E402

_TRUTHY = {"1", "true", "yes", "on"}
# Keyless Shiller/S&P monthly dataset (Date, SP500, …, Earnings) back to 1871.
_SHILLER_CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv"
EARNINGS_PUBLICATION_LAG_MONTHS = 3


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def _add_months(iso_day: str, n: int) -> str:
    """``YYYY-MM-DD`` + n months (clamped day 1..28-safe: keeps the day)."""
    y, m, d = int(iso_day[:4]), int(iso_day[5:7]), int(iso_day[8:10])
    m0 = (m - 1) + n
    y += m0 // 12
    m = m0 % 12 + 1
    d = min(d, 28)
    return f"{y:04d}-{m:02d}-{d:02d}"


def parse_shiller_earnings_yield(
    text: str, *, lag_months: int = EARNINGS_PUBLICATION_LAG_MONTHS
) -> list[tuple[str, float]]:
    """Parse the datahub Shiller CSV → dated S&P trailing earnings yield
    ``[(date, E/P), ...]`` ascending, with each observation's date shifted forward
    by ``lag_months`` (publication delay). Rows with a non-positive price/earnings
    (not-yet-reported recent months) are skipped — honest-null, never a fake 0."""
    out: list[tuple[str, float]] = []
    lines = [ln for ln in (text or "").strip().splitlines() if ln]
    if len(lines) < 2:
        return out
    header = [h.strip().lower() for h in lines[0].split(",")]
    try:
        di, pi, ei = header.index("date"), header.index("sp500"), header.index("earnings")
    except ValueError:
        return out
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= max(di, pi, ei):
            continue
        day = parts[di].strip()[:10]
        if len(day) != 10:
            continue
        try:
            price, earn = float(parts[pi]), float(parts[ei])
        except ValueError:
            continue
        if price <= 0 or earn <= 0:
            continue
        out.append((_add_months(day, lag_months), earn / price))
    out.sort()
    return out


def _price_source_symbol(source: str) -> Optional[str]:
    """``price_gld`` → ``GLD``; anything not a ``price_*`` source → None."""
    if source.startswith("price_"):
        return source[len("price_"):].upper()
    return None


def fetch_source_series_dated(
    config,
    *,
    candle_download=None,
    candle_stooq=None,
    shiller_urlopen=None,
    start: str = "2005-01-01",
    timeout: float = 25.0,
) -> dict:
    """Resolve every non-FRED ``source`` the config needs into a dated series
    ``{source_name: [(date, val), ...]}``. Injectable for tests; real fetches are
    off-VM (the candle fetcher enforces the guard). A source that can't be resolved
    is simply omitted (its metric then honest-nulls, unchanged)."""
    sources = required_series(config).get("sources", [])
    # Resolve the real candle fetchers off-VM when none are injected; otherwise
    # (not off-VM, nothing injected) they stay None → symbol_close_pairs returns []
    # → the source is omitted → its metric honest-nulls, unchanged. No raise.
    cd, cs = candle_download, candle_stooq
    if cd is None and cs is None and _offvm_enabled():
        cd, cs = _resolve_fetchers(None, None, start)

    out: dict[str, list[tuple[str, float]]] = {}
    for src in sources:
        sym = _price_source_symbol(src)
        if sym is not None:
            pairs = symbol_close_pairs(sym, download=cd, stooq_urlopen=cs, timeout=timeout)
            if pairs:
                out[src] = pairs
        elif src == "sp500_earnings_yield":
            uo = shiller_urlopen
            if uo is None and _offvm_enabled():
                uo = urllib.request.urlopen
            if uo is not None:
                try:
                    with uo(_SHILLER_CSV_URL, timeout=timeout) as resp:
                        ey = parse_shiller_earnings_yield(resp.read().decode())
                    if ey:
                        out[src] = ey
                except Exception as exc:  # noqa: BLE001
                    print(f"sp500_earnings_yield: fetch failed ({exc})")
    return out
