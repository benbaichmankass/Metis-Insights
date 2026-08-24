"""Yahoo Finance symbol map + history caps — the LEAF that carries no imports.

Why this file exists, and why it is deliberately import-free
------------------------------------------------------------
This is a dict of ticker strings and a dict of ints. Reading it used to cost
the entire ``ml.datasets`` package: importing
``ml.datasets.adapters.yfinance_offvm`` executes ``ml/datasets/__init__.py``,
which imports ``.registry``, which imports **fourteen** dataset-family
builders — one of which (``families/account_context.py``) does ``import
yaml``. So a candle puller that needs a ticker string could not run on a host
that had ``pandas`` and ``yfinance`` but no ``pyyaml``, and the failure
surfaced as ``yfinance fetch failed: No module named 'yaml'`` — a message
naming a network fetch that never happened
(``diagnostic-provenance-guard`` sub-class **A**).

The remedy is NOT to install the transitive dependency on every consumer: that
leaves the coupling in place and the next transitive import breaks it again.
The data moves to a leaf that imports **nothing** local, so a caller can load
it by file path without executing any package ``__init__``.

⚠️ **This module must stay import-free of anything but ``typing``.** Adding a
relative import here re-creates exactly the coupling it was written to remove,
and would do so silently — the by-path loader in
``scripts/ops/fetch_backtest_candles.py`` would start failing with a
``ImportError: attempted relative import`` that reads like a missing file.
``tests/test_fetch_backtest_candles_yfinance.py`` asserts the import-free
property rather than trusting this comment.

``ml.datasets.adapters.yfinance_offvm`` re-exports every name here, so the map
still has exactly ONE home and existing importers are unchanged.
"""
from __future__ import annotations

from typing import Mapping

# Default yfinance ticker per bot symbol. `ES=F` (continuous front-month
# E-mini S&P) shares MES's price level and has the deepest yfinance
# history. Callers can override per build via the `ticker=` kwarg.
_DEFAULT_TICKER_MAP: Mapping[str, str] = {
    # `ES=F` (continuous front-month E-mini S&P) shares MES's price level and
    # has far deeper yfinance history than `MES=F`. Same reasoning for GC/HG.
    "MES": "ES=F",
    "MESUSD": "ES=F",
    "ES": "ES=F",
    "MGC": "GC=F",
    "XAUUSD": "GC=F",   # spot gold and the micro future both read GC=F here
    "MHG": "HG=F",
    # --- US equities / ETFs: pass through unchanged --------------------------
    # Listed EXPLICITLY rather than left to a pass-through default, so that
    # `known_symbols()` can answer "is this leg servable?" without guessing.
    # An unlisted symbol is UNKNOWN, not "probably fine as-is" — the whole
    # point of a coverage question is that absence must be visible.
    **{t: t for t in (
        "GDX", "GLD", "IAUM", "IEF", "IWM", "QLD", "QQQ",
        "SCHA", "SLV", "SPLG", "SPY", "TLT", "TQQQ", "USO",
    )},
}

# yfinance's intraday history caps, which BOUND what this adapter can serve.
# `1d` reaches back decades; `60m` is capped at roughly 730 days and `15m`/`5m`
# far less. A caller asking for a 5-year 1h span will silently get ~2 years
# unless it reads this, so the limit is DATA here rather than a comment.
YF_MAX_HISTORY_DAYS: Mapping[str, int | None] = {
    "1d": None,     # None = no practical cap
    "1h": 730,
    "15m": 60,
    "5m": 60,
    "1m": 7,
}


def known_symbols() -> frozenset[str]:
    """Symbols this adapter can resolve to a yfinance ticker.

    Exposed so a coverage caller can distinguish "we can serve this leg" from
    "we did not look", instead of discovering an unmapped symbol at fetch time.
    """
    return frozenset(_DEFAULT_TICKER_MAP)


def max_history_days(timeframe: str) -> int | None:
    """yfinance's history cap for `timeframe`, or None when uncapped.

    Raises for an unknown timeframe rather than returning None: silently
    reporting "uncapped" for a bar this adapter cannot serve is the failure
    mode that would make a truncated span look like a complete one.
    """
    if timeframe not in YF_MAX_HISTORY_DAYS:
        raise KeyError(
            f"no yfinance history cap recorded for timeframe {timeframe!r}; "
            f"known: {sorted(YF_MAX_HISTORY_DAYS)}")
    return YF_MAX_HISTORY_DAYS[timeframe]
