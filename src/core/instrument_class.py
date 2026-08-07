"""Instrument classification — the canonical asset-class + news-group resolver.

**Domain layer.** This is the single place that answers "what KIND of thing is
this symbol?", derived from ``config/instruments.yaml`` (the registry you must
edit anyway to trade an instrument). It lives here, next to
``src.core.profile_loader``, rather than in a consumer package, because the
answer is a property of the instrument — not of whoever happens to be asking.

Two questions, one source:

``asset_class_for_symbol(symbol)``
    Coarse reporting bucket — ``crypto`` / ``index`` / ``commodity`` / ``bond``
    / ``equity`` / ``fx`` / ``unknown``. Consumed by the ``/performance``
    ``perAssetClass`` aggregate and the ``assetClass`` field on
    ``/positions`` / ``/trades/closed`` / ``/order-packages``.

``news_group_for_symbol(symbol)``
    Which NEWS FEED GROUP the symbol's stories come from. Derived from the
    asset class by default, with an optional per-instrument ``news_group``
    override in ``instruments.yaml`` for the cases where the coarse class is
    genuinely wrong (``USO`` is ``commodity`` but wants ENERGY headlines, not
    metals).

⚠️ **SCOPE CHANGE, 2026-08-07 — read before assuming this is reporting-only.**
This module's predecessor (``src/web/api/_asset_class.py``) documented itself as
"reporting-only ... NOTHING in the order path". That is **no longer true** and
the change is deliberate: ``news_group_for_symbol`` selects which RSS feeds the
M9 news layer reads, and the news layer can VETO a signal for every account
including real money (``NEWS_VETO_ENABLED``, default on when the source is
active). So a misclassification here can now suppress a trade.

It still does not influence SIZING or ROUTING — those read
``contract_value_usd`` / ``category`` from the same config, never this. But
"reporting-only" would be a stale comment, and a stale comment on a
trade-affecting path is exactly the class of defect this repo tracks.

Why this exists at all (``BL-20260807-NEWS-FEED-SYMBOL-COVERAGE-5-OF-24``):
the news layer used to carry its OWN hand-maintained per-symbol map
(``news_feeds.yaml::symbol_groups``) parallel to ``instruments.yaml``. A second
registry drifts — it covered 5 of 24 traded bases, so 19 symbols (including
every non-BTC/ETH crypto) read macro-only headlines while the veto was armed.
Deriving from the registry that already has to be correct removes the drift by
construction: adding an instrument gives it news coverage with no news edit.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CRYPTO = "crypto"
INDEX = "index"
COMMODITY = "commodity"
BOND = "bond"
EQUITY = "equity"
FX = "fx"
UNKNOWN = "unknown"

# Display order the consumers iterate (stable, business-readable).
CLASS_ORDER = [CRYPTO, INDEX, COMMODITY, BOND, EQUITY, FX, UNKNOWN]

# Heuristic roots (fallback only — the explicit override always wins). These
# are base-asset / symbol roots, not an exhaustive registry; they exist so an
# instrument added to instruments.yaml without an ``asset_class`` line still
# lands in the right bucket.
_INDEX_ROOTS = {"ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K"}
_COMMODITY_ROOTS = {
    "GC", "SI", "HG", "PL", "PA", "CL", "NG", "MGC", "MHG",
    "XAU", "XAG", "GLD", "SLV", "USO",
}
_BOND_ROOTS = {
    "TLT", "IEF", "AGG", "BND", "LQD", "HYG", "SHY", "TLH", "IEI", "SHV",
    "BNDX", "TIP",
}
_EQUITY_ROOTS = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI"}

# Quote/contract suffixes stripped when falling back from a full symbol
# (``XRPUSDT``) to its base (``XRP``). Longest-first so ``USDT`` wins over
# ``USD``.
_QUOTE_SUFFIXES = ("USDT", "USDC", "USDP", "PERP", "USD")


def base_of(symbol: Optional[str]) -> str:
    """Return the base asset of *symbol* (``XRPUSDT`` -> ``XRP``, ``SOL/ETH`` -> ``SOL``)."""
    base = str(symbol or "").upper().split("/")[0].strip()
    for suffix in _QUOTE_SUFFIXES:
        if base.endswith(suffix) and base != suffix:
            return base[: -len(suffix)]
    return base


def _infer(symbol: str, exchange: str, category: str, base_asset: str) -> str:
    """Best-effort asset class from an instrument's structural fields."""
    s = (symbol or "").upper()
    b = (base_asset or "").upper()
    e = (exchange or "").strip().lower()
    c = (category or "").strip().lower()

    # Commodity / index roots are checked first because their exchange
    # (interactive_brokers / alpaca) is ambiguous on its own.
    if b in _COMMODITY_ROOTS or s in _COMMODITY_ROOTS:
        return COMMODITY
    if b in _BOND_ROOTS or s in _BOND_ROOTS:
        return BOND
    if b in _INDEX_ROOTS or s in _INDEX_ROOTS:
        return INDEX
    if e == "bybit" or c in ("linear", "inverse"):
        return CRYPTO
    # Unregistered crypto perp convention (e.g. DOGEUSDT) — suffix heuristic so
    # a not-yet-tagged symbol still buckets as crypto instead of "unknown".
    if s.endswith(("USDT", "USDC", "USDP")):
        return CRYPTO
    if e == "oanda":
        return FX
    if e == "alpaca" or b in _EQUITY_ROOTS:
        return EQUITY
    return UNKNOWN


