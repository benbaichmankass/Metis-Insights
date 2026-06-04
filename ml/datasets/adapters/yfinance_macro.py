"""Yahoo Finance off-VM cross-asset/macro fetcher (S-MLOPT-S12, M14 Phase 2.4).

Produces the daily macro side-stream that `market_features` joins (as-of,
past-only) to compute the S-MLOPT-S12 macro conditioning columns for MES — the
volatility complex (VIX + VIX3M term structure), the dollar (DXY), and the rates
curve (10y + 3m yields). Kept SEPARATE from `market_raw` (which stays canonical
OHLCV-only), the same architectural split S9 / S11 / WS5-B used.

**Off-VM only.** Reuses the exact `ICT_OFFVM_BUILD_HOST=1` guard contract as the
Bybit + yfinance OHLCV adapters — heavy market-data pulls do not belong on the
live trading VM (WS9 rule). Runs on the trainer VM / a build host.

The macro series are daily; the leakage-lag (a day's close-based features are
stamped at the next day's open) lives in
``macro_features.compute_macro_feature_rows`` — this adapter only fetches the raw
daily closes and merges them by date before handing off to that computation.

Default Yahoo tickers (override per build via ``tickers=``):

| series   | Yahoo ticker | meaning                                   |
|----------|--------------|-------------------------------------------|
| ``vix``  | ``^VIX``     | CBOE 30-day implied vol                   |
| ``vix3m``| ``^VIX3M``   | CBOE 3-month implied vol (term structure) |
| ``dxy``  | ``DX-Y.NYB`` | US Dollar Index                           |
| ``ust10y``| ``^TNX``    | 10-year Treasury yield (×10)              |
| ``ust3m``| ``^IRX``     | 13-week T-bill yield (×10)                |

Tests monkeypatch ``_download`` so CI never touches the network (same hook as
the OHLCV yfinance adapter).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

from ..macro_features import compute_macro_feature_rows

# Reuse the one off-VM env-gate every network adapter shares.
from .bybit_offvm import OFFVM_ENV, OFFVM_EXPECTED, OffVmGuardrailViolation

# series-name -> default Yahoo ticker.
DEFAULT_TICKERS: Mapping[str, str] = {
    "vix": "^VIX",
    "vix3m": "^VIX3M",
    "dxy": "DX-Y.NYB",
    "ust10y": "^TNX",
    "ust3m": "^IRX",
}


def _enforce_offvm() -> None:
    if os.environ.get(OFFVM_ENV, "") != OFFVM_EXPECTED:
        raise OffVmGuardrailViolation(
            f"yfinance_macro requires {OFFVM_ENV}={OFFVM_EXPECTED} to run. It "
            "MUST NOT run on the Oracle live VM. Set the env var only on a build "
            "host that is not the live VM."
        )


def _to_date(value: Any) -> str:
    """Normalise a pandas Timestamp / datetime to a UTC ``YYYY-MM-DD`` date."""
    ts = value
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if not isinstance(ts, datetime):
        ts = datetime.fromisoformat(str(value))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).date().isoformat()


def _download(*, ticker: str, start: str, end: str | None) -> Any:
    """Fetch a daily OHLCV DataFrame from yfinance. Tests monkeypatch this.

    Lazy-imports yfinance so a build host without it installed still hits the
    env-gate guardrail (and the unit tests, which patch this hook).
    """
    try:
        import yfinance as yf  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "yfinance is required for yfinance_macro; install with "
            "`pip install yfinance` on the build host."
        ) from e
    return yf.download(
        tickers=ticker,
        interval="1d",
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=False,
    )


def _daily_closes(ticker: str, start: str, end: str | None) -> dict[str, float]:
    """``{date: close}`` for one ticker over ``[start, end)``."""
    frame = _download(ticker=ticker, start=start, end=end)
    if frame is None or len(frame) == 0:
        return {}
    columns = frame.columns
    if hasattr(columns, "nlevels") and columns.nlevels > 1:
        frame = frame.copy()
        frame.columns = columns.get_level_values(0)
    col = {c.lower(): c for c in frame.columns}
    if "close" not in col:
        raise ValueError(
            f"yfinance frame for {ticker!r} missing 'close'; got {list(frame.columns)}"
        )
    close_col = col["close"]
    out: dict[str, float] = {}
    for index_value, row in frame.iterrows():
        close = row[close_col]
        if close is None or close != close:  # noqa: PLR0124 (NaN check)
            continue
        out[_to_date(index_value)] = float(close)
    return out


def fetch_macro_rows(
    *,
    start: str,
    end: str | None = None,
    tickers: Mapping[str, str] | None = None,
    zscore_window_n: int = 20,
    return_window_n: int = 5,
) -> list[Mapping[str, Any]]:
    """Computed daily macro feature rows over ``[start, end)``.

    Fetches each macro series' daily close, merges them by calendar date into raw
    daily observations, then runs ``compute_macro_feature_rows`` (which applies
    the trailing-window z-scores/slopes and the one-day leakage lag). The result
    rows carry ``ts`` + :data:`macro_features.MACRO_FEATURE_COLUMNS` and are what
    ``market_features`` as-of joins.
    """
    _enforce_offvm()
    # Caller overrides win, but unspecified series keep their default ticker.
    tmap_final: dict[str, str] = {**DEFAULT_TICKERS, **(dict(tickers) if tickers else {})}

    by_series: dict[str, dict[str, float]] = {}
    for series, ticker in tmap_final.items():
        by_series[series] = _daily_closes(ticker, start, end)

    # Union of all observed dates; each daily row carries whatever each series had.
    all_dates: set[str] = set()
    for closes in by_series.values():
        all_dates.update(closes.keys())

    daily: list[dict[str, Any]] = []
    for date_str in sorted(all_dates):
        row: dict[str, Any] = {"date": date_str}
        for series in tmap_final:
            row[series] = by_series[series].get(date_str)
        daily.append(row)

    return compute_macro_feature_rows(
        daily, zscore_window_n=zscore_window_n, return_window_n=return_window_n
    )
