"""FRED (Federal Reserve Economic Data) keyless daily macro fetcher (M19 corpus C0).

The **keyless, authoritative** counterpart to :mod:`ml.datasets.adapters.yfinance_macro`.
It produces the SAME daily macro side-stream `market_features` already joins (as-of,
past-only) — the volatility complex (VIX + VIX3M term structure), the dollar
(DXY-analog), and the rates curve (10y + 3m yields) — but sources every series from
**FRED's keyless CSV download endpoint** instead of Yahoo Finance.

Why a second source (M19 corpus workstream C0,
[`docs/research/T0-data-corpus-DESIGN.md`](../../../docs/research/T0-data-corpus-DESIGN.md)):
the rates leg was the weakest-wired part of the macro block — Yahoo's `^TNX` / `^IRX`
are *unofficial* tickers that rate-limit and break, whereas FRED (a US-government
data service) publishes the same Treasury-yield series keylessly and stably. So this
adapter **completes the half-wired MES rates leg** with a reliable source, and — because
FRED also carries the VIX / 3-month-VIX / broad-dollar series — it can supply the whole
macro complex with no API key at all. It is the first concrete step of the wide
multi-asset corpus (a clean, free, stable daily source to build the ingestion pattern on).

**No new feature columns / no schema change.** This adapter feeds the *existing*
:func:`ml.datasets.macro_features.compute_macro_feature_rows`, so it emits exactly the
:data:`ml.datasets.macro_features.MACRO_FEATURE_COLUMNS` the Yahoo adapter does — it is a
drop-in *source* alternative, joinable through the same `macro_path`. The corpus's
standing parquet catalog is a later increment (design C1+); this C0 step reuses the
tested side-stream mechanism.

**Off-VM only.** Reuses the exact ``ICT_OFFVM_BUILD_HOST=1`` guard contract every
network adapter shares — heavy market-data pulls do not belong on the live trading VM.
Runs on the trainer VM / a build host, never the money box. Read-mostly, never a
live-path or `trade_journal.db` write.

Default FRED series ids (override per build via ``series=``):

| series   | FRED id    | meaning                                        |
|----------|------------|------------------------------------------------|
| ``vix``  | ``VIXCLS`` | CBOE Volatility Index (30-day implied vol)     |
| ``vix3m``| ``VXVCLS`` | CBOE 3-Month Volatility Index (term structure) |
| ``dxy``  | ``DTWEXBGS``| Nominal Broad US Dollar Index (dollar level)  |
| ``ust10y``| ``DGS10`` | 10-Year Treasury Constant-Maturity yield (%)   |
| ``ust3m``| ``DGS3MO`` | 3-Month Treasury Constant-Maturity yield (%)   |

FRED marks a missing/holiday observation with a bare ``.`` — those rows are skipped
(the series simply has no reading that day), exactly like an absent Yahoo close.

Tests monkeypatch ``_download`` so CI never touches the network (same hook as the
yfinance adapters).
"""
from __future__ import annotations

import csv
import io
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping

from ..macro_features import compute_macro_feature_rows

# Reuse the one off-VM env-gate every network adapter shares.
from .bybit_offvm import OFFVM_ENV, OFFVM_EXPECTED, OffVmGuardrailViolation

# series-name -> default FRED series id.
DEFAULT_SERIES: Mapping[str, str] = {
    "vix": "VIXCLS",
    "vix3m": "VXVCLS",
    "dxy": "DTWEXBGS",
    "ust10y": "DGS10",
    "ust3m": "DGS3MO",
}

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED encodes a missing observation as a bare period.
_MISSING_TOKENS = frozenset({"", "."})


def _enforce_offvm() -> None:
    if os.environ.get(OFFVM_ENV, "") != OFFVM_EXPECTED:
        raise OffVmGuardrailViolation(
            f"fred_macro requires {OFFVM_ENV}={OFFVM_EXPECTED} to run. It "
            "MUST NOT run on the Oracle live VM. Set the env var only on a build "
            "host that is not the live VM."
        )


def _to_date(value: Any) -> str:
    """Normalise a FRED observation date to a UTC ``YYYY-MM-DD`` string."""
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _download(*, series_id: str, start: str, end: str | None) -> str:
    """Fetch one FRED series as raw CSV text. Tests monkeypatch this.

    Uses FRED's **keyless** `fredgraph.csv` download endpoint — no API key, no SDK.
    Lazy stdlib-only (``urllib``) so a build host needs nothing extra installed.
    """
    params: dict[str, str] = {"id": series_id, "cosd": _to_date(start)}
    if end:
        params["coed"] = _to_date(end)
    url = f"{FRED_CSV_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
        return resp.read().decode("utf-8")


def _daily_values(series_id: str, start: str, end: str | None) -> dict[str, float]:
    """``{date: value}`` for one FRED series over ``[start, end]``.

    FRED CSV is ``observation_date,<SERIES_ID>`` with a bare ``.`` for a missing
    reading. The header column name varies (older exports use ``DATE``); we read
    positionally (col 0 = date, col 1 = value) so a header rename can't break us.
    """
    text = _download(series_id=series_id, start=start, end=end)
    out: dict[str, float] = {}
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header
    for row in reader:
        if len(row) < 2:
            continue
        date_raw, value_raw = row[0].strip(), row[1].strip()
        if not date_raw or value_raw in _MISSING_TOKENS:
            continue
        try:
            out[_to_date(date_raw)] = float(value_raw)
        except ValueError:
            continue
    return out


def fetch_fred_macro_rows(
    *,
    start: str,
    end: str | None = None,
    series: Mapping[str, str] | None = None,
    zscore_window_n: int = 20,
    return_window_n: int = 5,
) -> list[dict[str, Any]]:
    """Computed daily macro feature rows over ``[start, end]`` from FRED.

    Fetches each series' daily value keylessly from FRED, merges them by calendar
    date into raw daily observations, then runs the SAME
    :func:`ml.datasets.macro_features.compute_macro_feature_rows` the Yahoo adapter
    uses (trailing-window z-scores/slopes + the one-day leakage lag). The result rows
    carry ``ts`` + :data:`ml.datasets.macro_features.MACRO_FEATURE_COLUMNS` and are what
    ``market_features`` as-of joins — identical shape, different (keyless, stable) source.
    """
    _enforce_offvm()
    # Caller overrides win, but unspecified series keep their default FRED id.
    smap: dict[str, str] = {**DEFAULT_SERIES, **(dict(series) if series else {})}

    by_series: dict[str, dict[str, float]] = {}
    for name, series_id in smap.items():
        by_series[name] = _daily_values(series_id, start, end)

    # Union of all observed dates; each daily row carries whatever each series had.
    all_dates: set[str] = set()
    for values in by_series.values():
        all_dates.update(values.keys())

    daily: list[dict[str, Any]] = []
    for date_str in sorted(all_dates):
        row: dict[str, Any] = {"date": date_str}
        for name in smap:
            row[name] = by_series[name].get(date_str)
        daily.append(row)

    return compute_macro_feature_rows(
        daily, zscore_window_n=zscore_window_n, return_window_n=return_window_n
    )
