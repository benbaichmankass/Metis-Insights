"""Asset-class resolution for the reporting layer (read-only).

Maps a trade ``symbol`` to a coarse **asset class** so the dashboard / mobile
app can show a P&L breakdown across crypto / index / commodity / equity / fx —
the "where is performance divided" executive view.

This is a **reporting-only** helper. It is imported by the web API's
``/performance`` aggregation and NOTHING in the order path — asset class never
influences sizing, routing, or execution (those use ``contract_value_usd`` /
``category`` from the same config).

Source of truth, in order:
  1. An explicit per-instrument ``asset_class`` override in
     ``config/instruments.yaml`` (optional field — config-driven, so a future
     instrument can pin its class without a code change).
  2. A heuristic over the instrument's existing ``exchange`` / ``category`` /
     ``base_asset`` fields (so an *untagged* new instrument still classifies
     sensibly with no edit).

Canonical class tokens: ``crypto``, ``index``, ``commodity``, ``equity``,
``fx``, ``unknown``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CRYPTO = "crypto"
INDEX = "index"
COMMODITY = "commodity"
EQUITY = "equity"
FX = "fx"
UNKNOWN = "unknown"

# Display order the consumers iterate (stable, business-readable).
CLASS_ORDER = [CRYPTO, INDEX, COMMODITY, EQUITY, FX, UNKNOWN]

# Heuristic roots (fallback only — the explicit override always wins). These
# are base-asset / symbol roots, not an exhaustive registry; they exist so an
# instrument added to instruments.yaml without an ``asset_class`` line still
# lands in the right bucket.
_INDEX_ROOTS = {"ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K"}
_COMMODITY_ROOTS = {
    "GC", "SI", "HG", "PL", "PA", "CL", "NG", "MGC", "MHG",
    "XAU", "XAG", "GLD", "SLV", "USO",
}
_EQUITY_ROOTS = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI"}


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
def _table() -> Dict[str, str]:
    """Build {symbol: asset_class} from config/instruments.yaml (cached)."""
    table: Dict[str, str] = {}
    try:
        import yaml

        from src.core.profile_loader import _DEFAULT_INSTRUMENTS_PATH

        with open(_DEFAULT_INSTRUMENTS_PATH, "r") as fh:
            raw = yaml.safe_load(fh) or {}
        for symbol, data in (raw.get("instruments", {}) or {}).items():
            data = data or {}
            override = data.get("asset_class")
            if override and str(override).strip():
                table[symbol.upper()] = str(override).strip().lower()
            else:
                table[symbol.upper()] = _infer(
                    symbol,
                    data.get("exchange", ""),
                    data.get("category", ""),
                    data.get("base_asset", symbol),
                )
    except FileNotFoundError:
        logger.debug("_asset_class: instruments.yaml not found; heuristic-only")
    except Exception:  # noqa: BLE001  # reporting-only: never break /performance on a config parse error
        logger.warning("_asset_class: failed to load instruments.yaml", exc_info=True)
    return table


def asset_class_for_symbol(symbol: Optional[str]) -> str:
    """Return the coarse asset class for *symbol* (``unknown`` if unresolved)."""
    if not symbol:
        return UNKNOWN
    s = str(symbol).strip().upper()
    table = _table()
    if s in table:
        return table[s]
    # Symbol absent from instruments.yaml — infer from the symbol root alone so
    # a not-yet-registered instrument still buckets instead of vanishing.
    return _infer(s, "", "", s)


def reset_cache() -> None:
    """Clear the cached table (tests / hot-reload)."""
    _table.cache_clear()