@lru_cache(maxsize=1)
def _tables() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Build ``({symbol: asset_class}, {symbol: news_group_override})``.

    One pass over ``config/instruments.yaml``, cached. Best-effort: a missing
    or malformed file yields empty tables and every lookup falls through to the
    per-symbol heuristic, so classification degrades rather than failing.
    """
    classes: Dict[str, str] = {}
    news_overrides: Dict[str, str] = {}
    try:
        import yaml

        from src.core.profile_loader import _DEFAULT_INSTRUMENTS_PATH

        with open(_DEFAULT_INSTRUMENTS_PATH, "r") as fh:
            raw = yaml.safe_load(fh) or {}
        for symbol, data in (raw.get("instruments", {}) or {}).items():
            data = data or {}
            key = str(symbol).upper()
            override = data.get("asset_class")
            if override and str(override).strip():
                classes[key] = str(override).strip().lower()
            else:
                classes[key] = _infer(
                    symbol,
                    data.get("exchange", ""),
                    data.get("category", ""),
                    data.get("base_asset", symbol),
                )
            ng = data.get("news_group")
            if ng and str(ng).strip():
                news_overrides[key] = str(ng).strip().lower()
    except FileNotFoundError:
        logger.debug("instrument_class: instruments.yaml not found; heuristic-only")
    except Exception:  # noqa: BLE001  # allow-silent: classification resolver — a config parse error is logged (logger.warning, exc_info) and falls back to the per-symbol heuristic; it must never break the read path or the news fetch
        logger.warning(
            "instrument_class: failed to load instruments.yaml", exc_info=True
        )
    return classes, news_overrides


def asset_class_for_symbol(symbol: Optional[str]) -> str:
    """Return the coarse asset class for *symbol* (``unknown`` if unresolved).

    Resolution order: explicit ``asset_class`` in instruments.yaml → the same
    entry looked up by BASE asset (so ``XRPUSDT`` resolves off an ``XRP`` row)
    → structural heuristic on the symbol root alone.
    """
    if not symbol:
        return UNKNOWN
    s = str(symbol).strip().upper()
    classes, _ = _tables()
    if s in classes:
        return classes[s]
    b = base_of(s)
    if b and b in classes:
        return classes[b]
    # Symbol absent from instruments.yaml — infer from the symbol root alone so
    # a not-yet-registered instrument still buckets instead of vanishing.
    return _infer(s, "", "", s)


def news_group_for_symbol(symbol: Optional[str]) -> Optional[str]:
    """Return the NEWS FEED GROUP token for *symbol*, or ``None``.

    ``None`` means "no class-specific group" — the caller still adds the shared
    ``global`` macro feeds. That is the correct answer for ``bond`` and ``fx``,
    whose news genuinely IS the macro feed (rates/Fed/inflation), not a
    mistake to be patched.

    Resolution order:
      1. explicit ``news_group`` on the instrument (full symbol, then base) —
         the narrow escape hatch for a symbol whose coarse asset class points
         at the wrong desk (``USO`` is ``commodity`` but wants ENERGY);
      2. otherwise the asset class itself, which the news config maps to a
         feed group via ``asset_class_groups``.

    Returning the asset class (rather than a feed group) keeps the
    class→group mapping in ``config/news_feeds.yaml`` where the rest of the
    news config lives — this module stays a pure classifier.
    """
    if not symbol:
        return None
    s = str(symbol).strip().upper()
    _, overrides = _tables()
    if s in overrides:
        return overrides[s]
    b = base_of(s)
    if b and b in overrides:
        return overrides[b]
    cls = asset_class_for_symbol(s)
    return None if cls == UNKNOWN else cls


def reset_cache() -> None:
    """Clear the cached tables (tests / hot-reload)."""
    _tables.cache_clear()
