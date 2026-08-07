"""Asset-class resolution for the reporting layer — **re-export shim**.

The implementation moved to :mod:`src.core.instrument_class` on 2026-08-07.
It lives in the domain layer now because an instrument's class is a property of
the INSTRUMENT, not of the reporting surface that first needed it — and because
a second consumer (the M9 news layer, Layer 1 Signals) now resolves feed groups
from the same table. Reaching into a *private* module of the web API package
from a runtime signals component would have been the band-aid version.

This module stays so the ``/performance`` ``perAssetClass`` aggregate and the
``assetClass`` field on ``/positions`` / ``/trades/closed`` /
``/order-packages`` keep importing the path they always have. Nothing here has
behaviour of its own — edit :mod:`src.core.instrument_class`.

⚠️ The predecessor of this module described itself as "reporting-only ...
NOTHING in the order path". That is no longer true — see the scope note in
:mod:`src.core.instrument_class`: ``news_group_for_symbol`` selects the RSS
feeds the news layer reads, and the news layer can veto a signal. Asset class
still never influences sizing or routing.
"""

from __future__ import annotations

from src.core.instrument_class import (  # noqa: F401  (re-exported API)
    BOND,
    CLASS_ORDER,
    COMMODITY,
    CRYPTO,
    EQUITY,
    FX,
    INDEX,
    UNKNOWN,
    _infer,
    asset_class_for_symbol,
    reset_cache,
)

__all__ = [
    "CRYPTO",
    "INDEX",
    "COMMODITY",
    "BOND",
    "EQUITY",
    "FX",
    "UNKNOWN",
    "CLASS_ORDER",
    "asset_class_for_symbol",
    "reset_cache",
]
